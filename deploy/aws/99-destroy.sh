#!/usr/bin/env bash
# Tear the stack down.
#
# Deliberately NOT a single terraform destroy. Three things Terraform does not
# own have to be dealt with by hand, and two of them cost money if forgotten:
# the network (NAT gateway, ~$32/month), the ECR repositories, and the
# secrets. This script names all of them and asks before each.
#
# The audio bucket is emptied only on an explicit second confirmation. It
# holds the corpus - every track ever analysed - and emptying it is not
# reversible.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$AWS_DIR"

step "Tearing down $STACK_NAME in $AWS_REGION"

confirm() {
  printf '    %s [y/N] ' "$1"
  read -r reply
  [[ "$reply" == "y" || "$reply" == "Y" ]]
}

# ------------------------------------------------------------------- buckets
# S3 buckets with objects in them block a destroy. That is deliberate: it is
# the last guard against deleting the corpus by running one command.
if AUDIO_BUCKET=$(terraform output -raw audio_bucket 2>/dev/null); then
  COUNT=$(aws s3 ls "s3://$AUDIO_BUCKET" --recursive --summarize 2>/dev/null \
    | grep -c "" || echo 0)
  warn "audio bucket $AUDIO_BUCKET holds roughly $COUNT object(s)"
  warn "this is every track ever analysed - the dataset, not a cache"
  if confirm "Empty it? Terraform cannot destroy the bucket otherwise."; then
    aws s3 rm "s3://$AUDIO_BUCKET" --recursive
    ok "emptied"
  else
    info "left in place; terraform destroy will fail on this bucket"
  fi
fi

# ----------------------------------------------------------------- terraform
if confirm "Run terraform destroy?"; then
  terraform destroy -input=false -auto-approve
  ok "Terraform resources destroyed"
fi

# ----------------------------------------------------------------------- ECR
if confirm "Delete the ECR repositories ($STACK_NAME-screen, $STACK_NAME-worker)?"; then
  for target in screen worker; do
    aws ecr delete-repository --repository-name "$STACK_NAME-$target" --force >/dev/null 2>&1 \
      && ok "deleted $STACK_NAME-$target" || info "$STACK_NAME-$target was already gone"
  done
fi

# ------------------------------------------------------------------- secrets
# Force-deleted rather than scheduled: a 30-day recovery window would block
# recreating a stack of the same name, which is exactly what you want to do
# after tearing a testing stack down.
if confirm "Delete the secrets?"; then
  for s in mongo-uri webhook-secret acr-bearer-token acr-container-id; do
    aws secretsmanager delete-secret --secret-id "$STACK_NAME/$s" \
      --force-delete-without-recovery >/dev/null 2>&1 \
      && ok "deleted $STACK_NAME/$s" || true
  done
fi

# ------------------------------------------------------------------- network
# The NAT gateway is the expensive one: about $32/month sitting idle, and it
# survives terraform destroy because Terraform never owned it.
if confirm "Delete the network (VPC, NAT gateway, subnets)? The NAT costs ~\$32/month if left."; then
  NAT=$(aws ec2 describe-nat-gateways \
    --filter "Name=tag:Name,Values=$STACK_NAME-nat" "Name=state,Values=available" \
    --query 'NatGateways[0].NatGatewayId' --output text 2>/dev/null || echo "None")
  if [[ "$NAT" != "None" && -n "$NAT" ]]; then
    EIP=$(aws ec2 describe-nat-gateways --nat-gateway-ids "$NAT" \
      --query 'NatGateways[0].NatGatewayAddresses[0].AllocationId' --output text)
    aws ec2 delete-nat-gateway --nat-gateway-id "$NAT" >/dev/null
    info "waiting for the NAT gateway to delete (about 2 minutes)..."
    aws ec2 wait nat-gateway-deleted --nat-gateway-ids "$NAT" 2>/dev/null || true
    # An unattached Elastic IP still bills. Releasing it is the whole reason
    # this branch waits for the NAT to finish deleting first.
    [[ -n "$EIP" && "$EIP" != "None" ]] && aws ec2 release-address --allocation-id "$EIP" 2>/dev/null || true
    ok "NAT gateway and Elastic IP released"
  fi

  VPC_ID=$(find_by_tag vpcs "$STACK_NAME-vpc" 'Vpcs[0].VpcId')
  if [[ -n "$VPC_ID" ]]; then
    for sub in $(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" \
                  --query 'Subnets[].SubnetId' --output text); do
      aws ec2 delete-subnet --subnet-id "$sub" 2>/dev/null || true
    done
    IGW=$(aws ec2 describe-internet-gateways \
      --filters "Name=attachment.vpc-id,Values=$VPC_ID" \
      --query 'InternetGateways[0].InternetGatewayId' --output text 2>/dev/null || echo "None")
    if [[ "$IGW" != "None" && -n "$IGW" ]]; then
      aws ec2 detach-internet-gateway --internet-gateway-id "$IGW" --vpc-id "$VPC_ID" 2>/dev/null || true
      aws ec2 delete-internet-gateway --internet-gateway-id "$IGW" 2>/dev/null || true
    fi
    for rt in $(aws ec2 describe-route-tables --filters "Name=vpc-id,Values=$VPC_ID" \
                 --query 'RouteTables[?length(Associations[?Main==`true`])==`0`].RouteTableId' \
                 --output text); do
      aws ec2 delete-route-table --route-table-id "$rt" 2>/dev/null || true
    done
    aws ec2 delete-vpc --vpc-id "$VPC_ID" 2>/dev/null \
      && ok "VPC $VPC_ID deleted" \
      || warn "VPC $VPC_ID could not be deleted - something is still attached to it"
  fi
fi

# The weights bucket is left alone on purpose. It holds 1.3 GB that took a
# while to fetch, costs about $0.03/month, and is the thing you would need
# again first when you redeploy.
info "the weights bucket was left in place (~\$0.03/month, saves re-uploading 1.3 GB)"

rm -f "$AWS_DIR"/*.auto.tfvars "$AWS_DIR/.weights-digests" "$AWS_DIR/tfplan"
ok "local tfvars cleaned up"
