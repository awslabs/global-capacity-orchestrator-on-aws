# Analytics Environment

End-to-end guide to the optional GCO analytics environment — a
SageMaker Studio domain plus EMR Serverless, Cognito user pool, and
presigned-URL API gateway route, bolted onto an existing GCO
deployment via a single toggle.

The analytics environment is **off by default**. Enable it only when
you want interactive notebook analytics; the rest of GCO works exactly
the same whether or not it is deployed. The always-on
`Cluster_Shared_Bucket` that cluster jobs read and write is **not**
part of this feature's toggle — it is always on regardless of whether
analytics is enabled. See
[`docs/CLUSTER_SHARED_BUCKET.md`](CLUSTER_SHARED_BUCKET.md) for that
bucket's reference.

## Table of Contents

- [Cost](#cost)
- [Default image](#default-image)
- [(a) What the analytics environment provisions](#a-what-the-analytics-environment-provisions)
- [(b) Enabling and deploying the stack](#b-enabling-and-deploying-the-stack)
- [(c) Managing Cognito users via the CLI](#c-managing-cognito-users-via-the-cli)
- [(d) Logging into Studio](#d-logging-into-studio)
- [(e) Optional user-driven install of GCO CLI and MCP server](#e-optional-user-driven-install-of-gco-cli-and-mcp-server)
- [(f) Using the GCO CLI once installed inside JupyterLab](#f-using-the-gco-cli-once-installed-inside-jupyterlab)
- [(g) Submitting HyperPod jobs when the sub-toggle is enabled](#g-submitting-hyperpod-jobs-when-the-sub-toggle-is-enabled)
- [(h) Opening the environment in Kiro](#h-opening-the-environment-in-kiro)
- [(i) Running the example cluster jobs and reading their output from a notebook](#i-running-the-example-cluster-jobs-and-reading-their-output-from-a-notebook)
- [(j) The deploy/destroy test loop](#j-the-deploydestroy-test-loop)
- [(k) Two-bucket access model](#k-two-bucket-access-model)
- [(l) EFS persistent-home-folder behavior](#l-efs-persistent-home-folder-behavior)
- [(m) The `gco-cluster-shared-bucket` ConfigMap schema](#m-the-gco-cluster-shared-bucket-configmap-schema)
- [(n) Cross-region data-transfer caveat](#n-cross-region-data-transfer-caveat)

## Cost

The analytics environment is **off by default** and incurs zero cost
until you run `gco analytics enable` + `gco stacks deploy gco-analytics`.
When enabled, these resources drive cost:

- **SageMaker Studio** — per-user JupyterLab apps on `ml.t3.medium` by
  default. Charged per app-hour while the app is running. Shut idle
  apps down from the Studio UI to stop the meter.
- **EMR Serverless** — SPARK application, charged per vCPU-hour and
  GB-hour of actual job execution. Zero cost when no jobs are
  running.
- **KMS** — `Analytics_KMS_Key` ($1/month) and `Cluster_Shared_KMS_Key`
  ($1/month if you count it under this feature — it is actually owned
  by `gco-global` and always on). Plus per-request charges on key
  usage.
- **S3** — `Studio_Only_Bucket` and its access-logs bucket. Typical
  notebook usage is sub-dollar; the cost depends on how much data
  you stage.
- **Cognito** — The Lite tier includes 50,000 monthly active users (MAUs) for free (no automatic expiry). Advanced security features on the Essentials and Plus tiers are additional; see the [Amazon Cognito pricing page](https://aws.amazon.com/cognito/pricing/) for current rates. The new tier names (Lite / Essentials / Plus) landed in November 2024, so older blog posts may reference the legacy free-tier structure.
- **Lambda** — The presigned-URL Lambda is invoked once per Studio
  login. Effectively free at human-scale usage.
- **Studio_EFS** — Per-user home folders on EFS. Charged per GB-month
  of storage plus per-GB for IA-tier transitions.
- **VPC endpoints** — The analytics VPC creates interface endpoints
  for `SAGEMAKER_API`, `SAGEMAKER_RUNTIME`, `SAGEMAKER_STUDIO`,
  `SAGEMAKER_NOTEBOOK`, `STS`, `CLOUDWATCH_LOGS`, `ECR`, `ECR_DOCKER`,
  and `ELASTIC_FILE_SYSTEM`. Each interface endpoint is roughly
  $7/month per AZ plus per-GB processing. The gateway endpoint for
  `S3` is free.

To see the running cost:

```bash
gco costs summary --days 7
gco costs regions --days 7
```

Disable the feature at any time with `gco analytics disable` followed
by `gco stacks destroy gco-analytics`. The analytics resources are all
`RemovalPolicy.DESTROY` so destroy cleanly removes them without
orphaned retained resources (see section (l) for the opt-in retain
override on `Studio_EFS`).

## Default image

The Studio domain uses the **stock AWS-published SageMaker
Distribution image**. There is no custom image build, no
`sagemaker.CfnImage` resource, no ECR repository, and no Dockerfiles
in this repo for the Studio runtime. `DefaultUserSettings.JupyterLabAppSettings.CustomImages`
is explicitly empty, and that emptiness is a tested invariant.

If a user needs extra Python packages inside JupyterLab, install them
into `/home/sagemaker-user` using `pip install --user`:

```bash
pip install --user pandas pyarrow duckdb
```

Because `/home/sagemaker-user` is mounted from `Studio_EFS`, packages
installed this way **persist across JupyterLab app restarts and user
sessions**. No container image rebuild is required, and no operator
intervention is needed to support new libraries.

System-level tooling (for example, a binary published via `apt`) is
out of scope for this feature — use a shell-out to `subprocess` or a
vendored wheel instead.

## (a) What the analytics environment provisions

<details>
<summary>📊 Analytics stack architecture diagram (click to expand)</summary>

![Analytics Stack Architecture](../diagrams/analytics-stack.png)

*Auto-generated from the CDK app via AWS PDK cdk-graph. Regenerate with `python diagrams/generate.py --stack analytics`.*

</details>

When `analytics_environment.enabled=true` and `gco-analytics` is
deployed, the following resources appear in the API-gateway region
(default `us-east-2`):

- **SageMaker Studio domain** (`gco-analytics-domain`) with
  `auth_mode=IAM` and `app_network_access_type=VpcOnly`. No public
  network exposure for notebooks.
- **EMR Serverless application** (`SPARK` type) pinned to
  `EMR_SERVERLESS_RELEASE_LABEL` from
  [`gco/stacks/constants.py`](../gco/stacks/constants.py).
- **Cognito user pool** with a strong password policy (12+ chars,
  digits/symbols/uppercase required), SRP auth flow, and a hosted
  `UserPoolDomain` at prefix `gco-studio-<account>`.
- **Analytics VPC** — private-isolated subnets only, plus interface
  endpoints for the SageMaker, STS, CloudWatch Logs, ECR, and EFS
  services; gateway endpoint for S3.
- **Studio_EFS** — encrypted EFS file system for per-user home
  folders, one access point per Studio user profile.
- **Studio_Only_Bucket** — KMS-encrypted S3 bucket scoped to the
  SageMaker execution role only. Not accessible from cluster pods.
  Comes with a dedicated access-logs bucket.
- **Analytics_KMS_Key** — customer-managed KMS key encrypting the
  VPC's logs, Studio_Only_Bucket, Studio_EFS, and the Cognito user
  pool's message attributes.
- **SageMaker_Execution_Role** — role name begins with
  `AmazonSageMaker-gco-analytics-exec-<region>` (SageMaker requires
  the prefix). Granted RW on `Studio_Only_Bucket` and
  `Cluster_Shared_Bucket`, plus invoke rights on the GCO API for
  job/inference submission. When the `hyperpod` sub-toggle is on,
  the role additionally gets
  `sagemaker:CreateTrainingJob|DescribeTrainingJob|StopTrainingJob`
  and `sagemaker:ClusterInstance*`.
- **Presigned-URL Lambda** — the backend for the `/studio/login`
  API Gateway route. Calls
  `CreatePresignedDomainUrl` and creates per-user profiles on first
  login.
- **API Gateway `/studio/*` routes** — grafted onto the existing
  `gco-api-gateway` via an `analytics_config` constructor parameter
  (no second API Gateway). The `/studio/login` route uses Cognito
  authorization; the existing `/api/v1/*` and `/inference/*` routes
  keep IAM authorization unchanged.

When `analytics_environment.enabled=false`, none of these resources
exist. The regional stacks and the always-on `Cluster_Shared_Bucket`
are unaffected.

## (b) Enabling and deploying the stack

Two commands, in order:

```bash
gco analytics enable
gco stacks deploy gco-analytics
```

`gco analytics enable` flips `analytics_environment.enabled` to `true`
in `cdk.json` and prints the follow-up deploy command — it does not
deploy automatically. This is deliberate: you review the diff, then
decide to apply.

With HyperPod:

```bash
gco analytics enable --hyperpod
gco stacks deploy gco-analytics
```

The `--hyperpod` flag additionally sets
`analytics_environment.hyperpod.enabled=true`, which adds HyperPod
training-job permissions to the SageMaker execution role. See
section (g) for what that unlocks.

Skip the confirmation prompt with `-y`:

```bash
gco analytics enable -y
gco analytics enable --hyperpod -y
```

Run the pre-flight checks before deploying:

```bash
gco analytics doctor
```

`doctor` verifies that `gco-global`, `gco-api-gateway`, and every
regional stack listed in `deployment_regions.regional` are
`CREATE_COMPLETE`; that the three `/gco/cluster-shared-bucket/*` SSM
parameters are present in the global region; and that no orphaned
analytics resources are left over from a previous retain-policy
destroy. Exits non-zero on any failure, with a remediation line per
check.

Deploy takes about 15-20 minutes end-to-end. After the first
`gco-analytics` deploy, you must redeploy `gco-api-gateway` once so
the `/studio/*` routes appear on the existing REST API (cold-start
ordering — the analytics stack is in the same region as the API
Gateway stack and the `analytics_config` flows in as a constructor
parameter):

```bash
gco stacks deploy gco-api-gateway
```

Check status at any time:

```bash
gco analytics status
```

This prints the current `cdk.json` toggle state and the deployment
state of `gco-analytics`. Useful for confirming whether the stack is
enabled/deployed, disabled/deployed (about to be destroyed), or
disabled/undeployed (the steady state when unused).

Disable and tear down:

```bash
gco analytics disable
gco stacks destroy gco-analytics
```

`disable` leaves the `hyperpod`, `cognito`, and `efs` sub-blocks
untouched so a subsequent `enable` preserves your preferences.

## (c) Managing Cognito users via the CLI

The analytics CLI wraps the Cognito admin APIs so you never need the
AWS console to onboard users. All commands auto-discover the user-pool
ID from the `gco-analytics` CloudFormation outputs.

Create a user:

```bash
gco analytics users add --username alice --email alice@example.com
```

The command calls `cognito-idp:AdminCreateUser`, prints the
temporary password exactly once to stdout, and (by default) sends
Cognito's welcome email to the address on file. Suppress the email
with `--no-email` — useful when you want to hand the credentials over
out-of-band:

```bash
gco analytics users add --username bob --email bob@example.com --no-email
```

Example output:

```text
✓ Created Cognito user: alice
  Temporary password (printed exactly once): Tmp#V2xQ!f1yPq
```

List users:

```bash
gco analytics users list
gco analytics users list --as-json
```

The default output is a formatted table via the existing
`OutputFormatter`. `--as-json` emits a JSON array for scripting.

Remove a user:

```bash
gco analytics users remove --username alice
gco analytics users remove --username alice --yes
```

The first form prompts for confirmation. `--yes` skips the prompt.
Removing a user in Cognito does **not** automatically delete their
Studio user profile; the presigned-URL Lambda creates profiles on
first login and leaves them in place. If you need to clean up the
profile side, use `aws sagemaker delete-user-profile` directly.

Error path — when `gco-analytics` is not deployed:

```bash
$ gco analytics users list
✗ gco-analytics stack not deployed — run `gco analytics enable` then
  `gco stacks deploy gco-analytics`
```

## (d) Logging into Studio

```bash
gco analytics studio login --username alice
```

The command prompts for the password (use `--password <p>` or set
`$GCO_STUDIO_PASSWORD` to pass it non-interactively — for example in
CI), performs an SRP authentication against the Cognito user pool
using `USER_SRP_AUTH`, retrieves the Cognito `IdToken`, and exchanges
it for a SageMaker Studio presigned URL via
`GET {API_Gateway_URL}/prod/studio/login`.

The URL is printed on its own line on stdout so it's pipe-friendly.
Add `--open` to launch the default browser automatically:

```bash
gco analytics studio login --username alice --open
```

Override the API endpoint if auto-discovery can't find it (for
example, during local testing against a deployed API from a machine
without CloudFormation access):

```bash
gco analytics studio login \
  --username alice \
  --api-url https://abc123.execute-api.us-east-2.amazonaws.com
```

The password, the `IdToken`, and the Studio URL are never written to
disk. If login fails, the CLI prints the Cognito error code (for
example `NotAuthorizedException`, `UserNotFoundException`) or the
HTTP status + correlation ID from the `/studio/login` call, and exits
with status 1 or 2 respectively.

SRP flow explained:

1. CLI calls `InitiateAuth` with `AuthFlow=USER_SRP_AUTH`.
2. Cognito returns an SRP challenge; the CLI computes the password
   verifier locally using the pure-Python SRP implementation (no
   password is sent over the wire as a hash).
3. CLI calls `RespondToAuthChallenge`; Cognito returns an
   `AuthenticationResult` containing `IdToken`, `AccessToken`, and
   `RefreshToken`.
4. CLI sends the `IdToken` in the `Authorization` header to
   `/studio/login`. API Gateway's Cognito authorizer validates the
   JWT against the user-pool ARN.
5. Presigned-URL Lambda looks up the Studio domain, creates the user
   profile if it doesn't exist, and calls
   `CreatePresignedDomainUrl` with a 300-second expiry.
6. Lambda returns `{url, expires_in}`; API Gateway returns it to
   the CLI; the CLI prints `url` on its own line.

The URL expires in 5 minutes — open it promptly.

## (e) Optional user-driven install of GCO CLI and MCP server

Nothing in this section is installed by CDK. Every step below is
**manual, user-driven, and runs from inside a JupyterLab terminal**
on the SageMaker Studio instance. The CDK deploy only provisions the
Studio domain and the user profile; the tooling inside the notebook
is yours to install.

### Installing the GCO CLI inside SageMaker Studio

Open a JupyterLab terminal (`File` → `New` → `Terminal`) and run:

```bash
git clone https://github.com/awslabs/global-capacity-orchestrator-on-aws ~/gco
pip install -e ~/gco
```

Because `/home/sagemaker-user` (which contains `~/gco`) is mounted
from `Studio_EFS`, the install **persists across JupyterLab restarts
and kernel switches**. You install it once, and every subsequent
session has `gco` on the `PATH`.

Point the CLI at your deployment. Add these to `~/.bashrc` so they
survive terminal restarts:

```bash
cat >> ~/.bashrc <<'BASHRC_EOF'
export GCO_API_ENDPOINT=https://<api-id>.execute-api.us-east-2.amazonaws.com
export GCO_DEFAULT_REGION=us-east-1
BASHRC_EOF
source ~/.bashrc
```

Replace `<api-id>` with the API Gateway ID from
`aws cloudformation describe-stacks --stack-name gco-api-gateway`
(or from the `ApiGatewayUrl` output in the AWS console). The
`GCO_DEFAULT_REGION` should point at the regional region you want
the CLI to talk to by default — typically the region closest to the
Studio domain.

Verify:

```bash
gco --help
gco capacity status
```

Because the Studio execution role has the invoke permissions the CLI
needs (SQS send, API Gateway invoke, CloudWatch read), the CLI works
out of the box without additional IAM configuration.

### Optional: wiring GCO's MCP server into Amazon Q inside Studio

If you want to talk to the GCO MCP server from Amazon Q's chat
interface inside Studio, add an entry to `~/.aws/amazonq/mcp.json`:

```json
{
  "mcpServers": {
    "gco": {
      "command": "python",
      "args": ["-m", "mcp.run_mcp"],
      "cwd": "/home/sagemaker-user/gco",
      "env": {
        "GCO_DEFAULT_REGION": "us-east-1"
      }
    }
  }
}
```

This is **manual and user-driven, not part of the CDK deploy**. The
CDK code knows nothing about Amazon Q. You edit the file once inside
your Studio home directory and the MCP server is available to Amazon
Q from then on.

Restart Amazon Q from inside Studio to pick up the new server.

## (f) Using the GCO CLI once installed inside JupyterLab

With the CLI installed and env vars exported per section (e), every
`gco` command works from a JupyterLab terminal or from a notebook
cell via `!` shell-out.

Submit a job directly to a cluster:

```bash
gco jobs submit-direct examples/cluster-shared-bucket-upload-job.yaml -r us-east-1
```

Get a capacity recommendation before launching a training run:

```bash
gco capacity recommend-region --gpu --instance-type g5.xlarge
```

Deploy an inference endpoint from a notebook:

```bash
gco inference deploy exploration-llm \
  --image vllm/vllm-openai:v0.20.0 \
  --replicas 1 --gpu-count 1 \
  --region us-east-1
```

List jobs across all regions:

```bash
gco jobs list --all-regions
```

Tail logs for a specific job:

```bash
gco jobs logs analytics-s3-upload -r us-east-1 -n gco-jobs
```

All of these commands work identically from a workstation — the
Studio instance simply happens to have AWS credentials and network
access to the deployed stacks, so the same CLI invocations succeed
there too.

## (g) Submitting HyperPod jobs when the sub-toggle is enabled

When `analytics_environment.hyperpod.enabled=true` (flipped via
`gco analytics enable --hyperpod`), the SageMaker execution role gets
these additional permissions:

- `sagemaker:CreateTrainingJob`
- `sagemaker:DescribeTrainingJob`
- `sagemaker:StopTrainingJob`
- `sagemaker:ClusterInstance*` (cluster-instance management actions)

Notebook users can then launch HyperPod training jobs directly from
a Studio cell via `boto3` — no additional IAM setup, no cluster-side
changes:

```python
import boto3

sm = boto3.client("sagemaker")

response = sm.create_training_job(
    TrainingJobName="exploration-run-001",
    AlgorithmSpecification={
        "TrainingImage": "763104351884.dkr.ecr.us-east-1.amazonaws.com/pytorch-training:2.4.0-gpu-py311-cu124-ubuntu22.04-sagemaker",
        "TrainingInputMode": "File",
    },
    RoleArn="arn:aws:iam::123456789012:role/AmazonSageMaker-gco-analytics-exec-us-east-2",
    InputDataConfig=[{
        "ChannelName": "training",
        "DataSource": {"S3DataSource": {
            "S3DataType": "S3Prefix",
            "S3Uri": "s3://gco-cluster-shared-123456789012-us-east-2/training-data/",
            "S3DataDistributionType": "FullyReplicated",
        }},
    }],
    OutputDataConfig={
        "S3OutputPath": "s3://gco-cluster-shared-123456789012-us-east-2/training-output/",
    },
    ResourceConfig={
        "InstanceType": "ml.p4d.24xlarge",
        "InstanceCount": 2,
        "VolumeSizeInGB": 100,
    },
    StoppingCondition={"MaxRuntimeInSeconds": 3600},
)
print(response["TrainingJobArn"])
```

Notes:

- Replace the `RoleArn` with the actual role ARN from the
  `gco-analytics` stack outputs (key
  `SageMakerExecutionRoleArn`).
- `Cluster_Shared_Bucket` works as both input and output because
  the SageMaker role has RW on it (always, whenever analytics is
  enabled).
- HyperPod-style multi-node jobs use the same API — set
  `InstanceCount > 1` and configure a VPC peering between the
  training job and the analytics VPC if you want EFS access from
  the training containers.

Disable HyperPod later by flipping the sub-toggle off in `cdk.json`
and redeploying `gco-analytics`:

```bash
python3 -c "import json; d=json.load(open('cdk.json')); d['context']['analytics_environment']['hyperpod']['enabled']=False; json.dump(d, open('cdk.json','w'), indent=2)"
gco stacks deploy gco-analytics
```

## (h) Opening the environment in Kiro

[Kiro](https://kiro.dev) is an AI-powered IDE that can connect to a
remote JupyterLab instance. The combination lets you edit code in
Kiro (with its inline agent) while running it on SageMaker compute.

End-to-end flow:

1. **Obtain the Studio login URL** from the CLI:

    ```bash
    gco analytics studio login --username alice
    ```

    Copy the URL from stdout.

2. **Open Studio in a browser** by pasting the URL. This creates a
    signed Studio session. Leave this tab open — the session token
    is needed for the Kiro connection.

3. **Connect Kiro to the JupyterLab instance** via its remote
    workspace feature. Kiro's connection mechanics (whether via a
    Jupyter server URL, an SSH tunnel, or an SSM port-forward) are
    documented in the official Kiro docs — **refer there** rather
    than copying the steps here, since they evolve independently
    of GCO.

4. **Verify the connection** by opening a terminal inside Kiro
    (attached to the Studio instance) and running:

    ```bash
    gco --help
    ```

    If the CLI was installed per section (e), `--help` prints the
    full command tree. If not, install it first via:

    ```bash
    git clone https://github.com/awslabs/global-capacity-orchestrator-on-aws ~/gco
    pip install -e ~/gco
    ```

GCO does not add Kiro-specific configuration beyond what's documented
above. No workspace URL, SSH key, or SSM tunnel is provisioned by
CDK. If Kiro's connection mechanism requires additional setup (for
example, a public JupyterLab server endpoint), that setup is entirely
on the Kiro side and is not a GCO feature.

If Kiro eventually needs in-repo configuration (for example a
`.kiro/workspace.json` file) to connect to Studio, that will be a
separate feature with its own spec — the current feature explicitly
scopes itself to documentation only for the Kiro path.

## (i) Running the example cluster jobs and reading their output from a notebook

Three example manifests ship with the repo specifically for this
workflow. See
[`docs/CLUSTER_SHARED_BUCKET.md`](CLUSTER_SHARED_BUCKET.md#consuming-from-job-manifests)
for their full descriptions.

End-to-end example — cluster writes, notebook reads:

**Step 1 — submit the upload job from a CLI** (from your workstation
or from a JupyterLab terminal once section (e) is done):

```bash
gco jobs submit-direct examples/analytics-s3-upload-job.yaml -r us-east-1
```

The job reads the `gco-cluster-shared-bucket` ConfigMap via
`envFrom`, uploads a small CSV + schema manifest under
`s3://$sharedBucketName/analytics-data/`, and exits. Takes about 30
seconds end-to-end.

**Step 2 — wait for the job to complete**:

```bash
gco jobs list -r us-east-1 -n gco-jobs
gco jobs logs analytics-s3-upload -r us-east-1 -n gco-jobs
```

**Step 3 — read the output from a Studio notebook cell**:

```python
import boto3
import os
import pandas

# The Studio execution role has RW on Cluster_Shared_Bucket whenever
# analytics is enabled. The bucket name comes from a notebook-local
# environment variable you set once from a JupyterLab terminal:
#   export sharedBucketName=gco-cluster-shared-<account>-<global-region>
# (or hard-code it if you prefer — the value is stable across deploys).

s3 = boto3.client("s3")
obj = s3.get_object(
    Bucket=os.environ["sharedBucketName"],
    Key="analytics-data/dataset.csv",
)
df = pandas.read_csv(obj["Body"])
df.head()
```

Alternatively, surface `sharedBucketName` to the notebook via the
Studio environment variables pane, or build a tiny helper that reads
from a project-local config.

**Step 4 — cross-region consideration**: the bucket lives in the
global region (default `us-east-2`). If your Studio domain is also
in `us-east-2` (the default), the notebook-to-S3 calls are
same-region — no egress charges. Cluster pods, however, may be in
`us-east-1` or elsewhere — their uploads cross a region boundary.
See section (n) and
[`docs/CLUSTER_SHARED_BUCKET.md`](CLUSTER_SHARED_BUCKET.md#cross-region-egress)
for the full discussion.

## (j) The deploy/destroy test loop

The feature ships with an iteration-loop script that drives the full
`enable → deploy → test → destroy → verify-clean` cycle against the
`gco-analytics` stack. This is the tool the maintainers use to
iterate on `gco-analytics` without touching `gco-global`,
`gco-api-gateway`, or any regional stack.

The script lives at `scripts/test_analytics_lifecycle.py` and the
`gco analytics iterate` subcommand is a thin wrapper over it.

Dry-run the current state:

```bash
gco analytics iterate status --dry-run --json
```

`status` inspects CloudFormation + `cdk.json` and reports what the
script would do next. `--dry-run` adds an explicit planned-action
line without executing. `--json` emits a machine-readable JSON object
so you can pipe the output into other tools.

Run the full cycle:

```bash
gco analytics iterate all
```

This runs:

1. `deploy` — `cdk deploy gco-analytics`.
2. `test` — smoke test the `/studio/login` route end-to-end.
3. `destroy` — `cdk destroy gco-analytics`.
4. `verify-clean` — list IAM roles, S3 buckets, KMS keys, EFS file
   systems, and Cognito pools and assert no analytics resources are
   left retained.

Individual phases:

```bash
gco analytics iterate deploy
gco analytics iterate test
gco analytics iterate destroy
gco analytics iterate verify-clean
```

Each phase is idempotent — re-running `iterate deploy` on an already-
deployed stack is a no-op, and re-running `iterate destroy` on an
already-destroyed stack exits 0. This is by design, so the loop is
safe to run in CI.

Target a specific region:

```bash
gco analytics iterate deploy -r us-east-2
```

Defaults to `deployment_regions.api_gateway` from `cdk.json`. The
`-r` override is rarely needed — the analytics stack always deploys
into the API-gateway region — but it's available for edge cases
(for example, when testing against a secondary account in a
different region).

The script **never** runs `cdk destroy --all`, `gco stacks
destroy-all`, or any command that would destroy the baseline GCO
stacks (`gco-global`, `gco-api-gateway`, `gco-<region>`, or
`gco-monitoring`). It is scoped strictly to `gco-analytics` — by
policy.

## (k) Two-bucket access model

The analytics environment introduces a clear split between two S3
buckets. Understanding which bucket to write to is the main
user-facing decision when designing a notebook-plus-cluster workflow.

| Bucket | Owned by | Home region | RW for cluster pods | RW for SageMaker role | Read by notebooks | Always on |
|--------|----------|-------------|---------------------|------------------------|-------------------|-----------|
| `Cluster_Shared_Bucket` | `gco-global` | Global region (default `us-east-2`) | **Yes** (unconditional) | **Yes** (when analytics enabled) | **Yes** (when analytics enabled) | **Yes** |
| `Studio_Only_Bucket` | `gco-analytics` | API-gateway region (default `us-east-2`) | **No** (never — no IAM grant) | **Yes** | **Yes** | No — analytics-only |

Use cases:

- **Cluster job writes, notebook reads** → use `Cluster_Shared_Bucket`.
  This is the default handoff path. Cluster pods write via the
  `gco-cluster-shared-bucket` ConfigMap; notebooks read via the
  SageMaker execution role. See
  [`docs/CLUSTER_SHARED_BUCKET.md`](CLUSTER_SHARED_BUCKET.md) for the
  authoritative reference.
- **Notebook-only scratch** → use `Studio_Only_Bucket`. Artifacts
  stay inside the analytics VPC's KMS boundary and are invisible to
  cluster pods. No risk of a cluster job accidentally writing to or
  reading from a user's personal notebook data.
- **Both at the same time**: a notebook can read both buckets via
  `boto3` in the same cell — the SageMaker role has RW on both.

The cluster-pod side is asymmetric by design: no cluster pod ever
gets access to `Studio_Only_Bucket`, because the bucket's purpose is
notebook-private scratch. IAM is verified by a property-based test,
which asserts (for all toggle states and all regional regions) that
regional job-pod IAM statements reference
`arn:aws:s3:::gco-cluster-shared-*` and never
`arn:aws:s3:::gco-analytics-studio-*`.

## (l) EFS persistent-home-folder behavior

Each Studio user gets a per-user home folder on `Studio_EFS`,
accessed via a per-user EFS access point created by the presigned-
URL Lambda on first login. The access point POSIX-isolates the user
to `/home/<username>` on the EFS file system.

### Default removal policy: DESTROY

By default, `Studio_EFS` uses `RemovalPolicy.DESTROY`. This means:

- `cdk destroy gco-analytics` cleanly removes the EFS file system,
  its mount targets, and every access point.
- User home folders (installed packages, notebook history,
  checkpoints saved under `/home/sagemaker-user`) are **lost on
  destroy**.
- The iteration loop (section (j)) relies on this — each
  deploy/destroy cycle leaves no retained EFS behind.

### Opt-in: retain across destroys

Set `analytics_environment.efs.removal_policy = "retain"` in
`cdk.json` to preserve user home folders across `cdk destroy`:

```json
{
  "context": {
    "analytics_environment": {
      "enabled": true,
      "efs": {
        "removal_policy": "retain"
      }
    }
  }
}
```

Redeploy `gco-analytics` to apply the change. With retain enabled:

- The EFS file system survives `cdk destroy gco-analytics`.
- On next `cdk deploy gco-analytics`, CDK creates a **new** EFS
  file system (not re-uses the retained one — CDK has no first-
  class import-retained-resource flow). The retained file system
  becomes an orphaned AWS resource that you pay for until you
  manually delete it.
- You must therefore either (a) destroy the retained EFS manually
  before redeploying (defeating the point of retain), or (b) use
  retain as a one-way off-ramp when permanently decommissioning
  the feature.

### Manual cleanup steps when using retain

To clean up a retained EFS after a destroy, run these commands in
order:

```bash
# 1. Discover the retained file system
aws efs describe-file-systems --region us-east-2 \
  --query 'FileSystems[?contains(Tags[?Key==`Project`].Value, `GCO`)]'

# 2. For each mount target on the file system, delete it
aws efs describe-mount-targets --file-system-id fs-xxxxxxxx --region us-east-2
aws efs delete-mount-target --mount-target-id fsmt-xxxxxxxx --region us-east-2
# Repeat for every mount target

# 3. Delete per-user access points
aws efs describe-access-points --file-system-id fs-xxxxxxxx --region us-east-2
aws efs delete-access-point --access-point-id fsap-xxxxxxxx --region us-east-2
# Repeat for every access point

# 4. Delete the file system itself
aws efs delete-file-system --file-system-id fs-xxxxxxxx --region us-east-2
```

`gco analytics doctor` flags retained orphans on next
`analytics enable` and prints these remediation commands inline so
you don't need to hunt them down — but they are reproduced above so
you can script the cleanup yourself if you prefer.

Cognito pools support the same retain opt-in via
`analytics_environment.cognito.removal_policy = "retain"` with
analogous cleanup steps (`aws cognito-idp delete-user-pool-domain`
before `aws cognito-idp delete-user-pool`).

## (m) The `gco-cluster-shared-bucket` ConfigMap schema

The always-on `gco-cluster-shared-bucket` ConfigMap is the
cluster-side interface for `Cluster_Shared_Bucket`. It is applied
into the `gco-jobs`, `gco-system`, and `gco-inference` namespaces in
every regional cluster, regardless of the analytics toggle. The
authoritative reference is
[`docs/CLUSTER_SHARED_BUCKET.md`](CLUSTER_SHARED_BUCKET.md#configmap-schema);
what follows is a recap.

Schema (live cluster):

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: gco-cluster-shared-bucket
  namespace: gco-jobs
data:
  sharedBucketName: "gco-cluster-shared-123456789012-us-east-2"
  sharedBucketArn: "arn:aws:s3:::gco-cluster-shared-123456789012-us-east-2"
  sharedBucketRegion: "us-east-2"
```

Consumption from a job manifest — use `envFrom.configMapRef` to
inject all three keys as env vars in one block:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: my-uploader
  namespace: gco-jobs
spec:
  template:
    spec:
      serviceAccountName: gco-service-account
      containers:
      - name: uploader
        image: python:3.14.4-slim
        command: ["python", "-c", "import os; print(os.environ['sharedBucketName'])"]
        envFrom:
        - configMapRef:
            name: gco-cluster-shared-bucket
      restartPolicy: Never
```

Inside the container, `os.environ["sharedBucketName"]`,
`os.environ["sharedBucketArn"]`, and
`os.environ["sharedBucketRegion"]` are all populated.

Cross-reference: [`docs/CLUSTER_SHARED_BUCKET.md`](CLUSTER_SHARED_BUCKET.md)
is the single source of truth for this ConfigMap — the schema,
`envFrom` pattern, and ownership semantics are all documented there.
This section is a pointer, not a replacement.

## (n) Cross-region data-transfer caveat

`Cluster_Shared_Bucket` lives in the global region (default
`us-east-2`). Cluster pods writing to it from other regions incur
small cross-region egress charges on every API call and every byte
transferred. For small artifacts the cost is negligible; for large
datasets, batch uploads or keep the data on regional EFS/FSx
instead.

Full explanation with guidance on what to put in
`Cluster_Shared_Bucket` vs. local regional storage is in
[`docs/CLUSTER_SHARED_BUCKET.md` → Cross-region egress](CLUSTER_SHARED_BUCKET.md#cross-region-egress).
