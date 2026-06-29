# HOBAILabs Production Deployment

## Quick Start

```bash
cd /Users/amitmishra/Documents/GitHub/HOBAILabs
./deploy/prod.sh
```

This script will:
1. Verify Docker, AWS CLI, and AWS credentials
2. Build `hobailabs:prod-YYYYMMDD-HHMMSS` locally (arm64)
3. Push to ECR (`ap-south-1`)
4. Sync the EC2 security group so port 22 allows your current public IP (prunes stale `/32`s), then update the instance over SSH (pulls new image, restarts container)
5. Commit and push to git branch

Override `EC2_SG_ID` or set `SKIP_SG_SYNC=1` if SSH ingress is managed elsewhere.

## Manual Deployment (if script fails)

### 1. Build and tag locally
```bash
docker buildx build --platform linux/arm64 \
  -t hobailabs:prod-latest \
  -f Dockerfile .
```

### 2. Login to ECR and push
```bash
export AWS_REGION=ap-south-1
export ECR_REPO=117572456595.dkr.ecr.ap-south-1.amazonaws.com/hobailabs

aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ECR_REPO

docker tag hobailabs:prod-latest $ECR_REPO:prod-latest
docker push $ECR_REPO:prod-latest
```

### 3. Update EC2 manually (SSH or SSM)

Via SSM:
```bash
aws ssm send-command \
  --document-name AWS-RunShellScript \
  --targets "Key=InstanceIds,Values=i-0b3e0d5c5b3dd37b0" \
  --parameters 'commands=[
    "docker pull 117572456595.dkr.ecr.ap-south-1.amazonaws.com/hobailabs:prod-latest",
    "docker rm -f hob_prod || true",
    "docker run -d --name hob_prod --restart unless-stopped -p 7860:7860 \
      -v /srv/hob:/data \
      117572456595.dkr.ecr.ap-south-1.amazonaws.com/hobailabs:prod-latest"
  ]' \
  --region ap-south-1
```

## Verify Deployment

After deployment, visit **https://creative.kevat.ai**:
- Browse folder upload should work
- Multi-shot coverage checkbox visible
- Voiceover track plays once (no loop)

## Rollback

If deployment fails, the previous container still exists (unless you remove it):
```bash
docker pull 117572456595.dkr.ecr.ap-south-1.amazonaws.com/hobailabs:prod-previous
docker rm -f hob_prod
docker run -d --name hob_prod --restart unless-stopped -p 7860:7860 \
  -v /srv/hob:/data \
  117572456595.dkr.ecr.ap-south-1.amazonaws.com/hobailabs:prod-previous
```

## Logs

Local container logs:
```bash
docker logs hob_prod -f
```

EC2 logs via SSH:
```bash
ssh -i ~/.ssh/kevat-ec2.pem ec2-user@creative.kevat.ai
docker logs hob_prod -f
```

## Environment Variables (loaded from SSM Parameter Store)

Located at `/hobailabs/*` in SSM Parameter Store (SecureString):
- `ELEVENLABS_API_KEY`
- `SYNCLABS_API_KEY`
- `HEDRA_API_KEY`
- `FAL_KEY`
- `KLING_API_KEY`
- `HIGGSFIELD_API_KEY`
- `SUNO_API_KEY`
- `OPENAI_API_KEY`
- `GOOGLE_API_KEY`
- `HOB_AUTH_SECRET` — **required**: 32+ random hex that signs operator JWTs. Without it,
  tokens reset on every restart and differ per gunicorn worker (random logouts). Generate:
  `python -c "import secrets;print(secrets.token_hex(32))"`.

See `deploy/userdata.sh` for how they are loaded on startup. Non-secret persistence config
(`HOB_RUNS_DB`, `HOB_GOVERNANCE_DB`, `HOB_DB_DIR`, `HOB_RUNS_DIR`, `HOB_COOKIE_SECURE`) is
appended by `userdata.sh`/`run.sh` so SQLite state lands on the `/data/.hob_cache` EBS volume
and survives redeploys — do not also set these in SSM.

## Operator auth (required since the P0 auth change)

The app now gates money/rights routes behind operator login. After the first deploy, seed
operators once (the DB is on the persistent volume, so this survives restarts):

```bash
docker exec hobailabs python -m agents.auth add-operator amit amit@hob.tv --role admin
docker exec hobailabs python -m agents.auth add-operator <editor> <email> --role operator
```

`--password` is optional (a strong one is generated and printed). Do **not** set
`HOB_AUTH_DISABLED` in production. For local dev only, `HOB_AUTH_DISABLED=1` bypasses auth.
