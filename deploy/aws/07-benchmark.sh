#!/usr/bin/env bash
# Run a corpus through the deployed stack and render a PDF report.
#
# Designed to be started in its own terminal and left alone: a hundred tracks
# is over an hour of backbone time, and every intermediate result is written to
# disk as it arrives so an interrupted run still has its data.
#
#   ./07-benchmark.sh                          100 tracks from the default corpus
#   COUNT=25 ./07-benchmark.sh                 a quicker pass
#   DATASET=~/Desktop/other ./07-benchmark.sh  a different corpus
#   API_KEY=labs_live_... ./07-benchmark.sh    reuse a key instead of minting one
#
# Output lands in  <repo>/benchmarks/<timestamp>/  as results.json, the raw
# per-phase JSON, run.log, and report.pdf.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$AWS_DIR"

DATASET="${DATASET:-$HOME/Desktop/ai-beats-dataset/data}"
COUNT="${COUNT:-100}"
SEED="${SEED:-20260916}"
MODE="${MODE:-ai}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUTDIR="${OUTDIR:-$REPO_ROOT/benchmarks/$STAMP}"

step "Benchmark"

[[ -d "$DATASET" ]] || die "no corpus at $DATASET (set DATASET=...)"
TOTAL=$(find "$DATASET" -name '*.mp3' | wc -l | tr -d ' ')
[[ "$TOTAL" -gt 0 ]] || die "no .mp3 files under $DATASET"
info "corpus: $TOTAL files in $DATASET"
info "sampling $COUNT with seed $SEED"

# -------------------------------------------------------------- interpreter --
# reportlab and matplotlib are report-only dependencies, so they live in their
# own venv rather than in requirements.txt. Built once and reused.
PY="$REPO_ROOT/.venv-bench/bin/python"
if [[ ! -x "$PY" ]]; then
  step "Creating the report venv (once)"
  python3 -m venv "$REPO_ROOT/.venv-bench"
  "$REPO_ROOT/.venv-bench/bin/pip" install -q --upgrade pip
  "$REPO_ROOT/.venv-bench/bin/pip" install -q reportlab matplotlib
  ok "venv ready"
fi

# --------------------------------------------------------------- endpoint ----
# Prefer the HTTPS front door. An API key travelling over plain HTTP is
# readable by every hop in between, and this script sends one a hundred times.
BASE="${BASE_URL:-}"
if [[ -z "$BASE" ]]; then
  BASE=$(terraform output -raw https_url 2>/dev/null || true)
  if [[ -z "$BASE" || "$BASE" == "null" ]]; then
    ALB=$(terraform output -raw alb_dns_name 2>/dev/null) \
      || die "no Terraform output - run ./05-apply.sh first"
    BASE="http://$ALB"
    warn "no HTTPS endpoint; falling back to plaintext $BASE"
  fi
fi
info "endpoint: $BASE"

step "Waiting for the stack to answer"
for i in $(seq 1 90); do
  curl -fsS --max-time 8 "$BASE/health" >/dev/null 2>&1 && { ok "healthy"; break; }
  [[ "$i" == "90" ]] && die "/health never came up. Check the ECS services."
  sleep 5
done

# ------------------------------------------------------------------- key -----
if [[ -n "${API_KEY:-}" ]]; then
  reuse "using the API key from the environment"
else
  step "Issuing an API key"
  # Runs the CLI inside the VPC as a one-off task; see issue_api_key in lib.sh
  # for why this cannot be a local `docker run`.
  API_KEY=$(issue_api_key "benchmark-$STAMP") \
    || die "could not create an API key"
  [[ ${#API_KEY} -gt 30 ]] || die "parsed a value too short to be a key \
(most likely the fingerprint): '$API_KEY'"
  ok "key issued"
fi

mkdir -p "$OUTDIR"
printf '%s\n' "$API_KEY" > "$OUTDIR/.api-key"
chmod 600 "$OUTDIR/.api-key"

# ---------------------------------------------------------- pre-warm fleet ----
# Submitting 100 jobs onto a cold fleet means most of them queue for the
# 4-8 minutes it takes a new EC2 instance to pull the 2.5 GB image. Scaling
# to max first pays that cost once, before the clock starts.
step "Pre-warming the worker fleet"
ASG_NAME=$(terraform output -raw worker_asg_name 2>/dev/null || true)
CLUSTER_NAME=$(terraform output -raw cluster_name 2>/dev/null || true)

if [[ -n "$ASG_NAME" && -n "$CLUSTER_NAME" ]]; then
  ASG_MAX=$(aws autoscaling describe-auto-scaling-groups \
    --auto-scaling-group-names "$ASG_NAME" \
    --query 'AutoScalingGroups[0].MaxSize' \
    --output text 2>/dev/null || echo "")
  if [[ -n "$ASG_MAX" && "$ASG_MAX" -gt 1 ]]; then
    info "scaling ASG $ASG_NAME to $ASG_MAX (was desired=1)"
    aws autoscaling set-desired-capacity \
      --auto-scaling-group-name "$ASG_NAME" \
      --desired-capacity "$ASG_MAX" 2>/dev/null || true

    # ECS worker service: set desired count to ASG max so tasks start filling
    # the new instances immediately rather than waiting for the scaling alarm.
    WORKER_SVC=$(aws ecs list-services --cluster "$CLUSTER_NAME" \
      --query 'serviceArns[?contains(@,`worker`)]|[0]' \
      --output text 2>/dev/null | awk -F/ '{print $NF}' || true)
    if [[ -n "$WORKER_SVC" && "$WORKER_SVC" != "None" ]]; then
      info "scaling ECS worker service $WORKER_SVC desired=$ASG_MAX"
      aws ecs update-service \
        --cluster "$CLUSTER_NAME" \
        --service "$WORKER_SVC" \
        --desired-count "$ASG_MAX" >/dev/null 2>&1 || true
    fi

    # Wait for at least half the ASG instances to be InService so the queue
    # has real capacity to drain into. Up to 5 minutes.
    HALF=$(( (ASG_MAX + 1) / 2 ))
    info "waiting for at least $HALF/$ASG_MAX instances to be InService (up to 5 min)…"
    for i in $(seq 1 60); do
      READY=$(aws autoscaling describe-auto-scaling-groups \
        --auto-scaling-group-names "$ASG_NAME" \
        --query 'AutoScalingGroups[0].Instances[?LifecycleState==`InService`] | length(@)' \
        --output text 2>/dev/null || echo 0)
      [[ "$READY" -ge "$HALF" ]] && { ok "$READY/$ASG_MAX instances InService"; break; }
      [[ "$i" == "60" ]] && { warn "only $READY/$ASG_MAX ready after 5 min; continuing anyway"; break; }
      sleep 5
    done
  else
    warn "could not determine ASG max — skipping pre-warm"
  fi
else
  warn "no Terraform outputs for asg/cluster — skipping pre-warm"
fi

# ------------------------------------------------------------------- run -----
step "Running $COUNT tracks (this takes a while; output streams below)"
info "results -> $OUTDIR"
printf '\n'

"$PY" "$REPO_ROOT/scripts/aws_benchmark.py" \
  --base-url "$BASE" \
  --api-key "$API_KEY" \
  --dataset "$DATASET" \
  --count "$COUNT" \
  --seed "$SEED" \
  --mode "$MODE" \
  --workers 8 \
  --screen-rpm 25 \
  --submit-rpm 60 \
  --out "$OUTDIR"

# ---------------------------------------------------------------- report -----
step "Rendering the PDF"
"$PY" "$REPO_ROOT/scripts/aws_report.py" \
  --results "$OUTDIR/results.json" \
  --out "$OUTDIR/report.pdf"

printf '\n'
ok "done"
printf '    %s%s/report.pdf%s\n' "$_c_bold" "$OUTDIR" "$_c_reset"
printf '    raw json:  %s/results.json\n\n' "$OUTDIR"
command -v open >/dev/null && open "$OUTDIR/report.pdf" 2>/dev/null || true
