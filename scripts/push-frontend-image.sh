#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/deploy.env"

AWS_REGION="us-east-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REPO="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/wikimania-frontend"

echo "Authenticating to ECR..."
aws ecr get-login-password --region "$AWS_REGION" \
  | podman login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

echo "Building frontend with VITE_API_URL=$BACKEND_URL ..."
podman build \
  --build-arg VITE_API_URL="$BACKEND_URL" \
  -t "$REPO:latest" \
  "$(dirname "$SCRIPT_DIR")/frontend"

echo "Pushing frontend image..."
podman push "$REPO:latest"

echo "Done. Frontend image pushed to $REPO:latest"
