#!/usr/bin/env bash
# Run Terraform. Every input has already been written into *.auto.tfvars by
# the earlier scripts, so there is no -var-file to remember and no way to
# apply against a stale one by accident.
#
# Pass --auto-approve to skip the confirmation prompt (used by deploy.sh
# when DEPLOY_AUTO_APPROVE=1).

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$AWS_DIR"

step "Terraform"

require_cmd terraform "brew install hashicorp/tap/terraform"

# Fail early and legibly if an earlier step was skipped, rather than letting
# Terraform report a missing variable by its internal name.
[[ -f "$NETWORK_TFVARS" ]] || die "no network.auto.tfvars - run ./01-network.sh first"
[[ -f "$SECRETS_TFVARS" ]] || die "no secrets.auto.tfvars - run ./02-secrets.sh first"
[[ -f "$WEIGHTS_TFVARS" ]] || die "no weights.auto.tfvars - run ./03-weights.sh first"

# Sizing and retention. Written here rather than committed so a testing stack
# and a production stack can differ without a branch.
#
# warm_pool_size = 1 is the interesting one. A warm pool instance is STOPPED:
# it bills only for its EBS volume (~$3/month) but resumes in ~30 s instead of
# the 3-5 minutes a cold launch takes, because the image is already pulled and
# the weights are already on the volume. At low volume that is the difference
# between a usable first request and a timeout, for the price of a coffee.
tfvar_set "$STACK_TFVARS" name                  "\"$STACK_NAME\""
tfvar_set "$STACK_TFVARS" region                "\"$AWS_REGION\""
tfvar_set "$STACK_TFVARS" worker_instance_type  "\"${WORKER_INSTANCE_TYPE:-c7g.xlarge}\""
# Task size has to track the instance, and the AMI architecture is derived from
# the instance type inside main.tf. Setting the type without the sizing leaves a
# task that can never be placed - which surfaces as "service stuck at 0/1" and
# reads like a capacity problem rather than the sizing mistake it is.
tfvar_set "$STACK_TFVARS" worker_task_cpu       "${WORKER_TASK_CPU:-4096}"
tfvar_set "$STACK_TFVARS" worker_task_memory    "${WORKER_TASK_MEMORY:-8192}"
tfvar_set "$STACK_TFVARS" worker_torch_threads  "${WORKER_TORCH_THREADS:-4}"
# HTTPS without owning a domain. See the enable_cloudfront variable.
tfvar_set "$STACK_TFVARS" enable_cloudfront     "${ENABLE_CLOUDFRONT:-true}"
tfvar_set "$STACK_TFVARS" lock_alb_to_cloudfront "${LOCK_ALB:-false}"
tfvar_set "$STACK_TFVARS" worker_max_size       "${WORKER_MAX_SIZE:-4}"
tfvar_set "$STACK_TFVARS" warm_pool_size        "${WARM_POOL_SIZE:-1}"
tfvar_set "$STACK_TFVARS" worker_base_tasks     "${WORKER_BASE_TASKS:-0}"
tfvar_set "$STACK_TFVARS" burst_to_fargate      "${BURST_TO_FARGATE:-false}"
# 0 everywhere means keep forever. The audio, the reports and the features
# derived from them are the dataset: they are what dedup reads, what an appeal
# is re-analysed from, and the only corpus that exists if the models are ever
# retrained.
tfvar_set "$STACK_TFVARS" audio_ttl_days        "${AUDIO_TTL_DAYS:-0}"
tfvar_set "$STACK_TFVARS" result_ttl_days       "${RESULT_TTL_DAYS:-0}"
tfvar_set "$STACK_TFVARS" screen_retain_seconds "${SCREEN_RETAIN_SECONDS:-0}"
tfvar_set "$STACK_TFVARS" certificate_arn       "\"${CERTIFICATE_ARN:-}\""

# Remote state if 00-backend.sh has run, local otherwise. Not silently either
# way: an operator should know which one is holding the record of their stack.
if [[ -f "$AWS_DIR/backend.hcl" ]]; then
  info "state: s3 (backend.hcl)"
  terraform init -input=false -backend-config="$AWS_DIR/backend.hcl"
else
  warn "state: LOCAL. Losing this directory orphans the whole stack in AWS."
  warn "Run ./00-backend.sh once to move it to S3."
  terraform init -input=false
fi
terraform validate

step "Plan"
terraform plan -input=false -out=tfplan

if [[ "${1:-}" == "--auto-approve" || "${DEPLOY_AUTO_APPROVE:-}" == "1" ]]; then
  info "applying without prompting"
else
  printf '\n    Review the plan above. Apply? [y/N] '
  read -r reply
  [[ "$reply" == "y" || "$reply" == "Y" ]] || die "aborted"
fi

step "Apply (8-12 minutes; the ALB and the ASG are the slow parts)"
terraform apply -input=false tfplan

ALB=$(terraform output -raw alb_dns_name)
HTTPS=$(terraform output -raw https_url 2>/dev/null || true)
printf '\n'
ok "stack is up"
if [[ -n "$HTTPS" && "$HTTPS" != "null" ]]; then
  printf '    %s%s%s   <- use this\n' "$_c_bold" "$HTTPS" "$_c_reset"
  printf '    http://%s   (origin; plaintext)\n\n' "$ALB"
else
  printf '    %shttp://%s%s\n' "$_c_bold" "$ALB" "$_c_reset"
  warn "no TLS: API keys will cross the internet in cleartext"
  printf '\n'
fi
