# Helm Installer Lambda
# Installs Helm charts (KEDA, NVIDIA DRA Driver, etc.) to EKS clusters
# Note: This Lambda runs on x86_64 (AMD64) because, in testing, the ARM64
# Lambda base image had issues with the Helm/kubectl binary entrypoint.
# This Lambda only runs during stack deploys, so cost savings from ARM64
# would be negligible.
FROM public.ecr.aws/lambda/python:3.14

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR ${LAMBDA_TASK_ROOT}

# Install Helm
# hadolint ignore=DL3041
RUN dnf install -y tar gzip && \
    curl -fsSL https://get.helm.sh/helm-v4.1.3-linux-amd64.tar.gz | tar xz && \
    mv linux-amd64/helm /usr/local/bin/helm && \
    rm -rf linux-amd64 && \
    dnf clean all

# Install kubectl for cluster connectivity verification
RUN curl -LO "https://dl.k8s.io/release/v1.35.4/bin/linux/amd64/kubectl" && \
    chmod +x kubectl && \
    mv kubectl /usr/local/bin/kubectl

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy handler code
COPY handler.py .
COPY charts.yaml .

CMD ["handler.lambda_handler"]
