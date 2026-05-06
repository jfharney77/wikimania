#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/deploy.env"

AWS_REGION="us-east-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
IMAGE="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/wikimania-backend:latest"

echo "Updating backend CORS_ORIGINS to $FRONTEND_URL ..."

aws ecs update-express-gateway-service \
  --service-arn "$BACKEND_SERVICE_ARN" \
  --primary-container "{
    \"image\": \"$IMAGE\",
    \"containerPort\": 8080,
    \"environment\": [
      {\"name\": \"DATABASE_URL\",    \"value\": \"$DATABASE_URL\"},
      {\"name\": \"PROVIDER\",        \"value\": \"cerebras\"},
      {\"name\": \"MODEL_FAST\",      \"value\": \"llama3.1-8b\"},
      {\"name\": \"MODEL_REASONING\", \"value\": \"llama-3.3-70b\"},
      {\"name\": \"API_KEY\",         \"value\": \"$CEREBRAS_API_KEY\"},
      {\"name\": \"CORS_ORIGINS\",    \"value\": \"$FRONTEND_URL\"}
    ]
  }" \
  --region "$AWS_REGION"

echo "Done. Backend will redeploy with CORS locked to $FRONTEND_URL"
