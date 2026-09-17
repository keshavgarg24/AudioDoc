#!/usr/bin/env bash
# Create the S3 bucket and DynamoDB table that hold Terraform state, then write
# the backend config the other scripts init against.
#
# WHY THIS RUNS FIRST AND SEPARATELY
# ----------------------------------
# Terraform cannot create its own backend: the bucket would have to exist
# before the state that records the bucket exists. So this one piece is
# bootstrapped with the AWS CLI and never appears in main.tf.
#
# WHAT GOES WRONG WITHOUT IT
# --------------------------
# Local state means the only record of a running stack is one file in one
# directory on one machine. Lose the directory and the infrastructure keeps
# running and keeps billing, but Terraform no longer knows it exists - every
# resource then has to be imported by hand or hunted down in the console.
#
# COST
# ----
# Inside the always-free tier at this size: S3 charges for a few KB, and the
# lock table is PAY_PER_REQUEST with a handful of writes per apply.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

step "Terraform state backend"

require_cmd aws "brew install awscli"

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="${TF_STATE_BUCKET:-${STACK_NAME}-tfstate-${ACCOUNT}}"
TABLE="${TF_LOCK_TABLE:-${STACK_NAME}-tflock}"
KEY="${TF_STATE_KEY:-${STACK_NAME}/terraform.tfstate}"

# ------------------------------------------------------------------ bucket --
if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  reuse "s3://$BUCKET"
else
  info "creating s3://$BUCKET"
  # us-east-1 rejects a LocationConstraint; every other region requires one.
  if [[ "$AWS_REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$AWS_REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$AWS_REGION" \
      --create-bucket-configuration "LocationConstraint=$AWS_REGION" >/dev/null
  fi

  # Versioning is the actual disaster recovery here: a corrupted or truncated
  # state push can be rolled back to the previous object version.
  aws s3api put-bucket-versioning --bucket "$BUCKET" \
    --versioning-configuration Status=Enabled

  aws s3api put-bucket-encryption --bucket "$BUCKET" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

  # State contains resource IDs, ARNs and every non-secret attribute of the
  # stack. It is never public.
  aws s3api put-public-access-block --bucket "$BUCKET" \
    --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

  ok "bucket created, versioned, encrypted, private"
fi

# ------------------------------------------------------------- lock table --
if aws dynamodb describe-table --table-name "$TABLE" --region "$AWS_REGION" \
  >/dev/null 2>&1; then
  reuse "dynamodb://$TABLE"
else
  info "creating lock table $TABLE"
  aws dynamodb create-table \
    --table-name "$TABLE" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "$AWS_REGION" >/dev/null
  aws dynamodb wait table-exists --table-name "$TABLE" --region "$AWS_REGION"
  ok "lock table ready"
fi

# ------------------------------------------------------------ backend file --
# Not committed: the bucket name embeds the account id.
cat > "$AWS_DIR/backend.hcl" <<EOF
bucket         = "$BUCKET"
key            = "$KEY"
region         = "$AWS_REGION"
dynamodb_table = "$TABLE"
encrypt        = true
EOF

ok "wrote backend.hcl"
printf '\n'
info "If this stack already has LOCAL state, migrate it once:"
printf '      cd %s\n' "$AWS_DIR"
printf '      terraform init -migrate-state -backend-config=backend.hcl\n\n'
info "Otherwise 05-apply.sh picks it up automatically."
