from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    raise SystemExit("Python 3.11+ is required (for tomllib).")

from .errors import ConfigError
from .models import Settings


_DEFAULTS_TOML = b"""
[encoding]
profile = "CBR320"          # maps to -b:a 320k
sample_rate = "44100"       # or "preserve"

[id3]
version = "2.3"
strip_frames = ["TXXX:iTunNORM", "PRIV"]

[artwork]
target_px = 500
format = "jpeg"
mode = "center_crop"
single_image_only = true

[replace_in_place]
enabled = true
delete_mode = "trash"       # or "hard"
require_success_checks = true

[collision]
if_target_exists = "overwrite"   # or "skip" | "version"

[validation]
min_duration_sec = 10
verify_cover = true
verify_id3_v23 = true

[logging]
level = "info"
write_manifest = true

[system]
ffmpeg_path = "ffmpeg"
"""


def _coerce_settings(raw: dict[str, Any]) -> Settings:
    def get(path: list[str], default: Any) -> Any:
        cur: Any = raw
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
        return cur

    bitrate_mode = get(["encoding", "profile"], "CBR320")
    if bitrate_mode != "CBR320":
        raise ConfigError(
            f"Unsupported encoding.profile={bitrate_mode!r}; only 'CBR320' is supported in v1.")

    sample_rate = get(["encoding", "sample_rate"], "44100")
    if sample_rate not in ("44100", "preserve"):
        raise ConfigError(
            "encoding.sample_rate must be '44100' or 'preserve'.")

    id3_version = get(["id3", "version"], "2.3")
    if id3_version != "2.3":
        raise ConfigError("Only ID3 v2.3 is supported in v1.")

    strip_frames = tuple(
        get(["id3", "strip_frames"], ["TXXX:iTunNORM", "PRIV"]))

    art_px = int(get(["artwork", "target_px"], 500))
    if art_px <= 0:
        raise ConfigError("artwork.target_px must be > 0")

    art_format = get(["artwork", "format"], "jpeg")
    if art_format not in ("png", "jpeg"):
        raise ConfigError("artwork.format must be 'png' or 'jpeg' in v1.")

    art_mode = get(["artwork", "mode"], "center_crop")
    if art_mode not in ("center_crop", "pad"):
        raise ConfigError("artwork.mode must be 'center_crop' or 'pad'.")

    single_image_only = bool(get(["artwork", "single_image_only"], True))

    replace_enabled = bool(get(["replace_in_place", "enabled"], True))
    delete_mode = get(["replace_in_place", "delete_mode"], "trash")
    if delete_mode not in ("trash", "hard"):
        raise ConfigError(
            "replace_in_place.delete_mode must be 'trash' or 'hard'.")

    require_checks = bool(
        get(["replace_in_place", "require_success_checks"], True))

    collision = get(["collision", "if_target_exists"], "overwrite")
    if collision not in ("overwrite", "skip", "version"):
        raise ConfigError(
            "collision.if_target_exists must be 'overwrite' | 'skip' | 'version'.")

    min_duration = int(get(["validation", "min_duration_sec"], 10))
    verify_cover = bool(get(["validation", "verify_cover"], True))
    verify_id3_v23 = bool(get(["validation", "verify_id3_v23"], True))

    log_level = get(["logging", "level"], "info")
    if log_level not in ("info", "debug"):
        raise ConfigError("logging.level must be 'info' or 'debug'.")
    write_manifest = bool(get(["logging", "write_manifest"], True))

    ffmpeg_path = get(["system", "ffmpeg_path"], "ffmpeg")
    # Environment override wins
    ffmpeg_path = os.getenv("FFMPEG_PATH", ffmpeg_path)

    return Settings(
        bitrate_mode="CBR320",
        sample_rate=sample_rate,  # type: ignore[arg-type]
        id3_version="2.3",
        strip_frames=strip_frames,
        art_target_px=art_px,
        art_format=art_format,  # type: ignore[arg-type]
        art_mode=art_mode,  # type: ignore[arg-type]
        single_image_only=single_image_only,
        replace_in_place=replace_enabled,
        delete_mode=delete_mode,  # type: ignore[arg-type]
        require_success_checks=require_checks,
        collision=collision,  # type: ignore[arg-type]
        min_duration_sec=min_duration,
        verify_cover=verify_cover,
        verify_id3_v23=verify_id3_v23,
        log_level=log_level,  # type: ignore[arg-type]
        write_manifest=write_manifest,
        ffmpeg_path=ffmpeg_path,
    )


def load_settings(config_path: Path | None) -> Settings:
    """
    Load settings.toml if present; otherwise use built-in defaults.
    Apply environment overrides (FFMPEG_PATH).
    """
    if config_path is None:
        # Try repo default path first
        default_file = Path("config") / "settings.toml"
        if default_file.is_file():
            config_path = default_file
        else:
            # No file → use embedded defaults
            raw = tomllib.loads(_DEFAULTS_TOML.decode("utf-8"))
            return _coerce_settings(raw)

    try:
        data = config_path.read_bytes()
    except FileNotFoundError as e:  # pragma: no cover
        raise ConfigError(f"Config file not found: {config_path}") from e

    try:
        raw = tomllib.loads(data.decode("utf-8"))
    except Exception as e:  # pragma: no cover
        raise ConfigError(f"Failed parsing TOML at {config_path}: {e}") from e

    return _coerce_settings(raw)
