#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/deploy.env"

AWS_REGION="us-east-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
IMAGE="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/wikimania-frontend:latest"

echo "Creating wikimania-frontend ECS Express service..."

aws ecs create-express-gateway-service \
  --service-name wikimania-frontend \
  --primary-container "{
    \"image\": \"$IMAGE\",
    \"containerPort\": 8080
  }" \
  --execution-role-arn "arn:aws:iam::$ACCOUNT_ID:role/ecsTaskExecutionRole" \
  --infrastructure-role-arn "arn:aws:iam::$ACCOUNT_ID:role/ecsInfrastructureRoleForExpressServices" \
  --region "$AWS_REGION"

echo ""
echo "Done. Copy the ingressPaths[0].endpoint value above — that is your frontend URL."
echo "Then fill in FRONTEND_URL and FRONTEND_SERVICE_ARN in scripts/deploy.env"
