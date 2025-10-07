from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from .models import ArtSource, Settings, TrackPlan
from .utils_ffmpeg import run_ffmpeg, run_ffprobe_json, ProcError


_FOLDER_CANDIDATES = [
    "cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.jpeg", "folder.png",
]


def _find_folder_art(directory: Path) -> Optional[Path]:
    # Priority order; first match wins
    for name in _FOLDER_CANDIDATES:
        cand = directory / name
        if cand.is_file():
            return cand
    return None


def detect_art_source(plan: TrackPlan, settings: Settings) -> ArtSource:
    """
    Decide where art will come from:
      1) embedded 'attached_pic' stream (first one)
      2) folder file cover/folder.*
      3) none
    """
    # Try folder art first if present (your workflow often keeps cover.jpg)
    folder = _find_folder_art(plan.src.parent)
    if folder:
        return ArtSource(kind="folder", file_path=folder, detail=folder.name)

    # Probe embedded image streams
    try:
        data = run_ffprobe_json(
            settings,
            [
                "-v", "error",
                "-select_streams", "v",
                "-show_entries", "stream=index,disposition,codec_name,width,height",
                "-of", "json",
                str(plan.src),
            ],
        )
        streams = data.get("streams", [])
        # Choose first stream where disposition.attached_pic == 1
        for st in streams:
            disp = st.get("disposition", {})
            if disp.get("attached_pic") == 1:
                idx = int(st.get("index", 0))
                w = st.get("width")
                h = st.get("height")
                detail = f"embedded:v:{idx} ({st.get('codec_name','?')} {w}x{h})"
                return ArtSource(kind="embedded", stream_index=idx, detail=detail)
        # If any video stream exists, fall back to the first one
        if streams:
            idx = int(streams[0].get("index", 0))
            w = streams[0].get("width")
            h = streams[0].get("height")
            detail = f"embedded:v:{idx} ({streams[0].get('codec_name','?')} {w}x{h})"
            return ArtSource(kind="embedded", stream_index=idx, detail=detail)
    except ProcError:
        # probing failed → ignore and fall back to none
        pass

    return ArtSource(kind="none", detail="no art")


def _temp_art_path(plan: TrackPlan, suffix: str) -> Path:
    return plan.temp_path.with_suffix(suffix)


def extract_art_to_file(plan: TrackPlan, settings: Settings, art: ArtSource) -> Optional[Path]:
    """
    Extract raw source art into a temporary file (jpg/png).
    Returns the extracted file path, or None if no art.
    """
    if art.kind == "none":
        return None

    out_path = _temp_art_path(plan, ".art_src.jpg")
    if art.kind == "folder" and art.file_path:
        # Just copy the folder file to temp (keep original format extension if png)
        out_path = _temp_art_path(plan, art.file_path.suffix.lower())
        shutil.copy2(art.file_path, out_path)
        return out_path

    if art.kind == "embedded" and art.stream_index is not None:
        # Extract the attached picture stream to a single frame file
        # Use -frames:v 1 to avoid image2 sequence warnings
        run_ffmpeg(
            settings,
            [
                "-y",
                "-i", str(plan.src),
                "-map", f"0:v:{art.stream_index}",
                "-c", "copy",
                "-frames:v", "1",
                str(out_path),
            ],
        )
        return out_path

    return None


def normalize_art_to_png_500(plan: TrackPlan, settings: Settings, src_image: Optional[Path]) -> Optional[Path]:
    """
    Convert/crop the src image to a 500x500 PNG (center-crop).
    Returns the normalized PNG path or None.
    """
    if not src_image or not src_image.exists():
        return None
    out_png = _temp_art_path(plan, ".cover_500.png")

    vf = (
        f"scale={settings.art_target_px}:{settings.art_target_px}:force_original_aspect_ratio=increase,"
        f"crop={settings.art_target_px}:{settings.art_target_px}"
        if settings.art_mode == "center_crop"
        else f"scale={settings.art_target_px}:{settings.art_target_px}:force_original_aspect_ratio=decrease,"
             f"pad={settings.art_target_px}:{settings.art_target_px}:(ow-iw)/2:(oh-ih)/2"
    )

    run_ffmpeg(
        settings,
        [
            "-y",
            "-i", str(src_image),
            "-vf", vf,
            str(out_png),
        ],
    )
    return out_png
