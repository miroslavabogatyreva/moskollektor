#!/bin/sh
# Одна последовательная итерация: свежий score -> атомарная публикация -> worker.
# Оба режима требуют явного среза; live принимает подтвержденную границу источника.
set -eu
IMAGE="${ML_SCORE_IMAGE:?Set ML_SCORE_IMAGE to the approved 24h model image}"
DATA="${ML_SCORE_DATA:?Set ML_SCORE_DATA to the absolute real data directory}"
VOLUME="${ML_SCORE_VOLUME:-moskollektor_score}"
MODE="${ML_SCORE_MODE:?Set ML_SCORE_MODE=live or archive}"
case "$MODE" in
  archive) AS_OF="${ML_SCORE_AS_OF:?Archive replay requires an explicit ML_SCORE_AS_OF}" ;;
  live) AS_OF="${ML_SCORE_AS_OF:?Live mode requires the upstream committed ML_SCORE_AS_OF watermark}" ;;
  *) echo 'ML_SCORE_MODE must be live or archive' >&2; exit 2 ;;
esac
if [ -n "${ML_SCORE_LOG:-}" ]; then exec >> "$ML_SCORE_LOG" 2>&1; fi
# The data watermark is a Moscow-local journal timestamp, not the host's now().
# Check freshness with the same image runtime; this needs only Python stdlib.
if [ "$MODE" = live ]; then
  docker run --rm --entrypoint python "$IMAGE" -c '
import math
import sys
from datetime import datetime, timedelta, timezone
try:
    watermark = datetime.strptime(sys.argv[1], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=3)))
    bound = float(sys.argv[2])
    if not math.isfinite(bound) or bound <= 0:
        raise ValueError("ML_SCORE_MAX_INPUT_AGE_S must be finite and positive")
except ValueError as exc:
    raise SystemExit(f"Invalid live watermark/freshness bound: {exc}")
age = (datetime.now(timezone.utc) - watermark).total_seconds()
if age < 0 or age > bound:
    raise SystemExit(f"Live watermark age {age:.1f}s outside [0, {bound:g}]s; refresh the committed source")
print(f"live committed watermark accepted: age={age:.1f}s, limit={bound:g}s")
' "$AS_OF" "${ML_SCORE_MAX_INPUT_AGE_S:-300}"
fi
# mkdir is available on macOS and Linux. A second scheduled run skips the locked
# iteration; a failed process removes its lock through trap.
LOCK="${ML_SCORE_LOCK:-/tmp/moskollektor-ml-score.lock}"
if ! mkdir "$LOCK" 2>/dev/null; then echo 'score iteration already running'; exit 0; fi
trap 'rmdir "$LOCK"' EXIT HUP INT TERM
started=$(date +%s)
tmp="score.$$.tmp"
echo "start mode=$MODE as_of=$AS_OF image=$IMAGE"
docker run --rm --cpus "${ML_SCORE_CPUS:-8}" \
  -v "$DATA":/app/data:ro -v "$VOLUME":/out \
  "$IMAGE" --as-of "$AS_OF" --out "/out/$tmp"
docker run --rm -v "$VOLUME":/out --entrypoint sh "$IMAGE" \
  -c 'test -s "/out/$1" && mv "/out/$1" /out/score.json' sh "$tmp"
# Scheduling a separate worker can add an entire timer interval to latency.
# Run it on the same explicit cut after publication; its existing DB lock also
# protects against the independently running scheduler.
if [ "${ML_SCORE_RUN_WORKER:-1}" = 1 ]; then
  DEPLOY=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
  iso=$(printf '%s' "$AS_OF" | tr ' ' T)
  docker compose --project-directory "$DEPLOY" --profile app run --rm \
    -e SCORE_V3_PATH=/score/score.json -v "$VOLUME":/score:ro worker \
    python -m app.worker.run --as-of "${iso}+03:00"
fi
elapsed=$(( $(date +%s) - started ))
echo "score and requested worker complete in ${elapsed}s (schedule wait excluded)"
if [ "$elapsed" -ge "${ML_SCORE_WARN_S:-300}" ]; then
  echo 'pipeline exceeded the configured runtime budget' >&2; exit 1
fi
