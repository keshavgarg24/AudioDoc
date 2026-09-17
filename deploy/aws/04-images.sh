#!/usr/bin/env bash
# Build both container images for ARM64 and push them to ECR.
#
# THE REPOSITORY NAMES ARE NOT FREE CHOICES. Terraform builds the image
# reference as "${var.name}-screen" and "${var.name}-worker", so with
# STACK_NAME=labs-test the repositories must be labs-test-screen and
# labs-test-worker. Creating them under any other name produces a deploy that
# applies cleanly and then fails to pull, because ECS looks for a repository
# that does not exist.
#
# THE ARCHITECTURES ARE NOT FREE CHOICES EITHER.
#   screen  -> linux/arm64, always: it runs on Fargate ARM64.
#   worker  -> derived from WORKER_INSTANCE_TYPE, because the worker fleet can
#              be Graviton (c7g) or x86 (m7i-flex) depending on what the region
#              has capacity for.
# A mismatch fails with `exec format error`, which names neither the
# architecture nor the image.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ACCOUNT_ID=$(aws_account_id)
ECR_HOST="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

step "Images for $STACK_NAME, tag $IMAGE_TAG"

require_cmd docker "Install Docker Desktop and start it."
docker info >/dev/null 2>&1 || die "Docker is installed but not running. Start Docker Desktop."
docker buildx version >/dev/null 2>&1 || die "docker buildx is missing. Update Docker Desktop."

# Two images, and they are genuinely different rather than tags of one thing:
#
#   screen  ~350 MB  numpy/scipy/librosa/onnxruntime, no torch
#   worker  ~2.5 GB  the above plus torch, transformers, lightning
#
# The api fleet deliberately runs the SCREEN image. It validates, stores and
# enqueues but never analyses, so shipping torch to it would be 2.1 GB of cold
# start for code that never executes.
for target in screen worker; do
  repo="$STACK_NAME-$target"
  if aws ecr describe-repositories --repository-names "$repo" >/dev/null 2>&1; then
    reuse "ECR repository $repo"
  else
    aws ecr create-repository --repository-name "$repo" \
      --image-scanning-configuration scanOnPush=true >/dev/null
    ok "ECR repository $repo"
  fi
done

info "authenticating to $ECR_HOST"
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ECR_HOST" >/dev/null
ok "logged in"

# A builder that can actually produce ARM64. The default `docker` driver on
# some hosts cannot, and fails only at the end of a long build.
if ! docker buildx inspect labs-builder >/dev/null 2>&1; then
  docker buildx create --name labs-builder --use >/dev/null
  ok "created buildx builder"
else
  docker buildx use labs-builder
  reuse "buildx builder"
fi

# The two images run on different hosts, so they are built for different
# architectures:
#
#   screen  -> Fargate ARM64, always
#   worker  -> whatever WORKER_INSTANCE_TYPE is, which may be either
#
# The worker's architecture is DERIVED from the instance type rather than set
# by hand, using the same rule main.tf uses to pick the ECS AMI: Graviton
# families end their size prefix in `g` (c7g, m7g, r8g, t4g), everything else
# is x86_64. Hardcoding it in two places is how you get an image and an AMI
# that disagree, and that failure surfaces only as `exec format error` on a
# task that has already pulled 2 GB.
WORKER_FAMILY="${WORKER_INSTANCE_TYPE%%.*}"
if [[ "$WORKER_FAMILY" =~ g[a-z]*$ ]]; then
  WORKER_PLATFORM="linux/arm64"; WORKER_ARCH="arm64"
else
  WORKER_PLATFORM="linux/amd64"; WORKER_ARCH="amd64"
fi
info "worker instance type ${WORKER_INSTANCE_TYPE:-unset} -> $WORKER_PLATFORM"

declare -A TARGET_PLATFORM=( [screen]=linux/arm64 [worker]="$WORKER_PLATFORM" )
declare -A TARGET_ARCH=(     [screen]=arm64       [worker]="$WORKER_ARCH" )

for target in screen worker; do
  image="$ECR_HOST/$STACK_NAME-$target:$IMAGE_TAG"
  platform="${TARGET_PLATFORM[$target]}"
  step "Building $target for $platform"
  info "$image"
  docker buildx build \
    --platform "$platform" \
    --target "$target" \
    --tag "$image" \
    --push \
    "$REPO_ROOT"
  ok "pushed $target"
done

# Verify from the registry rather than trusting the push. A push that
# succeeded locally but produced the wrong architecture is exactly the failure
# this catches, and it is far cheaper to catch here than as a PENDING task.
step "Verifying what landed in ECR"
for target in screen worker; do
  arch=$(aws ecr batch-get-image --repository-name "$STACK_NAME-$target" \
    --image-ids "imageTag=$IMAGE_TAG" \
    --query 'images[0].imageManifest' --output text 2>/dev/null \
    | python3 -c 'import sys,json
m=json.load(sys.stdin)
if "manifests" in m:
    print(",".join(x["platform"]["architecture"] for x in m["manifests"]))
else:
    print("single-manifest")' 2>/dev/null || echo "unknown")
  want="${TARGET_ARCH[$target]}"
  if [[ "$arch" == *"$want"* ]]; then
    ok "$STACK_NAME-$target:$IMAGE_TAG is $want"
  else
    warn "$STACK_NAME-$target:$IMAGE_TAG reports architecture: $arch (expected $want)"
    warn "Tasks will fail with 'exec format error' if the architecture is wrong."
  fi
done

tfvar_set "$STACK_TFVARS" image_tag "\"$IMAGE_TAG\""
ok "wrote image_tag to $(basename "$STACK_TFVARS")"
