# HOBAILabs — AWS Deploy Runbook (MVP, single EC2)

This is the minimum viable production deploy: **one EC2 instance**, the app in a
Docker container, Caddy for HTTPS, caches on a persistent EBS volume, secrets in
SSM, and an IAM role so the Bedrock LLM backend needs **no API key**.

> Why a single instance: render state lives in-process (`_runs`) and work runs in
> background threads polled by `/progress`. Multiple instances behind a load
> balancer would break progress polling. Scale-out (SQS + worker + shared state)
> is a later step.

---

## 1. Build & push the image
On your Mac (or in CI):
```bash
# Build
docker build -t hobailabs:latest .

# (Option A) push to Amazon ECR
aws ecr create-repository --repository-name hobailabs || true
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <acct>.dkr.ecr.us-east-1.amazonaws.com
docker tag hobailabs:latest <acct>.dkr.ecr.us-east-1.amazonaws.com/hobailabs:latest
docker push <acct>.dkr.ecr.us-east-1.amazonaws.com/hobailabs:latest
```
(Option B: skip ECR and just `git clone` + `docker build` on the EC2 box.)

## 2. Secrets → SSM Parameter Store (SecureString)
Store every API key the app still pays cash for (Bedrock needs none):
```bash
for k in OPENAI_API_KEY KLING_ACCESS_KEY KLING_SECRET_KEY \
         HIGGSFIELD_KEY_ID HIGGSFIELD_KEY_SECRET FAL_API_KEY ELEVENLABS_API_KEY; do
  aws ssm put-parameter --name "/hobailabs/$k" --type SecureString --value "<value>" --overwrite
done
```

## 3. IAM instance role
Attach a role to the EC2 instance with:
- `bedrock:InvokeModel` (for the Claude LLM backend)
- `ssm:GetParametersByPath` on `/hobailabs/*` + `kms:Decrypt`
- *(optional)* `s3:PutObject`/`s3:GetObject` on your outputs bucket
Also enable the Claude models in **Bedrock → Model access** in your region.

## 4. Provision EC2
- `t3.large` (CPU render + FFmpeg), Amazon Linux 2023, the IAM role above.
- Security group: 80 + 443 inbound (and 22 from your IP).
- Attach a **gp3 EBS volume**, mount at `/data` (persists caches across restarts):
```bash
sudo mkfs -t xfs /dev/nvme1n1            # first time only
sudo mkdir -p /data && sudo mount /dev/nvme1n1 /data
echo '/dev/nvme1n1 /data xfs defaults,nofail 0 2' | sudo tee -a /etc/fstab
sudo dnf install -y docker && sudo systemctl enable --now docker
```

## 5. Load secrets into an env file at boot
```bash
mkdir -p /data
aws ssm get-parameters-by-path --path /hobailabs --with-decryption \
  --query "Parameters[].[Name,Value]" --output text \
  | sed -E 's#/hobailabs/##' | awk '{print $1"="$2}' > /data/hobailabs.env
# Select the LLM brain (default openai; flip to bedrock to use AWS credits):
echo "LLM_PROVIDER=bedrock"  >> /data/hobailabs.env
echo "AWS_REGION=us-east-1"  >> /data/hobailabs.env
```

## 6. Run the app container
```bash
docker run -d --name hobailabs --restart unless-stopped \
  -p 127.0.0.1:7860:7860 \
  --env-file /data/hobailabs.env \
  -v /data/hob_cache:/data/.hob_cache \
  <image>            # hobailabs:latest or the ECR URI
```
- `HOME=/data` (set in the Dockerfile) → `~/.hob_cache` resolves to the mounted
  volume, so clip/scene/description caches survive redeploys (no re-spend).
- Bedrock uses the instance role automatically — no key needed.

## 7. HTTPS with Caddy
```bash
# point DNS A record at the instance's Elastic IP, set the domain in deploy/Caddyfile
docker run -d --name caddy --restart unless-stopped --network host \
  -v $PWD/deploy/Caddyfile:/etc/caddy/Caddyfile \
  -v caddy_data:/data caddy:2
```

## 8. Assets (single-user MVP)
The UI takes a **server** filesystem path for the assets folder. `rsync` your
photo folders to the box and type that path in the UI:
```bash
rsync -avz ~/Downloads/my_story_assets/ ec2-user@<eip>:/data/assets/my_story/
# then in the UI, Assets folder = /data/assets/my_story
```
(Multi-user folder/zip upload → S3 is the later enhancement, reusing `/upload-photo`.)

---

## Smoke test
1. Open `https://<domain>` → the UI loads, `/models` returns JSON.
2. Run a **dev / Ken Burns** render (no paid models) end-to-end → downloadable mp4.
3. With `LLM_PROVIDER=bedrock`, parse with **Smart-match** on → matcher works with
   **no `OPENAI_API_KEY`** present (proves Bedrock + IAM role).
4. Restart the container → confirm `~/.hob_cache` (on `/data`) still has cached clips.

## Switching the brain
- `LLM_PROVIDER=openai` (default) — cash, no AWS needed.
- `LLM_PROVIDER=bedrock` — Claude via Bedrock, **credit-funded**, no key.
- `LLM_PROVIDER=gemini` — needs `GEMINI_API_KEY`.
Per-tier model overrides: `LLM_REASONING_MODEL`, `LLM_VISION_MODEL`. Catalog: `config/llm.json`.
