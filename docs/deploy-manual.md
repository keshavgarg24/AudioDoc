# Manual deployment to AWS

Every step is run by hand from a laptop. Nothing in this document is triggered
by a git push, a webhook or a CI job — there is no pipeline, by design, so a
testing account can be brought up and torn down without touching the
production repository.

Budget about **90 minutes** the first time, most of it waiting for image
pushes and the MongoDB cluster.

- [0. What you need first](#0-what-you-need-first)
- [0.5 Create the VPC and network prerequisites](#05-create-the-vpc-and-network-prerequisites)
- [1. Account and region](#1-account-and-region)
- [2. MongoDB](#2-mongodb)
- [3. Secrets](#3-secrets)
- [4. Mirror the checkpoints to S3](#4-mirror-the-checkpoints-to-s3)
- [5. Build and push the images](#5-build-and-push-the-images)
- [6. Terraform](#6-terraform)
- [7. Create an API key](#7-create-an-api-key)
- [8. Smoke test](#8-smoke-test)
- [9. Watching it work](#9-watching-it-work)
- [10. Updating a deployment](#10-updating-a-deployment)
- [11. Tearing it down](#11-tearing-it-down)
- [Troubleshooting](#troubleshooting)

---

## 0. What you need first

On the laptop:

```bash
aws --version          # v2.x
docker --version       # must be running, with buildx
terraform version      # >= 1.5
```

Terraform is not in homebrew-core any more — it moved to HashiCorp's own tap
when the licence changed:

```bash
brew install hashicorp/tap/terraform
```

The Terraform deliberately does not create the VPC, subnets, NAT or
certificate — those belong to the account, not to one service. If your testing
account already has a VPC with private subnets and a NAT gateway, skip to
[section 1](#1-account-and-region) and just collect the IDs. If starting from a
blank account, section 0.5 builds everything from scratch in about ten minutes.

> **Check the NAT before anything else.** A private subnet with no route out is
> the single most common reason a first deploy hangs with tasks stuck in
> `PENDING` and no useful error anywhere. ECR, SQS, Secrets Manager, and the
> S3 weights bucket are all reached from the **private** subnets where the
> containers run.

---

## 0.5 Create the VPC and network prerequisites

Skip this section if the account already has a suitable VPC.

Everything here uses AWS CLI. Each command prints an ID — keep them, you will
paste them into `testing.tfvars` later.

### Set the region before you start

```bash
export AWS_REGION=us-east-1        # change if you want a different region
export AWS_PROFILE=labs-testing    # your named profile, or omit to use the default
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Building network in account $ACCOUNT_ID / $AWS_REGION"
```

### Create the VPC

```bash
VPC_ID=$(aws ec2 create-vpc \
  --cidr-block 10.0.0.0/16 \
  --tag-specifications "ResourceType=vpc,Tags=[{Key=Name,Value=labs-vpc}]" \
  --query Vpc.VpcId --output text)
echo "VPC_ID=$VPC_ID"

# Enable DNS so containers resolve endpoint hostnames.
aws ec2 modify-vpc-attribute --vpc-id $VPC_ID --enable-dns-support
aws ec2 modify-vpc-attribute --vpc-id $VPC_ID --enable-dns-hostnames
```

### Create two public subnets (one per AZ)

These host the ALB and the NAT gateway. The ALB requires at least two AZs.

```bash
PUB_A=$(aws ec2 create-subnet \
  --vpc-id $VPC_ID --cidr-block 10.0.0.0/24 \
  --availability-zone ${AWS_REGION}a \
  --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=labs-public-a}]" \
  --query Subnet.SubnetId --output text)

PUB_B=$(aws ec2 create-subnet \
  --vpc-id $VPC_ID --cidr-block 10.0.1.0/24 \
  --availability-zone ${AWS_REGION}b \
  --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=labs-public-b}]" \
  --query Subnet.SubnetId --output text)

echo "PUB_A=$PUB_A  PUB_B=$PUB_B"
```

### Create two private subnets (one per AZ)

These host every ECS task (API, screen, worker). They never have a direct
route to the internet — outbound traffic goes through the NAT gateway.

```bash
PRIV_A=$(aws ec2 create-subnet \
  --vpc-id $VPC_ID --cidr-block 10.0.10.0/24 \
  --availability-zone ${AWS_REGION}a \
  --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=labs-private-a}]" \
  --query Subnet.SubnetId --output text)

PRIV_B=$(aws ec2 create-subnet \
  --vpc-id $VPC_ID --cidr-block 10.0.11.0/24 \
  --availability-zone ${AWS_REGION}b \
  --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=labs-private-b}]" \
  --query Subnet.SubnetId --output text)

echo "PRIV_A=$PRIV_A  PRIV_B=$PRIV_B"
```

### Internet gateway (for the public subnets and the NAT gateway)

```bash
IGW=$(aws ec2 create-internet-gateway \
  --tag-specifications "ResourceType=internet-gateway,Tags=[{Key=Name,Value=labs-igw}]" \
  --query InternetGateway.InternetGatewayId --output text)

aws ec2 attach-internet-gateway --vpc-id $VPC_ID --internet-gateway-id $IGW
echo "IGW=$IGW"
```

### Route the public subnets through the internet gateway

```bash
PUB_RT=$(aws ec2 create-route-table \
  --vpc-id $VPC_ID \
  --tag-specifications "ResourceType=route-table,Tags=[{Key=Name,Value=labs-public-rt}]" \
  --query RouteTable.RouteTableId --output text)

aws ec2 create-route --route-table-id $PUB_RT \
  --destination-cidr-block 0.0.0.0/0 --gateway-id $IGW

aws ec2 associate-route-table --route-table-id $PUB_RT --subnet-id $PUB_A
aws ec2 associate-route-table --route-table-id $PUB_RT --subnet-id $PUB_B
```

### Allocate an Elastic IP and create the NAT gateway

The NAT gateway lives in one of the **public** subnets and gives the
**private** subnets a route out. One NAT is sufficient for testing; for
production use one per AZ so an AZ outage does not take down routing.

```bash
EIP=$(aws ec2 allocate-address --domain vpc \
  --query AllocationId --output text)
echo "EIP=$EIP"

NAT=$(aws ec2 create-nat-gateway \
  --subnet-id $PUB_A \
  --allocation-id $EIP \
  --tag-specifications "ResourceType=natgateway,Tags=[{Key=Name,Value=labs-nat}]" \
  --query NatGateway.NatGatewayId --output text)
echo "NAT=$NAT  (takes ~2 minutes to become available)"

# Wait for the NAT gateway to be ready before creating the route.
aws ec2 wait nat-gateway-available --nat-gateway-ids $NAT
echo "NAT gateway is up."
```

### Route the private subnets through the NAT gateway

```bash
PRIV_RT=$(aws ec2 create-route-table \
  --vpc-id $VPC_ID \
  --tag-specifications "ResourceType=route-table,Tags=[{Key=Name,Value=labs-private-rt}]" \
  --query RouteTable.RouteTableId --output text)

aws ec2 create-route --route-table-id $PRIV_RT \
  --destination-cidr-block 0.0.0.0/0 --nat-gateway-id $NAT

aws ec2 associate-route-table --route-table-id $PRIV_RT --subnet-id $PRIV_A
aws ec2 associate-route-table --route-table-id $PRIV_RT --subnet-id $PRIV_B
```

### Verify the NAT route before moving on

This is the single most common deployment blocker. Confirm the private subnets
have a route table entry pointing at the NAT:

```bash
aws ec2 describe-route-tables \
  --filters "Name=route-table-id,Values=$PRIV_RT" \
  --query 'RouteTables[0].Routes[*].{dest:DestinationCidrBlock,via:NatGatewayId}' \
  --output table
```

You should see `0.0.0.0/0` pointing at the `nat-...` ID. If that row is
missing, ECS tasks in the private subnets cannot reach ECR, S3, SQS or
Secrets Manager — they will start and immediately hang.

### Record the IDs you will need for testing.tfvars

```bash
echo "vpc_id              = \"$VPC_ID\""
echo "private_subnet_ids  = [\"$PRIV_A\", \"$PRIV_B\"]"
echo "public_subnet_ids   = [\"$PUB_A\", \"$PUB_B\"]"
```

Copy these three lines exactly as printed into `testing.tfvars` at step 6.

### ACM certificate (optional — HTTP only if skipped)

A certificate is needed for HTTPS on the ALB. If you do not have a domain,
leave `certificate_arn = ""` in `testing.tfvars` and the ALB will serve HTTP
only. **Never expose HTTP to the public internet in production.**

If you have a domain, issue a certificate in the same region as the
deployment. DNS validation is faster than email validation:

```bash
CERT_ARN=$(aws acm request-certificate \
  --domain-name api.yourdomain.com \
  --validation-method DNS \
  --query CertificateArn --output text)
echo "CERT_ARN=$CERT_ARN"

# Print the CNAME record ACM needs you to create in your DNS.
aws acm describe-certificate --certificate-arn $CERT_ARN \
  --query 'Certificate.DomainValidationOptions[0].ResourceRecord'
```

Create that CNAME in your DNS provider (Cloudflare, Route 53, etc.), then wait
for it to validate — typically 2–5 minutes with DNS propagated:

```bash
aws acm wait certificate-validated --certificate-arn $CERT_ARN
echo "Certificate validated. ARN: $CERT_ARN"
```

Put the ARN in `testing.tfvars`:
```hcl
certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/..."
```

With a certificate, the ALB listens on 443 and redirects 80 → 443
automatically. Without one, it listens on 80 only.

---

## 1. Account and region

```bash
export AWS_PROFILE=labs-testing
export AWS_REGION=us-east-1
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Deploying into $ACCOUNT_ID / $AWS_REGION"
```

Confirm that account id is the **testing** account before continuing. Every
remaining step writes into whatever this prints.

---

## 2. MongoDB

MongoDB carries job state and every stored report. Without it the service runs
in memory, deduplication stops, and a job accepted by one container is
invisible to every other one.

Atlas M10 (~$60/month) is the smallest size worth using in a deployment with
more than one container.

1. Create a cluster in **the same region** as the deployment.
2. Network access: allow the VPC's NAT gateway public IP, or set up VPC
   peering. Do not use `0.0.0.0/0`.
3. Create a database user with `readWrite` on the `labs` database.
4. Copy the SRV connection string:
   `mongodb+srv://USER:PASS@cluster.xxxxx.mongodb.net/labs`

Keep it on the clipboard for the next step and **do not** paste it into a
`.tfvars` file.

---

## 3. Secrets

Nothing secret is ever passed to Terraform as a value. A Terraform variable
holding a password is written to state in plaintext, and state is readable by
anyone who can run `plan`. Terraform only ever sees **ARNs**; the ECS agent
resolves them at container start.

```bash
# Required.
MONGO_ARN=$(aws secretsmanager create-secret \
  --name labs/mongo-uri \
  --secret-string 'mongodb+srv://USER:PASS@cluster.xxxxx.mongodb.net/labs' \
  --query ARN --output text)

# Signs webhook callbacks so a receiver can verify they came from you.
WEBHOOK_ARN=$(aws secretsmanager create-secret \
  --name labs/webhook-secret \
  --secret-string "$(openssl rand -hex 32)" \
  --query ARN --output text)

echo "MONGO_ARN=$MONGO_ARN"
echo "WEBHOOK_ARN=$WEBHOOK_ARN"
```

Optional — ACRCloud catalogue verification. Skip both if you are not using it;
the service runs without them.

The app uses ACRCloud's **File Scanning API** (Bearer auth), not the
Fingerprinting API (HMAC). Get the Bearer token and container ID from your
ACRCloud dashboard → File Scanning → the container you created.

```bash
ACR_TOKEN_ARN=$(aws secretsmanager create-secret \
  --name labs/acr-bearer-token --secret-string 'eyJ...' --query ARN --output text)
ACR_CID_ARN=$(aws secretsmanager create-secret \
  --name labs/acr-container-id --secret-string 'your-container-id' --query ARN --output text)
echo "ACR_TOKEN_ARN=$ACR_TOKEN_ARN"
echo "ACR_CID_ARN=$ACR_CID_ARN"
```

---

## 4. Mirror the checkpoints to S3

`LABS_OFFLINE=true` in production, so a worker **will not** download 1.3 GB
from a third-party mirror during a deploy. It fails loudly instead. Mirroring
is also what makes a deploy reproducible: an upstream can change or vanish,
your bucket cannot.

```bash
export WEIGHTS_BUCKET=labs-weights-$ACCOUNT_ID

aws s3 mb s3://$WEIGHTS_BUCKET
aws s3api put-public-access-block --bucket $WEIGHTS_BUCKET \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

# ~1.3 GB. Takes a few minutes.
aws s3 cp checkpoints/Stage-1.ckpt s3://$WEIGHTS_BUCKET/models/checkpoints/Stage-1.ckpt
aws s3 cp checkpoints/Stage-2.ckpt s3://$WEIGHTS_BUCKET/models/checkpoints/Stage-2.ckpt

export WEIGHTS_URI=s3://$WEIGHTS_BUCKET/models
```

### Record the digests

A revision pins *what you asked for*. A digest pins *what you got* — and they
catch different failures. A revision cannot detect a truncated upload, a stale
file on a reused volume, or a bucket somebody else can write to. The failure
being defended against is not a crash; it is **a different model answering
confidently in your name**.

```bash
shasum -a 256 checkpoints/Stage-1.ckpt checkpoints/Stage-2.ckpt
```

Keep both hex strings. They become `LABS_STAGE1_SHA256` and
`LABS_STAGE2_SHA256`, and a mismatch then refuses to start rather than serving
verdicts from an unknown model.

---

## 5. Build and push the images

Three targets are built from one Dockerfile. They are genuinely different
images, not tags of the same one:

| Target | Size | Contains | Used by |
|---|---|---|---|
| `screen` | ~350 MB | numpy, scipy, librosa, ONNX Runtime | screen fleet **and** api fleet |
| `worker` | ~2.5 GB | the above plus torch, transformers, lightning | worker fleet |
| `api` | ~2.5 GB | full stack, single-container mode | not used in this topology |

The api fleet deliberately runs the **screen** image. It validates, stores and
enqueues; it never analyses, so shipping torch to it would be 2.1 GB of cold
start for code that never executes.

```bash
aws ecr create-repository --repository-name labs-screen || true
aws ecr create-repository --repository-name labs-worker || true

ECR=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
aws ecr get-login-password | docker login --username AWS --password-stdin $ECR
```

**Build for ARM64.** The Terraform specifies Graviton instances and ARM
Fargate; an x86 image on them fails with a cryptic `exec format error`.

```bash
docker buildx build --platform linux/arm64 --target screen \
  -t $ECR/labs-screen:v1 --push .

docker buildx build --platform linux/arm64 --target worker \
  -t $ECR/labs-worker:v1 --push .
```

Verify both landed:

```bash
aws ecr describe-images --repository-name labs-screen \
  --query 'imageDetails[].imageTags' --output text
```

> Use a real tag like `v1`, not `latest`. `latest` makes "which image is
> actually running" unanswerable during an incident, and makes a rollback a
> guess.

---

## 6. Terraform

```bash
cd deploy/aws
terraform init
terraform validate      # must print: Success! The configuration is valid.
```

Write `testing.tfvars` — **gitignored**, because this is where account ids and
subnet ids accumulate:

```hcl
name       = "labs-test"
region     = "us-east-1"
vpc_id     = "vpc-0abc123"

private_subnet_ids = ["subnet-0aaa", "subnet-0bbb"]
public_subnet_ids  = ["subnet-0ccc", "subnet-0ddd"]

image_tag      = "v1"
weights_s3_uri = "s3://labs-weights-123456789012/models"

mongo_uri_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:labs/mongo-uri-AbCdEf"
webhook_secret_arn   = "arn:aws:secretsmanager:us-east-1:123456789012:secret:labs/webhook-secret-GhIjKl"

# ACRCloud File Scanning — omit both to run without catalogue verification.
# acr_bearer_token_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:labs/acr-bearer-token-XxXxXx"
# acr_container_id_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:labs/acr-container-id-YyYyYy"

# Testing: keep it small and cheap.
worker_instance_type = "c7g.xlarge"
worker_max_size      = 4
warm_pool_size       = 0     # save ~$10/mo; accept a 3-5 min cold start
burst_to_fargate     = false

# Retention: 0 everywhere means keep forever, which is the default.
audio_ttl_days        = 0
result_ttl_days       = 0
screen_retain_seconds = 0

# No ACM certificate in testing, so HTTP only. Never do this with a
# public-facing listener.
certificate_arn = ""
```

Read the plan before applying. This is the step where a wrong subnet or a
wrong account shows up:

```bash
terraform plan -var-file=testing.tfvars -out=tfplan
terraform show tfplan | head -60
terraform apply tfplan
```

Roughly 8–12 minutes. The ALB and the ASG are the slow parts.

```bash
export ALB=$(terraform output -raw alb_dns_name)
echo "http://$ALB"
```

---

## 7. Create an API key

`LABS_REQUIRE_AUTH=true` in production, so every call needs a key. Keys live in
MongoDB, which means creating one requires the database rather than a redeploy
— and it also means a key can be revoked without one.

```bash
docker run --rm \
  -e LABS_MONGO_URI='mongodb+srv://USER:PASS@cluster.xxxxx.mongodb.net/labs' \
  $ECR/labs-worker:v1 \
  python -m labs.cli.manage_keys create \
    --name "testing" --scopes screen,analyze,deep,read
```

It prints the key **once**. It is stored only as a hash, so there is no way to
recover it later — losing it means issuing a new one.

Scopes, and what each actually buys:

| Scope | Grants | Cost shape |
|---|---|---|
| `screen` | `POST /v1/screen` | free, ~1.5 s, anonymous holds this by default |
| `analyze` | `POST /v1/analyses` with `mode=audio` | DSP only, no backbone |
| `deep` | `mode=ai` / `mode=full`, and appeals | loads the 1.29 GB backbone |
| `read` | polling results | — |

`analyze` deliberately does **not** imply `deep`. `mode=audio` runs the DSP and
musicological passes and never touches Stage-1 or Stage-2, so gating it behind
`deep` would charge for a tier it does not use.

---

## 8. Smoke test

Work up from the cheapest thing that can fail.

**Liveness** — no auth, answers before any model is loaded:

```bash
curl -s http://$ALB/health
```

**Readiness** — reports whether the backbone actually loaded:

```bash
curl -s http://$ALB/v1/ready | python3 -m json.tool
```

**Level 1**, synchronous, answers in the response:

```bash
curl -s -X POST http://$ALB/v1/screen \
  -H "X-API-Key: $LABS_KEY" \
  -F file=@audio/ai1.mp3 | python3 -m json.tool
```

Expect `verdict`, `next_step`, and `elapsed_s` around 1–3 s.

**Level 2**, asynchronous — returns a job id immediately:

```bash
JOB=$(curl -s -X POST http://$ALB/v1/analyses \
  -H "X-API-Key: $LABS_KEY" \
  -F file=@audio/1.mp3 -F mode=full | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# Poll. 40-90 s on a warm worker; several minutes on a cold one.
watch -n 5 "curl -s http://$ALB/v1/analyses/$JOB \
  -H 'X-API-Key: $LABS_KEY' | python3 -c \
  'import sys,json;d=json.load(sys.stdin);print(d[\"status\"], d.get(\"progress\"))'"
```

**Deduplication** — submit the same file twice. The second returns the stored
result rather than re-running 60 s of CPU, matched on SHA-256 of the bytes:

```bash
curl -s -X POST http://$ALB/v1/analyses -H "X-API-Key: $LABS_KEY" \
  -F file=@audio/1.mp3 -F mode=full | python3 -m json.tool
```

**A tool**:

```bash
curl -s -X POST http://$ALB/v1/tools/tempo-lab \
  -H "X-API-Key: $LABS_KEY" -F file=@audio/1.mp3 | python3 -m json.tool
```

---

## 9. Watching it work

**Is anything queued, and is anyone consuming it?**

```bash
aws sqs get-queue-attributes \
  --queue-url $(terraform output -raw queue_url) \
  --attribute-names ApproximateNumberOfMessages \
                    ApproximateNumberOfMessagesNotVisible
```

`Messages` is the backlog. `NotVisible` is what workers are holding right now.
A large backlog with `NotVisible = 0` means **nothing is consuming** — check
the worker service before anything else.

**The dead-letter queue should be empty.** Anything in it failed three times,
which means it failed deterministically:

```bash
aws sqs get-queue-attributes \
  --queue-url $(terraform output -raw dlq_url) \
  --attribute-names ApproximateNumberOfMessages
```

**Logs:**

```bash
aws logs tail /ecs/labs-test --follow --since 10m
aws logs tail /ecs/labs-test --follow --filter-pattern "ERROR"
```

**Did the autoscaling fire?**

```bash
aws autoscaling describe-scaling-activities \
  --auto-scaling-group-name labs-test-worker --max-items 10
```

---

## 10. Updating a deployment

Build a new tag, then roll it. Never overwrite a tag that is running — that
makes a rollback a guess.

```bash
docker buildx build --platform linux/arm64 --target worker \
  -t $ECR/labs-worker:v2 --push .

terraform apply -var-file=testing.tfvars -var image_tag=v2
```

ECS replaces tasks one at a time behind the load balancer, and the worker
drains in-flight analyses before stopping (`ECS_CONTAINER_STOP_TIMEOUT=3m`),
so a deploy does not discard work a caller was told had been accepted.

Rollback is the same command with the old tag:

```bash
terraform apply -var-file=testing.tfvars -var image_tag=v1
```

---

## 11. Tearing it down

```bash
terraform destroy -var-file=testing.tfvars
```

S3 buckets with objects in them will block the destroy. That is deliberate —
it is the last guard against deleting the corpus:

```bash
aws s3 rm s3://labs-test-audio-$ACCOUNT_ID --recursive
aws s3 rm s3://$WEIGHTS_BUCKET --recursive
```

Not managed by Terraform, so delete by hand if you are finished with them:

```bash
aws secretsmanager delete-secret --secret-id labs/mongo-uri --force-delete-without-recovery
aws ecr delete-repository --repository-name labs-screen --force
aws ecr delete-repository --repository-name labs-worker --force
```

And delete the Atlas cluster in the Atlas console.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Tasks stay `PENDING` forever | No route to ECR from the private subnet | NAT gateway, or VPC endpoints for ECR + S3 |
| `exec format error` | x86 image on Graviton | Rebuild with `--platform linux/arm64` |
| `/v1/ready` says not ready, logs show missing checkpoint | `weights_s3_uri` wrong, or the prefix does not contain `checkpoints/` | `aws s3 ls $WEIGHTS_URI/checkpoints/` |
| Worker starts then exits | `AccessDenied` on the weights bucket | Check the worker role has `s3:GetObject` + `s3:ListBucket` on it |
| `503 model_loading` on `/v1/analyses` | Backbone still loading (~12 s) | Wait, or set `LABS_EAGER_LOAD=true` |
| Every analysis re-runs for identical audio | MongoDB unreachable | Logs say so at startup; check Atlas network access |
| Jobs accepted, never finish | Nothing consuming the queue | Check `NotVisible`; check the worker service has running tasks |
| Messages in the DLQ | A file breaks the pipeline deterministically | Pull one and run it locally: `python -m labs analyse <file> --mode full` |
| `403 forbidden` on `mode=full` | Key lacks the `deep` scope | Reissue with `--scopes screen,analyze,deep,read` |
| First analysis after idle takes minutes | Cold start, no warm pool | `warm_pool_size = 2` |

### A file in the DLQ

Messages land there after three failed attempts, which means the failure is
deterministic rather than transient. Reproduce locally rather than guessing:

```bash
aws sqs receive-message --queue-url $(terraform output -raw dlq_url) \
  --max-number-of-messages 1
# take the s3 key out of the body
aws s3 cp s3://labs-test-audio-$ACCOUNT_ID/uploads/<key> /tmp/bad.mp3
python -m labs analyse /tmp/bad.mp3 --mode full
```
