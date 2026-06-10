#!/bin/bash
# EC2 user-data — self-bootstraps the HOBAILabs box on first boot.
# Installs Docker, pulls the image from ECR (via instance role), loads secrets
# from SSM, starts the app, and fronts it with Caddy for auto-HTTPS.
# Logs to /var/log/hob-userdata.log  (also visible in /var/log/cloud-init-output.log)
set -xe
exec > /var/log/hob-userdata.log 2>&1

REGION=ap-south-1
ACCT=117572456595
ECR=$ACCT.dkr.ecr.$REGION.amazonaws.com
IMAGE=$ECR/hobailabs:latest
DOMAIN=creative.kevat.ai

dnf install -y docker
systemctl enable --now docker

mkdir -p /data/hob_cache /data/assets

# Pull the app image (instance role has ECR read)
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR
docker pull $IMAGE

# Secrets: SSM SecureStrings -> env file (instance role has ssm:Get* + kms:Decrypt)
# NOTE: GetParametersByPath needs the IAM role to allow BOTH the path node
# (parameter/hobailabs) and its children (parameter/hobailabs/*). awk splits on
# TAB only so secret values containing spaces stay intact.
aws ssm get-parameters-by-path --region $REGION --path /hobailabs --with-decryption \
  --query "Parameters[].[Name,Value]" --output text \
  | sed -E 's#^/hobailabs/##' | awk -F'\t' '{print $1"="$2}' > /data/hobailabs.env
# Start on OpenAI; flip to bedrock once the Anthropic use-case form clears:
#   sudo sed -i 's/^LLM_PROVIDER=.*/LLM_PROVIDER=bedrock/' /data/hobailabs.env \
#     && docker restart hobailabs
cat >> /data/hobailabs.env <<EOF
LLM_PROVIDER=openai
AWS_REGION=ap-south-1
BEDROCK_REGION=us-east-1
HOB_CACHE_BACKEND=fs
EOF

# App container (bound to localhost; Caddy terminates TLS in front)
docker rm -f hobailabs 2>/dev/null || true
docker run -d --name hobailabs --restart unless-stopped \
  -p 127.0.0.1:7860:7860 --env-file /data/hobailabs.env \
  -v /data/hob_cache:/data/.hob_cache -v /data/assets:/data/assets \
  $IMAGE

# Static team guide (ships inside the image) → host dir Caddy can serve
mkdir -p /data/guide
docker cp hobailabs:/app/docs/OPERATOR_GUIDE.html /data/guide/OPERATOR_GUIDE.html 2>/dev/null || true

# Caddy auto-HTTPS (retries cert until the DNS A record points here)
cat > /data/Caddyfile <<EOF
$DOMAIN {
    @guide path /guide /guide/
    handle @guide {
        root * /srv/guide
        rewrite * /OPERATOR_GUIDE.html
        file_server
    }
    handle {
        reverse_proxy localhost:7860
    }
}
EOF
docker rm -f caddy 2>/dev/null || true
docker run -d --name caddy --restart unless-stopped --network host \
  -v /data/Caddyfile:/etc/caddy/Caddyfile \
  -v caddy_data:/data \
  -v /data/guide:/srv/guide:ro \
  caddy:2

echo "HOBAILabs bootstrap complete"
