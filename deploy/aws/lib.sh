#!/usr/bin/env bash
# shellcheck disable=SC2034
#   Several variables here are defined for the scripts that SOURCE this file.
#   Shellcheck lints lib.sh in isolation and cannot see those uses.
#
# Shared helpers for the bootstrap scripts. Sourced, never run directly.
#
# Every script that sources this is IDEMPOTENT: it looks for what it would
# create before creating it, and re-running after a failure half way through
# picks up where it stopped rather than making a second copy of everything.
# That property is the whole reason these are scripts instead of a list of
# commands in a document - a runbook that cannot be safely re-run turns a
# transient AWS error into a manual cleanup job.

set -euo pipefail

# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
_c_reset=$'\033[0m'; _c_red=$'\033[31m'; _c_green=$'\033[32m'
_c_yellow=$'\033[33m'; _c_blue=$'\033[34m'; _c_bold=$'\033[1m'

step()  { printf '\n%s==>%s %s%s%s\n' "$_c_blue" "$_c_reset" "$_c_bold" "$*" "$_c_reset"; }
info()  { printf '    %s\n' "$*"; }
ok()    { printf '    %s[ok]%s %s\n' "$_c_green" "$_c_reset" "$*"; }
warn()  { printf '    %s[warn]%s %s\n' "$_c_yellow" "$_c_reset" "$*" >&2; }
die()   { printf '\n%s[error]%s %s\n\n' "$_c_red" "$_c_reset" "$*" >&2; exit 1; }

# Something already existed, so we used it instead of making another.
reuse() { printf '    %s[reuse]%s %s\n' "$_c_yellow" "$_c_reset" "$*"; }

# --------------------------------------------------------------------------
# Paths and configuration
# --------------------------------------------------------------------------
AWS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$AWS_DIR/../.." && pwd)"

# Written by the scripts, read by Terraform. `.auto.tfvars` is loaded
# automatically by every terraform command in this directory, so there is no
# -var-file to remember and no way to apply with a stale one by accident.
NETWORK_TFVARS="$AWS_DIR/network.auto.tfvars"
SECRETS_TFVARS="$AWS_DIR/secrets.auto.tfvars"
WEIGHTS_TFVARS="$AWS_DIR/weights.auto.tfvars"
STACK_TFVARS="$AWS_DIR/stack.auto.tfvars"

# Read a value back out of a tfvars file. Prints nothing if absent.
# Strips surrounding quotes and ignores commented-out lines.
tfvar_get() {
  local file="$1" key="$2"
  [[ -f "$file" ]] || return 0
  grep -E "^[[:space:]]*${key}[[:space:]]*=" "$file" 2>/dev/null \
    | tail -1 | sed -E 's/^[^=]*=[[:space:]]*//; s/^"//; s/"[[:space:]]*$//'
}

# Everything is namespaced by this so two stacks can share an account.
#
# These three MUST agree with what Terraform actually deployed, so they are
# read from stack.auto.tfvars rather than hardcoded. They used to default to
# us-east-1 and v1, which silently broke every script in this directory once
# the stack moved to ap-south-1 and v2: exporting AWS_REGION overrides the
# CLI's own configured region, so `aws secretsmanager get-secret-value` looked
# in an empty region and reported the secret as missing rather than as
# misaddressed. An explicit environment variable still wins, for the case
# where you are genuinely targeting a second stack.
export STACK_NAME="${STACK_NAME:-$(tfvar_get "$STACK_TFVARS" name)}"
export STACK_NAME="${STACK_NAME:-labs-test}"
export AWS_REGION="${AWS_REGION:-$(tfvar_get "$STACK_TFVARS" region)}"
export AWS_REGION="${AWS_REGION:-ap-south-1}"
export IMAGE_TAG="${IMAGE_TAG:-$(tfvar_get "$STACK_TFVARS" image_tag)}"
export IMAGE_TAG="${IMAGE_TAG:-v1}"

export WORKER_INSTANCE_TYPE="${WORKER_INSTANCE_TYPE:-$(tfvar_get "$STACK_TFVARS" worker_instance_type)}"
export WORKER_INSTANCE_TYPE="${WORKER_INSTANCE_TYPE:-c7g.2xlarge}"

# The worker image is built for the architecture of the instance it will run
# on, which is NOT necessarily the architecture of the laptop running these
# scripts. Any `docker run` of the worker image here therefore needs an
# explicit --platform, or Docker resolves the manifest against the host and
# fails with "no matching manifest for linux/arm64/v8" on an Apple Silicon
# Mac driving an x86_64 fleet.
#
# Graviton families end in `g` (c7g, m7g, r8g, and the `gd`/`gn` variants);
# everything else is x86_64. Same rule as 04-images.sh, kept in agreement.
worker_platform() {
  local family="${WORKER_INSTANCE_TYPE%%.*}"
  if [[ "$family" =~ g[a-z]*$ ]]; then echo "linux/arm64"; else echo "linux/amd64"; fi
}

# Mint an API key by running the CLI as a one-off ECS task. Prints the key.
#
# WHY NOT `docker run` LOCALLY
# ---------------------------
# The obvious implementation - pull the image and run the CLI on the operator's
# laptop - cannot work against a locked-down database. Keys live in MongoDB, and
# Atlas is firewalled to the VPC's NAT egress IP. A container on a laptop still
# dials Atlas *from the laptop's IP*, so it is refused at the TLS layer with a
# bare `TLSV1_ALERT_INTERNAL_ERROR` that looks like a certificate bug rather
# than a firewall rule. Allowlisting each operator's IP would work but means
# editing the production database's firewall for a routine operation, and
# home IPs change.
#
# Running it as a task puts the CLI inside the VPC, where the allowlist already
# applies. It also avoids pulling a 2.5 GB image to run a function that needs
# only pymongo. The screen task definition is used rather than the worker's: it
# carries the same LABS_MONGO_URI secret, is Fargate-compatible so it needs no
# spare room on the EC2 fleet, and is a quarter of the size.
issue_api_key() {
  local keyname="${1:-key-$(date +%Y%m%d-%H%M%S)}"
  local scopes="${2:-screen,analyze,deep,read}"

  local svc_net
  svc_net=$(aws ecs describe-services --cluster "$STACK_NAME" \
    --services "$STACK_NAME-screen" --region "$AWS_REGION" \
    --query 'services[0].networkConfiguration.awsvpcConfiguration' --output json 2>/dev/null)
  [[ -n "$svc_net" && "$svc_net" != "null" ]] \
    || { echo "could not read the screen service's network config" >&2; return 1; }

  local subnets sgs
  subnets=$(printf '%s' "$svc_net" | python3 -c 'import sys,json;print(",".join(json.load(sys.stdin)["subnets"]))')
  sgs=$(printf '%s' "$svc_net" | python3 -c 'import sys,json;print(",".join(json.load(sys.stdin)["securityGroups"]))')

  local task_arn
  task_arn=$(aws ecs run-task --cluster "$STACK_NAME" --region "$AWS_REGION" \
    --task-definition "$STACK_NAME-screen" --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[$subnets],securityGroups=[$sgs],assignPublicIp=DISABLED}" \
    --overrides "{\"containerOverrides\":[{\"name\":\"screen\",\"command\":[\"python\",\"-m\",\"labs.cli.manage_keys\",\"create\",\"--name\",\"$keyname\",\"--scopes\",\"$scopes\"]}]}" \
    --query 'tasks[0].taskArn' --output text 2>/dev/null)
  [[ -n "$task_arn" && "$task_arn" != "None" ]] || { echo "run-task did not start" >&2; return 1; }

  aws ecs wait tasks-stopped --cluster "$STACK_NAME" --tasks "$task_arn" \
    --region "$AWS_REGION" 2>/dev/null

  local exit_code
  exit_code=$(aws ecs describe-tasks --cluster "$STACK_NAME" --tasks "$task_arn" \
    --region "$AWS_REGION" --query 'tasks[0].containers[0].exitCode' --output text 2>/dev/null)
  [[ "$exit_code" == "0" ]] || { echo "key task exited $exit_code" >&2; return 1; }

  # The CLI prints a FINGERPRINT alongside the key and both start with
  # `labs_live_`, so match the `key` line by field rather than by prefix.
  aws logs get-log-events --log-group-name "/ecs/$STACK_NAME" \
    --log-stream-name "screen/screen/$(basename "$task_arn")" \
    --region "$AWS_REGION" --query 'events[].message' --output text 2>/dev/null \
    | tr '\t' '\n' | awk '$1 == "key" { print $2; exit }'
}

# --------------------------------------------------------------------------
# AWS helpers
# --------------------------------------------------------------------------
aws_account_id() { aws sts get-caller-identity --query Account --output text; }

# Look a resource up by its Name tag. Prints the id, or nothing.
# `|| true` on the aws call: a filter that matches nothing is a successful
# query returning "None", not an error, but a missing permission IS an error
# and must not be swallowed into "does not exist".
find_by_tag() {
  local type="$1" name="$2" query="$3"
  local out
  out=$(aws ec2 "describe-${type}" \
    --filters "Name=tag:Name,Values=$name" \
    --query "$query" --output text 2>/dev/null) || return 0
  [[ "$out" == "None" || -z "$out" ]] && return 0
  printf '%s' "$out"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "$1 is required but not installed. $2"
}

# Write a key = value line into a tfvars file, replacing any existing key.
# Rewriting in place rather than appending keeps the file valid HCL across
# re-runs; appending would produce duplicate keys and Terraform would refuse
# to parse it.
tfvar_set() {
  local file="$1" key="$2" value="$3"
  touch "$file"
  local tmp; tmp=$(mktemp)
  grep -v "^${key}[[:space:]]*=" "$file" > "$tmp" 2>/dev/null || true
  printf '%s = %s\n' "$key" "$value" >> "$tmp"
  mv "$tmp" "$file"
}

