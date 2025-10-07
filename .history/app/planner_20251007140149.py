from __future__ import annotations

import itertools
import os
import uuid
from pathlib import Path
from typing import Iterable

from .errors import PlanError
from .models import Settings, TrackPlan


_AUDIO_EXTS = {
    ".flac": "flac",
    ".alac": "alac",
    ".m4a": "m4a",
    ".aac": "m4a",
    ".wav": "wav",
    ".aiff": "aiff",
    ".aif": "aiff",
    ".ogg": "ogg",
    ".oga": "ogg",
    ".mp3": "mp3",
}


def _iter_audio_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        p = p.resolve()
        if p.is_file():
            if p.suffix.lower() in _AUDIO_EXTS:
                files.append(p)
        elif p.is_dir():
            for root, _, filenames in os.walk(p):
                for name in filenames:
                    ext = Path(name).suffix.lower()
                    if ext in _AUDIO_EXTS:
                        files.append(Path(root) / name)
        else:
            # ignore invalid paths
            continue
    # stable, human-friendly order
    files.sort()
    return files


def _temp_path_for(target_dir: Path, basename: str) -> Path:
    # Windows-friendly temp in same dir; hidden-ish name
    return target_dir / f".~{basename}.{uuid.uuid4().hex}.tmp"


def build_run_plan(input_paths: list[Path], settings: Settings) -> list[TrackPlan]:
    audio_files = _iter_audio_files(input_paths)
    if not audio_files:
        raise PlanError("No supported audio files found.")

    plans: list[TrackPlan] = []
    for src in audio_files:
        ext = src.suffix.lower()
        src_codec = _AUDIO_EXTS[ext]

        basename = src.stem  # keep same base name
        target = src.with_suffix(".mp3")

        needs_encode = src_codec != "mp3"

        # Collision policy is applied later at commit time; we plan regardless.
        temp = _temp_path_for(src.parent, basename)

        plan = TrackPlan(
            src=src,
            src_codec=src_codec,
            needs_encode=needs_encode,
            target=target,
            temp_path=temp,
            delete_mode=settings.delete_mode,
            collision=settings.collision,
            sample_rate=settings.sample_rate,
            bitrate_mode=settings.bitrate_mode,
            art_source="unknown",
            notes=[],
        )

        # Policy notes (helpful in dry-run)
        if not needs_encode:
            plan.notes.append("will copy audio (already MP3)")
        else:
            plan.notes.append("will encode to 320 CBR, 44.1 kHz")

        plans.append(plan)

    # Keep directory & track-like ordering (best-effort)
    plans.sort(key=lambda p: (str(p.src.parent), p.src.name))
    return plans
