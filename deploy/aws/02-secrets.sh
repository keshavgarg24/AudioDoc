#!/usr/bin/env bash
# Put the secrets into Secrets Manager and record only their ARNs.
#
# No secret VALUE ever reaches Terraform. A value passed as a Terraform
# variable is written to state in plaintext, and state is readable by anyone
# who can run `plan` - so the value goes straight from here into Secrets
# Manager, and Terraform only learns the ARN. The ECS agent resolves it at
# container start.
#
# Usage:
#   ./02-secrets.sh 'mongodb+srv://user:pass@cluster.xxxxx.mongodb.net/labs'
#
# or set MONGO_URI in the environment. Safe to re-run: an existing secret is
# updated in place, which keeps the ARN stable so nothing has to be reapplied.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

MONGO_URI="${1:-${MONGO_URI:-}}"

step "Secrets for $STACK_NAME"

# Create, or update if it already exists, and print the ARN either way.
put_secret() {
  local name="$1" value="$2" arn
  if arn=$(aws secretsmanager describe-secret --secret-id "$name" \
            --query ARN --output text 2>/dev/null); then
    aws secretsmanager put-secret-value --secret-id "$name" \
      --secret-string "$value" >/dev/null
    reuse "$name (new version)" >&2
  else
    arn=$(aws secretsmanager create-secret --name "$name" \
      --secret-string "$value" --query ARN --output text)
    ok "$name" >&2
  fi
  printf '%s' "$arn"
}

# ------------------------------------------------------------ mongo (required)
if [[ -z "$MONGO_URI" ]]; then
  cat >&2 <<'EOF'

    A MongoDB connection string is required.

    MongoDB carries job state, every stored report and the SHA-256 index that
    makes deduplication work. Without it the service runs in memory: a job
    accepted by one container is invisible to every other one, and identical
    audio is re-analysed at full cost every time.

    Create a free M0 or a production M10 cluster at
    https://cloud.mongodb.com, add a database user with readWrite on the
    `labs` database, then re-run:

        ./02-secrets.sh 'mongodb+srv://USER:PASS@cluster.xxxxx.mongodb.net/labs'

    In Atlas -> Network Access, allowlist the NAT gateway IP that
    01-network.sh printed. Do not use 0.0.0.0/0.

EOF
  die "no MongoDB URI supplied"
fi

[[ "$MONGO_URI" == mongodb* ]] || die "that does not look like a MongoDB URI: ${MONGO_URI:0:20}..."

MONGO_ARN=$(put_secret "$STACK_NAME/mongo-uri" "$MONGO_URI")

# --------------------------------------------------------- webhook (generated)
# Signs outbound webhook callbacks so a receiver can verify they came from us.
# Generated rather than asked for: it is a shared secret with no other system,
# so there is nothing for a human to choose and every reason not to invent one.
if aws secretsmanager describe-secret --secret-id "$STACK_NAME/webhook-secret" >/dev/null 2>&1; then
  WEBHOOK_ARN=$(aws secretsmanager describe-secret --secret-id "$STACK_NAME/webhook-secret" \
    --query ARN --output text)
  reuse "$STACK_NAME/webhook-secret (kept existing value)"
else
  WEBHOOK_ARN=$(put_secret "$STACK_NAME/webhook-secret" "$(openssl rand -hex 32)")
fi

tfvar_set "$SECRETS_TFVARS" mongo_uri_secret_arn "\"$MONGO_ARN\""
tfvar_set "$SECRETS_TFVARS" webhook_secret_arn   "\"$WEBHOOK_ARN\""

# ------------------------------------------------------------ ACR (optional)
# ACRCloud is catalogue verification - a positive match against a real
# recording is stronger evidence than any statistical inference, so it
# outranks the local model when present. Entirely optional; the service runs
# without it and simply never returns a verification block.
if [[ -n "${ACR_BEARER_TOKEN:-}" && -n "${ACR_CONTAINER_ID:-}" ]]; then
  ACR_TOKEN_ARN=$(put_secret "$STACK_NAME/acr-bearer-token" "$ACR_BEARER_TOKEN")
  ACR_CID_ARN=$(put_secret "$STACK_NAME/acr-container-id" "$ACR_CONTAINER_ID")
  tfvar_set "$SECRETS_TFVARS" acr_bearer_token_arn "\"$ACR_TOKEN_ARN\""
  tfvar_set "$SECRETS_TFVARS" acr_container_id_arn "\"$ACR_CID_ARN\""
else
  info "ACRCloud not configured (set ACR_BEARER_TOKEN and ACR_CONTAINER_ID to enable)"
fi

ok "wrote $(basename "$SECRETS_TFVARS") - ARNs only, no values"
