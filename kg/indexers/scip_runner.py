"""Drives scip-typescript as a subprocess.

scip-typescript itself runs in Node — we shell out to the pinned
binary under scip-indexer/node_modules. The output is a .scip file
which the rest of the pipeline reads via scip_loader.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCIP_TS_BIN = REPO_ROOT / "scip-indexer" / "node_modules" / ".bin" / "scip-typescript"
OUTPUT_DIR = REPO_ROOT / "scip-indexer"


@dataclass(slots=True)
class ScipRunResult:
    scip_path: Path
    duration_ms: int
    log_tail: str


def index_project(project: str, code_root: Path) -> ScipRunResult:
    """Run scip-typescript on the project. Returns path to the produced
    .scip file. Re-runs are full re-indexes — incremental support comes
    later via watchdog (`indexers/incremental.py`)."""
    if not SCIP_TS_BIN.exists():
        raise FileNotFoundError(
            f"scip-typescript binary missing at {SCIP_TS_BIN}. "
            f"Run `pnpm install` in scip-indexer/."
        )
    if not (code_root / "tsconfig.json").exists():
        raise FileNotFoundError(f"no tsconfig.json under {code_root}")

    out_file = OUTPUT_DIR / f"{project}.scip"
    cmd = [
        str(SCIP_TS_BIN),
        "index",
        "--cwd", str(code_root),
        "--output", str(out_file),
    ]
    # Workspace projects need --pnpm-workspaces; we detect via the
    # presence of a sibling pnpm-workspace.yaml.
    pnpm_ws = code_root.parent / "pnpm-workspace.yaml"
    if pnpm_ws.exists():
        # Index the whole workspace so cross-package refs resolve, then
        # rely on path filters downstream to keep just `code_root`.
        cmd = [
            str(SCIP_TS_BIN),
            "index",
            "--cwd", str(pnpm_ws.parent),
            "--pnpm-workspaces",
            "--output", str(out_file),
        ]

    started = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    duration = int((time.monotonic() - started) * 1000)
    log_tail = (proc.stdout + proc.stderr).strip().splitlines()
    tail = "\n".join(log_tail[-10:])
    if proc.returncode != 0 or not out_file.exists():
        raise RuntimeError(
            f"scip-typescript failed (rc={proc.returncode}). Last lines:\n{tail}"
        )
    return ScipRunResult(scip_path=out_file, duration_ms=duration, log_tail=tail)


def have_scip_runner() -> bool:
    return SCIP_TS_BIN.exists() and shutil.which("node") is not None
