# AWS Deployment Guide

## Architecture

```
Browser → ECS Express Mode (React frontend container)
Browser → ECS Express Mode (FastAPI backend container)
Backend  → Supabase PostgreSQL
Images   → ECR (Elastic Container Registry)
CI/CD    → GitLab CI → ECR → ECS
```

**Services and approximate monthly cost (minimal setup):**
| Service | Purpose | ~Cost |
|---------|---------|-------|
| ECR | Stores Docker images | Free (500 MB free tier) |
| ECS Express Mode (backend) | Runs FastAPI container | ~$10–15 |
| ECS Express Mode (frontend) | Runs React container | ~$5–10 |
| Supabase PostgreSQL | Database | Free tier |
| Cerebras API | LLM inference | pay-per-token |
| **Total** | | **~$15–25/month + LLM usage** |

> **Note:** App Runner was deprecated April 30, 2026. ECS Express Mode is its replacement — same experience (give it a container image, it provisions the load balancer, scaling, and HTTPS automatically).

---

## One-time setup

Work through these steps in order. The whole thing takes about 45 minutes.

---

### Step 1 — Set up Supabase PostgreSQL

1. Go to [supabase.com](https://supabase.com) → **New project**.
2. Name: `wikimania` | Region: **US East (N. Virginia)** | set a strong password → **Create project**.
3. Once ready → click **Connect** (top of dashboard) → copy the **URI** connection string.

Your `DATABASE_URL` will look like:
```
postgresql://postgres:<password>@db.xxxx.supabase.co:5432/postgres
```

Save it — you'll need it in Step 6.

---

### Step 2 — Create ECR repositories

1. Go to **ECR** → **Create repository** — do this twice:
   - Name: `wikimania-backend`
   - Name: `wikimania-frontend`
2. Leave all settings default. Note your **AWS account ID** and **region** (shown in the repo URIs).

---

### Step 3 — Create IAM roles for ECS

ECS Express Mode needs two roles.

#### Task Execution Role
1. **IAM** → **Roles** → **Create role**.
2. Trusted entity: **AWS service** → **Elastic Container Service Task**.
3. Attach policy: `AmazonECSTaskExecutionRolePolicy`.
4. Name: `ecsTaskExecutionRole` → Create.

#### Infrastructure Role
1. **IAM** → **Roles** → **Create role**.
2. Trusted entity: **Custom trust policy** — paste:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": { "Service": "ecs.amazonaws.com" },
       "Action": "sts:AssumeRole"
     }]
   }
   ```
3. Attach policy: `AmazonECSInfrastructureRoleForExpressGatewayServices`.
4. Name: `ecsInfrastructureRoleForExpressServices` → Create.

---

### Step 4 — Create IAM user for GitLab CI

1. **IAM** → **Users** → **Create user** → name: `wikimania-gitlab-ci`.
2. **Attach policies directly**:
   - `AmazonECS_FullAccess`
   - `AmazonEC2ContainerRegistryPowerUser`
3. Add this **inline policy** (replace `ACCOUNT_ID` with your 12-digit account number):
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": "iam:PassRole",
       "Resource": [
         "arn:aws:iam::ACCOUNT_ID:role/ecsTaskExecutionRole",
         "arn:aws:iam::ACCOUNT_ID:role/ecsInfrastructureRoleForExpressServices"
       ]
     }]
   }
   ```
4. Open the user → **Security credentials** → **Create access key** → select **Application running outside AWS** → save both keys.

---

### Step 5 — Push the first backend image manually

```bash
# Authenticate Docker to ECR
aws ecr get-login-password --region REGION \
  | docker login --username AWS --password-stdin ACCOUNT_ID.dkr.ecr.REGION.amazonaws.com

# Build and push
cd backend
docker build -t wikimania-backend .
docker tag wikimania-backend:latest ACCOUNT_ID.dkr.ecr.REGION.amazonaws.com/wikimania-backend:latest
docker push ACCOUNT_ID.dkr.ecr.REGION.amazonaws.com/wikimania-backend:latest
```

---

### Step 6 — Create the backend ECS Express Mode service

```bash
aws ecs create-express-gateway-service \
  --service-name wikimania-backend \
  --primary-container '{
    "image": "ACCOUNT_ID.dkr.ecr.REGION.amazonaws.com/wikimania-backend:latest",
    "containerPort": 8080,
    "environment": [
      {"name": "DATABASE_URL",   "value": "postgresql://wikimania:<pw>@<endpoint>:5432/wikimania"},
      {"name": "PROVIDER",       "value": "cerebras"},
      {"name": "MODEL_FAST",     "value": "llama3.1-8b"},
      {"name": "MODEL_REASONING","value": "llama-3.3-70b"},
      {"name": "API_KEY",        "value": "<your-cerebras-api-key>"},
      {"name": "CORS_ORIGINS",   "value": "*"}
    ]
  }' \
  --execution-role-arn arn:aws:iam::ACCOUNT_ID:role/ecsTaskExecutionRole \
  --infrastructure-role-arn arn:aws:iam::ACCOUNT_ID:role/ecsInfrastructureRoleForExpressServices \
  --health-check-path /docs \
  --region REGION
```

From the response, copy `ingressPaths[0].endpoint` — this is the backend URL (e.g. `https://cl-abc123.ecs.us-east-1.on.aws`).

---

### Step 7 — Push the first frontend image manually

`VITE_API_URL` is baked into the JS bundle at build time, so you must pass it as a build arg:

```bash
cd frontend
docker build \
  --build-arg VITE_API_URL=https://cl-abc123.ecs.REGION.on.aws \
  -t wikimania-frontend .
docker tag wikimania-frontend:latest ACCOUNT_ID.dkr.ecr.REGION.amazonaws.com/wikimania-frontend:latest
docker push ACCOUNT_ID.dkr.ecr.REGION.amazonaws.com/wikimania-frontend:latest
```

---

### Step 8 — Create the frontend ECS Express Mode service

```bash
aws ecs create-express-gateway-service \
  --service-name wikimania-frontend \
  --primary-container '{
    "image": "ACCOUNT_ID.dkr.ecr.REGION.amazonaws.com/wikimania-frontend:latest",
    "containerPort": 8080
  }' \
  --execution-role-arn arn:aws:iam::ACCOUNT_ID:role/ecsTaskExecutionRole \
  --infrastructure-role-arn arn:aws:iam::ACCOUNT_ID:role/ecsInfrastructureRoleForExpressServices \
  --region REGION
```

Copy the frontend URL from `ingressPaths[0].endpoint`.

---

### Step 9 — Update backend CORS to the frontend URL

```bash
aws ecs update-express-gateway-service \
  --service-arn <BACKEND_SERVICE_ARN> \
  --primary-container '{
    "image": "ACCOUNT_ID.dkr.ecr.REGION.amazonaws.com/wikimania-backend:latest",
    "containerPort": 8080,
    "environment": [
      {"name": "DATABASE_URL",   "value": "postgresql://wikimania:<pw>@<endpoint>:5432/wikimania"},
      {"name": "PROVIDER",       "value": "cerebras"},
      {"name": "MODEL_FAST",     "value": "llama3.1-8b"},
      {"name": "MODEL_REASONING","value": "llama-3.3-70b"},
      {"name": "API_KEY",        "value": "<your-cerebras-api-key>"},
      {"name": "CORS_ORIGINS",   "value": "https://cl-FRONTEND.ecs.REGION.on.aws"}
    ]
  }' \
  --region REGION
```

Open the frontend URL in your browser — the app is live. ✓

---

### Step 10 — Set up GitLab CI for automatic deployments

In your GitLab repo → **Settings** → **CI/CD** → **Variables** → add:

| Variable | Value | Masked? |
|----------|-------|---------|
| `AWS_ACCESS_KEY_ID` | IAM user access key | No |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret | **Yes** |
| `AWS_REGION` | e.g. `us-east-1` | No |
| `AWS_ACCOUNT_ID` | 12-digit account number | No |
| `DATABASE_URL` | full postgres URL | **Yes** |
| `CEREBRAS_API_KEY` | your Cerebras key | **Yes** |
| `BACKEND_URL` | backend ECS URL from step 6 | No |
| `FRONTEND_URL` | frontend ECS URL from step 8 | No |
| `BACKEND_SERVICE_ARN` | backend service ARN | No |
| `FRONTEND_SERVICE_ARN` | frontend service ARN | No |

From now on, every push to `main` automatically:
1. Builds new Docker images → pushes to ECR.
2. Updates both ECS Express Mode services to the new image.

---

## Deploying new code

```bash
git push origin main
```

Pipeline takes ~5 minutes end to end.

---

## Useful commands

```bash
# List your ECS Express services
aws ecs list-express-gateway-services --region REGION

# Describe a service (get URL, status, etc.)
aws ecs describe-express-gateway-service --service-arn ARN --region REGION

# View backend logs
aws logs tail /ecs/wikimania-backend --follow
```

---

## Next steps (when you're ready)

- **Custom domain** — Route 53 + ACM certificate, point CNAME at ECS URL.
- **Secrets Manager** — Move `API_KEY` and `DATABASE_URL` out of env vars into AWS Secrets Manager.
- **Upgrade Supabase** — Move to a paid Supabase plan for more storage, no pause risk, and point-in-time recovery.
- **Auto-scaling** — Configure min/max tasks in ECS service settings.
