"""
Inference Monitor — reconciliation controller for inference endpoints.

Runs in each regional EKS cluster and polls the global DynamoDB table
(gco-inference-endpoints) to reconcile desired state with actual
Kubernetes resources. Follows a GitOps-style reconciliation pattern:

    DynamoDB (desired state) → inference_monitor → Kubernetes (actual state)

The monitor:
- Creates Deployments, Services, and Ingress rules for new endpoints
- Updates existing deployments when spec changes
- Scales deployments up/down
- Tears down resources when endpoints are deleted
- Reports per-region status back to DynamoDB

Environment Variables:
    CLUSTER_NAME: Name of the EKS cluster
    REGION: AWS region this monitor runs in
    INFERENCE_ENDPOINTS_TABLE_NAME: DynamoDB table name
    RECONCILE_INTERVAL_SECONDS: Seconds between reconciliation loops (default: 15)
    INFERENCE_NAMESPACE: Namespace for inference workloads (default: gco-inference)
"""

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

from kubernetes import client, config
from kubernetes.client.models import V1Deployment
from kubernetes.client.rest import ApiException

from gco.services.inference_store import InferenceEndpointStore
from gco.services.structured_logging import configure_structured_logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class InferenceMonitor:
    """
    Reconciliation controller for inference endpoints.

    Polls DynamoDB for desired endpoint state and reconciles with
    the actual Kubernetes resources in the local cluster.
    """

    def __init__(
        self,
        cluster_id: str,
        region: str,
        store: InferenceEndpointStore,
        namespace: str = "gco-inference",
        reconcile_interval: int = 15,
    ):
        self.cluster_id = cluster_id
        self.region = region
        self.store = store
        self.namespace = namespace
        self.reconcile_interval = reconcile_interval
        self._running = False

        # Initialize Kubernetes clients
        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes configuration")
        except config.ConfigException:
            try:
                config.load_kube_config()
                logger.info("Loaded local Kubernetes configuration")
            except config.ConfigException as e:
                logger.error("Failed to load Kubernetes configuration: %s", e)
                raise

        self.apps_v1 = client.AppsV1Api()
        self.core_v1 = client.CoreV1Api()
        self.networking_v1 = client.NetworkingV1Api()

        # Timeout for Kubernetes API calls (seconds)
        self._k8s_timeout = int(os.environ.get("K8S_API_TIMEOUT", "30"))

        # Health watchdog: tracks when each endpoint first became unready.
        # If an endpoint stays unready for longer than _ingress_removal_threshold,
        # the watchdog removes its Ingress to protect the shared ALB from
        # having an unhealthy target group (which would make GA mark the
        # entire ALB as unhealthy, blocking all inference in the region).
        self._unready_since: dict[str, datetime] = {}
        self._ingress_removal_threshold = int(
            os.environ.get("INFERENCE_UNHEALTHY_THRESHOLD_SECONDS", "300")
        )  # 5 minutes default

        # Metrics
        self._reconcile_count = 0
        self._errors_count = 0

    # ------------------------------------------------------------------
    # Reconciliation loop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the reconciliation loop with leader election.

        Uses a Kubernetes Lease object for leader election so that only
        one replica reconciles at a time. Other replicas stay on standby
        and take over if the leader dies.
        """
        if self._running:
            logger.warning("Inference monitor already running")
            return
        self._running = True
        logger.info(
            "Starting inference monitor for %s in %s (interval=%ds)",
            self.cluster_id,
            self.region,
            self.reconcile_interval,
        )

        # Namespace and ServiceAccount are pre-created by the kubectl-applier
        # at deploy time (00-namespaces.yaml, 01-serviceaccounts.yaml). The
        # inference-monitor SA has namespace-scoped RBAC only — it cannot
        # read_namespace/create_namespace, so we don't try. If the namespace
        # is ever missing, deployments below will fail with a clear 404.

        # Get pod identity for leader election
        pod_name = os.environ.get("HOSTNAME", f"monitor-{id(self)}")
        lease_name = "inference-monitor-leader"

        while self._running:
            try:
                if self._try_acquire_lease(lease_name, pod_name):
                    await self.reconcile()
                else:
                    logger.debug("Not the leader, waiting...")
            except Exception as e:
                logger.error("Reconciliation error: %s", e, exc_info=True)
                self._errors_count += 1
            try:
                await asyncio.sleep(self.reconcile_interval)
            except Exception as e:
                logger.error("Sleep interrupted: %s", e)
                break

    def _try_acquire_lease(self, lease_name: str, holder: str) -> bool:
        """Try to acquire or renew a Kubernetes Lease for leader election.

        Uses optimistic concurrency via resourceVersion — if two monitors
        race to update the same lease, K8s returns 409 Conflict for the
        loser, preventing split-brain.

        Returns True if this instance is the leader.
        """

        coordination_v1 = client.CoordinationV1Api()
        now = datetime.now(UTC)

        try:
            lease = coordination_v1.read_namespaced_lease(lease_name, self.namespace)
            current_holder = lease.spec.holder_identity
            renew_time = lease.spec.renew_time

            # Check if lease is expired (holder hasn't renewed in 3x interval)
            if renew_time:
                elapsed = (now - renew_time.replace(tzinfo=UTC)).total_seconds()
                if elapsed > self.reconcile_interval * 3:
                    # Lease expired — take over
                    logger.info("Lease expired (held by %s), taking over", current_holder)
                    current_holder = None

            if current_holder == holder:
                # We're the leader — renew
                lease.spec.renew_time = now
                try:
                    coordination_v1.replace_namespaced_lease(lease_name, self.namespace, lease)
                except ApiException as conflict:
                    if conflict.status == 409:
                        logger.debug("Lease renew conflict (another writer), retrying next cycle")
                        return False
                    raise
                return True
            if current_holder is None or current_holder == "":
                # No leader — claim it
                lease.spec.holder_identity = holder
                lease.spec.renew_time = now
                try:
                    coordination_v1.replace_namespaced_lease(lease_name, self.namespace, lease)
                except ApiException as conflict:
                    if conflict.status == 409:
                        logger.info("Lost lease race to another monitor")
                        return False
                    raise
                logger.info("Acquired leader lease as %s", holder)
                return True
            # Someone else is the leader
            return False

        except ApiException as e:
            if e.status == 404:
                # Lease doesn't exist — create it
                lease = client.V1Lease(
                    metadata=client.V1ObjectMeta(
                        name=lease_name,
                        namespace=self.namespace,
                    ),
                    spec=client.V1LeaseSpec(
                        holder_identity=holder,
                        lease_duration_seconds=self.reconcile_interval * 3,
                        renew_time=now,
                    ),
                )
                try:
                    coordination_v1.create_namespaced_lease(self.namespace, lease)
                    logger.info("Created leader lease as %s", holder)
                    return True
                except ApiException:
                    return False
            logger.warning("Lease check failed: %s", e.reason)
            return False

    def stop(self) -> None:
        """Stop the reconciliation loop."""
        self._running = False
        logger.info("Inference monitor stopped")

    async def reconcile(self) -> list[dict[str, Any]]:
        """
        Run one reconciliation cycle.

        Returns a list of actions taken (for logging/testing).
        """
        self._reconcile_count += 1
        actions: list[dict[str, Any]] = []

        # Get all endpoints from DynamoDB
        try:
            endpoints = self.store.list_endpoints()
        except Exception as e:
            logger.error("Failed to list endpoints from DynamoDB: %s", e)
            return actions

        for endpoint in endpoints:
            try:
                action = await self._reconcile_endpoint(endpoint)
                if action:
                    actions.append(action)
            except Exception as e:
                name = endpoint.get("endpoint_name", "unknown")
                logger.error("Failed to reconcile endpoint %s: %s", name, e)
                self._errors_count += 1
                self.store.update_region_status(
                    name,
                    self.region,
                    "error",
                    error=str(e),
                )

        # Purge fully-deleted endpoints from DynamoDB to prevent unbounded growth.
        # An endpoint is fully deleted when desired_state is "deleted" and all
        # target regions report "deleted" status.
        for endpoint in endpoints:
            if endpoint.get("desired_state") != "deleted":
                continue
            region_status = endpoint.get("region_status", {})
            target_regions = endpoint.get("target_regions", [])
            if not target_regions:
                continue
            all_deleted = all(
                isinstance(region_status.get(r), dict)
                and region_status.get(r, {}).get("state") == "deleted"
                for r in target_regions
            )
            if all_deleted:
                ep_name = endpoint["endpoint_name"]
                try:
                    self.store.delete_endpoint(ep_name)
                    logger.info("Purged fully-deleted endpoint %s from DynamoDB", ep_name)
                    actions.append({"action": "purge", "endpoint": ep_name})
                except Exception as e:
                    logger.warning("Failed to purge endpoint %s: %s", ep_name, e)

        return actions

    async def _reconcile_endpoint(self, endpoint: dict[str, Any]) -> dict[str, Any] | None:
        """Reconcile a single endpoint."""
        name = endpoint["endpoint_name"]
        desired_state = endpoint.get("desired_state", "deploying")
        target_regions = endpoint.get("target_regions", [])
        spec = endpoint.get("spec", {})
        ns = endpoint.get("namespace", self.namespace)

        # Am I a target region?
        if self.region not in target_regions:
            # If I have resources for this endpoint, clean them up
            if self._deployment_exists(name, ns):
                logger.info(
                    "Endpoint %s no longer targets %s, cleaning up",
                    name,
                    self.region,
                )
                self._delete_resources(name, ns)
                self.store.update_region_status(
                    name,
                    self.region,
                    "deleted",
                )
                return {"action": "cleanup", "endpoint": name, "reason": "region_removed"}
            return None

        # Reconcile based on desired state
        if desired_state in ("deploying", "running"):
            return await self._reconcile_running(name, ns, spec, endpoint)
        if desired_state == "stopped":
            return self._reconcile_stopped(name, ns)
        if desired_state == "deleted":
            return self._reconcile_deleted(name, ns)

        return None

    async def _reconcile_running(
        self,
        name: str,
        namespace: str,
        spec: dict[str, Any],
        endpoint: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Ensure the endpoint is running with the correct spec."""
        deployment = self._get_deployment(name, namespace)

        if deployment is None:
            # Create everything
            logger.info("Creating endpoint %s in %s", name, self.region)
            self._create_deployment(name, namespace, spec)
            self._create_service(name, namespace, spec)
            self._update_ingress_rule(name, namespace, spec, endpoint)
            if spec.get("autoscaling", {}).get("enabled"):
                self._create_or_update_hpa(name, namespace, spec)
            self.store.update_region_status(
                name,
                self.region,
                "creating",
                replicas_desired=spec.get("replicas", 1),
            )
            return {"action": "create", "endpoint": name}

        # Deployment exists — ensure Service and Ingress also exist
        # (they may have been manually deleted or lost during a rollout)
        self._ensure_service(name, namespace, spec)

        # Check readiness before ensuring Ingress — the health watchdog may
        # remove the Ingress if the endpoint has been unready too long
        desired_replicas = spec.get("replicas", 1)
        current_replicas = deployment.spec.replicas or 1
        ready_replicas = deployment.status.ready_replicas or 0

        ingress_removed = self._check_health_watchdog(
            name, namespace, ready_replicas, desired_replicas, spec, endpoint
        )
        if not ingress_removed:
            self._ensure_ingress(name, namespace, spec, endpoint)

        if current_replicas != desired_replicas:
            logger.info(
                "Scaling endpoint %s: %d → %d replicas",
                name,
                current_replicas,
                desired_replicas,
            )
            self._scale_deployment(name, namespace, desired_replicas)
            self.store.update_region_status(
                name,
                self.region,
                "updating",
                replicas_ready=ready_replicas,
                replicas_desired=desired_replicas,
            )
            return {"action": "scale", "endpoint": name, "replicas": desired_replicas}

        # Check if image changed
        current_image = self._get_deployment_image(deployment)
        desired_image = spec.get("image", "")
        if current_image and desired_image and current_image != desired_image:
            logger.info("Updating endpoint %s image: %s → %s", name, current_image, desired_image)
            self._update_deployment_image(name, namespace, desired_image)
            self.store.update_region_status(
                name,
                self.region,
                "updating",
                replicas_ready=ready_replicas,
                replicas_desired=desired_replicas,
            )
            return {"action": "update_image", "endpoint": name, "image": desired_image}

        # Everything is in sync — report status
        state = "running" if ready_replicas >= desired_replicas else "creating"
        self.store.update_region_status(
            name,
            self.region,
            state,
            replicas_ready=ready_replicas,
            replicas_desired=desired_replicas,
        )

        # Reconcile canary deployment if present
        canary = spec.get("canary")
        if canary:
            self._reconcile_canary(name, namespace, spec, canary, endpoint)
        else:
            # No canary — clean up canary resources if they exist
            self._cleanup_canary(name, namespace)

        # If all replicas are ready and desired_state is "deploying", promote to "running"
        if state == "running" and endpoint.get("desired_state") == "deploying":
            # Check if all target regions are running
            all_running = True
            for r_status in endpoint.get("region_status", {}).values():
                if isinstance(r_status, dict) and r_status.get("state") != "running":
                    all_running = False
                    break
            if all_running:
                self.store.update_desired_state(name, "running")

        return None

    def _reconcile_stopped(self, name: str, namespace: str) -> dict[str, Any] | None:
        """Scale deployment to zero."""
        deployment = self._get_deployment(name, namespace)
        if deployment is None:
            return None

        current_replicas = deployment.spec.replicas or 0
        if current_replicas > 0:
            logger.info("Stopping endpoint %s (scaling to 0)", name)
            self._scale_deployment(name, namespace, 0)
            self.store.update_region_status(
                name,
                self.region,
                "stopped",
                replicas_ready=0,
                replicas_desired=0,
            )
            return {"action": "stop", "endpoint": name}

        self.store.update_region_status(
            name,
            self.region,
            "stopped",
            replicas_ready=0,
            replicas_desired=0,
        )
        return None

    def _reconcile_deleted(self, name: str, namespace: str) -> dict[str, Any] | None:
        """Delete all resources for the endpoint."""
        # Clean up health watchdog tracker
        self._unready_since.pop(name, None)

        if self._deployment_exists(name, namespace):
            logger.info("Deleting endpoint %s from %s", name, self.region)
            self._delete_resources(name, namespace)
            self.store.update_region_status(name, self.region, "deleted")
            return {"action": "delete", "endpoint": name}

        self.store.update_region_status(name, self.region, "deleted")
        return None

    # ------------------------------------------------------------------
    # Kubernetes resource management
    # ------------------------------------------------------------------

    def _deployment_exists(self, name: str, namespace: str) -> bool:
        try:
            self.apps_v1.read_namespaced_deployment(
                name, namespace, _request_timeout=self._k8s_timeout
            )
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            raise

    def _get_deployment(self, name: str, namespace: str) -> V1Deployment | None:
        try:
            return self.apps_v1.read_namespaced_deployment(
                name, namespace, _request_timeout=self._k8s_timeout
            )
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def _get_deployment_image(self, deployment: V1Deployment) -> str | None:
        """Get the image of the first container in a deployment."""
        containers = deployment.spec.template.spec.containers
        if containers:
            image: str = containers[0].image
            return image
        return None

    def _create_deployment(self, name: str, namespace: str, spec: dict[str, Any]) -> None:
        """Create a Kubernetes Deployment for an inference endpoint."""
        replicas = spec.get("replicas", 1)
        image = spec["image"]
        port = spec.get("port", 8000)
        gpu_count = spec.get("gpu_count", 1)
        health_path = spec.get("health_check_path", "/health")
        env_vars = spec.get("env", {})
        resources = spec.get("resources", {})
        model_path = spec.get("model_path")
        command = spec.get("command")
        args = spec.get("args")

        # Build container
        container_env = [client.V1EnvVar(name=k, value=str(v)) for k, v in env_vars.items()]

        # Inject --root-path for servers that support it (vLLM, TGI).
        # This tells the server to mount its API at /inference/{name}.
        # We append to existing args (from --extra-args) rather than replacing them.
        ingress_prefix = f"/inference/{name}"
        root_path_images = ("vllm", "text-generation-inference", "tgi")
        image_lower = image.lower()
        if not command and any(tag in image_lower for tag in root_path_images):
            if args:
                # Append --root-path to user-provided args if not already present
                if "--root-path" not in args:
                    args = list(args) + ["--root-path", ingress_prefix]
            else:
                args = ["--root-path", ingress_prefix]

        resource_reqs = client.V1ResourceRequirements(
            requests=resources.get("requests", {"cpu": "1", "memory": "4Gi"}),
            limits=resources.get("limits", {"cpu": "4", "memory": "16Gi"}),
        )
        # Add accelerator resources (GPU or Neuron)
        accelerator = spec.get("accelerator", "nvidia")
        if gpu_count > 0:
            if accelerator == "neuron":
                # AWS Trainium/Inferentia — request Neuron devices
                if resource_reqs.limits is None:
                    resource_reqs.limits = {}
                resource_reqs.limits["aws.amazon.com/neuron"] = str(gpu_count)
                if resource_reqs.requests is None:
                    resource_reqs.requests = {}
                resource_reqs.requests["aws.amazon.com/neuron"] = str(gpu_count)
            else:
                # NVIDIA GPU (default)
                if resource_reqs.limits is None:
                    resource_reqs.limits = {}
                resource_reqs.limits["nvidia.com/gpu"] = str(gpu_count)
                if resource_reqs.requests is None:
                    resource_reqs.requests = {}
                resource_reqs.requests["nvidia.com/gpu"] = str(gpu_count)

        volume_mounts = []
        volumes = []
        init_containers = []
        model_source = spec.get("model_source")

        if model_path or model_source:
            volume_mounts.append(
                client.V1VolumeMount(
                    name="model-storage",
                    mount_path="/models",
                )
            )
            volumes.append(
                client.V1Volume(
                    name="model-storage",
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                        claim_name="efs-claim",
                    ),
                )
            )

        # Add init container to sync model from S3 if model_source is set
        if model_source and model_source.startswith("s3://"):
            model_dest = f"/models/{name}"
            init_containers.append(
                client.V1Container(
                    name="model-sync",
                    image="amazon/aws-cli:latest",
                    command=["sh", "-c"],
                    args=[
                        f"if [ -d '{model_dest}' ] && [ \"$(ls -A '{model_dest}')\" ]; then "
                        f"echo 'Model already cached at {model_dest}, skipping sync'; "
                        f"else echo 'Syncing model from {model_source}...'; "
                        f"aws s3 sync {model_source} {model_dest} --quiet; "
                        f"echo 'Model sync complete'; fi"
                    ],
                    volume_mounts=[
                        client.V1VolumeMount(
                            name="model-storage",
                            mount_path="/models",
                        )
                    ],
                    resources=client.V1ResourceRequirements(
                        requests={"cpu": "1", "memory": "2Gi"},
                        limits={"cpu": "4", "memory": "8Gi"},
                    ),
                )
            )

        # Probe path depends on whether the server handles the prefix
        uses_root_path = args is not None and "--root-path" in args
        probe_health = f"{ingress_prefix}{health_path}" if uses_root_path else health_path

        container = client.V1Container(
            name="inference",
            image=image,
            ports=[client.V1ContainerPort(container_port=port)],
            env=container_env if container_env else None,
            resources=resource_reqs,
            volume_mounts=volume_mounts if volume_mounts else None,
            command=command,
            args=args,
            liveness_probe=client.V1Probe(
                http_get=client.V1HTTPGetAction(path=probe_health, port=port),
                initial_delay_seconds=120,
                period_seconds=15,
                failure_threshold=5,
            ),
            readiness_probe=client.V1Probe(
                http_get=client.V1HTTPGetAction(path=probe_health, port=port),
                initial_delay_seconds=30,
                period_seconds=10,
            ),
        )

        # Build tolerations based on accelerator type
        if accelerator == "neuron":
            tolerations = [
                client.V1Toleration(
                    key="aws.amazon.com/neuron",
                    operator="Equal",
                    value="true",
                    effect="NoSchedule",
                )
            ]
        else:
            tolerations = [
                client.V1Toleration(
                    key="nvidia.com/gpu",
                    operator="Equal",
                    value="true",
                    effect="NoSchedule",
                )
            ]

        # Node selector based on accelerator type
        node_selector = spec.get("node_selector", {})
        if gpu_count > 0 and not node_selector:
            if accelerator == "neuron":
                node_selector = {"accelerator": "neuron"}
            else:
                node_selector = {"eks.amazonaws.com/instance-gpu-manufacturer": "nvidia"}

        # Apply capacity type preference (spot/on-demand)
        capacity_type = spec.get("capacity_type")
        if capacity_type in ("spot", "on-demand"):
            node_selector["karpenter.sh/capacity-type"] = capacity_type

        deployment = client.V1Deployment(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=namespace,
                labels={
                    "app": name,
                    "project": "gco",
                    "gco.io/type": "inference",
                },
            ),
            spec=client.V1DeploymentSpec(
                replicas=replicas,
                selector=client.V1LabelSelector(
                    match_labels={"app": name},
                ),
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(
                        labels={
                            "app": name,
                            "project": "gco",
                            "gco.io/type": "inference",
                        },
                    ),
                    spec=client.V1PodSpec(
                        service_account_name="gco-service-account",
                        containers=[container],
                        init_containers=init_containers if init_containers else None,
                        tolerations=tolerations,
                        node_selector=node_selector if node_selector else None,
                        volumes=volumes if volumes else None,
                    ),
                ),
            ),
        )

        self.apps_v1.create_namespaced_deployment(
            namespace, deployment, _request_timeout=self._k8s_timeout
        )
        logger.info("Created deployment %s/%s", namespace, name)

    def _create_service(self, name: str, namespace: str, spec: dict[str, Any]) -> None:
        """Create a Kubernetes Service for an inference endpoint."""
        port = spec.get("port", 8000)

        service = client.V1Service(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=namespace,
                labels={
                    "app": name,
                    "project": "gco",
                    "gco.io/type": "inference",
                },
            ),
            spec=client.V1ServiceSpec(
                selector={"app": name},
                ports=[
                    client.V1ServicePort(
                        port=80,
                        target_port=port,
                        protocol="TCP",
                    )
                ],
                type="ClusterIP",
            ),
        )

        try:
            self.core_v1.create_namespaced_service(
                namespace, service, _request_timeout=self._k8s_timeout
            )
            logger.info("Created service %s/%s", namespace, name)
        except ApiException as e:
            if e.status == 409:
                logger.info("Service %s/%s already exists", namespace, name)
            else:
                raise

    def _ensure_service(self, name: str, namespace: str, spec: dict[str, Any]) -> None:
        """Ensure the Service exists, recreating it if missing."""
        try:
            self.core_v1.read_namespaced_service(
                name, namespace, _request_timeout=self._k8s_timeout
            )
        except ApiException as e:
            if e.status == 404:
                logger.warning("Service %s/%s missing, recreating", namespace, name)
                self._create_service(name, namespace, spec)
            else:
                raise

    def _ensure_ingress(
        self,
        name: str,
        namespace: str,
        spec: dict[str, Any],
        endpoint: dict[str, Any],
    ) -> None:
        """Ensure the Ingress exists, recreating it if missing."""
        try:
            self.networking_v1.read_namespaced_ingress(
                f"inference-{name}", namespace, _request_timeout=self._k8s_timeout
            )
        except ApiException as e:
            if e.status == 404:
                logger.warning("Ingress for %s missing, recreating", name)
                self._update_ingress_rule(name, namespace, spec, endpoint)
            else:
                raise

    def _update_ingress_rule(
        self,
        name: str,
        namespace: str,
        spec: dict[str, Any],
        endpoint: dict[str, Any],
    ) -> None:
        """Create or update an Ingress for the inference endpoint.

        The Ingress is created in the same namespace as the Service and pods.
        IngressClassParams with group.name merges all Ingresses onto a single
        shared ALB regardless of namespace.
        """
        ingress_path = endpoint.get("ingress_path", f"/inference/{name}")
        image = spec.get("image", "")
        image_lower = image.lower()
        root_path_images = ("vllm", "text-generation-inference", "tgi")
        uses_root_path = any(tag in image_lower for tag in root_path_images)
        base_health = spec.get("health_check_path", "/health")
        health_path = f"/inference/{name}{base_health}" if uses_root_path else base_health

        ingress = client.V1Ingress(
            metadata=client.V1ObjectMeta(
                name=f"inference-{name}",
                namespace=namespace,
                labels={
                    "app": name,
                    "project": "gco",
                    "gco.io/type": "inference",
                },
                annotations={
                    "alb.ingress.kubernetes.io/healthcheck-path": health_path,
                    "alb.ingress.kubernetes.io/healthcheck-interval-seconds": "15",
                },
            ),
            spec=client.V1IngressSpec(
                ingress_class_name="alb",
                rules=[
                    client.V1IngressRule(
                        http=client.V1HTTPIngressRuleValue(
                            paths=[
                                client.V1HTTPIngressPath(
                                    path=ingress_path,
                                    path_type="Prefix",
                                    backend=client.V1IngressBackend(
                                        service=client.V1IngressServiceBackend(
                                            name=name,
                                            port=client.V1ServiceBackendPort(
                                                number=80,
                                            ),
                                        ),
                                    ),
                                )
                            ]
                        )
                    )
                ],
            ),
        )

        try:
            self.networking_v1.create_namespaced_ingress(
                namespace, ingress, _request_timeout=self._k8s_timeout
            )
            logger.info("Created ingress for %s at %s", name, ingress_path)
        except ApiException as e:
            if e.status == 409:
                self.networking_v1.patch_namespaced_ingress(
                    f"inference-{name}", namespace, ingress, _request_timeout=self._k8s_timeout
                )
                logger.info("Updated ingress for %s", name)
            else:
                raise

    def _check_health_watchdog(
        self,
        name: str,
        namespace: str,
        ready_replicas: int,
        desired_replicas: int,
        spec: dict[str, Any],
        endpoint: dict[str, Any],
    ) -> bool:
        """Health watchdog: remove Ingress for persistently unhealthy endpoints.

        If an endpoint has zero ready replicas for longer than the configured
        threshold, the watchdog removes its Ingress to protect the shared ALB.
        Global Accelerator considers an ALB unhealthy if ANY target group has
        zero healthy targets, so one bad endpoint can block all inference
        traffic to the region.

        When the endpoint recovers (ready_replicas > 0), the Ingress is
        automatically re-created by _ensure_ingress on the next cycle.

        Returns:
            True if the Ingress was removed (caller should skip _ensure_ingress).
            False if the endpoint is healthy or still within the grace period.
        """
        if ready_replicas > 0:
            # Endpoint is healthy — clear the tracker
            if name in self._unready_since:
                logger.info(
                    "Endpoint %s recovered, re-enabling Ingress",
                    name,
                )
                del self._unready_since[name]
            return False

        # Endpoint has zero ready replicas
        now = datetime.now(UTC)

        if name not in self._unready_since:
            # First time seeing this endpoint as unready — start the clock
            self._unready_since[name] = now
            logger.warning(
                "Endpoint %s has 0/%d ready replicas, starting health watchdog timer",
                name,
                desired_replicas,
            )
            return False

        # Check how long it's been unready
        unready_duration = (now - self._unready_since[name]).total_seconds()

        if unready_duration < self._ingress_removal_threshold:
            remaining = self._ingress_removal_threshold - unready_duration
            logger.warning(
                "Endpoint %s unready for %ds (removing Ingress in %ds)",
                name,
                int(unready_duration),
                int(remaining),
            )
            return False

        # Threshold exceeded — remove the Ingress to protect the ALB
        ingress_name = f"inference-{name}"
        try:
            self.networking_v1.delete_namespaced_ingress(
                ingress_name, namespace, _request_timeout=self._k8s_timeout
            )
            logger.warning(
                "WATCHDOG: Removed Ingress for unhealthy endpoint %s "
                "(unready for %ds > %ds threshold). "
                "Ingress will be re-created when the endpoint recovers.",
                name,
                int(unready_duration),
                self._ingress_removal_threshold,
            )
        except ApiException as e:
            if e.status == 404:
                logger.debug("Ingress for %s already removed", name)
            else:
                logger.error("Failed to remove Ingress for %s: %s", name, e)

        return True

    def _scale_deployment(self, name: str, namespace: str, replicas: int) -> None:
        """Scale a deployment to the desired replica count."""
        self.apps_v1.patch_namespaced_deployment(
            name,
            namespace,
            body={"spec": {"replicas": replicas}},
            _request_timeout=self._k8s_timeout,
        )

    def _update_deployment_image(self, name: str, namespace: str, image: str) -> None:
        """Update the container image of a deployment."""
        self.apps_v1.patch_namespaced_deployment(
            name,
            namespace,
            body={
                "spec": {
                    "template": {"spec": {"containers": [{"name": "inference", "image": image}]}}
                }
            },
            _request_timeout=self._k8s_timeout,
        )

    def _reconcile_canary(
        self,
        name: str,
        namespace: str,
        spec: dict[str, Any],
        canary: dict[str, Any],
        endpoint: dict[str, Any],
    ) -> None:
        """Reconcile canary deployment and weighted ingress routing.

        Creates a canary deployment and service alongside the primary,
        then updates the ingress to use ALB action-based weighted routing.
        """
        canary_name = f"{name}-canary"
        canary_image = canary.get("image", "")
        canary_replicas = canary.get("replicas", 1)
        canary_weight = canary.get("weight", 10)
        primary_weight = 100 - canary_weight

        # Build canary spec (same as primary but with canary image/replicas)
        canary_spec = dict(spec)
        canary_spec["image"] = canary_image
        canary_spec["replicas"] = canary_replicas
        # Remove canary field from the canary spec to avoid recursion
        canary_spec.pop("canary", None)

        # Create or update canary deployment
        canary_deployment = self._get_deployment(canary_name, namespace)
        if canary_deployment is None:
            logger.info("Creating canary deployment %s with image %s", canary_name, canary_image)
            self._create_deployment(canary_name, namespace, canary_spec)
            self._create_service(canary_name, namespace, canary_spec)
        else:
            # Update image if changed
            current_image = self._get_deployment_image(canary_deployment)
            if current_image != canary_image:
                self._update_deployment_image(canary_name, namespace, canary_image)
            # Update replicas if changed
            if (canary_deployment.spec.replicas or 1) != canary_replicas:
                self._scale_deployment(canary_name, namespace, canary_replicas)

        # Update ingress with weighted routing via ALB actions annotation
        self._update_canary_ingress(name, namespace, spec, endpoint, primary_weight, canary_weight)

    def _update_canary_ingress(
        self,
        name: str,
        namespace: str,
        spec: dict[str, Any],
        endpoint: dict[str, Any],
        primary_weight: int,
        canary_weight: int,
    ) -> None:
        """Update ingress with ALB weighted target group routing."""
        import json as _json

        ingress_path = endpoint.get("ingress_path", f"/inference/{name}")
        image = spec.get("image", "")
        image_lower = image.lower()
        root_path_images = ("vllm", "text-generation-inference", "tgi")
        uses_root_path = any(tag in image_lower for tag in root_path_images)
        base_health = spec.get("health_check_path", "/health")
        health_path = f"/inference/{name}{base_health}" if uses_root_path else base_health

        # ALB weighted routing via forward action annotation
        forward_config = _json.dumps(
            {
                "type": "forward",
                "forwardConfig": {
                    "targetGroups": [
                        {
                            "serviceName": name,
                            "servicePort": 80,
                            "weight": primary_weight,
                        },
                        {
                            "serviceName": f"{name}-canary",
                            "servicePort": 80,
                            "weight": canary_weight,
                        },
                    ]
                },
            }
        )

        ingress = client.V1Ingress(
            metadata=client.V1ObjectMeta(
                name=f"inference-{name}",
                namespace=namespace,
                labels={
                    "app": name,
                    "project": "gco",
                    "gco.io/type": "inference",
                    "gco.io/canary": "true",
                },
                annotations={
                    "alb.ingress.kubernetes.io/healthcheck-path": health_path,
                    "alb.ingress.kubernetes.io/healthcheck-interval-seconds": "15",
                    "alb.ingress.kubernetes.io/actions.weighted-routing": forward_config,
                },
            ),
            spec=client.V1IngressSpec(
                ingress_class_name="alb",
                rules=[
                    client.V1IngressRule(
                        http=client.V1HTTPIngressRuleValue(
                            paths=[
                                client.V1HTTPIngressPath(
                                    path=ingress_path,
                                    path_type="Prefix",
                                    backend=client.V1IngressBackend(
                                        service=client.V1IngressServiceBackend(
                                            name="weighted-routing",
                                            port=client.V1ServiceBackendPort(
                                                name="use-annotation",
                                            ),
                                        ),
                                    ),
                                )
                            ]
                        )
                    )
                ],
            ),
        )

        try:
            self.networking_v1.patch_namespaced_ingress(
                f"inference-{name}", namespace, ingress, _request_timeout=self._k8s_timeout
            )
            logger.info(
                "Updated ingress for %s: primary=%d%% canary=%d%%",
                name,
                primary_weight,
                canary_weight,
            )
        except ApiException as e:
            if e.status == 404:
                self.networking_v1.create_namespaced_ingress(
                    namespace, ingress, _request_timeout=self._k8s_timeout
                )
                logger.info("Created canary ingress for %s", name)
            else:
                raise

    def _cleanup_canary(self, name: str, namespace: str) -> None:
        """Remove canary deployment, service, and restore primary-only ingress."""
        canary_name = f"{name}-canary"

        # Delete canary deployment
        try:
            self.apps_v1.delete_namespaced_deployment(
                canary_name, namespace, _request_timeout=self._k8s_timeout
            )
            logger.info("Deleted canary deployment %s", canary_name)
        except ApiException as e:
            if e.status != 404:
                logger.error("Failed to delete canary deployment %s: %s", canary_name, e)

        # Delete canary service
        try:
            self.core_v1.delete_namespaced_service(
                canary_name, namespace, _request_timeout=self._k8s_timeout
            )
            logger.info("Deleted canary service %s", canary_name)
        except ApiException as e:
            if e.status != 404:
                logger.error("Failed to delete canary service %s: %s", canary_name, e)

    def _delete_resources(self, name: str, namespace: str) -> None:
        """Delete all Kubernetes resources for an endpoint."""
        # Delete canary resources first
        self._cleanup_canary(name, namespace)

        # Delete deployment
        try:
            self.apps_v1.delete_namespaced_deployment(
                name, namespace, _request_timeout=self._k8s_timeout
            )
            logger.info("Deleted deployment %s/%s", namespace, name)
        except ApiException as e:
            if e.status != 404:
                logger.error("Failed to delete deployment %s: %s", name, e)

        # Delete service
        try:
            self.core_v1.delete_namespaced_service(
                name, namespace, _request_timeout=self._k8s_timeout
            )
            logger.info("Deleted service %s/%s", namespace, name)
        except ApiException as e:
            if e.status != 404:
                logger.error("Failed to delete service %s: %s", name, e)

        # Delete ingress
        try:
            self.networking_v1.delete_namespaced_ingress(
                f"inference-{name}", namespace, _request_timeout=self._k8s_timeout
            )
            logger.info("Deleted ingress for %s", name)
        except ApiException as e:
            if e.status != 404:
                logger.error("Failed to delete ingress for %s: %s", name, e)

        # Delete HPA
        try:
            autoscaling_v2 = client.AutoscalingV2Api()
            autoscaling_v2.delete_namespaced_horizontal_pod_autoscaler(name, namespace)
            logger.info("Deleted HPA for %s", name)
        except ApiException as e:
            if e.status != 404:
                logger.error("Failed to delete HPA for %s: %s", name, e)

    def _create_or_update_hpa(self, name: str, namespace: str, spec: dict[str, Any]) -> None:
        """Create or update a Horizontal Pod Autoscaler for an inference endpoint."""
        autoscaling_config = spec.get("autoscaling", {})
        if not autoscaling_config.get("enabled"):
            return

        min_replicas = autoscaling_config.get("min_replicas", 1)
        max_replicas = autoscaling_config.get("max_replicas", 10)
        metrics_config = autoscaling_config.get("metrics", [{"type": "cpu", "target": 70}])

        # Build HPA metrics
        hpa_metrics = []
        for m in metrics_config:
            metric_type = m.get("type", "cpu")
            target_value = m.get("target", 70)

            if metric_type == "cpu":
                hpa_metrics.append(
                    client.V2MetricSpec(
                        type="Resource",
                        resource=client.V2ResourceMetricSource(
                            name="cpu",
                            target=client.V2MetricTarget(
                                type="Utilization",
                                average_utilization=target_value,
                            ),
                        ),
                    )
                )
            elif metric_type == "memory":
                hpa_metrics.append(
                    client.V2MetricSpec(
                        type="Resource",
                        resource=client.V2ResourceMetricSource(
                            name="memory",
                            target=client.V2MetricTarget(
                                type="Utilization",
                                average_utilization=target_value,
                            ),
                        ),
                    )
                )

        if not hpa_metrics:
            # Default to CPU if no recognized metrics
            hpa_metrics.append(
                client.V2MetricSpec(
                    type="Resource",
                    resource=client.V2ResourceMetricSource(
                        name="cpu",
                        target=client.V2MetricTarget(
                            type="Utilization",
                            average_utilization=70,
                        ),
                    ),
                )
            )

        hpa = client.V2HorizontalPodAutoscaler(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=namespace,
                labels={
                    "app": name,
                    "project": "gco",
                    "gco.io/type": "inference",
                },
            ),
            spec=client.V2HorizontalPodAutoscalerSpec(
                scale_target_ref=client.V2CrossVersionObjectReference(
                    api_version="apps/v1",
                    kind="Deployment",
                    name=name,
                ),
                min_replicas=min_replicas,
                max_replicas=max_replicas,
                metrics=hpa_metrics,
            ),
        )

        autoscaling_v2 = client.AutoscalingV2Api()
        try:
            autoscaling_v2.create_namespaced_horizontal_pod_autoscaler(namespace, hpa)
            logger.info("Created HPA for %s (min=%d, max=%d)", name, min_replicas, max_replicas)
        except ApiException as e:
            if e.status == 409:
                autoscaling_v2.patch_namespaced_horizontal_pod_autoscaler(name, namespace, hpa)
                logger.info("Updated HPA for %s", name)
            else:
                raise

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def get_metrics(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "region": self.region,
            "running": self._running,
            "reconcile_count": self._reconcile_count,
            "errors_count": self._errors_count,
        }


def create_inference_monitor_from_env() -> InferenceMonitor:
    """Create an InferenceMonitor from environment variables."""
    cluster_id = os.getenv("CLUSTER_NAME", "unknown-cluster")
    region = os.getenv("REGION", "unknown-region")
    namespace = os.getenv("INFERENCE_NAMESPACE", "gco-inference")
    interval = int(os.getenv("RECONCILE_INTERVAL_SECONDS", "15"))

    # Enable structured JSON logging for CloudWatch Insights
    configure_structured_logging(
        service_name="inference-monitor",
        cluster_id=cluster_id,
        region=region,
    )

    store = InferenceEndpointStore()  # Uses DYNAMODB_REGION env var, falls back to REGION

    return InferenceMonitor(
        cluster_id=cluster_id,
        region=region,
        store=store,
        namespace=namespace,
        reconcile_interval=interval,
    )


async def main() -> None:
    """Entry point for the inference monitor."""
    monitor = create_inference_monitor_from_env()
    logger.info("Inference monitor initialized: %s", monitor.get_metrics())

    while True:
        try:
            await monitor.start()
        except KeyboardInterrupt:
            logger.info("Shutting down inference monitor")
            monitor.stop()
            break
        except Exception as e:
            logger.error("Monitor crashed, restarting in 10s: %s", e, exc_info=True)
            monitor.stop()
            monitor._running = False
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
