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
# ARM64 IS ALSO NOT A CHOICE. The workers are Graviton (c7g) and the Fargate
# tasks are ARM. An x86 image on them fails with `exec format error`, which
# names neither the architecture nor the image.

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

for target in screen worker; do
  image="$ECR_HOST/$STACK_NAME-$target:$IMAGE_TAG"
  step "Building $target for linux/arm64"
  info "$image"
  docker buildx build \
    --platform linux/arm64 \
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
  if [[ "$arch" == *arm64* ]]; then
    ok "$STACK_NAME-$target:$IMAGE_TAG is arm64"
  else
    warn "$STACK_NAME-$target:$IMAGE_TAG reports architecture: $arch"
    warn "If this is not arm64 the tasks will fail with 'exec format error'."
  fi
done

tfvar_set "$STACK_TFVARS" image_tag "\"$IMAGE_TAG\""
ok "wrote image_tag to $(basename "$STACK_TFVARS")"
