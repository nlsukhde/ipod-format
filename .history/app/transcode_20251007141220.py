from __future__ import annotations

import shutil
from pathlib import Path

from .models import Settings, TrackPlan
from .utils_ffmpeg import run_ffmpeg


def encode_to_temp(plan: TrackPlan, settings: Settings) -> None:
    """
    Encode non-MP3 sources to MP3 320 kbps CBR, 44.1 kHz, writing to plan.temp_path.
    """
    assert plan.needs_encode, "encode_to_temp called for MP3 source"
    args = [
        "-y",
        "-i", str(plan.src),
        "-vn",
        "-c:a", "libmp3lame",
        "-b:a", "320k",
    ]
    if settings.sample_rate == "44100":
        args += ["-ar", "44100"]
    args += [str(plan.temp_path)]
    run_ffmpeg(settings, args)


def copy_mp3_to_temp(plan: TrackPlan) -> None:
    """
    For MP3 inputs when we don't re-encode: copy the file to plan.temp_path.
    """
    # Keep it simple and robust: straight file copy. Tagging step will rewrite ID3 anyway.
    shutil.copy2(plan.src, plan.temp_path)
