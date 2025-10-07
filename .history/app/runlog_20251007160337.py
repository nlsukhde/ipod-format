from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_run_dir(base: Path | None = None) -> Path:
    """
    Create logs/run-YYYYMMDD-HHMMSS directory and return it.
    """
    base = base or Path("logs")
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("run-%Y%m%d-%H%M%S")
    run_dir = base / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_manifest(run_dir: Path, manifest: Dict[str, Any]) -> Path:
    out = run_dir / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2,
                   ensure_ascii=False), encoding="utf-8")
    return out
