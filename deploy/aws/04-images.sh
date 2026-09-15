#!/usr/bin/env bash
# Build both container images and push them to ECR.
#
# THE REPOSITORY NAMES ARE NOT FREE CHOICES. Terraform builds the image
# reference as "${var.name}-screen" and "${var.name}-worker", so with
# STACK_NAME=labs-test the repositories must be labs-test-screen and
# labs-test-worker. Creating them under any other name produces a deploy that
# applies cleanly and then fails to pull, because ECS looks for a repository
# that does not exist.
#
# THE ARCHITECTURES ARE NOT FREE CHOICES EITHER.
#   screen  -> linux/arm64  (runs on Fargate ARM64)
#   worker  -> linux/amd64  (runs on EC2 x86, e.g. m7i-flex.large)
# Mismatched architecture fails with 'exec format error'.

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

# screen runs on Fargate ARM64; worker runs on EC2 x86_64 (m7i-flex family).
# They are built for different platforms because they run on different hosts.
declare -A TARGET_PLATFORM=( [screen]=linux/arm64 [worker]=linux/amd64 )
declare -A TARGET_ARCH=(     [screen]=arm64       [worker]=amd64 )

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
  want="${TARGET_ARCH[$target]}"
  arch=$(aws ecr batch-get-image --repository-name "$STACK_NAME-$target" \
    --image-ids "imageTag=$IMAGE_TAG" \
    --query 'images[0].imageManifest' --output text 2>/dev/null \
    | python3 -c 'import sys,json
m=json.load(sys.stdin)
if "manifests" in m:
    print(",".join(x["platform"]["architecture"] for x in m["manifests"]))
else:
    print("single-manifest")' 2>/dev/null || echo "unknown")
  if [[ "$arch" == *"$want"* ]]; then
    ok "$STACK_NAME-$target:$IMAGE_TAG is $want"
  else
    warn "$STACK_NAME-$target:$IMAGE_TAG reports architecture: $arch (expected $want)"
    warn "Tasks will fail with 'exec format error' if the architecture is wrong."
  fi
done

tfvar_set "$STACK_TFVARS" image_tag "\"$IMAGE_TAG\""
ok "wrote image_tag to $(basename "$STACK_TFVARS")"
