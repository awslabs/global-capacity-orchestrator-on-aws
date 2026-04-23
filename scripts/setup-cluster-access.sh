#!/bin/bash
# Setup script for configuring kubectl access to GCO cluster

set -e

CLUSTER_NAME="${1:-gco-us-east-1}"
REGION="${2:-us-east-1}"

echo "Setting up access to cluster: $CLUSTER_NAME in region: $REGION"
echo ""

# Update kubeconfig
echo "1. Updating kubeconfig..."
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$REGION"

# Get current IAM principal
echo ""
echo "2. Getting your IAM principal..."
PRINCIPAL_ARN=$(aws sts get-caller-identity --query Arn --output text)
echo "   Principal: $PRINCIPAL_ARN"

# Handle assumed roles
if [[ "$PRINCIPAL_ARN" == *":assumed-role/"* ]]; then
    ROLE_NAME=$(echo "$PRINCIPAL_ARN" | sed 's/.*:assumed-role\/\([^\/]*\)\/.*/\1/')
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    PRINCIPAL_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
    echo "   Using role ARN: $PRINCIPAL_ARN"
fi

# Create access entry
echo ""
echo "3. Creating EKS access entry..."
aws eks create-access-entry \
  --cluster-name "$CLUSTER_NAME" \
  --region "$REGION" \
  --principal-arn "$PRINCIPAL_ARN" 2>&1 || echo "   Access entry may already exist"

# Associate admin policy
echo ""
echo "4. Associating cluster admin policy..."
aws eks associate-access-policy \
  --cluster-name "$CLUSTER_NAME" \
  --region "$REGION" \
  --principal-arn "$PRINCIPAL_ARN" \
  --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy \
  --access-scope type=cluster 2>&1 || echo "   Policy may already be associated"

# Verify access
echo ""
echo "5. Verifying access..."
echo "   Waiting for permissions to propagate..."
sleep 10
kubectl get nodes

echo ""
echo "✓ Setup complete! You can now use kubectl with cluster: $CLUSTER_NAME"
