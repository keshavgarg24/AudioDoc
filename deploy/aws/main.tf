###############################################################################
# LABS on AWS, CPU only, autoscaling.
#
#   ALB ──▶ screen service   (Fargate ARM, ~350 MB image, no torch)
#        └▶ api service      (Fargate ARM, accepts + enqueues, never analyses)
#                │
#                ├─▶ S3      the audio, because the worker is another container
#                └─▶ SQS ──▶ worker ASG  (EC2 Graviton, one analysis per task)
#                                │
#                                └─▶ MongoDB (job state + reports)
#
# WHY THE WORKERS ARE EC2 AND NOT FARGATE
# ---------------------------------------
# Two reasons, both about the 1.29 GB Stage-1 checkpoint.
#
#   1. Fargate has no persistent local cache. Every task start re-fetches the
#      weights, which is ~12 s of load on top of the pull. An EC2 host keeps
#      them on an EBS volume across task replacements, so only a genuinely new
#      instance pays.
#   2. Cost. At the sizes this workload wants, Fargate ARM is ~$0.0324/vCPU-hr
#      against ~$0.0363/vCPU-hr for c7g on-demand - close - but the worker runs
#      at ~100% CPU continuously, which is exactly the shape a Spot ASG or a
#      Savings Plan discounts hardest.
#
# The API and screen tiers ARE Fargate: they are bursty, stateless and tiny,
# which is the shape Fargate is priced for.
#
# Deliberately NOT in this file: the VPC, the MongoDB cluster and TLS
# certificates. Those are almost always pre-existing and shared, and a module
# that insists on creating its own is one you cannot adopt incrementally.
###############################################################################

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
  }

  # State lives in S3, configured by `terraform init -backend-config=...` from
  # 00-backend.sh rather than hardcoded here, because the bucket name has to be
  # account-unique and this file is committed.
  #
  # WHY THIS IS NOT OPTIONAL
  # ------------------------
  # With local state, the only record of what exists in AWS is one file on one
  # laptop. Delete the directory and the stack is still running, still billing,
  # and no longer manageable by Terraform - every resource has to be imported
  # by hand or found and deleted in the console. This stack was in exactly that
  # position: state lived in an untracked sibling directory, and the deploy
  # fixes lived only there too.
  #
  # An S3 bucket with versioning plus a DynamoDB lock table is inside the
  # always-free tier at this size.
  backend "s3" {}
}

# Pinned to var.region explicitly.
#
# Without this block the provider falls back to AWS_REGION / AWS_PROFILE, and
# `var.region` becomes decorative: a tfvars saying eu-west-1 would deploy to
# whatever the shell happened to export, with no error anywhere. The failure is
# silent and expensive - a full stack in the wrong region, billed, while the
# state file insists it is where you asked for.
#
# default_tags land on every taggable resource, which is what makes "what does
# LABS cost in this account" answerable in Cost Explorer without tagging each
# resource by hand.
provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.name
      ManagedBy = "terraform"
    }
  }
}

variable "name" { default = "labs" }
variable "region" { default = "us-east-1" }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "public_subnet_ids" { type = list(string) }
variable "certificate_arn" {
  type        = string
  default     = ""
  description = "ACM cert for HTTPS. Empty serves HTTP only, which is for a private VPC and nothing else."
}
variable "mongo_uri_secret_arn" {
  type        = string
  description = "Secrets Manager ARN holding LABS_MONGO_URI. Never a plain variable: a URI in tfvars ends up in state, and state is read by anyone who can plan."
}
variable "image_tag" { default = "latest" }

variable "weights_s3_uri" {
  type        = string
  description = <<-EOT
    s3://bucket/prefix holding the mirrored checkpoints, e.g.
    s3://labs-weights/models. The prefix must contain checkpoints/Stage-1.ckpt
    and checkpoints/Stage-2.ckpt.

    REQUIRED for the deep tier. LABS_OFFLINE is true in production, so a
    worker will not fall back to downloading 1.3 GB from a third-party mirror
    during a deploy - it fails loudly instead. Mirroring is also what makes a
    deploy reproducible: the upstream can change or disappear, your bucket
    cannot.

    On EC2 workers the weights land on the host volume at /opt/labs/models and
    survive task replacement, so only a genuinely new instance pays the
    download. Fargate burst tasks have no host volume and re-fetch each time,
    which is the accepted cost of the spike path.

    Set to "" ONLY for a screen-only deployment that never runs Level 2.
  EOT
}

###############################################################################
# Secrets
#
# Every one of these is an ARN, never a value. A secret passed as a Terraform
# variable is written to state in plaintext, and state is readable by anyone
# who can run `plan` - so the value never enters Terraform at all. The ECS
# agent reads it from Secrets Manager at container start using the execution
# role, and the application sees an ordinary environment variable.
#
# Only the Mongo URI is required. The rest are omitted from the task
# definition entirely when unset.
###############################################################################

variable "webhook_secret_arn" {
  type        = string
  default     = ""
  description = "Secrets Manager ARN holding LABS_WEBHOOK_SECRET, used to sign webhook callbacks."
}

variable "api_keys_secret_arn" {
  type        = string
  default     = ""
  description = <<-EOT
    Secrets Manager ARN holding LABS_API_KEYS, a comma-separated static key
    list. Prefer MongoDB-backed keys instead: static keys carry no identity,
    no per-key quota and no revocation, so retiring one means a redeploy.
  EOT
}

variable "acr_bearer_token_arn" {
  type        = string
  default     = ""
  description = "Secrets Manager ARN holding ACR_BEARER_TOKEN, the Bearer auth token for the ACRCloud File Scanning API. Leave empty to run without catalogue verification."
}

variable "acr_container_id_arn" {
  type        = string
  default     = ""
  description = "Secrets Manager ARN holding ACR_CONTAINER_ID, the ACRCloud file-scanning container to upload audio into."
}

###############################################################################
# Retention
#
# All three default to 0, meaning KEEP FOREVER. The audio, the reports and the
# features derived from them are the dataset - they are what deduplication
# reads, what an appeal is re-analysed from, and the only corpus that exists
# if these models are ever retrained.
#
# Set a positive value only when a retention or privacy policy requires
# deletion, and understand that it is not reversible: a TTL index deletes, it
# does not archive.
###############################################################################

variable "audio_ttl_days" {
  default     = 0
  description = "Days before uploaded audio is expired from S3. 0 keeps it forever."
}

variable "result_ttl_days" {
  default     = 0
  description = "Days before an analysis or tool result is expired from MongoDB. 0 keeps it forever."
}

variable "screen_retain_seconds" {
  default     = 0
  description = "Seconds a retained Level-1 result is kept for appeal. 0 keeps it forever."
}

variable "audio_prefix" {
  default     = "uploads"
  description = "S3 key prefix for uploaded audio. Must match LABS_AUDIO_PREFIX."
}

# The worker instance type. c7g.2xlarge is 8 vCPU / 16 GiB. The backbone wants
# 4 physical cores to be worth running and about 4 GB resident, so this hosts
# two worker tasks comfortably, or one with headroom for the DSP passes.
variable "worker_instance_type" { default = "c7g.2xlarge" }

variable "worker_task_cpu" {
  default     = 4096
  description = <<-EOT
    CPU units per worker task. Must leave headroom on the host: the ECS agent
    and the container runtime take roughly 128 units and 256 MiB, so a task
    sized to the instance's full vCPU count will never be placed.

      c7g.2xlarge  (8 vCPU / 16 GiB)  ->  4096 / 8192, two tasks per host
      m7i-flex.large (2 vCPU / 8 GiB) ->  2048 / 6144, one task per host
  EOT
}

variable "worker_task_memory" {
  default     = 8192
  description = "MiB per worker task. The backbone holds ~3.5 GB resident and the DSP passes peak above that on a long track."
}

variable "worker_torch_threads" {
  default     = 4
  description = <<-EOT
    Torch intra-op threads. PHYSICAL cores, not vCPUs.

    Graviton has no SMT so the two are equal there. On x86 the right value is
    half the vCPU count: two hyperthreads sharing one core's vector units
    contend rather than scale, and setting this to the vCPU count costs
    throughput instead of buying it.
  EOT
}

variable "enable_cloudfront" {
  default     = true
  description = <<-EOT
    Put a CloudFront distribution in front of the ALB to get HTTPS without
    owning a domain.

    The `*.cloudfront.net` certificate is issued and rotated by AWS and costs
    nothing; CloudFront's always-free tier covers 1 TB/month of egress and 10M
    requests. Without this, and without an ACM `certificate_arn`, the ALB
    serves plain HTTP and every API key crosses the internet in the clear.

    Adds ~10 minutes to the first apply while the distribution propagates.
  EOT
}

variable "lock_alb_to_cloudfront" {
  default     = false
  description = <<-EOT
    Make the ALB refuse any request that did not come through CloudFront, by
    requiring the secret header the distribution injects.

    Off by default because turning it on breaks every existing caller that
    holds the ALB hostname - including 06-verify.sh. Turn it on once traffic
    has moved to the distribution URL, otherwise the HTTPS front door is
    advisory: anyone with the ALB hostname can still reach port 80 directly.
  EOT
}

variable "stage1_sha256" {
  default     = ""
  description = <<-EOT
    SHA-256 of Stage-1.ckpt, as uploaded to the weights mirror. Empty disables
    verification and the application logs a warning every boot.

    Written automatically by 03-weights.sh. Set it by hand only when attaching
    to a mirror this stack did not populate.
  EOT
}

variable "stage2_sha256" {
  default     = ""
  description = "SHA-256 of Stage-2.ckpt. See stage1_sha256."
}

variable "uncertain_margin" {
  default     = 0.15
  description = <<-EOT
    Half-width of the `uncertain` band around the 0.5 decision boundary in
    `assessment.band`. Shared by both tiers so one legend fits both.

    Reporting only: it never changes `verdict` and never suppresses `label`.
  EOT
}

variable "busybox_image" {
  # Pinned by digest, not by tag. `:latest` would mean the task definition
  # silently changes what it runs whenever upstream republishes, which is not
  # something a forensic service should allow into its own boot path.
  # Resolved from public.ecr.aws/docker/library/busybox:1.36.1.
  default     = "public.ecr.aws/docker/library/busybox@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662"
  description = "Init container that fixes host-volume ownership for the worker."
}

variable "worker_max_size" {
  default     = 20
  description = "Ceiling on the worker ASG. Also caps the warm pool's prepared capacity."
}

variable "worker_min_size" {
  default     = 1
  description = "Floor on the worker ASG. Raise to keep the fleet warm and avoid scale-in churn."
}

variable "worker_min_tasks" {
  default     = 1
  description = "Floor on worker ECS tasks. Set equal to worker_min_size to keep every instance busy."
}

variable "warm_pool_size" {
  default     = 2
  description = <<-EOT
    Stopped-but-initialised instances held ready for a burst. Billed as EBS
    only (~$4.80/month each at 60 GB gp3), and the difference between a
    3-5 minute cold start and a ~30 second resume. Set 0 only if bursts do
    not matter to you.
  EOT
}

variable "worker_base_tasks" {
  default     = 1
  description = <<-EOT
    Worker tasks pinned to the EC2 fleet before any Fargate overflow. Also the
    warm floor: a cold worker pays an image pull plus ~12 s of model load, so
    the first submission after an idle period would otherwise wait minutes.
    ~$212/month on c7g.2xlarge, and the thing that buys a predictable p50.
  EOT
}

variable "burst_to_fargate" {
  default     = true
  description = <<-EOT
    Overflow onto Fargate once the EC2 fleet is saturated.

    Fargate needs no instance launch, so a task starts in ~60 s even when the
    ASG has nothing warm left - it is the second line of defence behind the
    warm pool. It costs more per vCPU-hour and re-fetches the weights on every
    task, which is exactly the trade you want for the tail of a spike and
    exactly the wrong one for steady state. Hence base on EC2, overflow here.
  EOT
}

locals {
  tags = {
    Application = var.name
    ManagedBy   = "terraform"
  }
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

###############################################################################
# Queue
###############################################################################

# The dead-letter queue exists because a file CAN break the pipeline
# deterministically - a truncated container, an exotic codec - and without a
# redrive policy that message recirculates forever, occupying a worker every
# time. Three attempts then park it for inspection.
resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name}-analyses-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum
  sqs_managed_sse_enabled   = true
  tags                      = local.tags
}

resource "aws_sqs_queue" "analyses" {
  name = "${var.name}-analyses"

  # Must exceed the slowest analysis or SQS hands the message to a second
  # worker while the first is still working, and the track is analysed twice
  # at full cost. Measured p99 is under 90 s; this is deliberate headroom, and
  # the worker also heartbeats the deadline outward while it holds the message.
  visibility_timeout_seconds = 900

  # Long enough that a backlog survives a full worker-fleet replacement.
  message_retention_seconds = 86400

  # 20 s long polling is set by the consumer, not here, but this makes it the
  # default for anything else that ever reads the queue.
  receive_wait_time_seconds = 20

  sqs_managed_sse_enabled = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })

  tags = local.tags
}

###############################################################################
# Audio bucket
###############################################################################
resource "aws_s3_bucket" "audio" {
  bucket = "${var.name}-audio-${data.aws_caller_identity.current.account_id}"
  tags   = local.tags
}

resource "aws_s3_bucket_public_access_block" "audio" {
  bucket                  = aws_s3_bucket.audio.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "audio" {
  bucket = aws_s3_bucket.audio.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Audio retention.
#
# Default is KEEP FOREVER (audio_ttl_days = 0), matching LABS_AUDIO_TTL_DAYS.
# The audio and the features derived from it are the dataset: they are what
# makes deduplication work, what an appeal is re-analysed from, and the only
# corpus available if these models are ever retrained.
#
# Be deliberate if you turn expiry on. Uploaded audio is also a liability -
# it is other people's work, and a breach then exposes every track ever
# submitted rather than a recent window - so a retention policy is a real
# reason to set this. Losing the corpus by accident is not.
#
# The incomplete-multipart rule is ALWAYS on regardless. An aborted upload is
# not data, it is an invisible charge: parts bill at full storage rate and
# never appear in a bucket listing.
resource "aws_s3_bucket_lifecycle_configuration" "audio" {
  bucket = aws_s3_bucket.audio.id

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }

  dynamic "rule" {
    for_each = var.audio_ttl_days > 0 ? [1] : []
    content {
      id     = "expire-uploads"
      status = "Enabled"
      filter { prefix = "${var.audio_prefix}/" }
      expiration { days = var.audio_ttl_days }
    }
  }
}

###############################################################################
# IAM
###############################################################################
data "aws_iam_policy_document" "task_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The execution role pulls the image and writes logs; it also needs to read
# every secret, because injection happens before the container starts.
#
# Scoped to exactly the ARNs configured - never a wildcard. A task that can
# read one secret should not be able to enumerate the account's others, and
# `secretsmanager:*` on `*` is how one compromised container becomes all of
# them.
resource "aws_iam_role_policy" "execution_secrets" {
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = local.secret_arns
    }]
  })
}

# --- API task: may enqueue and write audio, may NOT consume -----------------
resource "aws_iam_role" "api" {
  name               = "${var.name}-api-task"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy" "api" {
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Send and inspect only. An API container that could ReceiveMessage
        # would silently steal work from the worker fleet and then run a 90 s
        # analysis inside the process serving health checks.
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:GetQueueAttributes"]
        Resource = [aws_sqs_queue.analyses.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = ["${aws_s3_bucket.audio.arn}/*"]
      },
    ]
  })
}

# --- Screen task: no queue, no bucket, no database --------------------------
# Level 1 holds the file in a temp path for ~1.5 s and answers in the response.
# It has no reason to touch any of it, and the tier most exposed to strangers
# is the one where that matters most.
resource "aws_iam_role" "screen" {
  name               = "${var.name}-screen-task"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
  tags               = local.tags
}

# --- Worker: may consume and read audio, may NOT enqueue --------------------
resource "aws_iam_role" "worker" {
  name = "${var.name}-worker"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = ["ec2.amazonaws.com", "ecs-tasks.amazonaws.com"] }
    }]
  })
  tags = local.tags
}

resource "aws_iam_role_policy" "worker" {
  role = aws_iam_role.worker.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage", "sqs:DeleteMessage",
          "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes",
        ]
        Resource = [aws_sqs_queue.analyses.arn]
      },
      {
        # Read only. A worker does not accept submissions, so it has no
        # legitimate reason to be able to write into the upload prefix.
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${aws_s3_bucket.audio.arn}/*"]
      },
      ], local.weights_bucket == "" ? [] : [
      {
        # The mirrored checkpoints. ListBucket as well as GetObject, because
        # the fetcher enumerates the prefix to discover what it needs rather
        # than assuming filenames - and a missing ListBucket surfaces as a
        # confusing AccessDenied on a HeadObject rather than as "no weights".
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          "arn:aws:s3:::${local.weights_bucket}",
          "arn:aws:s3:::${local.weights_bucket}/*",
        ]
      },
    ])
  })
}

resource "aws_iam_role_policy_attachment" "worker_ecs" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_instance_profile" "worker" {
  name = "${var.name}-worker"
  role = aws_iam_role.worker.name
}

###############################################################################
# Cluster, logs, ALB
###############################################################################
resource "aws_ecs_cluster" "this" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
  tags = local.tags
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name}"
  retention_in_days = 30
  tags              = local.tags
}

resource "aws_security_group" "alb" {
  name   = "${var.name}-alb"
  vpc_id = var.vpc_id
  ingress {
    from_port   = var.certificate_arn == "" ? 80 : 443
    to_port     = var.certificate_arn == "" ? 80 : 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.tags
}

resource "aws_security_group" "tasks" {
  name   = "${var.name}-tasks"
  vpc_id = var.vpc_id
  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.tags
}

resource "aws_lb" "this" {
  name               = var.name
  load_balancer_type = "application"
  subnets            = var.public_subnet_ids
  security_groups    = [aws_security_group.alb.id]

  # An analysis submission is a file upload, and the deep report is large.
  # The default 60 s is fine for both because BOTH are asynchronous - the only
  # synchronous route is /v1/screen at ~1.5 s. This is set anyway so that a
  # slow uploader on a bad connection is not cut mid-PUT.
  idle_timeout = 120
  tags         = local.tags
}

resource "aws_lb_target_group" "screen" {
  name        = "${var.name}-screen"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  # /v1/ready, not /health. A screen container is ready the moment its two
  # ORT sessions exist, which is under a second, so the strict probe is both
  # correct and fast here.
  health_check {
    path                = "/v1/ready"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  # Level 1 holds no session state, so draining only has to outlast one
  # in-flight request.
  deregistration_delay = 15
  tags                 = local.tags
}

resource "aws_lb_target_group" "api" {
  name        = "${var.name}-api"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  # /health, not /v1/ready. An API container in this deployment never loads
  # the backbone - the workers do - so a strict readiness probe would hold it
  # out of the target group forever waiting for weights it will never have.
  health_check {
    path                = "/health"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  deregistration_delay = 30
  tags                 = local.tags
}

locals {
  # Both must be true: locking the origin without a distribution in front of it
  # would refuse every request including the operator's own.
  lock_origin = var.enable_cloudfront && var.lock_alb_to_cloudfront
}

resource "aws_lb_listener" "this" {
  load_balancer_arn = aws_lb.this.arn
  port              = var.certificate_arn == "" ? 80 : 443
  protocol          = var.certificate_arn == "" ? "HTTP" : "HTTPS"
  ssl_policy        = var.certificate_arn == "" ? null : "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn == "" ? null : var.certificate_arn

  # Exactly one of these is rendered. Unlocked, the default forwards as it
  # always has. Locked, the default refuses and the forward moves into
  # `api_via_cloudfront`, which requires the distribution's secret header.
  dynamic "default_action" {
    for_each = local.lock_origin ? [] : [1]
    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.api.arn
    }
  }

  dynamic "default_action" {
    for_each = local.lock_origin ? [1] : []
    content {
      type = "fixed-response"
      fixed_response {
        content_type = "application/json"
        status_code  = "403"
        message_body = jsonencode({
          error  = "direct_origin_access_denied"
          detail = "This origin does not serve traffic directly. Use the HTTPS endpoint."
        })
      }
    }
  }
}

###############################################################################
# TLS without a domain
#
# An API key travelling over plaintext HTTP is readable by every hop between
# the caller and the ALB. The usual fix is ACM, which needs a domain the
# operator owns and can validate - so on a stack with no domain yet, TLS just
# never got turned on.
#
# CloudFront closes that gap: its default `*.cloudfront.net` certificate is
# issued and rotated by AWS, needs no domain, and costs nothing. The
# distribution terminates TLS and forwards to the ALB.
#
# Set `certificate_arn` later and the ALB serves HTTPS directly; this
# distribution is then redundant and can be disabled with enable_cloudfront.
###############################################################################
resource "aws_cloudfront_distribution" "this" {
  count   = var.enable_cloudfront ? 1 : 0
  enabled = true
  comment = "${var.name} - TLS front door"
  # PriceClass_100 is North America + Europe. The origin is in ap-south-1, so
  # the edge is not buying latency here - it is buying a certificate. The
  # cheapest class that still serves globally is the right one.
  price_class = "PriceClass_100"

  origin {
    domain_name = aws_lb.this.dns_name
    origin_id   = "alb"
    custom_origin_config {
      http_port  = 80
      https_port = 443
      # The ALB has no certificate of its own until `certificate_arn` is set,
      # so the edge-to-origin leg is HTTP inside AWS's network. The
      # caller-to-edge leg - the one crossing the public internet with an API
      # key on it - is HTTPS either way.
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      origin_read_timeout    = 60
    }
    custom_header {
      name  = "X-Origin-Verify"
      value = random_password.origin_verify.result
    }
  }

  default_cache_behavior {
    target_origin_id       = "alb"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    # CachingDisabled. Every response here is either an upload receipt or a
    # verdict for one specific file; a cache hit would serve one caller another
    # caller's analysis.
    cache_policy_id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    # AllViewerExceptHostHeader - forwards Authorization and the rest, but lets
    # the ALB see its own hostname so host-based routing still resolves.
    origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    minimum_protocol_version       = "TLSv1.2_2021"
  }

  tags = local.tags
}

# Lets the ALB tell edge traffic from someone who found the ALB hostname and
# went around the distribution. Generated here rather than configured, because
# it is a shared secret with no reason to be human-readable or reused.
resource "random_password" "origin_verify" {
  length  = 40
  special = false
}

# An ALB rule matches requests that DO satisfy its condition; there is no
# "unless". So the lock is expressed by inverting the DEFAULT instead: refuse
# everything, and let a rule carrying the header forward. A request that
# reached port 80 directly matches no rule, falls through to the default, and
# is refused there - which is both correct and the cheapest place to say no.
resource "aws_lb_listener_rule" "api_via_cloudfront" {
  count        = local.lock_origin ? 1 : 0
  listener_arn = aws_lb_listener.this.arn
  priority     = 20

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
  condition {
    http_header {
      http_header_name = "X-Origin-Verify"
      values           = [random_password.origin_verify.result]
    }
  }
}

# The free tier is routed to its own fleet, and this rule is the whole reason
# the split is worth having: a burst of anonymous screen traffic scales the
# cheap ~350 MB service and cannot touch the containers holding paid work.
resource "aws_lb_listener_rule" "screen" {
  listener_arn = aws_lb_listener.this.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.screen.arn
  }
  condition {
    path_pattern { values = ["/v1/screen"] }
  }

  # Conditions on one rule are ANDed, so when the origin is locked this path
  # needs the header too. Without it, /v1/screen would be the one route that
  # still answered on plain HTTP - and it is the ungated public one, which
  # makes it the worst possible exception.
  dynamic "condition" {
    for_each = local.lock_origin ? [1] : []
    content {
      http_header {
        http_header_name = "X-Origin-Verify"
        values           = [random_password.origin_verify.result]
      }
    }
  }
}

###############################################################################
# Shared container environment
###############################################################################
locals {
  ecr = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${data.aws_region.current.name}.amazonaws.com/${var.name}"

  common_env = [
    { name = "AWS_REGION", value = data.aws_region.current.name },
    { name = "LABS_AUDIO_BUCKET", value = aws_s3_bucket.audio.id },
    { name = "LABS_SQS_QUEUE_URL", value = aws_sqs_queue.analyses.url },
    # Weights are mirrored, so a missing file must fail loudly rather than
    # quietly pulling 1.3 GB from a third-party mirror during a deploy.
    { name = "LABS_OFFLINE", value = "true" },
    { name = "LABS_REQUIRE_AUTH", value = "true" },
    { name = "LABS_GATE_DEEP", value = "true" },
    { name = "LABS_HSTS_SECONDS", value = "31536000" },
    # Publish the schema, not the live endpoint: /openapi.json enumerates every
    # route and error code, which is a map for anyone probing the service.
    { name = "LABS_EXPOSE_DOCS", value = "false" },

    # Retention, stated explicitly rather than left to the application
    # defaults. These values and the S3 lifecycle rule above are the same
    # policy expressed in two places, and an operator reading this file should
    # not have to open the Python to find out how long anything is kept.
    # 0 = keep forever, which is the default for all three.
    { name = "LABS_AUDIO_TTL_DAYS", value = tostring(var.audio_ttl_days) },
    { name = "LABS_RESULT_TTL_DAYS", value = tostring(var.result_ttl_days) },
    { name = "LABS_SCREEN_RETAIN_S", value = tostring(var.screen_retain_seconds) },
    { name = "LABS_AUDIO_PREFIX", value = var.audio_prefix },

    # Where the mirrored checkpoints live. Paired with LABS_OFFLINE=true
    # above: a worker fetches from here or fails loudly, and never silently
    # pulls 1.3 GB from a third-party mirror mid-deploy.
    { name = "LABS_MODELS_S3_URI", value = var.weights_s3_uri },

    # Verdict provenance. Without these the app logs a warning on every boot
    # and accepts whatever bytes the mirror hands it - which means a verdict
    # cannot be traced back to the exact weights that produced it, and a
    # corrupted or swapped checkpoint is indistinguishable from a good one.
    # Written by 03-weights.sh from the files it actually uploaded.
    { name = "LABS_STAGE1_SHA256", value = var.stage1_sha256 },
    { name = "LABS_STAGE2_SHA256", value = var.stage2_sha256 },

    # Level 1 and Level 2 grade `assessment.band` against the same margin, so
    # one legend fits both tiers. Reporting only - it never gates a verdict.
    { name = "LABS_UNCERTAIN_MARGIN", value = tostring(var.uncertain_margin) },
  ]

  # Bucket ARN parsed out of the s3:// URI so the IAM policy can be scoped to
  # it. Empty URI yields an empty list, which is what a screen-only deployment
  # wants - no weights, no permission to read any.
  weights_bucket = var.weights_s3_uri == "" ? "" : split("/", replace(var.weights_s3_uri, "s3://", ""))[0]

  # Every secret the containers receive, injected by the ECS agent from
  # Secrets Manager. None of these values pass through Terraform, so none of
  # them land in state - which is the whole point, because state is readable
  # by anyone who can run `plan`.
  #
  # Optional ones are omitted entirely when their ARN is empty rather than
  # injected as "", because an empty ACR key reads as "configured but broken"
  # to the application and as "not configured" is what is actually true.
  mongo_secret = concat(
    [{
      name      = "LABS_MONGO_URI"
      valueFrom = var.mongo_uri_secret_arn
    }],
    var.webhook_secret_arn == "" ? [] : [{
      name      = "LABS_WEBHOOK_SECRET"
      valueFrom = var.webhook_secret_arn
    }],
    var.api_keys_secret_arn == "" ? [] : [{
      name      = "LABS_API_KEYS"
      valueFrom = var.api_keys_secret_arn
    }],
    var.acr_bearer_token_arn == "" ? [] : [{
      name      = "ACR_BEARER_TOKEN"
      valueFrom = var.acr_bearer_token_arn
    }],
    var.acr_container_id_arn == "" ? [] : [{
      name      = "ACR_CONTAINER_ID"
      valueFrom = var.acr_container_id_arn
    }],
  )

  # The IAM policy is built from the same list, so adding a secret above
  # cannot leave the execution role unable to read it.
  secret_arns = distinct(compact([
    var.mongo_uri_secret_arn,
    var.webhook_secret_arn,
    var.api_keys_secret_arn,
    var.acr_bearer_token_arn,
    var.acr_container_id_arn,
  ]))
}

###############################################################################
# Screen service (Level 1, free tier)
###############################################################################
resource "aws_ecs_task_definition" "screen" {
  family                   = "${var.name}-screen"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  runtime_platform {
    # ARM is ~20% cheaper per vCPU-hour on Fargate and this tier is pure
    # numpy/scipy/ORT, all of which have good aarch64 wheels.
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.screen.arn

  container_definitions = jsonencode([{
    name         = "screen"
    image        = "${local.ecr}-screen:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment = concat(local.common_env, [
      { name = "LABS_EAGER_LOAD", value = "false" },
      # One in-flight screen per vCPU. Level 1 is a short GEMM-bound burst, so
      # past the core count ORT's own intra-op threads and ours contend for
      # the same vector units.
      { name = "LABS_SCREEN_CONCURRENCY", value = "1" },
      { name = "LABS_SCREEN_ONNX_THREADS", value = "1" },
      # The free endpoint is the one strangers reach, so it gets its own,
      # tighter budget.
      { name = "LABS_RATE_LIMIT_PER_MIN", value = "30" },
    ])
    # The screen tier validates API keys against Mongo like every other tier.
    # Omitting this block does not fail the deploy - the service starts, logs
    # "running without persistence" once, and then 401s every authenticated
    # request. It cost a day to find; that is why it is called out here.
    secrets = local.mongo_secret
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.this.name
        awslogs-region        = data.aws_region.current.name
        awslogs-stream-prefix = "screen"
      }
    }
  }])
  tags = local.tags
}

resource "aws_ecs_service" "screen" {
  name            = "${var.name}-screen"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.screen.arn
  desired_count   = 2
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = var.private_subnet_ids
    security_groups = [aws_security_group.tasks.id]
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.screen.arn
    container_name   = "screen"
    container_port   = 8000
  }

  # Nothing to warm, so a new task is useful almost immediately.
  health_check_grace_period_seconds = 30

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    # Owned by the autoscaler below, not by terraform. Without this every
    # apply resets the fleet to 2 tasks, which at peak is an outage.
    ignore_changes = [desired_count]
  }
  tags = local.tags
}

###############################################################################
# API service (accepts, enqueues, never analyses)
###############################################################################
resource "aws_ecs_task_definition" "api" {
  family                   = "${var.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.api.arn

  container_definitions = jsonencode([{
    name         = "api"
    image        = "${local.ecr}-screen:${var.image_tag}"
    essential    = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment = concat(local.common_env, [
      # The SCREEN image, deliberately. This container validates, stores and
      # enqueues; it runs Level 1 inline and hands Level 2 to the queue, so it
      # needs no torch and no 1.29 GB of weights. That is the difference
      # between a ~350 MB and a ~2.5 GB pull on every scale-out event.
      { name = "LABS_EAGER_LOAD", value = "false" },
      { name = "LABS_SCREEN_CONCURRENCY", value = "1" },
    ])
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.this.name
        awslogs-region        = data.aws_region.current.name
        awslogs-stream-prefix = "api"
      }
    }
    secrets = local.mongo_secret
  }])
  tags = local.tags
}

resource "aws_ecs_service" "api" {
  name            = "${var.name}-api"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = 2
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = var.private_subnet_ids
    security_groups = [aws_security_group.tasks.id]
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  health_check_grace_period_seconds = 60
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  lifecycle {
    ignore_changes = [desired_count]
  }
  tags = local.tags
}

###############################################################################
# HTTP autoscaling: request count per target
###############################################################################
resource "aws_appautoscaling_target" "screen" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.screen.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = 2
  max_capacity       = 40
}

# Target tracking on requests-per-target, not on CPU. Level 1 saturates a core
# for ~1.5 s and then idles, so average CPU over a 60 s window badly
# under-reports a container that is actually at its concurrency limit.
resource "aws_appautoscaling_policy" "screen" {
  name               = "${var.name}-screen-rps"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.screen.service_namespace
  resource_id        = aws_appautoscaling_target.screen.resource_id
  scalable_dimension = aws_appautoscaling_target.screen.scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label = join("/", [
        # ALB portion of the ARN, then the target group portion.
        replace(aws_lb.this.arn_suffix, "/^.*loadbalancer\\//", ""),
        aws_lb_target_group.screen.arn_suffix,
      ])
    }
    # ~1.5 s per screen and one in flight per task: 40 requests a minute is
    # roughly a task's sustainable rate, so this targets ~60% utilisation and
    # leaves room to absorb a burst while new tasks start.
    target_value      = 24
    scale_in_cooldown = 300
    # 0: let target tracking add capacity as fast as it decides it needs to.
    # A screen task is a ~350 MB pull with nothing to load, so it is useful
    # within ~15 s - there is no thundering-herd risk to damp here, and any
    # cooldown is pure added latency during a burst.
    scale_out_cooldown = 0
  }
}

resource "aws_appautoscaling_target" "api" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = 2
  max_capacity       = 20
}

resource "aws_appautoscaling_policy" "api" {
  name               = "${var.name}-api-rps"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.api.service_namespace
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label = join("/", [
        replace(aws_lb.this.arn_suffix, "/^.*loadbalancer\\//", ""),
        aws_lb_target_group.api.arn_suffix,
      ])
    }
    # Submissions are I/O bound: hash, PUT to S3, one SQS call. A container
    # handles these at a far higher rate than it handles analyses, which is the
    # entire point of not analysing here.
    target_value       = 300
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

###############################################################################
# Worker fleet: EC2 Graviton, scaled on queue backlog
###############################################################################
# The AMI architecture has to match the instance family, and getting it wrong
# fails late and opaquely: the instance boots, the ECS agent never registers,
# and the service sits at 0/1 with no error pointing at the cause.
#
# Derived from the instance type rather than set by hand. Graviton families end
# their size prefix in `g` (c7g, m7g, r8g); everything else is x86_64. Both the
# task definition's `cpuArchitecture` and this parameter read the same local,
# so they cannot drift apart.
locals {
  worker_family = split(".", var.worker_instance_type)[0]
  worker_arch = can(regex("g[a-z]*$", local.worker_family)) ? "arm64" : "x86_64"
  ecs_ami_path = local.worker_arch == "arm64" ? "arm64/recommended" : "recommended"
}

data "aws_ssm_parameter" "ecs_ami" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/${local.ecs_ami_path}/image_id"
}

resource "aws_launch_template" "worker" {
  name_prefix   = "${var.name}-worker-"
  image_id      = data.aws_ssm_parameter.ecs_ami.value
  instance_type = var.worker_instance_type

  iam_instance_profile { arn = aws_iam_instance_profile.worker.arn }
  vpc_security_group_ids = [aws_security_group.tasks.id]

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      # 1.29 GB Stage-1 + 47 MB Stage-2 + the MERT HF cache + the beat-tracker
      # checkpoint, plus the image and room for staged uploads. gp3 because its
      # baseline 3000 IOPS is free, and the first load of the weights is the
      # only IO this host ever really does.
      volume_size = 60
      volume_type = "gp3"
      encrypted   = true
    }
  }

  user_data = base64encode(<<-EOT
    #!/bin/bash
    echo "ECS_CLUSTER=${aws_ecs_cluster.this.name}" >> /etc/ecs/ecs.config
    # Drain tasks on scale-in instead of killing them. Paired with the ASG
    # lifecycle hook, this is what stops a scale-in event from destroying an
    # analysis the caller was told had been accepted.
    echo "ECS_ENABLE_SPOT_INSTANCE_DRAINING=true" >> /etc/ecs/ecs.config
    echo "ECS_CONTAINER_STOP_TIMEOUT=3m" >> /etc/ecs/ecs.config
    # Belt to the fix-perms init container's braces. This runs before the agent
    # starts, so a warm-pool instance resuming with the weights already on its
    # volume keeps the right ownership without waiting for a task placement.
    # It is NOT sufficient alone - see the worker task definition for why.
    mkdir -p /opt/labs/models/checkpoints
    chown -R 10001:10001 /opt/labs/models
  EOT
  )

  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.tags, { Name = "${var.name}-worker" })
  }
}

resource "aws_autoscaling_group" "worker" {
  name                = "${var.name}-worker"
  vpc_zone_identifier = var.private_subnet_ids
  min_size            = var.worker_min_size
  max_size            = var.worker_max_size
  desired_capacity    = var.worker_min_size

  launch_template {
    id      = aws_launch_template.worker.id
    version = "$Latest"
  }

  # Required by the capacity provider; without it ECS cannot manage the group.
  protect_from_scale_in = true

  # A worker is not in a target group, so ELB health is meaningless here.
  health_check_type = "EC2"

  # Long enough for the instance to join the cluster, pull a 2.5 GB image and
  # load 1.3 GB of weights before the ASG judges it.
  default_instance_warmup = 300

  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 50
      # Instances are replaced only after their tasks drain, so a deploy does
      # not discard in-flight analyses.
      instance_warmup = 300
    }
  }

  tag {
    key                 = "AmazonECSManaged"
    value               = ""
    propagate_at_launch = true
  }

  # THE single biggest latency fix in this file.
  #
  # A warm pool keeps instances STOPPED but fully initialised: image pulled,
  # weights on the EBS volume, everything cached. Resuming one takes ~30 s
  # instead of 3-5 minutes.
  #
  # The cost is EBS only. A stopped instance bills no compute, so a pool of 2
  # is 2 x 60 GB gp3 at ~$0.08/GB-month = ~$9.60/month for a 6-10x improvement
  # in burst response. That is the cheapest latency in the whole architecture.
  warm_pool {
    # Stopped, not Running. Running would bill full compute for idle instances
    # and defeat the point; Hibernated needs instance-store support this AMI
    # and instance family do not reliably have.
    pool_state = "Stopped"

    # Instances held ready beyond the live fleet.
    min_size = var.warm_pool_size

    # Total (live + warm) never exceeds the ASG max, so the pool shrinks as the
    # live fleet grows into it - exactly the behaviour you want, because a
    # fleet already at scale does not need a burst buffer.
    max_group_prepared_capacity = var.worker_max_size

    instance_reuse_policy {
      # Return scaled-in instances to the pool instead of terminating them. A
      # scale-in immediately followed by a scale-out is the common pattern for
      # bursty traffic, and this makes that round trip ~30 s instead of
      # minutes.
      reuse_on_scale_in = true
    }
  }

  lifecycle {
    ignore_changes = [desired_capacity]
  }
}

#
# A brand-new worker costs 3-5 minutes before it can take a message: instance
# launch, then a ~2.5 GB image pull, then ~1.3 GB of weights, then ~12 s of
# model load. During a burst that is the entire user-visible wait, and no
# amount of scaling policy tuning touches it - the capacity simply is not there
# yet.
resource "aws_ecs_capacity_provider" "worker" {
  name = "${var.name}-worker"
  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.worker.arn
    managed_termination_protection = "ENABLED"
    managed_scaling {
      status = "ENABLED"
      # 80, not 100. Target capacity is the percentage of the fleet ECS aims
      # to keep BUSY, so 100 means "no spare slot ever" - every new task has
      # to wait for an instance. 80 keeps roughly one instance of headroom, so
      # the next task usually starts immediately rather than after a launch.
      #
      # The 20% is not waste: it is the difference between a task starting in
      # seconds and starting in minutes, on a workload where a task is 30-70 s
      # of user-visible latency.
      target_capacity           = 80
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 10
    }
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = [aws_ecs_capacity_provider.worker.name, "FARGATE"]
  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name}-worker"
  requires_compatibilities = ["EC2"]
  network_mode             = "awsvpc"
  # Sized to the host, not to a constant: a c7g.2xlarge fits two 4096/8192
  # tasks, an m7i-flex.large fits exactly one at 2048/6144. Overshooting the
  # host fails as a task stuck in PROVISIONING with no placement, which reads
  # like a capacity problem rather than a sizing one.
  cpu                = var.worker_task_cpu
  memory             = var.worker_task_memory
  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.worker.arn

  container_definitions = jsonencode([
    # ECS creates host-path volume directories as root. The worker runs as uid
    # 10001, so without this it cannot write the checkpoint cache into /models,
    # falls back to downloading from HuggingFace, and dies because
    # LABS_OFFLINE=true forbids exactly that.
    #
    # The equivalent chown in the launch template's user_data is NOT sufficient
    # on its own: it races the ECS agent, which recreates the bind-mount source
    # as root when it places the task. An init container cannot race, because
    # `dependsOn: SUCCESS` makes the worker wait for its exit code.
    {
      name      = "fix-perms"
      image     = var.busybox_image
      essential = false
      user      = "0"
      command   = ["sh", "-c", "chown -R 10001:10001 /models && chmod 755 /models"]
      mountPoints = [{
        sourceVolume  = "models"
        containerPath = "/models"
        readOnly      = false
      }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.this.name
          awslogs-region        = data.aws_region.current.name
          awslogs-stream-prefix = "fix-perms"
        }
      }
    },
    {
      name      = "worker"
      image     = "${local.ecr}-worker:${var.image_tag}"
      essential = true
      dependsOn = [{ containerName = "fix-perms", condition = "SUCCESS" }]
      environment = concat(local.common_env, [
        # ONE analysis per container, enforced by the worker loop itself rather
        # than by a setting: the pipeline is CPU-bound, so two concurrent runs
        # each go at half speed for identical throughput. Capacity comes from
        # more containers; the variable below sizes the one container.
        #
        # Physical cores, not vCPUs. Graviton has no SMT so these are equal
        # there, which is another reason to prefer it for this workload - on
        # x86 the right value is half the vCPU count and getting it wrong costs
        # throughput to cache contention.
        { name = "LABS_TORCH_THREADS", value = tostring(var.worker_torch_threads) },
        { name = "OMP_NUM_THREADS", value = tostring(var.worker_torch_threads) },
        { name = "LABS_EAGER_LOAD", value = "true" },
      ])
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.this.name
          awslogs-region        = data.aws_region.current.name
          awslogs-stream-prefix = "worker"
        }
      }
      secrets = local.mongo_secret
      mountPoints = [{
        sourceVolume  = "models"
        containerPath = "/models"
        readOnly      = false
      }]
    }
  ])

  # Host path, not an EBS volume per task. The weights survive task
  # replacement on the same instance, so only a genuinely new instance pays
  # the ~1.3 GB fetch - this is the main reason the workers are EC2 rather
  # than Fargate.
  volume {
    name      = "models"
    host_path = "/opt/labs/models"
  }
  tags = local.tags
}

resource "aws_ecs_service" "worker" {
  name            = "${var.name}-worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 1

  # Base on EC2, overflow to Fargate.
  #
  # `base` is a COUNT, `weight` is a RATIO applied to everything above it. So
  # this reads: put the first `worker_base_tasks` on EC2 - where the weights
  # are already on the host volume and the compute is cheap - and split
  # anything beyond that with Fargate, which needs no instance launch and can
  # therefore absorb a spike the warm pool has already been drained by.
  #
  # This is the third line of defence, behind the warm pool and the headroom
  # above. Each costs more than the last and responds faster than the last,
  # which is the right ordering.
  dynamic "capacity_provider_strategy" {
    for_each = var.burst_to_fargate ? [1] : []
    content {
      capacity_provider = "FARGATE"
      weight            = 1
      base              = 0
    }
  }

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.worker.name
    # Weighted 3:1 against Fargate above the base, so the steady state stays
    # overwhelmingly on the cheap fleet and Fargate only carries the tail.
    weight = var.burst_to_fargate ? 3 : 1
    base   = var.worker_base_tasks
  }

  network_configuration {
    subnets         = var.private_subnet_ids
    security_groups = [aws_security_group.tasks.id]
  }

  # 100/0 rather than the default 200/100. A worker task is 8 GiB and pins 4
  # cores, so allowing double during a deploy would demand an entire extra
  # instance per running task purely to roll out.
  deployment_maximum_percent         = 100
  deployment_minimum_healthy_percent = 0

  lifecycle {
    ignore_changes = [desired_count]
  }
  tags = local.tags
}

# Fargate cannot mount a host path, so the burst variant carries no models
# volume and re-fetches the weights per task. That is the trade being made
# deliberately: ~12 s of extra startup in exchange for not waiting on an
# instance launch at all. It is the right side of that trade only during a
# spike, which is why nothing runs here at steady state.
resource "aws_ecs_task_definition" "worker_fargate" {
  count                    = var.burst_to_fargate ? 1 : 0
  family                   = "${var.name}-worker-fargate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 4096
  memory                   = 8192
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.worker.arn

  # 21 GB: the image is ~2.5 GB and the weights ~1.4 GB, and the 20 GB default
  # leaves no room for a staged upload on top of both.
  ephemeral_storage { size_in_gib = 21 }

  container_definitions = jsonencode([{
    name      = "worker"
    image     = "${local.ecr}-worker:${var.image_tag}"
    essential = true
    environment = concat(local.common_env, [
      { name = "LABS_TORCH_THREADS", value = "4" },
      { name = "OMP_NUM_THREADS", value = "4" },
      { name = "LABS_EAGER_LOAD", value = "true" },
      # No host volume here, so the weights must come from the S3 mirror
      # rather than a warm cache. LABS_MODELS_S3_URI is set in common_env by
      # the operator; without it this task cannot start, which is why
      # burst_to_fargate is opt-out rather than unconditional.
      { name = "LABS_MODELS_DIR", value = "/tmp/models" },
      { name = "LABS_CKPT_DIR", value = "/tmp/models/checkpoints" },
    ])
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.this.name
        awslogs-region        = data.aws_region.current.name
        awslogs-stream-prefix = "worker-burst"
      }
    }
    secrets = local.mongo_secret
  }])
  tags = local.tags
}

###############################################################################
# Worker autoscaling: backlog per worker
###############################################################################
resource "aws_appautoscaling_target" "worker" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.worker.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  # A warm floor, not zero. A cold worker pays an image pull plus ~10 s of
  # model load, so the first submission after an idle period would wait
  # minutes. One warm worker is ~$210/month and buys a predictable p50.
  min_capacity = var.worker_min_tasks

  # Derived, never a literal. A worker task reserves `worker_task_cpu` and an
  # instance offers one instance-worth of CPU, so the fleet can only ever run
  # `worker_max_size` tasks. A larger ceiling here does not buy throughput: ECS
  # happily raises desiredCount to it, the extra tasks find no instance with
  # room, and they sit PENDING until the burst ends. A hardcoded 20 against a
  # 4-instance fleet left 13 tasks PENDING for an entire benchmark and made
  # every autoscaling signal unreadable.
  max_capacity = var.worker_max_size
}

# Backlog PER WORKER, not raw queue depth. A target on depth alone cannot
# express "enough capacity": 10 messages is fine with 10 workers and a crisis
# with one, and target tracking needs a ratio to converge on.
resource "aws_cloudwatch_metric_alarm" "backlog_per_worker" {
  alarm_name          = "${var.name}-backlog-per-worker"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  # 0, not 2. The old threshold waited until three analyses were stacked on
  # one worker before adding capacity - which is 1.5 to 3 MINUTES of queue by
  # the time it fires, on top of the launch it then has to wait for.
  #
  # Scaling on ANY backlog is affordable here precisely because the warm pool
  # and the Fargate overflow made a scale-out cheap and fast. Over-reacting to
  # a single message costs one resumed instance for a few minutes; reacting
  # late costs every queued caller the full launch time.
  threshold           = 0
  datapoints_to_alarm = 1
  # Missing data is NOT breaching. SQS stops publishing when a queue has been
  # idle, and treating that silence as a backlog would scale the fleet out
  # every time traffic stopped.
  treat_missing_data = "notBreaching"
  alarm_description  = "Analyses are queued and not yet being worked on."

  metric_query {
    id          = "backlog"
    expression  = "IF(workers > 0, (visible + inflight) / workers, visible + inflight)"
    label       = "BacklogPerWorker"
    return_data = true
  }
  metric_query {
    id = "visible"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesVisible"
      period      = 60
      stat        = "Average"
      dimensions  = { QueueName = aws_sqs_queue.analyses.name }
    }
  }
  metric_query {
    id = "inflight"
    metric {
      # Counted deliberately. Visible alone under-reports exactly when it
      # matters: once every worker is busy the visible count falls toward zero
      # while the backlog is at its worst, so scaling on it alone would scale
      # IN during a pile-up.
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesNotVisible"
      period      = 60
      stat        = "Average"
      dimensions  = { QueueName = aws_sqs_queue.analyses.name }
    }
  }
  metric_query {
    id = "workers"
    metric {
      namespace   = "ECS/ContainerInsights"
      metric_name = "RunningTaskCount"
      period      = 60
      stat        = "Average"
      dimensions = {
        ClusterName = aws_ecs_cluster.this.name
        ServiceName = aws_ecs_service.worker.name
      }
    }
  }

  alarm_actions = [aws_appautoscaling_policy.worker_out.arn]
  tags          = local.tags
}

# Step scaling, not target tracking. The step sizes matter: a backlog of 50 on
# one worker is 25 minutes of waiting, and adding one task per minute would
# take half an hour to clear it.
resource "aws_appautoscaling_policy" "worker_out" {
  name               = "${var.name}-worker-out"
  policy_type        = "StepScaling"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension

  step_scaling_policy_configuration {
    adjustment_type = "ChangeInCapacity"
    # Counts instances still warming toward the target, so a burst does not
    # trigger a second round of scaling for capacity already on its way.
    metric_aggregation_type = "Average"
    # 30 s. The cooldown exists to stop a second scale-out before the first has
    # had any effect; with a warm pool the first one IS effective in ~30 s, so
    # a longer cooldown just delays the response to a still-growing spike.
    cooldown = 30

    # Bounds are measured from the THRESHOLD (0), so these read as absolute
    # backlog-per-worker figures.
    #
    # The first step is the important one: one queued analysis with no spare
    # worker adds a task immediately. With a warm pool behind it that task is
    # running in ~30 s, so a single unlucky caller waits seconds rather than
    # minutes.
    step_adjustment {
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 2
      scaling_adjustment          = 1
    }
    step_adjustment {
      metric_interval_lower_bound = 2
      metric_interval_upper_bound = 5
      scaling_adjustment          = 3
    }
    step_adjustment {
      metric_interval_lower_bound = 5
      metric_interval_upper_bound = 20
      scaling_adjustment          = 8
    }
    step_adjustment {
      metric_interval_lower_bound = 20
      scaling_adjustment          = 15
    }
  }
}

# Scale in on an EMPTY queue, and slowly. An idle worker costs ~$0.09/hr; a
# worker killed mid-analysis costs the caller their result and makes SQS
# redeliver the message after the visibility timeout, so the asymmetry in
# these thresholds is deliberate.
resource "aws_cloudwatch_metric_alarm" "queue_empty" {
  alarm_name          = "${var.name}-queue-empty"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 15
  threshold           = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Average"
  dimensions          = { QueueName = aws_sqs_queue.analyses.name }
  alarm_actions       = [aws_appautoscaling_policy.worker_in.arn]
  tags                = local.tags
}

resource "aws_appautoscaling_policy" "worker_in" {
  name               = "${var.name}-worker-in"
  policy_type        = "StepScaling"
  service_namespace  = aws_appautoscaling_target.worker.service_namespace
  resource_id        = aws_appautoscaling_target.worker.resource_id
  scalable_dimension = aws_appautoscaling_target.worker.scalable_dimension

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    metric_aggregation_type = "Average"
    cooldown                = 300
    step_adjustment {
      metric_interval_upper_bound = 0
      scaling_adjustment          = -1
    }
  }
}

###############################################################################
# Alarms worth paging on
###############################################################################

# The real user-facing SLO. Depth says how much work there is; this says how
# long the unluckiest caller has been waiting, which is the thing anyone
# actually complains about.
resource "aws_cloudwatch_metric_alarm" "age_of_oldest" {
  alarm_name          = "${var.name}-queue-age"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = 600
  metric_name         = "ApproximateAgeOfOldestMessage"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  dimensions          = { QueueName = aws_sqs_queue.analyses.name }
  alarm_description   = "Analyses have been queued over 10 minutes. Either the fleet is at max_capacity or workers are failing to start."
  tags                = local.tags
}

# Anything here failed three times. It is a file the pipeline cannot handle,
# and it needs a human, not a retry.
resource "aws_cloudwatch_metric_alarm" "dlq" {
  alarm_name          = "${var.name}-dlq"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  dimensions          = { QueueName = aws_sqs_queue.dlq.name }
  alarm_description   = "An analysis failed repeatedly and was parked."
  tags                = local.tags
}

###############################################################################
# Outputs
###############################################################################
output "alb_dns_name" { value = aws_lb.this.dns_name }

# The HTTPS front door. Null when enable_cloudfront is false, in which case
# callers reach the ALB directly - over plain HTTP unless certificate_arn is set.
output "https_url" {
  value = (var.enable_cloudfront
    ? "https://${aws_cloudfront_distribution.this[0].domain_name}"
  : (var.certificate_arn != "" ? "https://${aws_lb.this.dns_name}" : null))
  description = "Use this, not alb_dns_name. API keys should not cross the internet in cleartext."
}

output "origin_locked" {
  value       = local.lock_origin
  description = "False means the ALB still answers on plain HTTP for anyone who knows its hostname."
}
output "queue_url" { value = aws_sqs_queue.analyses.url }
output "audio_bucket" { value = aws_s3_bucket.audio.id }

# The DLQ is where a deterministically-broken file ends up after three
# attempts. An operator needs its URL to pull the message and reproduce the
# failure locally, so it is an output rather than something to go and find in
# the console mid-incident.
output "dlq_url" { value = aws_sqs_queue.dlq.url }

output "log_group" { value = aws_cloudwatch_log_group.this.name }

output "cluster_name" { value = aws_ecs_cluster.this.name }

output "worker_asg_name" { value = aws_autoscaling_group.worker.name }

output "ecr_repositories" {
  value = {
    screen = "${local.ecr}-screen"
    worker = "${local.ecr}-worker"
  }
  description = "Build for ARM64: docker buildx build --platform linux/arm64 --target screen -t <screen>:tag --push ."
}
