#!/usr/bin/env bash
# Issue an API key and work up from the cheapest thing that can fail.
#
# The order matters. Liveness needs no model, readiness needs the backbone,
# Level 1 needs the ONNX weights, Level 2 needs the checkpoints AND the queue
# AND a worker. Testing them in that order means the first failure tells you
# which layer is broken instead of just "it does not work".

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$AWS_DIR"

ALB=$(terraform output -raw alb_dns_name 2>/dev/null) \
  || die "no Terraform output - run ./05-apply.sh first"
BASE="http://$ALB"

step "Verifying $BASE"

# ------------------------------------------------------------------ liveness
info "waiting for the load balancer to report healthy targets..."
for i in $(seq 1 60); do
  if curl -fsS --max-time 5 "$BASE/health" >/dev/null 2>&1; then
    ok "/health responding (after ~$((i * 5))s)"
    break
  fi
  [[ "$i" == "60" ]] && die "/health never came up after 5 minutes.
    Check:  aws ecs list-tasks --cluster $(terraform output -raw cluster_name)
    Logs:   aws logs tail $(terraform output -raw log_group) --since 10m"
  sleep 5
done

# ----------------------------------------------------------------- api key
# Keys live in MongoDB rather than in the environment, which is what makes a
# key revocable without a redeploy. It is printed once and stored only as a
# SHA-256 hash, so there is no way to recover it later.
step "Issuing an API key"
# Runs the CLI inside the VPC as a one-off ECS task rather than locally; see
# issue_api_key in lib.sh for why a local `docker run` cannot reach Atlas.
API_KEY=$(issue_api_key "smoke-test-$(date +%Y%m%d-%H%M%S)") \
  || die "could not create an API key"
[[ -n "$API_KEY" ]] || die "key task succeeded but no key could be parsed from its log"
# labs_live_ + 43 url-safe characters. A fingerprint is the prefix plus 6.
[[ ${#API_KEY} -gt 30 ]] || die "parsed '$API_KEY', which is too short to be a key
    (it is most likely the fingerprint)."
ok "API key issued (shown once, stored as a hash)"

# ----------------------------------------------------------------- readiness
step "Readiness"
curl -fsS "$BASE/v1/ready" | python3 -m json.tool | head -20 || warn "not ready yet"

# ------------------------------------------------------------------- level 1
step "Level 1 (synchronous, ~1.5 s)"
SAMPLE=$(ls "$REPO_ROOT"/audio/*.mp3 2>/dev/null | head -1 || true)
if [[ -z "$SAMPLE" ]]; then
  warn "no audio in $REPO_ROOT/audio - skipping the upload tests"
else
  curl -fsS -X POST "$BASE/v1/screen" -H "X-API-Key: $API_KEY" \
    -F "file=@$SAMPLE" \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); print(f"    verdict={d.get(\"verdict\")} next_step={d.get(\"next_step\")} elapsed={d.get(\"elapsed_s\")}s")' \
    || die "Level 1 failed. The ONNX weights ship in the image, so a failure
    here is the image itself rather than anything in S3."
  ok "Level 1 answered"

  # --------------------------------------------------------------- level 2
  # This is the end-to-end test: it exercises S3, SQS, the worker ASG, the
  # checkpoints in S3, and MongoDB. A failure here after Level 1 passed
  # almost always means the worker cannot read the weights bucket.
  step "Level 2 (asynchronous; a cold worker takes several minutes)"
  JOB=$(curl -fsS -X POST "$BASE/v1/analyses" -H "X-API-Key: $API_KEY" \
    -F "file=@$SAMPLE" -F mode=full \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
  info "job $JOB queued; polling for up to 10 minutes"

  for i in $(seq 1 120); do
    STATUS=$(curl -fsS "$BASE/v1/analyses/$JOB" -H "X-API-Key: $API_KEY" \
      | python3 -c 'import sys,json; print(json.load(sys.stdin).get("status",""))' 2>/dev/null || echo "")
    case "$STATUS" in
      completed) ok "analysis completed after ~$((i * 5))s"; break ;;
      failed)    die "analysis failed. Logs: aws logs tail $(terraform output -raw log_group) --since 15m" ;;
    esac
    [[ "$i" == "120" ]] && warn "still $STATUS after 10 minutes - check the worker ASG"
    sleep 5
  done
fi

# ---------------------------------------------------------------- dead letter
# Anything in the DLQ failed three times, which means it failed
# deterministically rather than transiently.
DLQ_DEPTH=$(aws sqs get-queue-attributes --queue-url "$(terraform output -raw dlq_url)" \
  --attribute-names ApproximateNumberOfMessages \
  --query 'Attributes.ApproximateNumberOfMessages' --output text 2>/dev/null || echo 0)
[[ "$DLQ_DEPTH" == "0" ]] && ok "dead-letter queue empty" || warn "$DLQ_DEPTH message(s) in the DLQ"

printf '\n'
step "Done"
printf '    %sAPI    %s%s\n' "$_c_bold" "$BASE" "$_c_reset"
printf '    %sKEY    %s%s\n' "$_c_bold" "$API_KEY" "$_c_reset"
printf '\n    Save that key now. It is stored only as a hash and cannot be recovered.\n\n'
