#!/bin/bash
# Production deployment script: rebuild, push to ECR, update EC2 instance

set -e

# ──────────────────────────────────────────────────────────────────────────────
# Configuration

AWS_PROFILE="new"  # Use the 'new' profile from aws configure
AWS_REGION="ap-south-1"
AWS_ACCOUNT_ID="117572456595"
ECR_REPO="hobailabs"
EC2_INSTANCE_ID="i-0813ff001cc8cc694"  # HOBAILabs instance
EC2_USER="ec2-user"
IMAGE_TAG="prod-$(date +%Y%m%d-%H%M%S)"
LATEST_TAG="prod-latest"

EC2_HOST="${EC2_HOST:-13.202.0.21}"            # public IP / DNS of the instance
SSH_KEY="${SSH_KEY:-$HOME/.ssh/hobailabs-key.pem}" # path to the .pem used to SSH in

echo "════════════════════════════════════════════════════════════════════════════════"
echo "HOBAILabs Production Deployment"
echo "════════════════════════════════════════════════════════════════════════════════"
echo ""

# ──────────────────────────────────────────────────────────────────────────────
# Step 1: Verify prerequisites

echo "[1/5] Verifying prerequisites..."
if ! command -v docker &> /dev/null; then
    echo "❌ Docker not found. Please install Docker and try again."
    exit 1
fi

if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI not found. Please install AWS CLI and try again."
    exit 1
fi

AWS_ACCT=$(aws sts get-caller-identity --profile "$AWS_PROFILE" --query Account --output text 2>/dev/null || echo "")
if [ "$AWS_ACCT" != "$AWS_ACCOUNT_ID" ]; then
    echo "⚠️  Warning: AWS account is $AWS_ACCT, expected $AWS_ACCOUNT_ID"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "✓ Prerequisites verified"
echo ""

# ──────────────────────────────────────────────────────────────────────────────
# Step 2: Rebuild Docker image

echo "[2/5] Building Docker image (hobailabs:$IMAGE_TAG)..."
cd "$(dirname "$0")/.."

if ! docker buildx build \
    --platform linux/arm64 \
    -t "$ECR_REPO:$IMAGE_TAG" \
    -t "$ECR_REPO:$LATEST_TAG" \
    -f Dockerfile \
    . ; then
    echo "❌ Docker build failed"
    exit 1
fi

echo "✓ Docker image built successfully"
echo "  Tags: $ECR_REPO:$IMAGE_TAG, $ECR_REPO:$LATEST_TAG"
echo ""

# ──────────────────────────────────────────────────────────────────────────────
# Step 3: Push to ECR

echo "[3/5] Pushing to ECR (${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com)..."

# Get ECR login token and authenticate
aws ecr get-login-password --region "$AWS_REGION" --profile "$AWS_PROFILE" | \
    docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# Tag for ECR
docker tag "$ECR_REPO:$IMAGE_TAG" "$ECR_REGISTRY/$ECR_REPO:$IMAGE_TAG"
docker tag "$ECR_REPO:$LATEST_TAG" "$ECR_REGISTRY/$ECR_REPO:$LATEST_TAG"

# Push both tags
if ! docker push "$ECR_REGISTRY/$ECR_REPO:$IMAGE_TAG"; then
    echo "❌ Push to ECR failed"
    exit 1
fi

if ! docker push "$ECR_REGISTRY/$ECR_REPO:$LATEST_TAG"; then
    echo "❌ Push of :$LATEST_TAG tag failed"
    exit 1
fi

echo "✓ Images pushed to ECR"
echo "  $ECR_REGISTRY/$ECR_REPO:$IMAGE_TAG"
echo "  $ECR_REGISTRY/$ECR_REPO:$LATEST_TAG"
echo ""

# ──────────────────────────────────────────────────────────────────────────────
# Step 4: Update EC2 instance over SSH

echo "[4/5] Updating EC2 instance ($EC2_USER@$EC2_HOST) over SSH..."

if [ ! -f "$SSH_KEY" ]; then
    echo "❌ SSH key not found at: $SSH_KEY"
    echo "   Set the right path:  SSH_KEY=~/.ssh/your-key.pem ./deploy/prod.sh"
    exit 1
fi

REMOTE_IMG="$ECR_REGISTRY/$ECR_REPO:$IMAGE_TAG"

ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$EC2_USER@$EC2_HOST" bash -s <<EOF
set -e
aws ecr get-login-password --region $AWS_REGION \
  | sudo docker login --username AWS --password-stdin $ECR_REGISTRY
sudo docker pull $REMOTE_IMG
sudo docker rm -f hob_prod || true
sudo docker run -d --name hob_prod --restart unless-stopped \
  -p 7860:7860 \
  -e AWS_REGION=$AWS_REGION -e BEDROCK_REGION=us-east-1 -e LLM_PROVIDER=bedrock \
  --env-file /etc/hobailabs.env \
  -v /srv/hob:/data \
  $REMOTE_IMG
sleep 3
sudo docker logs hob_prod 2>&1 | tail -10
EOF

if [ $? -eq 0 ]; then 
    echo "✓ EC2 container updated successfully"
else
    echo "❌ SSH deploy failed — check key path, host, and security-group port 22"
    exit 1
fi

echo ""

# ──────────────────────────────────────────────────────────────────────────────
# Step 5: Git commit and push

echo "[5/5] Committing changes to git..."

cd "$(dirname "$0")/.."

# Check if there are changes to commit
if git status --porcelain | grep -q .; then
    git add -A

    git commit -m "$(cat <<'EOF'
Promote to production: voiceover sync, multi-shot coverage, folder upload, /media security

- Voiceover: frame-exact audio padding/trimming (tts_generator._fit_seg)
- Multi-shot: B-roll coverage with duration splitting (coverage.py, expand_all)
- Folder upload: 40MB client batching, session asset persistence
- /media: path validation against allowed roots (web_app._path_allowed)
- Docs: HLD/LLD updated with new capabilities

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"

    git push origin feat/pipeline-expansion-roadmap
    echo "✓ Changes committed and pushed to feat/pipeline-expansion-roadmap"
else
    echo "ℹ️  No changes to commit"
fi

echo ""
echo "════════════════════════════════════════════════════════════════════════════════"
echo "✓ Production deployment complete!"
echo ""
echo "Image details:"
echo "  Local: $ECR_REPO:$IMAGE_TAG / $ECR_REPO:$LATEST_TAG"
echo "  ECR:   $ECR_REGISTRY/$ECR_REPO:$IMAGE_TAG"
echo "        $ECR_REGISTRY/$ECR_REPO:$LATEST_TAG"
echo ""
echo "Visit: https://creative.kevat.ai"
echo "════════════════════════════════════════════════════════════════════════════════"
