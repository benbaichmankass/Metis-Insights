#!/usr/bin/env python3
"""On-pod exec building blocks for the RunPod burst tier (M19 Tier-1).

Pure, unit-testable helpers the burst adapter uses to drive a rented pod over
**RunPod proxy SSH** with an **ephemeral per-run keypair** — so no account SSH
key and no GitHub secret are ever needed (the key is born and dies with the run).
Design: `docs/research/T1-gpu-burst-spend-SPEC.md` § "On-pod exec design".

Split out from `runpod_burst.py` so the SSH-argv construction, the remote
train-script assembly, and the artifact decode can be tested WITHOUT a live pod
(the parts that genuinely need a pod — the ssh round-trips — stay in the adapter
and are validated by a live `verify`/smoke run). Nothing here spends money or
opens a network socket.

Data contract (enforced by the script this builds): only PUBLIC code + PUBLIC
market data ever reach the pod; the trained model comes back as gzip|base64 on
the SSH stdout. No secret, money-DB, or live cred is referenced anywhere in the
remote script (asserted by tests).
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


def build_remote_train_script(
    *,
    repo_sha: str,
    manifest: str,
    dataset_family: str,
    artifact_name: str = "model.onnx",
) -> str:
    """Assemble the bash the pod runs over SSH: clone → deps → dataset → train →
    ONNX export + parity gate → gzip|base64 the artifact to stdout.

    v1-DRAFT for the exact `python -m ml …` invocations — the CLI flags are
    finalized against the first live pod (a cheap smoke run); the STRUCTURE +
    safety properties (pinned SHA, public-only inputs, parity-or-abort, artifact
    on stdout, `set -euo pipefail`) are fixed and asserted by tests.
    """
    if not repo_sha:
        raise ValueError("repo_sha must be pinned (no floating branch on a paid pod)")
    # A single marker frames the base64 payload so the runner can slice the
    # artifact cleanly out of any build chatter on stdout.
    return f"""set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
cd /workspace
rm -rf ict-trading-bot
git clone --quiet {REPO_URL}
cd ict-trading-bot
git checkout --quiet {repo_sha}
python -m pip install --quiet -r requirements.txt
# Build the dataset ON the pod from PUBLIC data + the committed corpus — never the
# money DB (a regime head's market_features needs no journal rows).
python -m ml build-dataset {dataset_family}
# Train on the GPU, export to CPU/ONNX, and gate on numeric parity vs onnxruntime.
python -m ml train {manifest} --export-onnx {artifact_name} --parity-gate
# Emit the parity-validated artifact as gzip|base64 between markers.
echo '---ICT-ARTIFACT-BEGIN---'
gzip -c {artifact_name} | base64 -w0
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
