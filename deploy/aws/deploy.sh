#!/usr/bin/env bash
# One command, whole stack, from a fresh clone and an empty AWS account.
#
#   MONGO_URI='mongodb+srv://...' ./deploy.sh
#
# Runs, in order: preflight, network, secrets, weights, images, terraform,
# verification. Every stage is idempotent, so if one fails you fix the cause
# and run this again - it skips what already exists rather than duplicating
# it.
#
# To run a single stage, call its script directly: ./04-images.sh

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

printf '\n%s' "$_c_bold"
cat <<'BANNER'
  LABS deployment
BANNER
printf '%s' "$_c_reset"

# ------------------------------------------------------------------ preflight
step "Preflight"

require_cmd aws       "brew install awscli"
require_cmd terraform "brew install hashicorp/tap/terraform"
require_cmd docker    "Install Docker Desktop and start it."
require_cmd python3   "brew install python"

aws sts get-caller-identity >/dev/null 2>&1 \
  || die "AWS credentials are not configured. Run: aws configure"

ACCOUNT_ID=$(aws_account_id)
IDENTITY=$(aws sts get-caller-identity --query Arn --output text)

ok "aws $(aws --version 2>&1 | cut -d' ' -f1 | cut -d/ -f2)"
ok "terraform $(terraform version -json 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin)["terraform_version"])' 2>/dev/null || echo 'unknown')"
ok "docker $(docker --version | cut -d' ' -f3 | tr -d ,)"

printf '\n'
printf '    %sAccount%s   %s\n' "$_c_bold" "$_c_reset" "$ACCOUNT_ID"
printf '    %sIdentity%s  %s\n' "$_c_bold" "$_c_reset" "$IDENTITY"
printf '    %sRegion%s    %s\n' "$_c_bold" "$_c_reset" "$AWS_REGION"
printf '    %sStack%s     %s\n' "$_c_bold" "$_c_reset" "$STACK_NAME"
printf '    %sTag%s       %s\n' "$_c_bold" "$_c_reset" "$IMAGE_TAG"
printf '\n'

# This is the guard against deploying a testing stack into the production
# account, which is cheap to check and expensive to discover afterwards.
if [[ "${DEPLOY_AUTO_APPROVE:-}" != "1" ]]; then
  printf '    Deploy into THIS account? [y/N] '
  read -r reply
  [[ "$reply" == "y" || "$reply" == "Y" ]] || die "aborted"
fi

# --------------------------------------------------------------------- stages
"$AWS_DIR/01-network.sh"
"$AWS_DIR/02-secrets.sh" "${MONGO_URI:-}"
"$AWS_DIR/03-weights.sh"
"$AWS_DIR/04-images.sh"
"$AWS_DIR/05-apply.sh" ${DEPLOY_AUTO_APPROVE:+--auto-approve}
"$AWS_DIR/06-verify.sh"

step "Deployment complete"
cat <<EOF

    Useful afterwards:

      Logs        aws logs tail \$(cd $AWS_DIR && terraform output -raw log_group) --follow
      Queue       aws sqs get-queue-attributes --queue-url \$(cd $AWS_DIR && terraform output -raw queue_url) \\
                    --attribute-names ApproximateNumberOfMessages
      Scaling     aws autoscaling describe-scaling-activities \\
                    --auto-scaling-group-name \$(cd $AWS_DIR && terraform output -raw worker_asg_name)

      New build   IMAGE_TAG=v2 ./04-images.sh && IMAGE_TAG=v2 ./05-apply.sh
      Tear down   ./99-destroy.sh

EOF
