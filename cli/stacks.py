"""
Stack management for GCO CLI.

Provides commands for deploying, updating, and managing CDK stacks.

This module handles:
- Container runtime detection (Docker, Finch, Podman)
- CDK stack deployment with proper dependency ordering
- Parallel deployment of regional stacks
- Lambda source synchronization before deployment
"""

from __future__ import annotations

import logging
import os
import shutil
import site
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from .config import GCOConfig

logger = logging.getLogger(__name__)


@dataclass
class StackInfo:
    """Information about a CDK stack."""

    name: str
    status: str
    region: str
    created_time: datetime | None = None
    updated_time: datetime | None = None
    outputs: dict[str, str] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "region": self.region,
            "created_time": self.created_time.isoformat() if self.created_time else None,
            "updated_time": self.updated_time.isoformat() if self.updated_time else None,
            "outputs": self.outputs,
            "tags": self.tags,
        }


def _safe_rmtree(path: Path) -> None:
    """Remove a directory tree, handling broken symlinks on macOS.

    shutil.rmtree can fail with ``OSError: [Errno 66] Directory not empty``
    on macOS when pip-installed packages (e.g. botocore) contain broken
    symlinks or extended-attribute resource forks.

    Falls back to ``rm -rf`` via subprocess, but only after validating the
    path is a real directory under the project tree to avoid accidents.
    """
    resolved = path.resolve()

    # Safety: refuse to remove anything that isn't clearly a build artifact
    # inside the project.  The path must contain "lambda" and end with "-build".
    if "lambda" not in resolved.parts or not resolved.name.endswith("-build"):
        raise ValueError(f"Refusing to remove unexpected path: {resolved}")

    try:
        shutil.rmtree(str(resolved))
    except OSError:
        subprocess.run(["rm", "-rf", "--", str(resolved)], check=True)


# Cached result for container runtime detection (None = not yet checked)
_container_runtime_cache: str | None = None
_container_runtime_checked: bool = False


def _detect_container_runtime() -> str | None:
    """
    Detect available container runtime for CDK asset bundling.

    CDK requires a container runtime to build Lambda function assets.
    This function checks for available runtimes in order of preference
    and verifies they are actually running (not just installed).

    Priority order: docker > finch > podman

    Returns:
        Runtime name ('docker', 'finch', or 'podman') if found and running,
        None if no runtime is available.

    Note:
        If CDK_DOCKER environment variable is already set, that value
        is returned without checking if the runtime is available.
    """
    global _container_runtime_cache, _container_runtime_checked
    if _container_runtime_checked:
        return _container_runtime_cache

    _container_runtime_cache = _detect_container_runtime_uncached()
    _container_runtime_checked = True
    return _container_runtime_cache


def _detect_container_runtime_uncached() -> str | None:
    """Uncached implementation of container runtime detection."""
    # Check if CDK_DOCKER is already set
    if os.environ.get("CDK_DOCKER"):
        return os.environ["CDK_DOCKER"]

    # Try docker first
    if shutil.which("docker"):
        # Verify docker is actually running
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return "docker"
        except subprocess.TimeoutExpired, Exception:
            pass

    # Try finch as fallback
    if shutil.which("finch"):
        try:
            result = subprocess.run(
                ["finch", "info"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return "finch"
        except subprocess.TimeoutExpired, Exception:
            pass

    # Try podman as last resort
    if shutil.which("podman"):
        try:
            result = subprocess.run(
                ["podman", "info"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return "podman"
        except subprocess.TimeoutExpired, Exception:
            pass

    return None


class StackManager:
    """Manages CDK stack operations."""

    def __init__(self, config: GCOConfig, project_root: Path | None = None):
        self.config = config
        self.project_root = project_root or self._find_project_root()
        self._cdk_path = self._find_cdk()

        # Ensure Lambda build directory exists before any CDK synth
        self._ensure_lambda_build()

    def _find_project_root(self) -> Path:
        """Find the project root by looking for cdk.json."""
        current = Path.cwd()
        for parent in [current] + list(current.parents):
            if (parent / "cdk.json").exists():
                return parent
        return current

    def _find_cdk(self) -> str:
        """Find CDK executable."""
        # Check if cdk is in PATH
        try:
            result = subprocess.run(["which", "cdk"], capture_output=True, text=True, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            pass

        # Check common locations
        for path in ["/usr/local/bin/cdk", "~/.npm-global/bin/cdk"]:
            expanded = os.path.expanduser(path)
            if os.path.exists(expanded):
                return expanded

        # Fall back to npx
        return "npx cdk"

    def _ensure_lambda_build(self) -> None:
        """Ensure the Lambda build directories exist for CDK synthesis.

        Called from __init__ so the directory is ready before any CDK synth
        (even ``cdk list`` needs it because ``app.py`` references the asset).

        This does a lightweight check — only builds if the directory is
        missing or incomplete. It does NOT do a full rebuild (no rmtree).
        The full rebuild happens in ``_rebuild_lambda_packages()``, which
        is called by ``deploy()`` before the actual CDK deploy.
        """
        kubectl_source = self.project_root / "lambda" / "kubectl-applier-simple"
        kubectl_build = self.project_root / "lambda" / "kubectl-applier-simple-build"
        helm_source = self.project_root / "lambda" / "helm-installer"
        helm_build = self.project_root / "lambda" / "helm-installer-build"

        # Only build if the directory is missing or deps aren't installed
        if kubectl_source.exists() and (
            not kubectl_build.exists() or not (kubectl_build / "yaml").exists()
        ):
            self._build_kubectl_lambda()

        if helm_source.exists() and not helm_build.exists():
            self._build_helm_installer_lambda()

    def _check_and_fix_stuck_stack(self, stack_name: str) -> None:
        """Check if a stack is in a stuck state and auto-recover.

        Stacks can get stuck in REVIEW_IN_PROGRESS, ROLLBACK_COMPLETE, or
        other non-deployable states. This detects those and cleans up so
        the next deploy can succeed.
        """
        import boto3

        region = self._get_deploy_region(stack_name)
        if not region:
            return

        try:
            cfn = boto3.client("cloudformation", region_name=region)
            response = cfn.describe_stacks(StackName=stack_name)
            status = response["Stacks"][0]["StackStatus"]

            stuck_states = {
                "REVIEW_IN_PROGRESS",
                "ROLLBACK_COMPLETE",
                "ROLLBACK_FAILED",
                "CREATE_FAILED",
                "DELETE_FAILED",
            }

            if status in stuck_states:
                print(f"  Stack {stack_name} is in {status} state, cleaning up...")
                cfn.delete_stack(StackName=stack_name)
                waiter = cfn.get_waiter("stack_delete_complete")
                waiter.wait(StackName=stack_name, WaiterConfig={"Delay": 10, "MaxAttempts": 60})
                print(f"  Stack {stack_name} cleaned up, will recreate on deploy")

        except Exception as e:
            logger.debug("Stack pre-check for %s: %s", stack_name, e)
            # Stack doesn't exist or can't be described — fine, deploy will create it

    def _diagnose_deploy_failure(self, stack_name: str) -> None:
        """Fetch CloudFormation events after a failed deploy and print diagnostics.

        Gives users actionable information instead of just the CDK error message.
        """
        import boto3

        region = self._get_deploy_region(stack_name)
        if not region:
            return

        try:
            cfn = boto3.client("cloudformation", region_name=region)

            # Get recent events
            response = cfn.describe_stack_events(StackName=stack_name)
            events = response.get("StackEvents", [])

            # Filter to failed events
            failed = [
                e
                for e in events[:20]
                if "FAILED" in e.get("ResourceStatus", "")
                or "ROLLBACK" in e.get("ResourceStatus", "")
            ]

            if failed:
                print(f"\n  CloudFormation failure details for {stack_name}:")
                for event in failed[:5]:
                    resource = event.get("LogicalResourceId", "unknown")
                    status = event.get("ResourceStatus", "unknown")
                    reason = event.get("ResourceStatusReason", "no reason given")
                    print(f"    {resource}: {status}")
                    print(f"      {reason}")

            # Check stack status for actionable advice
            try:
                stack_resp = cfn.describe_stacks(StackName=stack_name)
                status = stack_resp["Stacks"][0]["StackStatus"]

                advice = {
                    "REVIEW_IN_PROGRESS": (
                        "Stack is stuck in REVIEW_IN_PROGRESS. "
                        "Run: aws cloudformation delete-stack "
                        f"--stack-name {stack_name} --region {region}"
                    ),
                    "ROLLBACK_COMPLETE": (
                        "Stack rolled back. Delete it and retry: "
                        f"aws cloudformation delete-stack "
                        f"--stack-name {stack_name} --region {region}"
                    ),
                    "ROLLBACK_FAILED": (
                        "Stack rollback failed. Delete with --retain: "
                        f"aws cloudformation delete-stack "
                        f"--stack-name {stack_name} --region {region}"
                    ),
                    "UPDATE_ROLLBACK_COMPLETE": (
                        "Update rolled back but stack is stable. "
                        "Check the events above and retry the deploy."
                    ),
                }

                if status in advice:
                    print(f"\n  Suggested fix: {advice[status]}")

            except Exception as e:
                logger.debug("Failed to parse stack events: %s", e)

        except Exception as e:
            logger.debug("Failed to diagnose deploy failure for %s: %s", stack_name, e)
            # Best effort — don't fail the deploy further

    def _sync_lambda_sources(self) -> None:
        """
        Sync latest handler code and manifests into the build directory.

        Called at the start of ``deploy()`` to ensure CDK picks up the
        latest source changes. Only copies files — does NOT rebuild pip
        deps or rmtree. Runs once per StackManager instance.
        """
        if getattr(self, "_lambda_sources_synced", False):
            return
        self._lambda_sources_synced = True

        source_dir = self.project_root / "lambda" / "kubectl-applier-simple"
        build_dir = self.project_root / "lambda" / "kubectl-applier-simple-build"

        if not source_dir.exists() or not build_dir.exists():
            return

        # Sync handler.py
        source_handler = source_dir / "handler.py"
        build_handler = build_dir / "handler.py"
        if source_handler.exists():
            shutil.copy2(source_handler, build_handler)

        # Sync manifests directory
        source_manifests = source_dir / "manifests"
        build_manifests = build_dir / "manifests"
        if source_manifests.exists():
            build_manifests.mkdir(parents=True, exist_ok=True)
            for manifest_file in source_manifests.glob("*.yaml"):
                shutil.copy2(manifest_file, build_manifests / manifest_file.name)

    def _rebuild_lambda_packages(self) -> None:
        """Full rebuild of Lambda packages for deploy.

        Nukes the build directories and recreates them from scratch with
        fresh pip deps, handler code, and manifests. Called once at the
        start of a deploy to ensure CDK picks up all changes.

        Runs once per StackManager instance.
        """
        if getattr(self, "_lambda_packages_rebuilt", False):
            return
        self._lambda_packages_rebuilt = True
        self._build_lambda_packages()

    def _build_lambda_packages(self) -> None:
        """Build Lambda packages for kubectl-applier and helm-installer.

        Creates build directories with fresh copies of handler code, manifests,
        charts config, and pip dependencies. This ensures CDK always picks up
        the latest content regardless of Docker/asset caching.
        """
        self._build_kubectl_lambda()
        self._build_helm_installer_lambda()

    def _build_kubectl_lambda(self) -> None:
        """Build the kubectl-applier-simple Lambda package."""
        source_dir = self.project_root / "lambda" / "kubectl-applier-simple"
        build_dir = self.project_root / "lambda" / "kubectl-applier-simple-build"
        requirements = source_dir / "requirements.txt"

        if not source_dir.exists() or not requirements.exists():
            return

        print("  Building kubectl-applier-simple Lambda package...")

        # Clean stale build directory to avoid broken symlinks from previous
        # pip installs (botocore/data/ is a common source of dangling symlinks
        # that cause CDK's asset fingerprinting to fail with ENOENT).
        if build_dir.exists():
            _safe_rmtree(build_dir)

        # Create build directory
        build_dir.mkdir(parents=True, exist_ok=True)

        # Copy handler and manifests (always overwrite to prevent stale content)
        shutil.copy2(source_dir / "handler.py", build_dir / "handler.py")
        build_manifests = build_dir / "manifests"
        if (source_dir / "manifests").exists():
            if build_manifests.exists():
                shutil.rmtree(build_manifests)
            shutil.copytree(source_dir / "manifests", build_manifests, dirs_exist_ok=True)

        # Install pip dependencies for Lambda runtime
        import sys

        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit - static list: sys.executable + pip args + Path objects, no user input
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                str(requirements),
                "-t",
                str(build_dir),
                "--upgrade",
                "--platform",
                "manylinux2014_x86_64",
                "--only-binary=:all:",
                "--quiet",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  Warning: pip install failed: {result.stderr[:200]}")
        else:
            print("  Lambda package built successfully")

    def _build_helm_installer_lambda(self) -> None:
        """Build the helm-installer Lambda Docker context.

        Copies all source files into a clean build directory so CDK always
        detects changes to charts.yaml, handler.py, Dockerfile, etc.
        """
        source_dir = self.project_root / "lambda" / "helm-installer"
        build_dir = self.project_root / "lambda" / "helm-installer-build"

        if not source_dir.exists():
            return

        print("  Building helm-installer Lambda package...")

        # Clean and recreate build directory
        if build_dir.exists():
            _safe_rmtree(build_dir)
        build_dir.mkdir(parents=True, exist_ok=True)

        # Copy all source files into the build directory
        for item in source_dir.iterdir():
            if item.name == "__pycache__":
                continue
            if item.is_file():
                shutil.copy2(item, build_dir / item.name)
            elif item.is_dir():
                shutil.copytree(item, build_dir / item.name, dirs_exist_ok=True)

        print("  Helm installer package built successfully")

    def _get_python_path(self) -> str:
        """
        Get PYTHONPATH that includes the current Python's site-packages.

        This is critical for pipx installations where CDK runs `python3 app.py`
        using the system Python, which doesn't have aws_cdk installed.
        By setting PYTHONPATH, we ensure CDK's subprocess can find our modules.
        """
        # Get all site-packages directories from the current Python
        site_packages = site.getsitepackages()

        # Also include user site-packages if available
        user_site = site.getusersitepackages()
        if user_site and os.path.isdir(user_site):
            site_packages.append(user_site)

        # Include the directory containing the current module (for editable installs)
        current_module_dir = Path(__file__).parent.parent
        if current_module_dir.exists():
            site_packages.append(str(current_module_dir))

        # Combine with existing PYTHONPATH if any
        existing_path = os.environ.get("PYTHONPATH", "")
        all_paths = site_packages + ([existing_path] if existing_path else [])

        return os.pathsep.join(all_paths)

    def _run_cdk(
        self,
        command: list[str],
        capture_output: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a CDK command."""
        full_env = os.environ.copy()

        # Inject PYTHONPATH so CDK's python3 subprocess can find aws_cdk
        # This is essential for pipx installations
        full_env["PYTHONPATH"] = self._get_python_path()

        if env:
            full_env.update(env)

        cdk_cmd = self._cdk_path.split() + command

        if capture_output:
            return subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit - cdk_cmd is a list of static CDK subcommands, no user-controlled shell injection
                cdk_cmd,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                env=full_env,
            )
        return subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit - cdk_cmd is a list of static CDK subcommands, no user-controlled shell injection
            cdk_cmd,
            cwd=self.project_root,
            env=full_env,
            text=True,
        )

    def list_stacks(self) -> list[str]:
        """List all available CDK stacks."""
        result = self._run_cdk(["list"], capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to list stacks: {result.stderr}")
        return [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]

    def synth(self, stack_name: str | None = None, quiet: bool = True) -> str:
        """Synthesize CloudFormation templates."""
        cmd = ["synth"]
        if stack_name:
            cmd.append(stack_name)
        if quiet:
            cmd.append("--quiet")

        result = self._run_cdk(cmd, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(f"CDK synth failed: {result.stderr}")
        return str(result.stdout)

    def diff(self, stack_name: str | None = None) -> str:
        """Show diff between deployed and local stacks."""
        cmd = ["diff", "--no-color"]
        if stack_name:
            cmd.append(stack_name)

        result = self._run_cdk(cmd, capture_output=True)
        # diff returns non-zero if there are differences, which is expected
        return str(result.stdout or result.stderr)

    def deploy(
        self,
        stack_name: str | None = None,
        require_approval: bool = True,
        all_stacks: bool = False,
        outputs_file: str | None = None,
        parameters: dict[str, str] | None = None,
        tags: dict[str, str] | None = None,
        progress: str = "events",
        output_dir: str | None = None,
        exclusively: bool = False,
    ) -> bool:
        """Deploy CDK stacks.

        Args:
            stack_name: Name of the stack to deploy
            require_approval: Whether to require approval for changes
            all_stacks: Deploy all stacks
            outputs_file: File to write outputs to
            parameters: CDK parameters
            tags: Tags to apply to stacks
            progress: Progress display type
            output_dir: Custom CDK output directory (for parallel deployments)
            exclusively: Pass ``--exclusively`` to CDK so only the named
                stack is evaluated, not its transitive dependencies. Used by
                ``deploy_orchestrated`` once earlier phases have already
                deployed the globals — re-synthesizing them every phase
                forces custom resources (notably KubectlApplyManifests)
                to re-run each time, adding minutes per phase for no
                actual change.
        """
        # Full rebuild of Lambda packages on first deploy call (once per session),
        # then sync latest source on subsequent calls
        self._rebuild_lambda_packages()
        self._sync_lambda_sources()

        # Check for stuck stacks and auto-recover
        if stack_name:
            self._check_and_fix_stuck_stack(stack_name)

        # Ensure container runtime is available for building images
        runtime = _detect_container_runtime()
        if not runtime:
            raise RuntimeError(
                "No container runtime found. Please install Docker, Finch, or Podman.\n"
                "  - Docker: https://docs.docker.com/get-docker/\n"
                "  - Finch: brew install finch && finch vm init\n"
                "  - Podman: https://podman.io/getting-started/installation"
            )

        # Auto-bootstrap the target region if needed
        if stack_name:
            region = self._get_deploy_region(stack_name)
            if region and not self.ensure_bootstrapped(region):
                raise RuntimeError(
                    f"Region {region} could not be bootstrapped. "
                    "Run 'gco stacks bootstrap --region "
                    f"{region}' manually to diagnose."
                )

        cmd = ["deploy"]

        if all_stacks:
            cmd.append("--all")
        elif stack_name:
            cmd.append(stack_name)

        # --exclusively tells CDK to deploy *only* the named stack, not its
        # transitive dependencies. deploy_orchestrated sets this once the
        # earlier phases (global, api-gateway) are already in place so that
        # the regional and monitoring phases don't re-synthesize and
        # re-evaluate globals on every pass.
        if exclusively and stack_name and not all_stacks:
            cmd.append("--exclusively")

        if not require_approval:
            cmd.extend(["--require-approval", "never"])

        if outputs_file:
            cmd.extend(["--outputs-file", outputs_file])

        if parameters:
            for key, value in parameters.items():
                cmd.extend(["--parameters", f"{key}={value}"])

        if tags:
            for key, value in tags.items():
                cmd.extend(["--tags", f"{key}={value}"])

        cmd.extend(["--progress", progress])

        # Use custom output directory for parallel deployments
        if output_dir:
            cmd.extend(["--output", output_dir])

        # Set CDK_DOCKER env var if not already set
        env = {"CDK_DOCKER": runtime} if not os.environ.get("CDK_DOCKER") else None

        result = self._run_cdk(cmd, env=env)
        success = result.returncode == 0

        if not success and stack_name:
            self._diagnose_deploy_failure(stack_name)

        return success

    def destroy(
        self,
        stack_name: str | None = None,
        all_stacks: bool = False,
        force: bool = False,
        output_dir: str | None = None,
    ) -> bool:
        """Destroy CDK stacks.

        Args:
            stack_name: Name of the stack to destroy
            all_stacks: Destroy all stacks
            force: Skip confirmation prompts
            output_dir: Custom CDK output directory (for parallel deployments)
        """
        cmd = ["destroy"]

        if all_stacks:
            cmd.append("--all")
        elif stack_name:
            cmd.append(stack_name)

        if force:
            cmd.append("--force")

        # Use custom output directory for parallel deployments
        if output_dir:
            cmd.extend(["--output", output_dir])

        result = self._run_cdk(cmd)
        return result.returncode == 0

    def bootstrap(
        self,
        account: str | None = None,
        region: str | None = None,
    ) -> bool:
        """Bootstrap CDK in an AWS account/region."""
        cmd = ["bootstrap"]

        if account and region:
            cmd.append(f"aws://{account}/{region}")
        elif region:
            cmd.append(f"aws://unknown-account/{region}")

        result = self._run_cdk(cmd)
        return result.returncode == 0

    def is_bootstrapped(self, region: str) -> bool:
        """Check if CDK has been bootstrapped in a region.

        Looks for the CDKToolkit CloudFormation stack which is created
        by ``cdk bootstrap``. Result is cached per region for the lifetime
        of this StackManager instance.
        """
        if not hasattr(self, "_bootstrap_cache"):
            self._bootstrap_cache: dict[str, bool] = {}

        if region in self._bootstrap_cache:
            return self._bootstrap_cache[region]

        import boto3

        cf = boto3.client("cloudformation", region_name=region)
        try:
            response = cf.describe_stacks(StackName="CDKToolkit")
            stacks = response.get("Stacks", [])
            if stacks:
                status = stacks[0].get("StackStatus", "")
                # Any non-deleted state counts as bootstrapped
                result = "DELETE" not in status
                self._bootstrap_cache[region] = result
                return result
        except ClientError:
            pass  # Stack doesn't exist — not bootstrapped
        except Exception as e:
            logger.debug("Failed to check CDK bootstrap in %s: %s", region, e)

        self._bootstrap_cache[region] = False
        return False

    def ensure_bootstrapped(self, region: str) -> bool:
        """Ensure a region is CDK-bootstrapped, auto-bootstrapping if needed.

        Returns True if the region is (or was successfully) bootstrapped.
        """
        if self.is_bootstrapped(region):
            return True

        print(f"ℹ Region {region} is not CDK-bootstrapped. Bootstrapping now...")
        success = self.bootstrap(region=region)
        if success:
            # Update cache so we don't re-check this region
            if not hasattr(self, "_bootstrap_cache"):
                self._bootstrap_cache = {}
            self._bootstrap_cache[region] = True
            print(f"✓ CDK bootstrapped in {region}")
        else:
            print(f"✗ Failed to bootstrap CDK in {region}")
        return success

    def _get_deploy_region(self, stack_name: str) -> str | None:
        """Determine the target AWS region for a given stack name."""
        from .config import _load_cdk_json

        cdk_regions = _load_cdk_json()

        region: str | None
        if stack_name == "gco-global":
            region = cdk_regions.get("global") or self.config.global_region
            return region
        if stack_name == "gco-api-gateway":
            region = cdk_regions.get("api_gateway") or self.config.api_gateway_region
            return region
        if stack_name == "gco-monitoring":
            region = cdk_regions.get("monitoring") or self.config.monitoring_region
            return region

        # Regional stacks: gco-{region}
        prefix = "gco-"
        if stack_name.startswith(prefix):
            return stack_name[len(prefix) :]

        return None

    def get_outputs(self, stack_name: str, region: str) -> dict[str, str]:
        """Get stack outputs from CloudFormation."""
        import boto3

        cf = boto3.client("cloudformation", region_name=region)
        try:
            response = cf.describe_stacks(StackName=stack_name)
            if response["Stacks"]:
                stack = response["Stacks"][0]
                outputs: dict[str, str] = {}
                for output in stack.get("Outputs", []):
                    outputs[str(output["OutputKey"])] = str(output["OutputValue"])
                return outputs
        except Exception as e:
            logger.debug("Failed to get outputs for %s in %s: %s", stack_name, region, e)
        return {}

    def get_stack_status(self, stack_name: str, region: str) -> StackInfo | None:
        """Get detailed stack status from CloudFormation."""
        import boto3

        cf = boto3.client("cloudformation", region_name=region)
        try:
            response = cf.describe_stacks(StackName=stack_name)
            if response["Stacks"]:
                stack = response["Stacks"][0]
                return StackInfo(
                    name=stack["StackName"],
                    status=stack["StackStatus"],
                    region=region,
                    created_time=stack.get("CreationTime"),
                    updated_time=stack.get("LastUpdatedTime"),
                    outputs={o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])},
                    tags={t["Key"]: t["Value"] for t in stack.get("Tags", [])},
                )
        except Exception as e:
            logger.debug("Failed to get stack status for %s in %s: %s", stack_name, region, e)
        return None

    def deploy_orchestrated(
        self,
        require_approval: bool = True,
        outputs_file: str | None = None,
        parameters: dict[str, str] | None = None,
        tags: dict[str, str] | None = None,
        progress: str = "events",
        on_stack_start: Callable[[str], None] | None = None,
        on_stack_complete: Callable[[str, bool], None] | None = None,
        parallel: bool = False,
        max_workers: int = 4,
    ) -> tuple[bool, list[str], list[str]]:
        """
        Deploy all stacks in the correct order.

        Deploys global stacks first, then regional stacks (optionally in parallel),
        then the monitoring stack (which depends on regional stacks).

        Args:
            require_approval: Whether to require approval for changes
            outputs_file: File to write outputs to
            parameters: CDK parameters
            tags: Tags to apply to stacks
            progress: Progress display type
            on_stack_start: Callback(stack_name) called when starting a stack
            on_stack_complete: Callback(stack_name, success) called when stack completes
            parallel: Deploy regional stacks in parallel
            max_workers: Maximum number of parallel deployments (default: 4)

        Returns:
            Tuple of (overall_success, successful_stacks, failed_stacks)
        """
        stacks = self.list_stacks()
        ordered_stacks = get_stack_deployment_order(stacks)

        # Separate stacks into three groups:
        # 1. Pre-regional global stacks (gco-global, gco-api-gateway)
        # 2. Regional stacks (can be parallelized)
        # 3. Post-regional stacks (gco-monitoring - depends on regional stacks)
        pre_regional = {"gco-global", "gco-api-gateway"}
        post_regional = {"gco-monitoring"}

        pre_regional_stacks = [s for s in ordered_stacks if s in pre_regional]
        regional_stacks = [
            s for s in ordered_stacks if s not in pre_regional and s not in post_regional
        ]
        post_regional_stacks = [s for s in ordered_stacks if s in post_regional]

        successful: list[str] = []
        failed: list[str] = []

        # Phase 1: Deploy pre-regional global stacks sequentially
        for stack_name in pre_regional_stacks:
            if on_stack_start:
                on_stack_start(stack_name)

            success = self.deploy(
                stack_name=stack_name,
                require_approval=require_approval,
                outputs_file=outputs_file,
                parameters=parameters,
                tags=tags,
                progress=progress,
            )

            if success:
                successful.append(stack_name)
            else:
                failed.append(stack_name)

            if on_stack_complete:
                on_stack_complete(stack_name, success)

            # Stop on failure to prevent cascading issues
            if not success:
                return False, successful, failed

        # Phase 2: Deploy regional stacks (parallel or sequential)
        # All regional stacks pass --exclusively: globals are already deployed
        # in Phase 1, so CDK doesn't need to re-evaluate them. Skipping that
        # re-evaluation avoids re-running custom resources (notably
        # KubectlApplyManifests) on the global stacks every time a regional
        # stack is deployed — that would otherwise re-apply manifests and
        # rollout-restart controllers for no actual change.
        if regional_stacks:
            if parallel and len(regional_stacks) > 1:
                # Parallel deployment of regional stacks
                successful_regional, failed_regional = self._deploy_stacks_parallel(
                    stacks=regional_stacks,
                    require_approval=require_approval,
                    outputs_file=outputs_file,
                    parameters=parameters,
                    tags=tags,
                    progress=progress,
                    on_stack_start=on_stack_start,
                    on_stack_complete=on_stack_complete,
                    max_workers=max_workers,
                )
                successful.extend(successful_regional)
                failed.extend(failed_regional)

                # Stop if any regional stack failed
                if failed_regional:
                    return False, successful, failed
            else:
                # Sequential deployment
                for stack_name in regional_stacks:
                    if on_stack_start:
                        on_stack_start(stack_name)

                    success = self.deploy(
                        stack_name=stack_name,
                        require_approval=require_approval,
                        outputs_file=outputs_file,
                        parameters=parameters,
                        tags=tags,
                        progress=progress,
                        exclusively=True,
                    )

                    if success:
                        successful.append(stack_name)
                    else:
                        failed.append(stack_name)

                    if on_stack_complete:
                        on_stack_complete(stack_name, success)

                    # Stop on failure
                    if not success:
                        return False, successful, failed

        # Phase 3: Deploy post-regional stacks (monitoring) sequentially.
        # Same rationale as Phase 2: every upstream stack is already
        # deployed, so --exclusively prevents a redundant pass over
        # global/api-gateway/regional.
        for stack_name in post_regional_stacks:
            if on_stack_start:
                on_stack_start(stack_name)

            success = self.deploy(
                stack_name=stack_name,
                require_approval=require_approval,
                outputs_file=outputs_file,
                parameters=parameters,
                tags=tags,
                progress=progress,
                exclusively=True,
            )

            if success:
                successful.append(stack_name)
            else:
                failed.append(stack_name)

            if on_stack_complete:
                on_stack_complete(stack_name, success)

            if not success:
                return False, successful, failed

        return len(failed) == 0, successful, failed

    def _deploy_stacks_parallel(
        self,
        stacks: list[str],
        require_approval: bool,
        outputs_file: str | None,
        parameters: dict[str, str] | None,
        tags: dict[str, str] | None,
        progress: str,
        on_stack_start: Callable[[str], None] | None,
        on_stack_complete: Callable[[str, bool], None] | None,
        max_workers: int,
    ) -> tuple[list[str], list[str]]:
        """Deploy multiple stacks in parallel using separate CDK output directories."""
        import tempfile

        successful: list[str] = []
        failed: list[str] = []
        lock = Lock()

        def deploy_single(stack_name: str) -> tuple[str, bool]:
            # Use a unique output directory in /tmp for each parallel deployment
            # This avoids CDK copying cdk.out.* directories into assets
            output_dir = tempfile.mkdtemp(prefix=f"cdk-{stack_name}-")

            if on_stack_start:
                with lock:
                    on_stack_start(stack_name)

            success = self.deploy(
                stack_name=stack_name,
                require_approval=require_approval,
                outputs_file=outputs_file,
                parameters=parameters,
                tags=tags,
                progress=progress,
                output_dir=output_dir,
                exclusively=True,
            )

            # Clean up the temporary output directory
            try:
                import shutil

                if os.path.exists(output_dir):
                    shutil.rmtree(output_dir)
            except Exception as e:
                logger.debug("Cleanup of %s failed: %s", output_dir, e)

            return stack_name, success

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(deploy_single, stack): stack for stack in stacks}

            for future in as_completed(futures):
                stack_name, success = future.result()

                with lock:
                    if success:
                        successful.append(stack_name)
                    else:
                        failed.append(stack_name)

                    if on_stack_complete:
                        on_stack_complete(stack_name, success)

        return successful, failed

    def destroy_orchestrated(
        self,
        force: bool = False,
        on_stack_start: Callable[[str], None] | None = None,
        on_stack_complete: Callable[[str, bool], None] | None = None,
        parallel: bool = False,
        max_workers: int = 4,
    ) -> tuple[bool, list[str], list[str]]:
        """
        Destroy all stacks in the correct order.

        Destroys monitoring stack first, then regional stacks (optionally in parallel),
        then global stacks.

        Args:
            force: Skip confirmation prompts
            on_stack_start: Callback(stack_name) called when starting a stack
            on_stack_complete: Callback(stack_name, success) called when stack completes
            parallel: Destroy regional stacks in parallel
            max_workers: Maximum number of parallel destructions (default: 4)

        Returns:
            Tuple of (overall_success, successful_stacks, failed_stacks)
        """
        stacks = self.list_stacks()

        # Phase 0: Clean up backup vault recovery points so the global stack
        # can be deleted cleanly by CloudFormation.
        self._cleanup_backup_vault()

        # Separate stacks into three groups (reverse of deploy order):
        pre_regional = {"gco-global", "gco-api-gateway"}
        post_regional = {"gco-monitoring"}

        post_regional_stacks = [s for s in stacks if s in post_regional]
        regional_stacks = [s for s in stacks if s not in pre_regional and s not in post_regional]
        pre_regional_stacks = [s for s in stacks if s in pre_regional]

        # Sort regional stacks alphabetically (reversed for destroy)
        regional_stacks.sort(reverse=True)
        # Sort pre-regional stacks in reverse priority order
        pre_regional_order = {"gco-api-gateway": 1, "gco-global": 2}
        pre_regional_stacks.sort(key=lambda x: pre_regional_order.get(x, 0))

        successful: list[str] = []
        failed: list[str] = []

        # Phase 1: Destroy post-regional stacks (monitoring) first
        for stack_name in post_regional_stacks:
            if on_stack_start:
                on_stack_start(stack_name)

            success = self.destroy(
                stack_name=stack_name,
                force=force,
            )

            if success:
                successful.append(stack_name)
            else:
                failed.append(stack_name)

            if on_stack_complete:
                on_stack_complete(stack_name, success)

        # Phase 2: Destroy regional stacks (parallel or sequential)
        # Each regional destroy has a background watchdog that proactively
        # deletes EKS-managed security groups + orphaned ENIs as soon as
        # they appear. Without this, CloudFormation's VPC delete step sits
        # in DELETE_IN_PROGRESS for ~10 min waiting for EKS to GC the
        # ``eks-cluster-sg-<cluster>-*`` security group and its cluster
        # ENIs. The SG is owned by the EKS service (not CloudFormation),
        # so CFN can't delete it directly — it just polls the VPC's
        # dependencies until they drain. See ``_start_eks_sg_watchdog``.
        watchdog_stops: dict[str, Event] = {}
        watchdog_threads: dict[str, Thread] = {}
        for stack_name in regional_stacks:
            stop_event = Event()
            thread = self._start_eks_sg_watchdog(stack_name, stop_event)
            watchdog_stops[stack_name] = stop_event
            watchdog_threads[stack_name] = thread

        if regional_stacks:
            if parallel and len(regional_stacks) > 1:
                # Parallel destruction of regional stacks
                successful_regional, failed_regional = self._destroy_stacks_parallel(
                    stacks=regional_stacks,
                    force=force,
                    on_stack_start=on_stack_start,
                    on_stack_complete=on_stack_complete,
                    max_workers=max_workers,
                )
                successful.extend(successful_regional)
                failed.extend(failed_regional)
            else:
                # Sequential destruction
                for stack_name in regional_stacks:
                    if on_stack_start:
                        on_stack_start(stack_name)

                    success = self.destroy(
                        stack_name=stack_name,
                        force=force,
                    )

                    if success:
                        successful.append(stack_name)
                    else:
                        failed.append(stack_name)

                    if on_stack_complete:
                        on_stack_complete(stack_name, success)

        # Stop all watchdogs and do one final cleanup pass per stack to
        # catch anything EKS re-created during the destroy flow.
        for stack_name in regional_stacks:
            watchdog_stops[stack_name].set()
            watchdog_threads[stack_name].join(timeout=5)
            self._cleanup_eks_security_groups(stack_name)

        # Phase 3: Destroy pre-regional global stacks last
        for stack_name in pre_regional_stacks:
            if on_stack_start:
                on_stack_start(stack_name)

            success = self.destroy(
                stack_name=stack_name,
                force=force,
            )

            if success:
                successful.append(stack_name)
            else:
                failed.append(stack_name)

            if on_stack_complete:
                on_stack_complete(stack_name, success)

        return len(failed) == 0, successful, failed

    def _destroy_stacks_parallel(
        self,
        stacks: list[str],
        force: bool,
        on_stack_start: Callable[[str], None] | None,
        on_stack_complete: Callable[[str, bool], None] | None,
        max_workers: int,
    ) -> tuple[list[str], list[str]]:
        """Destroy multiple stacks in parallel using separate CDK output directories."""
        import tempfile

        successful: list[str] = []
        failed: list[str] = []
        lock = Lock()

        def destroy_single(stack_name: str) -> tuple[str, bool]:
            # Use a unique output directory in /tmp for each parallel destruction
            output_dir = tempfile.mkdtemp(prefix=f"cdk-{stack_name}-")

            if on_stack_start:
                with lock:
                    on_stack_start(stack_name)

            success = self.destroy(
                stack_name=stack_name,
                force=force,
                output_dir=output_dir,
            )

            # Clean up the temporary output directory
            try:
                import shutil

                if os.path.exists(output_dir):
                    shutil.rmtree(output_dir)
            except Exception as e:
                logger.debug("Cleanup of %s failed: %s", output_dir, e)

            return stack_name, success

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(destroy_single, stack): stack for stack in stacks}

            for future in as_completed(futures):
                stack_name, success = future.result()

                with lock:
                    if success:
                        successful.append(stack_name)
                    else:
                        failed.append(stack_name)

                    if on_stack_complete:
                        on_stack_complete(stack_name, success)

        return successful, failed

    def _cleanup_backup_vault(self) -> None:
        """Delete all recovery points from the GCO backup vault.

        This is called before destroy-all so CloudFormation can delete the
        backup vault cleanly. Without this, the vault deletion fails because
        it contains recovery points.
        """
        import boto3

        global_region = self.config.global_region
        project_name = self.config.project_name

        try:
            backup_client = boto3.client("backup", region_name=global_region)

            # Find the backup vault by listing vaults with the project prefix
            paginator = backup_client.get_paginator("list_backup_vaults")
            vault_name = None
            for page in paginator.paginate():
                for vault in page.get("BackupVaultList", []):
                    if project_name in vault["BackupVaultName"].lower():
                        vault_name = vault["BackupVaultName"]
                        break
                if vault_name:
                    break

            if not vault_name:
                return

            # Delete all recovery points in the vault
            rp_paginator = backup_client.get_paginator("list_recovery_points_by_backup_vault")
            deleted = 0
            for page in rp_paginator.paginate(BackupVaultName=vault_name):
                for rp in page.get("RecoveryPoints", []):
                    try:
                        backup_client.delete_recovery_point(
                            BackupVaultName=vault_name,
                            RecoveryPointArn=rp["RecoveryPointArn"],
                        )
                        deleted += 1
                    except Exception as e:
                        logger.debug("Failed to delete recovery point: %s", e)

            if deleted > 0:
                print(f"  Cleaned up {deleted} backup recovery points from {vault_name}")

        except Exception as e:
            print(f"  Warning: Backup vault cleanup failed (non-fatal): {e}")

    def cleanup_eks_security_groups(self) -> None:
        """Clean up EKS-managed security groups across all regional stacks.

        Called between destroy retries to remove orphaned security groups
        that block VPC deletion.
        """
        stacks = self.list_stacks()
        pre_regional = {"gco-global", "gco-api-gateway", "gco-monitoring"}
        regional_stacks = [s for s in stacks if s not in pre_regional]
        for stack_name in regional_stacks:
            self._cleanup_eks_security_groups(stack_name)

    def _cleanup_eks_security_groups(self, stack_name: str) -> None:
        """Clean up EKS-managed security groups that block VPC deletion.

        EKS creates a cluster security group (eks-cluster-sg-<cluster-name>-*)
        that is owned by EKS, not CloudFormation. When the stack is destroyed,
        this SG and its attached ENIs can linger, causing VPC deletion to fail.

        This method finds and deletes these orphaned security groups and their
        ENIs before the stack destroy runs.
        """
        import time as _time

        import boto3

        # Extract region from stack name (e.g., gco-us-east-1 -> us-east-1)
        project_name = self.config.project_name
        region = stack_name.replace(f"{project_name}-", "", 1)
        cluster_name = stack_name  # cluster name matches stack name

        try:
            ec2 = boto3.client("ec2", region_name=region)

            # Find EKS-managed security groups by name pattern
            response = ec2.describe_security_groups(
                Filters=[
                    {
                        "Name": "group-name",
                        "Values": [f"eks-cluster-sg-{cluster_name}-*"],
                    }
                ]
            )

            sgs = response.get("SecurityGroups", [])
            if not sgs:
                return

            for sg in sgs:
                sg_id = sg["GroupId"]
                sg_name = sg.get("GroupName", "")

                # First, detach and delete any ENIs using this SG
                eni_response = ec2.describe_network_interfaces(
                    Filters=[{"Name": "group-id", "Values": [sg_id]}]
                )

                for eni in eni_response.get("NetworkInterfaces", []):
                    eni_id = eni["NetworkInterfaceId"]
                    try:
                        if eni.get("Attachment"):
                            ec2.detach_network_interface(
                                AttachmentId=eni["Attachment"]["AttachmentId"],
                                Force=True,
                            )
                            _time.sleep(5)
                        ec2.delete_network_interface(NetworkInterfaceId=eni_id)
                        logger.debug("Deleted ENI %s from SG %s", eni_id, sg_name)
                    except Exception as e:
                        logger.debug("Failed to delete ENI %s: %s", eni_id, e)

                # Now delete the security group
                try:
                    ec2.delete_security_group(GroupId=sg_id)
                    print(f"  Cleaned up EKS security group: {sg_name} ({sg_id})")
                except Exception as e:
                    logger.debug(
                        "Failed to delete SG %s: %s (will retry on next attempt)", sg_id, e
                    )

        except Exception as e:
            # Non-fatal — the retry loop will handle it
            logger.debug("EKS security group cleanup for %s failed: %s", stack_name, e)

    def _start_eks_sg_watchdog(self, stack_name: str, stop_event: Event) -> Thread:
        """Start a background thread that polls for orphaned EKS security groups.

        EKS creates an ``eks-cluster-sg-<cluster-name>-*`` security group that
        is owned by the EKS service (not CloudFormation). When the stack's
        EKS cluster resource deletes, the SG is supposed to GC along with its
        cluster ENIs — but on EKS Auto Mode there's a window where the SG
        lingers after the cluster is gone, blocking the subsequent VPC
        delete with ``DependencyViolation``. CloudFormation then sits in
        ``DELETE_IN_PROGRESS`` on the VPC for ~10 minutes retrying.

        This watchdog runs for the full duration of the regional-stack
        destroy, polling every 30 seconds. As soon as an orphaned SG appears
        (which only happens after the cluster delete has progressed past
        the cluster resource itself), it deletes the SG and any ENIs still
        attached. That unblocks the VPC delete immediately instead of
        waiting for EKS's own GC timer.

        The thread exits when ``stop_event`` is set by the orchestrator at
        the end of the regional phase.
        """

        def _watchdog() -> None:
            while not stop_event.is_set():
                try:
                    self._cleanup_eks_security_groups(stack_name)
                except Exception as e:
                    logger.debug(
                        "EKS SG watchdog tick for %s failed (non-fatal): %s",
                        stack_name,
                        e,
                    )
                # ``wait`` returns immediately when the event is set, so this
                # doubles as the sleep-and-shutdown-check in one call.
                stop_event.wait(timeout=30)

        thread = Thread(
            target=_watchdog,
            name=f"eks-sg-watchdog-{stack_name}",
            daemon=True,
        )
        thread.start()
        return thread


def get_stack_manager(config: GCOConfig) -> StackManager:
    """Factory function to get a StackManager instance."""
    return StackManager(config)


def get_stack_deployment_order(stacks: list[str]) -> list[str]:
    """
    Get the correct deployment order for stacks.

    Order: global stacks first, then regional stacks.
    Global stacks: gco-global, gco-api-gateway, gco-monitoring
    Regional stacks: gco-{region} (e.g., gco-us-east-1)
    """
    global_stacks = []
    regional_stacks = []

    # Define global stack priority (lower = deploy first)
    global_priority = {
        "gco-global": 1,
        "gco-api-gateway": 2,
        "gco-monitoring": 3,
    }

    for stack in stacks:
        if stack in global_priority:
            global_stacks.append((global_priority[stack], stack))
        else:
            regional_stacks.append(stack)

    # Sort global stacks by priority, regional stacks alphabetically
    global_stacks.sort(key=lambda x: x[0])
    regional_stacks.sort()

    return [s[1] for s in global_stacks] + regional_stacks


def get_stack_destroy_order(stacks: list[str]) -> list[str]:
    """
    Get the correct destroy order for stacks.

    Order: regional stacks first, then global stacks (reverse of deploy).
    """
    deployment_order = get_stack_deployment_order(stacks)
    return list(reversed(deployment_order))


def get_fsx_config(region: str | None = None) -> dict[str, Any]:
    """Get current FSx for Lustre configuration from cdk.json.

    Args:
        region: Optional region to get config for. If provided, checks for
                region-specific overrides first.

    Returns:
        FSx configuration dictionary
    """
    cdk_json_path = _find_cdk_json()
    if not cdk_json_path:
        raise RuntimeError("cdk.json not found")

    import json

    with open(cdk_json_path, encoding="utf-8") as f:
        cdk_config = json.load(f)

    # Default FSx config
    default_config = {
        "enabled": False,
        "storage_capacity_gib": 1200,
        "deployment_type": "SCRATCH_2",
        "per_unit_storage_throughput": 200,
        "data_compression_type": "LZ4",
        "import_path": None,
        "export_path": None,
        "auto_import_policy": "NEW_CHANGED_DELETED",
    }

    # Get global FSx config
    global_config = cdk_config.get("context", {}).get("fsx_lustre", default_config)

    # Check for region-specific override
    if region:
        region_overrides = cdk_config.get("context", {}).get("fsx_lustre_regions", {})
        if region in region_overrides:
            # Merge region config over global config
            region_config = region_overrides[region]
            merged = {**global_config, **region_config}
            merged["region"] = region
            merged["is_region_specific"] = True
            return merged

    result = {**default_config, **global_config}
    result["is_region_specific"] = False
    return result


def update_fsx_config(settings: dict[str, Any], region: str | None = None) -> None:
    """Update FSx for Lustre configuration in cdk.json.

    Args:
        settings: FSx settings to update
        region: Optional region for region-specific config. If None, updates global config.
    """
    cdk_json_path = _find_cdk_json()
    if not cdk_json_path:
        raise RuntimeError("cdk.json not found")

    import json

    with open(cdk_json_path, encoding="utf-8") as f:
        cdk_config = json.load(f)

    # Ensure context exists
    if "context" not in cdk_config:
        cdk_config["context"] = {}

    if region:
        # Update region-specific config
        if "fsx_lustre_regions" not in cdk_config["context"]:
            cdk_config["context"]["fsx_lustre_regions"] = {}

        if region not in cdk_config["context"]["fsx_lustre_regions"]:
            cdk_config["context"]["fsx_lustre_regions"][region] = {}

        # Update with new settings
        for key, value in settings.items():
            if value is not None or key == "enabled":
                cdk_config["context"]["fsx_lustre_regions"][region][key] = value
    else:
        # Update global config
        if "fsx_lustre" not in cdk_config["context"]:
            cdk_config["context"]["fsx_lustre"] = {
                "enabled": False,
                "storage_capacity_gib": 1200,
                "deployment_type": "SCRATCH_2",
                "per_unit_storage_throughput": 200,
                "data_compression_type": "LZ4",
                "import_path": None,
                "export_path": None,
                "auto_import_policy": "NEW_CHANGED_DELETED",
            }

        # Update with new settings
        for key, value in settings.items():
            if value is not None or key == "enabled":
                cdk_config["context"]["fsx_lustre"][key] = value

    # Write back
    with open(cdk_json_path, "w", encoding="utf-8") as f:
        json.dump(cdk_config, f, indent=2)


def _find_cdk_json() -> Path | None:
    """Find cdk.json in current or parent directories."""
    current = Path.cwd()
    for parent in [current] + list(current.parents):
        cdk_path = parent / "cdk.json"
        if cdk_path.exists():
            return cdk_path
    return None
