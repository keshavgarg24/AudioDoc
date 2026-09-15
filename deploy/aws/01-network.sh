#!/usr/bin/env bash
# Create the VPC, subnets, gateways and routes the stack needs.
#
# Terraform deliberately does not own the network - a module that insists on
# creating its own VPC is one you cannot adopt into an existing account - so
# this script builds one when you do not already have it, and writes the ids
# into network.auto.tfvars.
#
# Safe to re-run. Everything is looked up by Name tag first.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

VPC_CIDR="10.0.0.0/16"
AZ_A="${AWS_REGION}a"
AZ_B="${AWS_REGION}b"

step "Network for $STACK_NAME in $AWS_REGION"

# -------------------------------------------------------------------- VPC --
VPC_ID=$(find_by_tag vpcs "$STACK_NAME-vpc" 'Vpcs[0].VpcId')
if [[ -n "$VPC_ID" ]]; then
  reuse "VPC $VPC_ID"
else
  VPC_ID=$(aws ec2 create-vpc --cidr-block "$VPC_CIDR" \
    --tag-specifications "ResourceType=vpc,Tags=[{Key=Name,Value=$STACK_NAME-vpc}]" \
    --query Vpc.VpcId --output text)
  # Both are required before ECS tasks can resolve the ECR, SQS and Secrets
  # Manager endpoints. A VPC without DNS hostnames produces tasks that start
  # and then fail to pull, with an error that does not mention DNS.
  aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-support
  aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-hostnames
  ok "VPC $VPC_ID ($VPC_CIDR)"
fi

# ---------------------------------------------------------------- subnets --
# $1 name suffix, $2 cidr, $3 az, $4 "public"|"private"
make_subnet() {
  local suffix="$1" cidr="$2" az="$3" tier="$4"
  local tag="$STACK_NAME-$suffix" id
  id=$(find_by_tag subnets "$tag" 'Subnets[0].SubnetId')
  if [[ -n "$id" ]]; then
    reuse "subnet $tag = $id"
  else
    id=$(aws ec2 create-subnet --vpc-id "$VPC_ID" --cidr-block "$cidr" \
      --availability-zone "$az" \
      --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=$tag},{Key=Tier,Value=$tier}]" \
      --query Subnet.SubnetId --output text)
    ok "subnet $tag = $id ($cidr, $az)"
  fi
  printf '%s' "$id"
}

# Two AZs is a hard requirement, not a preference: an ALB refuses to be
# created with only one subnet.
PUB_A=$(make_subnet public-a  10.0.0.0/24  "$AZ_A" public)
PUB_B=$(make_subnet public-b  10.0.1.0/24  "$AZ_B" public)
PRIV_A=$(make_subnet private-a 10.0.10.0/24 "$AZ_A" private)
PRIV_B=$(make_subnet private-b 10.0.11.0/24 "$AZ_B" private)

# ------------------------------------------------------- internet gateway --
IGW=$(find_by_tag internet-gateways "$STACK_NAME-igw" 'InternetGateways[0].InternetGatewayId')
if [[ -n "$IGW" ]]; then
  reuse "internet gateway $IGW"
else
  IGW=$(aws ec2 create-internet-gateway \
    --tag-specifications "ResourceType=internet-gateway,Tags=[{Key=Name,Value=$STACK_NAME-igw}]" \
    --query InternetGateway.InternetGatewayId --output text)
  aws ec2 attach-internet-gateway --vpc-id "$VPC_ID" --internet-gateway-id "$IGW"
  ok "internet gateway $IGW"
fi

# ----------------------------------------------------------- NAT gateway ---
# The single most important resource here. Every ECS task runs in a PRIVATE
# subnet and still has to reach ECR (pull), S3 (audio + weights), SQS (work)
# and Secrets Manager (config). Without a route out they start and hang in
# PENDING with no error that names the cause.
NAT=$(aws ec2 describe-nat-gateways \
  --filter "Name=tag:Name,Values=$STACK_NAME-nat" "Name=state,Values=available,pending" \
  --query 'NatGateways[0].NatGatewayId' --output text 2>/dev/null || true)
if [[ "$NAT" != "None" && -n "$NAT" ]]; then
  reuse "NAT gateway $NAT"
else
  # Reuse an EIP from a previous partial run if one was already tagged, so we
  # do not accumulate unattached EIPs (each bills $0.005/hr while unused).
  EIP=$(aws ec2 describe-addresses \
    --filters "Name=tag:Name,Values=$STACK_NAME-nat-eip" \
    --query 'Addresses[0].AllocationId' --output text 2>/dev/null || true)
  if [[ "$EIP" == "None" || -z "$EIP" ]]; then
    EIP=$(aws ec2 allocate-address --domain vpc --query AllocationId --output text)
    aws ec2 create-tags --resources "$EIP" --tags "Key=Name,Value=$STACK_NAME-nat-eip"
    ok "elastic IP $EIP"
  else
    reuse "elastic IP $EIP"
  fi
  # Use create-tags separately instead of --tag-specifications to avoid an
  # InvalidCharacter XML error in some AWS CLI versions.
  NAT=$(aws ec2 create-nat-gateway --subnet-id "$PUB_A" --allocation-id "$EIP" \
    --query NatGateway.NatGatewayId --output text)
  aws ec2 create-tags --resources "$NAT" --tags "Key=Name,Value=$STACK_NAME-nat"
  info "waiting for NAT $NAT (about 2 minutes)..."
  aws ec2 wait nat-gateway-available --nat-gateway-ids "$NAT"
  ok "NAT gateway $NAT"
fi

# ------------------------------------------------------------ route tables --
# $1 suffix, $2 "igw"|"nat", $3 target id, then subnet ids
make_rt() {
  local suffix="$1" kind="$2" target="$3"; shift 3
  local tag="$STACK_NAME-$suffix" rt
  rt=$(find_by_tag route-tables "$tag" 'RouteTables[0].RouteTableId')
  if [[ -z "$rt" ]]; then
    rt=$(aws ec2 create-route-table --vpc-id "$VPC_ID" \
      --tag-specifications "ResourceType=route-table,Tags=[{Key=Name,Value=$tag}]" \
      --query RouteTable.RouteTableId --output text)
    ok "route table $tag = $rt"
  else
    reuse "route table $tag = $rt"
  fi

  # create-route fails if the route exists; replace-route fails if it does
  # not. Try create, fall back to replace, so a half-finished earlier run
  # converges instead of aborting.
  if [[ "$kind" == "igw" ]]; then
    aws ec2 create-route --route-table-id "$rt" --destination-cidr-block 0.0.0.0/0 \
      --gateway-id "$target" >/dev/null 2>&1 \
    || aws ec2 replace-route --route-table-id "$rt" --destination-cidr-block 0.0.0.0/0 \
      --gateway-id "$target" >/dev/null
  else
    aws ec2 create-route --route-table-id "$rt" --destination-cidr-block 0.0.0.0/0 \
      --nat-gateway-id "$target" >/dev/null 2>&1 \
    || aws ec2 replace-route --route-table-id "$rt" --destination-cidr-block 0.0.0.0/0 \
      --nat-gateway-id "$target" >/dev/null
  fi

  for subnet in "$@"; do
    aws ec2 associate-route-table --route-table-id "$rt" --subnet-id "$subnet" >/dev/null 2>&1 || true
  done
}

make_rt public-rt  igw "$IGW" "$PUB_A" "$PUB_B"
make_rt private-rt nat "$NAT" "$PRIV_A" "$PRIV_B"

# ---------------------------------------------------------------- verify ---
# Assert the thing that actually breaks deploys, rather than assuming the
# commands above worked.
step "Verifying the private subnets can reach the internet"
PRIV_RT=$(find_by_tag route-tables "$STACK_NAME-private-rt" 'RouteTables[0].RouteTableId')
ROUTE=$(aws ec2 describe-route-tables --route-table-ids "$PRIV_RT" \
  --query 'RouteTables[0].Routes[?DestinationCidrBlock==`0.0.0.0/0`].NatGatewayId' \
  --output text)
[[ -n "$ROUTE" && "$ROUTE" != "None" ]] \
  || die "private route table $PRIV_RT has no 0.0.0.0/0 route to a NAT gateway.
    ECS tasks would start and hang in PENDING. Re-run this script."
ok "private subnets route through $ROUTE"

# The NAT's public IP is what MongoDB Atlas must allowlist - the containers
# have no public address of their own, so every outbound connection appears
# to come from here.
NAT_IP=$(aws ec2 describe-nat-gateways --nat-gateway-ids "$NAT" \
  --query 'NatGateways[0].NatGatewayAddresses[0].PublicIp' --output text)

tfvar_set "$NETWORK_TFVARS" vpc_id "\"$VPC_ID\""
tfvar_set "$NETWORK_TFVARS" private_subnet_ids "[\"$PRIV_A\", \"$PRIV_B\"]"
tfvar_set "$NETWORK_TFVARS" public_subnet_ids "[\"$PUB_A\", \"$PUB_B\"]"

ok "wrote $(basename "$NETWORK_TFVARS")"
printf '\n    %sNAT public IP: %s%s\n' "$_c_bold" "$NAT_IP" "$_c_reset"
printf '    Allowlist that IP in MongoDB Atlas -> Network Access.\n\n'
