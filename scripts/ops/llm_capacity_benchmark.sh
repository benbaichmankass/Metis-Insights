#!/usr/bin/env bash
# W6 P1 — CAN THE TRAINER VM HOST A SMALL LOCAL LLM USEFULLY?
#
# The operations audit asked whether the half-built local-LLM idea should carry
# any of the analyst load. That question has never been ANSWERED with a
# measurement — the trainer is 1 OCPU aarch64 (Neoverse-N1, `asimd asimddp
# fphp asimdhp`, no GPU) with ~5.3 GiB available, so the honest answer is a
# benchmark, not an opinion.
#
# WHY THIS IS A COMMITTED SCRIPT AND NOT A PASTED RELAY BLOB
#   The first attempt was pasted into a `trainer-vm-diag` issue as a nested
#   heredoc and died before running a single line — the relay's own wrapper ate
#   the terminator (`here-document ... delimited by end-of-file`). A committed
#   script makes the relay command one line, makes this repeatable, and makes
#   the thing that runs on the VM reviewable in the diff rather than in an
#   issue body.
#
# CONTRACT
#   * Takes the trainer heavy lock, and REFUSES TO RUN AT ALL if the helper is
#     missing — running a 1-core box unserialised is how a training cycle and a
#     benchmark starve each other.
#   * Cleans up on EXIT: the build tree and the weights are deleted whatever
#     happens. The trainer had 6.3G free when measured; a llama.cpp tree plus a
#     1.5B Q4 GGUF is roughly 2-3G of that.
#   * Ends in EXACTLY ONE `RESULT:` line, and the three are never collapsed:
#       benchmarked           — it ran; read the numbers
#       could_not_build       — the toolchain/box could not produce a binary
#       could_not_obtain_model— it built, and no candidate GGUF could be fetched
#     `could_not_build` and `could_not_obtain_model` are different findings and
#     only the first says anything about the hardware.
#
# Run it from the trainer relay as:  nohup setsid bash scripts/ops/llm_capacity_benchmark.sh &
# Then poll /tmp/llmbench.log and the /tmp/llmbench_DONE sentinel.
set -uo pipefail
exec > /tmp/llmbench.log 2>&1
rm -f /tmp/llmbench_DONE
finish() { echo "=== finished $(date -u +%FT%TZ) ==="; touch /tmp/llmbench_DONE; }
trap finish EXIT

cd /home/ubuntu/ict-trading-bot || { echo "RESULT: could_not_build (no repo dir)"; exit 0; }

# shellcheck disable=SC1091
. scripts/ops/_trainer_heavy_lock.sh 2>/dev/null || true
if declare -F take_trainer_heavy_lock >/dev/null; then
  take_trainer_heavy_lock "manual:w6-p1-llm-benchmark" || {
    echo "RESULT: could_not_build (heavy lock not acquired)"; exit 0; }
  echo "=== heavy lock ACQUIRED $(date -u +%FT%TZ) ==="
else
  echo "RESULT: could_not_build (heavy-lock helper not found — refusing to run unserialised)"
  exit 0
fi

echo "--- host ---"; uname -m; nproc; free -m | head -2; df -h / | tail -1

WORK=/tmp/llmbench_work
rm -rf "$WORK"; mkdir -p "$WORK"; cd "$WORK"

cleanup() { cd /tmp; rm -rf "$WORK"; echo "--- cleaned up; disk after ---"; df -h / | tail -1; finish; }
trap cleanup EXIT

echo "=== build llama.cpp ==="
# ⚠️ EVERY FAILURE PATH PRINTS ITS OWN CAUSE. The first version redirected the
# clone and configure steps to /dev/null, so the 2026-08-31 run reported
# `could_not_build (cmake configure failed)` and NOTHING about why — it took a
# second, separate trainer round-trip (#10586) to learn that `cmake` was simply
# not installed. A bucket without a cause is an honest label and a useless
# diagnosis, which is the whole defect class this repo calls unprovenanced
# diagnostic output. The tail is capped so a wall of build noise cannot bury
# the RESULT line the reader is actually looking for.
if ! git clone --depth 1 https://github.com/ggml-org/llama.cpp.git > /tmp/_clone.log 2>&1; then
  echo "--- clone failure, last 15 lines ---"; tail -15 /tmp/_clone.log
  echo "RESULT: could_not_build (clone failed)"; exit 0
fi
cd llama.cpp
# Report the toolchain BEFORE configuring, so a missing tool is visible in the
# log itself rather than needing a separate probe.
echo "--- toolchain ---"
for t in cmake make gcc g++ ninja; do
  printf '%-6s ' "$t"
  command -v "$t" >/dev/null 2>&1 && "$t" --version 2>&1 | head -1 || echo "MISSING"
done
if ! cmake -B build -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF > /tmp/_cfg.log 2>&1; then
  echo "--- cmake configure failure, last 25 lines ---"; tail -25 /tmp/_cfg.log
  echo "RESULT: could_not_build (cmake configure failed)"; exit 0
fi
# Log-and-tail rather than re-run-on-failure: the original re-invoked the whole
# build to obtain its error text, which on a 1-core box means paying for a
# failed compile twice.
if ! cmake --build build -j1 --target llama-bench llama-cli > /tmp/_build.log 2>&1; then
  echo "--- compile failure, last 25 lines ---"; tail -25 /tmp/_build.log
  echo "RESULT: could_not_build (compile failed)"; exit 0
fi
echo "build OK"

echo "=== fetch a small GGUF ==="
MODEL=""
for U in \
  "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf" \
  "https://huggingface.co/bartowski/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf" \
  "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf" ; do
  echo "trying $U"
  if timeout 900 curl -fsSL -o m.gguf "$U"; then MODEL="$U"; break; fi
done
if [ -z "$MODEL" ]; then echo "RESULT: could_not_obtain_model"; exit 0; fi
ls -lh m.gguf; echo "model: $MODEL"

echo "=== llama-bench ==="
timeout 1800 ./build/bin/llama-bench -m m.gguf -t 1 -p 128 -n 64 -r 2 2>&1 | tail -20

echo "=== end-to-end summariser-shaped prompt ==="
/usr/bin/time -v timeout 900 ./build/bin/llama-cli -m m.gguf -t 1 -n 200 -no-cnv \
  -p "Summarise for a trading operator, in 5 bullets: a live account refused 9 of 30 orders with venue error 110007 (insufficient margin) while its account-level available-balance field read empty and margin had to be derived from the per-coin block. What happened, why it matters, and what to check next." \
  2>&1 | tail -40

echo "RESULT: benchmarked"
