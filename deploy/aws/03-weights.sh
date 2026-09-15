#!/usr/bin/env bash
# Get the Level-2 checkpoints into S3, and pin what was uploaded.
#
# THE THING TO KNOW BEFORE YOU START
# ----------------------------------
# The Level-2 checkpoints are NOT in the git repository and cannot be. Stage-1
# is 1.2 GB and GitHub rejects any file over 100 MB, so a fresh clone contains
# the code and the Level-1 ONNX weights (1.2 MB, committed as package data)
# but nothing for the deep tier.
#
# So this script finds them in one of two places:
#
#   1. ./checkpoints/ on this machine, if you already have them
#   2. Hugging Face, downloaded once and then mirrored to your own bucket
#
# After the first run they live in YOUR S3 bucket and the origin stops
# mattering - which is the point. LABS_OFFLINE=true is set in production, so
# a worker will never reach out to a third party mid-deploy; it reads your
# bucket or it fails loudly.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ACCOUNT_ID=$(aws_account_id)
BUCKET="${WEIGHTS_BUCKET:-$STACK_NAME-weights-$ACCOUNT_ID}"
CKPT_DIR="$REPO_ROOT/checkpoints"
S3_PREFIX="models/checkpoints"

step "Level-2 checkpoints -> s3://$BUCKET/$S3_PREFIX"

# ------------------------------------------------------------------ source --
need_download=0
for f in Stage-1.ckpt Stage-2.ckpt; do
  [[ -f "$CKPT_DIR/$f" ]] || need_download=1
done

if [[ "$need_download" == "1" ]]; then
  # Maybe they are already in S3 from an earlier run on another machine, in
  # which case there is nothing to do and no reason to pull 1.3 GB.
  if aws s3 ls "s3://$BUCKET/$S3_PREFIX/Stage-1.ckpt" >/dev/null 2>&1 \
  && aws s3 ls "s3://$BUCKET/$S3_PREFIX/Stage-2.ckpt" >/dev/null 2>&1; then
    reuse "both checkpoints already in s3://$BUCKET/$S3_PREFIX"
    tfvar_set "$WEIGHTS_TFVARS" weights_s3_uri "\"s3://$BUCKET/models\""
    warn "digests not pinned: the local files are absent so they cannot be hashed."
    warn "To pin them, fetch the checkpoints locally and re-run this script."
    ok "wrote $(basename "$WEIGHTS_TFVARS")"
    exit 0
  fi

  step "Checkpoints not found locally - downloading from Hugging Face"
  info "Stage-1 is 1.2 GB. This is a one-off; afterwards they come from your bucket."

  python3 - "$CKPT_DIR" <<'PY' || die "download failed.

    Install the client and retry:   pip install huggingface_hub
    Or copy the files in by hand:   checkpoints/Stage-1.ckpt
                                    checkpoints/Stage-2.ckpt"
import sys, os
try:
    from huggingface_hub import hf_hub_download
except ImportError:
    sys.exit("huggingface_hub is not installed (pip install huggingface_hub)")

dest = sys.argv[1]
os.makedirs(dest, exist_ok=True)
repo = os.environ.get("LABS_CKPT_REPO",
                      "teamup-tech/FST-AI-Music-Detection-checkpoints")
rev = os.environ.get("LABS_CKPT_REVISION") or None
for name in ("Stage-1.ckpt", "Stage-2.ckpt"):
    print(f"  fetching {name} from {repo}...", flush=True)
    hf_hub_download(repo_id=repo, filename=name, revision=rev, local_dir=dest)
PY
  ok "downloaded to $CKPT_DIR"
else
  reuse "checkpoints found in $CKPT_DIR"
fi

# ------------------------------------------------------------------ bucket --
if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  reuse "bucket $BUCKET"
else
  # us-east-1 is the one region where create-bucket must NOT be given a
  # location constraint. Every other region requires it.
  if [[ "$AWS_REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$BUCKET" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" \
      --create-bucket-configuration "LocationConstraint=$AWS_REGION" >/dev/null
  fi
  ok "bucket $BUCKET"
fi

# Model weights are the thing an attacker would most like to swap: a modified
# checkpoint produces confident wrong verdicts rather than an error.
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled
ok "public access blocked, versioning on"

# ------------------------------------------------------------------ upload --
for f in Stage-1.ckpt Stage-2.ckpt; do
  local_size=$(wc -c < "$CKPT_DIR/$f" | tr -d ' ')
  remote_size=$(aws s3api head-object --bucket "$BUCKET" --key "$S3_PREFIX/$f" \
    --query ContentLength --output text 2>/dev/null || echo "")
  if [[ "$remote_size" == "$local_size" ]]; then
    reuse "$f already uploaded ($local_size bytes)"
  else
    info "uploading $f ($(( local_size / 1024 / 1024 )) MB)..."
    aws s3 cp "$CKPT_DIR/$f" "s3://$BUCKET/$S3_PREFIX/$f"
    ok "$f"
  fi
done

# ----------------------------------------------------------------- digests --
# A revision pins what you ASKED FOR; a digest pins what you GOT. They catch
# different failures - a revision cannot detect a truncated upload, a stale
# file on a reused volume, or a bucket someone else can write to. The failure
# being defended against is not a crash, it is a different model answering
# confidently in your name.
step "Pinning digests"
SHA1=$(shasum -a 256 "$CKPT_DIR/Stage-1.ckpt" | cut -d' ' -f1)
SHA2=$(shasum -a 256 "$CKPT_DIR/Stage-2.ckpt" | cut -d' ' -f1)
info "Stage-1 ${SHA1:0:16}..."
info "Stage-2 ${SHA2:0:16}..."

tfvar_set "$WEIGHTS_TFVARS" weights_s3_uri "\"s3://$BUCKET/models\""

# Not a tfvars entry: these are consumed by the containers as env vars, and
# the task definition reads them from stack.auto.tfvars via Terraform only if
# the module exposes them. Recorded here so 05-apply.sh can pass them and so
# a human can see what was pinned.
cat > "$AWS_DIR/.weights-digests" <<EOF
LABS_STAGE1_SHA256=$SHA1
LABS_STAGE2_SHA256=$SHA2
EOF

ok "wrote $(basename "$WEIGHTS_TFVARS") and .weights-digests"
