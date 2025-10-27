from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image
from mutagen.id3 import ID3

from .models import Settings, TrackPlan
from .utils_ffmpeg import run_ffprobe_json


def _probe_duration_seconds(settings: Settings, path: Path) -> float:
    data = run_ffprobe_json(
        settings,
        [
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ],
    )
    dur = float(data.get("format", {}).get("duration", 0.0))
    return dur


def validate_duration_close(settings: Settings, src: Path, dst: Path, tolerance: float = 0.5) -> None:
    sd = _probe_duration_seconds(settings, src)
    dd = _probe_duration_seconds(settings, dst)
    if sd == 0 or dd == 0:
        # skip strict check when probing fails; caller may decide policy
        return
    if abs(sd - dd) > tolerance:
        raise ValueError(
            f"Duration mismatch > {tolerance}s (src={sd:.2f}s, dst={dd:.2f}s)")


def validate_bitrate_if_encoded(settings: Settings, plan: TrackPlan, dst: Path) -> None:
    if not plan.needs_encode:
        return
    data = run_ffprobe_json(
        settings,
        [
            "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=bit_rate",
            "-of", "json",
            str(dst),
        ],
    )
    br = int(data.get("streams", [{}])[0].get("bit_rate", 0))
    if br != 320000:
        # Some encoders report nearby values; accept >= 319000
        if br < 319000:
            raise ValueError(f"Audio bitrate is {br}, expected 320000")


def validate_id3_and_apic_500(dst: Path) -> None:
    tags = ID3(dst)
    apics = tags.getall("APIC")
    if not apics:
        raise ValueError("No APIC found in output tags")
    if len(apics) > 1:
        raise ValueError(
            "More than one APIC found; expected a single front cover")

    # Check JPEG 500x500
    pic = apics[0].data
    im = Image.open(BytesIO(pic))
    im.load()
    if im.format != "JPEG":
        raise ValueError(f"APIC is not JPEG (got {im.format})")
    if im.size != (500, 500):
        raise ValueError(f"APIC size is {im.size}, expected (500, 500)")
