#!/usr/bin/env python3
"""On-pod exec building blocks for the RunPod burst tier (M19 Tier-1).

Pure, unit-testable helpers the burst adapter uses to drive a rented pod over
**direct public-IP SSH** with an **ephemeral per-run keypair** — the official
RunPod image installs the key (`PUBLIC_KEY`) + runs sshd itself, so no account SSH
key and no GitHub secret are ever needed (the key is born and dies with the run).
Design: `docs/research/T1-gpu-burst-spend-SPEC.md` § "On-pod exec design".

Split out from `runpod_burst.py` so the SSH-argv construction, the remote
train-script assembly, and the artifact decode can be tested WITHOUT a live pod
(the parts that genuinely need a pod — the ssh round-trips — stay in the adapter
and are validated by a live `verify`/`ssh-probe` run). Nothing here spends money
or opens a network socket.

Data contract (enforced by the script this builds): only PUBLIC code + PUBLIC
market data ever reach the pod; the trained model comes back as gzip|base64 on
the SSH stdout (a JSON bundle of the model_state + metrics + manifest). No secret,
money-DB, or live cred is referenced anywhere in the remote script (asserted by
tests).
"""
from __future__ import annotations

import base64
import gzip
import os
import subprocess
import tempfile

# The proxy-SSH host RunPod exposes for keyless-account, no-public-IP command
# execution. `<pod-id>@ssh.runpod.io` authenticates with the key injected via
# the pod's SSH_PUBLIC_KEY env at create time.
SSH_HOST = "ssh.runpod.io"

# The public repo the pod clones (no token — it's public). Pinned to a SHA per run.
REPO_URL = "https://github.com/benbaichmankass/ict-trading-bot.git"

# Where the returned CPU artifact lands in the mirror the trainer VM ingests from.
MIRROR_SUBDIR = "runtime_logs/trainer_mirror/gpu_burst"


def ssh_argv(
    pod_id: str,
    key_path: str,
    remote_command: str,
    *,
    connect_timeout: int = 30,
) -> list[str]:
    """Build the argv for a single proxy-SSH command against a RunPod pod.

    Non-interactive + ephemeral-key hygiene: no host-key prompt, no known_hosts
    pollution (the pod is throwaway), BatchMode so a missing key fails fast rather
    than hanging on a password prompt.
    """
    if not pod_id:
        raise ValueError("pod_id is required for ssh")
    return [
        "ssh",
        "-i", key_path,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={connect_timeout}",
        f"{pod_id}@{SSH_HOST}",
        remote_command,
    ]


def ssh_argv_direct(
    host: str,
    port: int,
    key_path: str,
    remote_command: str,
    *,
    user: str = "root",
    connect_timeout: int = 30,
) -> list[str]:
    """Build the argv for a DIRECT (public-IP) SSH command against a RunPod pod.

    Used when the pod is launched with a public IP + exposed port 22 and our own
    ephemeral key in authorized_keys (set by the docker start command) — this
    sidesteps the account-key-only proxy entirely. Same non-interactive hygiene
    as the proxy path.
    """
    if not host or not port:
        raise ValueError("host and port are required for direct ssh")
    return [
        "ssh",
        "-i", key_path,
        "-p", str(port),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={connect_timeout}",
        f"{user}@{host}",
        remote_command,
    ]


def gen_ephemeral_keypair(dirpath: str | None = None) -> tuple[str, str]:
    """Generate a throwaway ed25519 keypair for one burst run.

    Returns ``(private_key_path, public_key_str)``. The public string goes to the
    pod as ``SSH_PUBLIC_KEY``; the private file is used by ``ssh -i`` and should
    live in a run-scoped temp dir that's discarded when the job ends.
    """
    dirpath = dirpath or tempfile.mkdtemp(prefix="ict-burst-key-")
    key_path = os.path.join(dirpath, "id_ed25519")
    # -N "" → no passphrase (BatchMode ssh); -C a stable, non-secret comment.
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "ict-burst", "-f", key_path],
        check=True,
        capture_output=True,
    )
    with open(f"{key_path}.pub", encoding="utf-8") as fh:
        pub = fh.read().strip()
    return key_path, pub


# Crypto regime/direction heads read `market_features`, derived from the PUBLIC
# Bybit-klines `market_raw` family — no journal/money-DB rows. These are the fixed
# build params the trainer VM's daily cycle uses (build_trainer_datasets.sh
# ::build_bybit_pair), replicated so the pod builds a byte-identical dataset.
_CRYPTO_MARKET_FEATURES_PARAMS = (
    "vol_window_n=20 forward_window_m=5 "
    "vol_threshold=0.005 trend_threshold=0.005 n_vol_buckets=3"
)


def build_remote_train_script(
    *,
    repo_sha: str,
    manifest_path: str,
    symbol: str,
    timeframe: str,
    version: str = "v002",
) -> str:
    """Assemble the bash the pod runs over SSH: clone → deps → build the PUBLIC
    market dataset → `python -m ml train <manifest>` → gzip|base64 a JSON bundle
    (the trained model_state + metrics + manifest) back to stdout.

    Matches the REAL `ml` CLI (verified against `scripts/ops/run_training_cycle.sh`
    + `build_trainer_datasets.sh`): `build-dataset market_raw` → `build-dataset
    market_features` → `ml train`. There is **no** ONNX export or parity-gate in
    the codebase — the trained artifact is the JSON-embedded LightGBM booster in
    `model_state.json`, which is what we return.

    Scope: crypto regime/direction heads only (`dataset.family: market_features`,
    a Bybit `*USDT` symbol) — those need no journal rows, so ONLY public code +
    public market data ever reach the pod (the data contract). The caller enforces
    that scope before building this script.

    Safety properties (asserted by tests): pinned SHA (no floating branch on a paid
    pod), `set -euo pipefail`, `ICT_OFFVM_BUILD_HOST=1` (the off-VM adapter guard),
    the artifact framed on stdout, and NO secret / money-DB / cred reference.
    """
    if not repo_sha:
        raise ValueError("repo_sha must be pinned (no floating branch on a paid pod)")
    if not (symbol and timeframe):
        raise ValueError("symbol and timeframe are required to build the dataset")
    raw_path = f"datasets-out/market_raw/{symbol}/{timeframe}/{version}"
    # A single marker pair frames the base64 payload so the runner can slice the
    # artifact cleanly out of any build chatter on stdout.
    return f"""set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
# The Bybit/off-VM market adapter refuses to run without this guard (it exists so
# a build never fires on the live trader VM). A rented pod is not that VM.
export ICT_OFFVM_BUILD_HOST=1
cd /workspace
rm -rf ict-trading-bot
git clone --quiet {REPO_URL}
cd ict-trading-bot
git checkout --quiet {repo_sha}
# --ignore-installed: the RunPod image ships distutils-installed packages (e.g.
# blinker 1.4) that pip cannot uninstall to satisfy a transitive dep, which aborts
# a plain `-r requirements.txt` ("Cannot uninstall ... distutils installed").
# Installing fresh over them sidesteps the whole class (requirements pins no torch,
# so the image's CUDA torch is untouched).
python -m pip install --quiet --ignore-installed -r requirements.txt
python -m pip install --quiet "ccxt>=4.0" "lightgbm>=4.0"
# >=5y window for the regime label (matches the daily cycle's rolling window).
MARKET_START="$(date -u -d '5 years ago' +%Y-%m-%d 2>/dev/null || echo 2021-01-01)"
MARKET_END="$(date -u +%Y-%m-%d)"
# Build the PUBLIC market dataset ON the pod (Bybit klines -> derived features);
# never the money DB (a regime head's market_features needs no journal rows).
python -m ml build-dataset market_raw --output-dir datasets-out --version {version} \\
    --source bybit_v5_offvm --symbol-scope {symbol} --timeframe {timeframe} --overwrite \\
    adapter=bybit_v5_offvm symbol={symbol} timeframe={timeframe} start="$MARKET_START" end="$MARKET_END"
python -m ml build-dataset market_features --output-dir datasets-out --version {version} \\
    --source {raw_path} --symbol-scope {symbol} --timeframe {timeframe} --overwrite \\
    market_raw_path={raw_path} {_CRYPTO_MARKET_FEATURES_PARAMS}
# Train the manifest (registers into the pod-local registry-store; discarded with
# the pod -- only the returned bundle survives).
python -m ml train {manifest_path} --datasets-root datasets-out \\
    --experiments-root ml/experiments-runs --registry-root ml/registry-store
# Bundle the freshest experiment run's model_state + metrics + manifest as ONE
# JSON blob for return (there's exactly one run -- the pod trains one manifest).
python3 - > /workspace/bundle.json <<'PYEOF'
import glob, json, os
runs = sorted(glob.glob('ml/experiments-runs/*/*/'), key=os.path.getmtime)
if not runs:
    raise SystemExit('no experiment run produced -- train did not write an artifact')
run = runs[-1]
def _load(name):
    p = os.path.join(run, name)
    return json.load(open(p)) if os.path.exists(p) else None
print(json.dumps({{
    'run_dir': run,
    'model_state': _load('model_state.json'),
    'metrics': _load('metrics.json'),
    'manifest': _load('manifest.json'),
}}))
PYEOF
echo '---ICT-ARTIFACT-BEGIN---'
gzip -c /workspace/bundle.json | base64 -w0
echo
echo '---ICT-ARTIFACT-END---'
"""


_ARTIFACT_BEGIN = "---ICT-ARTIFACT-BEGIN---"
_ARTIFACT_END = "---ICT-ARTIFACT-END---"


def extract_artifact_b64(stdout: str) -> str:
    """Slice the base64 artifact payload out of the remote stdout (between markers)."""
    try:
        after = stdout.split(_ARTIFACT_BEGIN, 1)[1]
        payload = after.split(_ARTIFACT_END, 1)[0]
    except IndexError as e:
        raise ValueError("artifact markers not found in remote stdout") from e
    return "".join(payload.split())  # drop newlines/whitespace ssh may wrap in


def decode_artifact_stream(b64_text: str) -> bytes:
    """Decode the gzip|base64 artifact payload back to the raw ONNX bytes."""
    raw = base64.b64decode(b64_text.strip())
    return gzip.decompress(raw)
