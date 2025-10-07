from __future__ import annotations

import os
from pathlib import Path

from send2trash import send2trash

from .models import CollisionPolicy, Settings, TrackPlan


def _versioned_name(path: Path) -> Path:
    base = path.stem
    ext = path.suffix
    i = 2
    while True:
        cand = path.with_name(f"{base} ({i}){ext}")
        if not cand.exists():
            return cand
        i += 1


def atomic_commit(plan: TrackPlan) -> Path:
    """
    Move temp to final target according to collision policy.
    Returns the final target path.
    """
    target = plan.target
    if target.exists():
        if plan.collision == "skip":
            return target
        elif plan.collision == "version":
            target = _versioned_name(target)
        # overwrite → proceed

    # os.replace is atomic on the same filesystem
    os.replace(plan.temp_path, target)
    return target


def delete_source(plan: TrackPlan) -> None:
    if plan.delete_mode == "trash":
        send2trash(str(plan.src))
    else:
        try:
            plan.src.unlink()
        except FileNotFoundError:
            pass
