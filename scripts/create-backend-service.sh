#!/usr/bin/env bash
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
AWS_REGION="us-east-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
IMAGE="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/wikimania-backend:latest"

# Load secrets from deploy.env (gitignored)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy.env
source "$SCRIPT_DIR/deploy.env"

# ── Create service ────────────────────────────────────────────────────────────
echo "Creating wikimania-backend ECS Express service..."

aws ecs create-express-gateway-service \
  --service-name wikimania-backend \
  --primary-container "{
    \"image\": \"$IMAGE\",
    \"containerPort\": 8080,
    \"environment\": [
      {\"name\": \"DATABASE_URL\",    \"value\": \"$DATABASE_URL\"},
      {\"name\": \"PROVIDER\",        \"value\": \"cerebras\"},
      {\"name\": \"MODEL_FAST\",      \"value\": \"llama3.1-8b\"},
      {\"name\": \"MODEL_REASONING\", \"value\": \"gpt-oss-120b\"},
      {\"name\": \"API_KEY\",         \"value\": \"$CEREBRAS_API_KEY\"},
      {\"name\": \"CORS_ORIGINS\",    \"value\": \"*\"}
    ]
  }" \
  --execution-role-arn "arn:aws:iam::$ACCOUNT_ID:role/ecsTaskExecutionRole" \
  --infrastructure-role-arn "arn:aws:iam::$ACCOUNT_ID:role/ecsInfrastructureRoleForExpressServices" \
  --vpc-configuration "subnets=[subnet-bc3857b0,subnet-9212b4b8,subnet-be09a1e6,subnet-cc7108a9,subnet-0a89fc37,subnet-b9ca5acf],securityGroups=[sg-0723f6f02b7e8ffea],assignPublicIp=ENABLED" \
  --health-check-path /docs \
  --region "$AWS_REGION"

echo ""
echo "Done. Copy the ingressPaths[0].endpoint value above — that is your backend URL."
