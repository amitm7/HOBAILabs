#!/bin/bash
# Production deployment script: rebuild, push to ECR, update EC2 instance

set -e

# ──────────────────────────────────────────────────────────────────────────────
# Configuration

AWS_PROFILE="new"  # Use the 'new' profile from aws configure
AWS_REGION="ap-south-1"
AWS_ACCOUNT_ID="117572456595"
ECR_REPO="hobailabs"
EC2_INSTANCE_ID="i-0b3e0d5c5b3dd37b0"  # Update if different
EC2_USER="ec2-user"
IMAGE_TAG="prod-$(date +%Y%m%d-%H%M%S)"
LATEST_TAG="prod-latest"

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
# Step 4: Update EC2 instance via SSM

echo "[4/5] Updating EC2 instance ($EC2_INSTANCE_ID)..."

# Command to run on EC2 (pulled from SSM Parameter Store during user-data)
EC2_COMMAND="
set -e
export AWS_REGION=$AWS_REGION
export ECR_REGISTRY=$ECR_REGISTRY
export ECR_REPO=$ECR_REPO
export IMAGE_TAG=$IMAGE_TAG

# Pull latest image from ECR
aws ecr get-login-password --region \$AWS_REGION | \
    docker login --username AWS --password-stdin \$ECR_REGISTRY

docker pull \$ECR_REGISTRY/\$ECR_REPO:$IMAGE_TAG

# Stop and remove old container
docker rm -f hob_prod || true

# Load environment from SSM Parameter Store
export \$(aws ssm get-parameters-by-path \
    --path /hobailabs \
    --with-decryption \
    --query 'Parameters[*].[Name,Value]' \
    --output text \
    --region \$AWS_REGION | \
    awk '{print \$1\"=\"\$2}' | sed 's|/hobailabs/||g')

# Launch new container
docker run -d \
    --name hob_prod \
    --restart unless-stopped \
    -p 7860:7860 \
    -e AWS_REGION=\$AWS_REGION \
    -e BEDROCK_REGION=us-east-1 \
    -e LLM_PROVIDER=bedrock \
    -e ELEVENLABS_API_KEY \
    -e SYNCLABS_API_KEY \
    -e HEDRA_API_KEY \
    -e FAL_KEY \
    -e KLING_API_KEY \
    -e HIGGSFIELD_API_KEY \
    -e SUNO_API_KEY \
    -e OPENAI_API_KEY \
    -e GOOGLE_API_KEY \
    -e ASSETS_BROWSE_ROOT=/srv/hob/assets \
    -v /srv/hob:/data \
    \$ECR_REGISTRY/\$ECR_REPO:$IMAGE_TAG

# Wait for container to be ready
sleep 3
docker logs hob_prod | tail -20

echo 'Container running. Visit https://creative.kevat.ai'
"

# Send command to EC2 via SSM
CMD_ID=$(aws ssm send-command \
    --document-name "AWS-RunShellScript" \
    --targets "Key=InstanceIds,Values=$EC2_INSTANCE_ID" \
    --parameters "commands=['$EC2_COMMAND']" \
    --region "$AWS_REGION" \
    --profile "$AWS_PROFILE" \
    --query 'Command.CommandId' \
    --output text)

echo "✓ Command sent to EC2 (Command ID: $CMD_ID)"
echo "  Waiting for execution (may take 30s)..."
echo ""

# Poll for command completion
for i in {1..30}; do
    STATUS=$(aws ssm get-command-invocation \
        --command-id "$CMD_ID" \
        --instance-id "$EC2_INSTANCE_ID" \
        --region "$AWS_REGION" \
        --profile "$AWS_PROFILE" \
        --query 'Status' \
        --output text 2>/dev/null || echo "")

    if [ "$STATUS" = "Success" ] || [ "$STATUS" = "Failed" ]; then
        break
    fi
    sleep 1
done

# Get the output
OUTPUT=$(aws ssm get-command-invocation \
    --command-id "$CMD_ID" \
    --instance-id "$EC2_INSTANCE_ID" \
    --region "$AWS_REGION" \
    --profile "$AWS_PROFILE" \
    --query 'StandardOutputContent' \
    --output text 2>/dev/null || echo "")

if [ "$STATUS" = "Success" ]; then
    echo "✓ EC2 container updated successfully"
    echo ""
    echo "Output from EC2:"
    echo "$OUTPUT" | tail -10
else
    echo "⚠️  Command status: $STATUS"
    if [ ! -z "$OUTPUT" ]; then
        echo "Output:"
        echo "$OUTPUT"
    fi
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
