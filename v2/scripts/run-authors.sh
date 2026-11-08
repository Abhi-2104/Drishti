#!/usr/bin/env bash
# Run one author (an omp model) over the blind evidence bundles, in batches, with no web, no shell and no store access.
#   scripts/run-authors.sh <author> <omp-model-id> <input-dir> <out-dir> [batch-size] [prompt-file]
# RESUME=1 skips batches whose answers file is already complete (rerun after a failure; never use it for a fresh run).
# The input folder holds case-*.json bundles (set PATTERN to run over other files, e.g. PATTERN='rev-*.json' for critiques).
# e.g. scripts/run-authors.sh claude anthropic/<id> ~/eval/bundles ~/eval/answers 15
# Reasoning effort is the same for every author (default high); change it for all authors or none: export OMP_THINKING=high
# To use a named omp profile (separate logins), set it first:  export OMP_CMD="omp --profile personal"
# Output: <out-dir>/<author>.jsonl  (import it with `drishti decisions import`). The key file must NOT be in <bundles-dir>.
set -euo pipefail
author=${1:?author}; model=${2:?omp model id}; src=${3:?input dir}; out=${4:?output dir}; size=${5:-15}
pattern=${PATTERN:-case-*.json}
here=$(cd "$(dirname "$0")" && pwd); promptfile=$(realpath "${6:-$here/author-prompt.txt}")
[ -f "$promptfile" ] || { echo "prompt file not found: $promptfile" >&2; exit 2; }
ls "$src"/*key* >/dev/null 2>&1 && { echo "refusing: a key file is inside $src" >&2; exit 2; }
work=$(mktemp -d); mkdir -p "$out/$author"; trap 'rm -rf "$work"' EXIT
cp "$src"/$pattern "$work"/
cd "$work"
ls $pattern | split -l "$size" - batch-
for b in batch-*; do
  files=$(paste -sd' ' "$b"); ans="answers-$b.jsonl"
  if [ "${RESUME:-0}" = "1" ] && [ -f "$out/$author/$ans" ] && [ "$(wc -l < "$out/$author/$ans")" -eq "$(wc -l < "$b")" ]; then
    echo "[$author] $b: already complete, skipped (RESUME=1)"; cp "$out/$author/$ans" "$ans"; continue
  fi
  prompt=$(sed -e "s|@FILES@|$files|" -e "s|@OUT@|$ans|" "$promptfile")
  echo "[$author] $b: $(wc -l < "$b") cases"
  ${OMP_CMD:-omp} -p --model "$model" --thinking "${OMP_THINKING:-high}" --cwd "$work" --tools read,write --approval-mode yolo --max-time 20m --no-title "$prompt" \
    > "$out/$author/$b.log" 2>&1 || echo "[$author] $b: omp exited non-zero (see $out/$author/$b.log)" >&2
  [ -f "$ans" ] && cp "$ans" "$out/$author/" || echo "[$author] $b: no answers file written" >&2
done
cat "$out/$author"/answers-batch-*.jsonl > "$out/$author.jsonl" 2>/dev/null || true
echo "[$author] $(wc -l < "$out/$author.jsonl") answers -> $out/$author.jsonl"
