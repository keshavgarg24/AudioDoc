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

# Everything is namespaced by this so two stacks can share an account.
export STACK_NAME="${STACK_NAME:-labs-test}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export IMAGE_TAG="${IMAGE_TAG:-v1}"

# Written by the scripts, read by Terraform. `.auto.tfvars` is loaded
# automatically by every terraform command in this directory, so there is no
# -var-file to remember and no way to apply with a stale one by accident.
NETWORK_TFVARS="$AWS_DIR/network.auto.tfvars"
SECRETS_TFVARS="$AWS_DIR/secrets.auto.tfvars"
WEIGHTS_TFVARS="$AWS_DIR/weights.auto.tfvars"
STACK_TFVARS="$AWS_DIR/stack.auto.tfvars"

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

tfvar_get() {
  local file="$1" key="$2"
  [[ -f "$file" ]] || return 0
  sed -n "s/^${key}[[:space:]]*=[[:space:]]*\"\{0,1\}\([^\"]*\)\"\{0,1\}.*/\1/p" "$file" | head -1
}
