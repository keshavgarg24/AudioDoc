# Deploying LABS to AWS

From a fresh clone and an empty AWS account to a working API.

Nothing here is triggered by a git push, a webhook or a CI job. There is no
pipeline, by design, so a testing account can be brought up and torn down
without the production repository being involved at all.

- [What you actually have to do](#what-you-actually-have-to-do)
- [Before you start](#before-you-start)
- [The one-command deploy](#the-one-command-deploy)
- [What each stage does](#what-each-stage-does)
- [Configuration](#configuration)
- [After it is up](#after-it-is-up)
- [Updating](#updating)
- [Tearing it down](#tearing-it-down)
- [Troubleshooting](#troubleshooting)
- [Doing it by hand](#doing-it-by-hand)

---

## What you actually have to do

Three things need a human. Everything else is automated.

| # | You do this | Why it cannot be automated |
|---|---|---|
| 1 | Install four CLI tools | One-off, on your laptop |
| 2 | Create a MongoDB cluster and copy its URI | An account on a third-party service |
| 3 | Run `./deploy.sh` | — |

Then one thing after the network exists:

| 4 | Paste the NAT gateway IP into MongoDB Atlas → Network Access | Atlas has to be told which IP may connect, and that IP does not exist until the NAT is created |

That is the whole list.

> **The checkpoints are not in this repository, and cannot be.** Stage-1 is
> 1.2 GB and GitHub rejects files over 100 MB. A fresh clone gives you all the
> code and the Level-1 ONNX weights (1.2 MB, committed as package data) but
> nothing for the deep tier. `03-weights.sh` handles this: it uses
> `checkpoints/` if you have it, and otherwise downloads from Hugging Face
> once and mirrors into your own S3 bucket. After the first run the origin
> stops mattering.

---

## Before you start

```bash
brew install awscli hashicorp/tap/terraform python
# Docker Desktop: https://docker.com/products/docker-desktop — install and start it
```

Terraform is not in homebrew-core any more; it moved to HashiCorp's own tap
when the licence changed.

Configure AWS credentials for the account you are deploying into:

```bash
aws configure
aws sts get-caller-identity      # confirm this is the TESTING account
```

Create a MongoDB cluster at [cloud.mongodb.com](https://cloud.mongodb.com).
M0 is free and fine for testing; M10 (~$60/month) is the smallest worth using
with more than one container. Add a database user with `readWrite` on the
`labs` database and copy the SRV connection string.

MongoDB is not optional. It carries job state, every stored report, and the
SHA-256 index that makes deduplication work. Without it the service runs in
memory: a job accepted by one container is invisible to every other one, and
identical audio is re-analysed at full cost every time.

---

## The one-command deploy

```bash
cd deploy/aws
MONGO_URI='mongodb+srv://USER:PASS@cluster.xxxxx.mongodb.net/labs' ./deploy.sh
```

It prints the account it is about to deploy into and waits for confirmation,
then runs every stage in order. Budget **25–40 minutes**, most of it the
worker image push (2.5 GB) and the ALB coming up.

Part way through, stage 1 prints something like:

```
    NAT public IP: 54.87.x.x
    Allowlist that IP in MongoDB Atlas -> Network Access.
```

Do that in the Atlas console while the build runs. Every container reaches
Mongo through the NAT, so that one IP is the only address Atlas ever sees —
there is no need for `0.0.0.0/0`, and you should not use it.

At the end you get the URL and an API key:

```
    API    http://labs-test-alb-123456.us-east-1.elb.amazonaws.com
    KEY    sk-xxxxxxxxxxxxxxxxxxxx
```

**Save the key.** It is stored only as a SHA-256 hash and cannot be recovered.

### If a stage fails

Fix the cause and run `./deploy.sh` again. **Every stage is idempotent** — it
looks for what it would create before creating it, so a re-run skips what
already exists rather than making a second copy. You can also run a single
stage directly:

```bash
./04-images.sh        # just rebuild and push the images
```

---

## What each stage does

| Script | Creates | Notes |
|---|---|---|
| `01-network.sh` | VPC, 2 public + 2 private subnets, IGW, NAT gateway, route tables | Two AZs is required — an ALB refuses to be created with one. Asserts the private `0.0.0.0/0` → NAT route before exiting. |
| `02-secrets.sh` | Mongo URI and a generated webhook signing secret in Secrets Manager | Writes **ARNs only** into tfvars. No secret value ever reaches Terraform. |
| `03-weights.sh` | Weights bucket, uploads both checkpoints, pins SHA-256 digests | Downloads from Hugging Face if `checkpoints/` is empty. |
| `04-images.sh` | ECR repositories, builds and pushes both images for ARM64 | Verifies the pushed architecture from the registry afterwards. |
| `05-apply.sh` | Everything else, via Terraform | ALB, ECS cluster, Fargate services, worker ASG, SQS + DLQ, audio bucket, IAM. |
| `06-verify.sh` | An API key, then a graduated smoke test | Liveness → readiness → Level 1 → Level 2 → DLQ check. |

Each script writes its outputs into a `*.auto.tfvars` file. Terraform loads
those automatically, so there is no `-var-file` to remember and no way to
apply against a stale one by accident. They are gitignored — they hold your
account's real subnet ids and secret ARNs.

### Why the smoke test goes in that order

Liveness needs no model. Readiness needs the backbone. Level 1 needs the ONNX
weights, which ship inside the image. Level 2 needs the checkpoints in S3
*and* the queue *and* a worker *and* MongoDB. Testing in that order means the
first failure tells you which layer broke, instead of just "it does not work".

---

## Configuration

Defaults are tuned for a testing stack at low volume. Override with
environment variables:

```bash
STACK_NAME=labs-prod \
AWS_REGION=eu-west-1 \
WORKER_INSTANCE_TYPE=c7g.2xlarge \
WARM_POOL_SIZE=2 \
MONGO_URI='mongodb+srv://...' \
./deploy.sh
```

| Variable | Default | What it controls |
|---|---|---|
| `STACK_NAME` | `labs-test` | Namespaces everything. Two stacks can share an account. |
| `AWS_REGION` | `us-east-1` | |
| `IMAGE_TAG` | `v1` | Never use `latest` — see below. |
| `WORKER_INSTANCE_TYPE` | `c7g.xlarge` | `c7g.2xlarge` for production. |
| `WORKER_MAX_SIZE` | `4` | Ceiling on the worker ASG. |
| `WARM_POOL_SIZE` | `1` | Stopped instances kept ready. |
| `WORKER_BASE_TASKS` | `0` | Always-running workers. |
| `BURST_TO_FARGATE` | `false` | Overflow to Fargate when the ASG is saturated. |
| `CERTIFICATE_ARN` | *(empty)* | ACM cert for HTTPS. Empty means HTTP only. |
| `DEPLOY_AUTO_APPROVE` | *(unset)* | `1` skips every prompt. |

### On `warm_pool_size` and `worker_base_tasks`

`WARM_POOL_SIZE=1, WORKER_BASE_TASKS=0` is the default and is the right
answer at low volume.

A warm-pool instance is **stopped**: it bills only for its EBS volume (~$3 a
month) but resumes in about 30 seconds, because the image is already pulled
and the weights are already on the volume. A cold launch is 3–5 minutes.

`WORKER_BASE_TASKS=1` keeps a `c7g.2xlarge` running permanently — about $212
a month. At 50 analyses a day that instance is 99.96% idle. Use it only when
a caller genuinely cannot wait 30 seconds for the first request after a quiet
period.

### On image tags

Use a real tag. `latest` makes "which image is actually running" unanswerable
during an incident, and turns a rollback into a guess.

### On HTTPS

Without `CERTIFICATE_ARN` the ALB serves **HTTP only**. That is acceptable for
a testing stack inside a VPC you control. Never expose it publicly.

To enable TLS, request a certificate in the same region, create the CNAME it
asks for in your DNS, then:

```bash
CERT_ARN=$(aws acm request-certificate --domain-name api.yourdomain.com \
  --validation-method DNS --query CertificateArn --output text)

aws acm describe-certificate --certificate-arn $CERT_ARN \
  --query 'Certificate.DomainValidationOptions[0].ResourceRecord'
# create that CNAME in your DNS, then:
aws acm wait certificate-validated --certificate-arn $CERT_ARN

CERTIFICATE_ARN=$CERT_ARN ./05-apply.sh
```

With a certificate the ALB listens on 443 and redirects 80 → 443.

---

## After it is up

```bash
cd deploy/aws

# Logs
aws logs tail $(terraform output -raw log_group) --follow

# Backlog. A large number with NotVisible=0 means nothing is consuming it.
aws sqs get-queue-attributes --queue-url $(terraform output -raw queue_url) \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible

# Anything here failed three times, so it fails deterministically.
aws sqs get-queue-attributes --queue-url $(terraform output -raw dlq_url) \
  --attribute-names ApproximateNumberOfMessages

# Did autoscaling fire?
aws autoscaling describe-scaling-activities \
  --auto-scaling-group-name $(terraform output -raw worker_asg_name) --max-items 10
```

### Issuing more API keys

```bash
docker run --rm -e LABS_MONGO_URI="$MONGO_URI" \
  $(aws sts get-caller-identity --query Account --output text).dkr.ecr.$AWS_REGION.amazonaws.com/labs-test-worker:v1 \
  python -m labs.cli.manage_keys create --name "frontend" --scopes screen,read
```

| Scope | Grants | Cost |
|---|---|---|
| `screen` | `POST /v1/screen` | free, ~1.5 s |
| `analyze` | `POST /v1/analyses` with `mode=audio` | DSP only, no backbone |
| `deep` | `mode=ai` / `mode=full`, and appeals | loads the 1.29 GB backbone |
| `read` | polling results | — |

`analyze` deliberately does **not** imply `deep`. `mode=audio` never touches
Stage-1 or Stage-2, so gating it behind `deep` would charge for a tier it does
not use.

---

## Updating

```bash
IMAGE_TAG=v2 ./04-images.sh
IMAGE_TAG=v2 ./05-apply.sh
```

ECS replaces tasks one at a time behind the load balancer, and the worker
drains in-flight analyses before stopping (`ECS_CONTAINER_STOP_TIMEOUT=3m`),
so a deploy does not discard work a caller was already told had been accepted.

Rollback is the same command with the old tag:

```bash
IMAGE_TAG=v1 ./05-apply.sh
```

---

## Tearing it down

```bash
./99-destroy.sh
```

Deliberately not a single `terraform destroy`. Three things Terraform does not
own have to be handled separately, and one of them bills whether you use it or
not:

- **The NAT gateway, ~$32/month.** It survives `terraform destroy` because
  Terraform never created it.
- The ECR repositories and the secrets.
- The audio bucket, which blocks the destroy while it has objects in it. That
  is deliberate: it holds every track ever analysed, and emptying it is not
  reversible. The script asks separately.

The weights bucket is left in place on purpose — about $0.03/month, and it
saves re-uploading 1.3 GB next time.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Tasks stay `PENDING` forever | No route out of the private subnets | `./01-network.sh` asserts this. Re-run it. |
| `exec format error` | x86 image on Graviton | `./04-images.sh` — it builds ARM64 and verifies the pushed architecture |
| `ImagePullBackOff`, repository not found | ECR repo name does not match `${STACK_NAME}-screen` | Use the same `STACK_NAME` for `04-images.sh` and `05-apply.sh` |
| `/v1/ready` not ready, logs show a missing checkpoint | Weights never reached S3 | `aws s3 ls $(terraform output -raw ... )`, or re-run `./03-weights.sh` |
| Worker starts then exits, `AccessDenied` | Worker role cannot read the weights bucket | Check `weights_s3_uri` in `weights.auto.tfvars` points at your bucket |
| `503 model_loading` | Backbone still loading (~12 s) | Wait, or set `LABS_EAGER_LOAD=true` |
| Identical audio re-analysed every time | MongoDB unreachable | Atlas → Network Access must allowlist the NAT IP |
| Jobs accepted, never finish | Nothing consuming the queue | Check `NotVisible`; check the worker ASG has instances |
| Messages in the DLQ | A file breaks the pipeline deterministically | Pull it and reproduce locally (below) |
| `403 forbidden` on `mode=full` | Key lacks the `deep` scope | Reissue with `--scopes screen,analyze,deep,read` |
| First analysis after idle takes minutes | No warm pool | `WARM_POOL_SIZE=1 ./05-apply.sh` |
| Deployed to the wrong region | — | The provider is pinned to `var.region`, which `05-apply.sh` sets from `AWS_REGION` |

### A file in the DLQ

Three failed attempts means the failure is deterministic, not transient.
Reproduce it locally rather than guessing:

```bash
aws sqs receive-message --queue-url $(terraform output -raw dlq_url) \
  --max-number-of-messages 1
aws s3 cp s3://<audio-bucket>/uploads/<key> /tmp/bad.mp3
python -m labs analyse /tmp/bad.mp3 --mode full
```

---

## Doing it by hand

The scripts are ordinary bash and are meant to be read — each one documents
why it does what it does. If you need to run a piece manually, read the script
for that stage rather than following a parallel copy of the same commands in a
document, which is the copy that goes stale.

Two constraints are worth repeating because violating either produces a
confusing failure rather than a clear one:

**ECR repository names are not free choices.** Terraform builds the image
reference as `${var.name}-screen` and `${var.name}-worker`. With
`STACK_NAME=labs-test` the repositories must be `labs-test-screen` and
`labs-test-worker`. Any other name produces a deploy that applies cleanly and
then cannot pull.

**ARM64 is not a choice either.** The workers are Graviton and the Fargate
tasks are ARM. An x86 image fails with `exec format error`, which names
neither the architecture nor the image.
