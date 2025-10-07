from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional


BitrateMode = Literal["CBR320"]
DeleteMode = Literal["trash", "hard"]
CollisionPolicy = Literal["overwrite", "skip", "version"]
SampleRatePolicy = Literal["44100", "preserve"]


@dataclass(frozen=True)
class Settings:
    # Encoding
    bitrate_mode: BitrateMode = "CBR320"
    sample_rate: SampleRatePolicy = "44100"

    # ID3
    id3_version: Literal["2.3"] = "2.3"
    strip_frames: tuple[str, ...] = ("TXXX:iTunNORM", "PRIV")

    # Artwork
    art_target_px: int = 500
    art_format: Literal["png"] = "png"
    art_mode: Literal["center_crop", "pad"] = "center_crop"
    single_image_only: bool = True

    # Replace-in-place behavior
    replace_in_place: bool = True
    delete_mode: DeleteMode = "trash"  # default to recycle bin
    require_success_checks: bool = True

    # Collision handling for target .mp3
    collision: CollisionPolicy = "overwrite"

    # Validation knobs
    min_duration_sec: int = 10
    verify_cover: bool = True
    verify_id3_v23: bool = True

    # Logging
    log_level: Literal["info", "debug"] = "info"
    write_manifest: bool = True

    # System / external tools
    ffmpeg_path: str = "ffmpeg"  # will honor FFMPEG_PATH env if present


@dataclass(frozen=True)
class TrackPlan:
    src: Path
    src_codec: str  # e.g. "flac", "alac", "wav", "mp3", "m4a", "ogg"
    needs_encode: bool  # False for mp3 inputs when we keep audio
    target: Path       # same directory, basename + .mp3
    temp_path: Path    # temporary file path (same dir, hidden/random)
    delete_mode: DeleteMode
    collision: CollisionPolicy
    sample_rate: SampleRatePolicy
    bitrate_mode: BitrateMode

    # Artwork decision is filled in next set
    art_source: str = "unknown"  # placeholder until artwork step
    notes: list[str] = field(default_factory=list)
