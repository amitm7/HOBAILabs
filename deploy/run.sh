#!/usr/bin/env bash
# HOBAILabs — run the app container on an EC2 box.
# Pulls secrets from SSM Parameter Store into an env file, selects the Bedrock
# LLM brain, and (re)starts the container with the cache on the persistent volume.
#
# Prereqs (one-time, see deploy/README.md):
#   - Docker installed; image present locally or pullable from ECR
#   - gp3 EBS volume mounted at /data
#   - EC2 instance IAM role with: bedrock:InvokeModel, ssm:GetParametersByPath
#     on /hobailabs/*, kms:Decrypt  (+ s3:*Object on the cache bucket if S3 cache)
#   - Bedrock model access enabled for Claude in $AWS_REGION
#
# Usage:
#   IMAGE=<acct>.dkr.ecr.us-east-1.amazonaws.com/hobailabs:latest \
#   AWS_REGION=us-east-1 ./deploy/run.sh
set -euo pipefail

IMAGE="${IMAGE:-hobailabs:latest}"
AWS_REGION="${AWS_REGION:-ap-south-1}"          # app/EC2/SSM/S3 region (Mumbai)
BEDROCK_REGION="${BEDROCK_REGION:-us-east-1}"   # Claude region (verify Bedrock access)
SSM_PREFIX="${SSM_PREFIX:-/hobailabs}"
DATA_DIR="${DATA_DIR:-/data}"
ENV_FILE="${DATA_DIR}/hobailabs.env"
PORT="${PORT:-7860}"

echo "[run] region=$AWS_REGION  image=$IMAGE  data=$DATA_DIR"

# 1. Secrets SSM → env file (paid-media keys + OpenAI for symbolic/moderation).
echo "[run] loading secrets from SSM ${SSM_PREFIX}/* …"
aws ssm get-parameters-by-path --region "$AWS_REGION" \
    --path "$SSM_PREFIX" --with-decryption \
    --query "Parameters[].[Name,Value]" --output text \
  | sed -E "s#${SSM_PREFIX}/##" | awk '{print $1"="$2}' > "$ENV_FILE"

# 2. Runtime config: Bedrock brain (no key — uses the instance role) + region.
#    Cache stays on the EBS volume (HOME=/data in the image); flip to s3 only for
#    multi-box. Append rather than duplicate if already present.
grep -q '^LLM_PROVIDER='      "$ENV_FILE" || echo "LLM_PROVIDER=bedrock"            >> "$ENV_FILE"
grep -q '^AWS_REGION='        "$ENV_FILE" || echo "AWS_REGION=${AWS_REGION}"        >> "$ENV_FILE"
grep -q '^BEDROCK_REGION='    "$ENV_FILE" || echo "BEDROCK_REGION=${BEDROCK_REGION}" >> "$ENV_FILE"
grep -q '^HOB_CACHE_BACKEND=' "$ENV_FILE" || echo "HOB_CACHE_BACKEND=fs"            >> "$ENV_FILE"

# 3. (Re)start the container. Bound to localhost — Caddy terminates TLS in front.
echo "[run] (re)starting container …"
docker rm -f hobailabs >/dev/null 2>&1 || true
docker run -d --name hobailabs --restart unless-stopped \
  -p "127.0.0.1:${PORT}:7860" \
  --env-file "$ENV_FILE" \
  -v "${DATA_DIR}/hob_cache:/data/.hob_cache" \
  -v "${DATA_DIR}/assets:/data/assets" \
  "$IMAGE"

echo "[run] up → http://127.0.0.1:${PORT}  (front with Caddy for HTTPS)"
echo "[run] logs: docker logs -f hobailabs"
