from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image
from mutagen import File
from mutagen.id3 import (
    ID3,
    APIC,
    ID3NoHeaderError,
    Encoding,
    TIT2, TALB, TPE1, TPE2, TRCK, TPOS, TDRC, TCON,
)

# --- helpers to read source tags from any supported input via mutagen ---


def _get_first(val):
    if isinstance(val, (list, tuple)) and val:
        return val[0]
    return val


def read_source_tags(src: Path) -> dict[str, str]:
    """
    Extract a minimal, portable tag set from the source file.
    We keep it conservative: artist, album, albumartist, title, track, tracktotal,
    disc, disctotal, date, genre. Missing fields are omitted.
    """
    audio = File(src)
    if audio is None or not getattr(audio, "tags", None):
        # Fall back title from filename if we have nothing
        return {"title": src.stem}

    tags: dict[str, str] = {}
    # Generic keys commonly present across formats
    for k in ("artist", "album", "albumartist", "title", "date", "genre"):
        v = audio.tags.get(k)
        if v:
            tags[k] = str(_get_first(v))

    # Track/Disc numbers can appear under different names/structures
    def pull_any(keys: tuple[str, ...]) -> Optional[object]:
        for k in keys:
            v = audio.tags.get(k)
            if v is not None:
                return v
        return None

    trk = pull_any(("tracknumber", "track", "trkn"))
    if isinstance(trk, (tuple, list)) and trk:
        tags["track"] = str(trk[0]) if trk[0] not in (None, 0) else ""
        if len(trk) > 1 and trk[1]:
            tags["tracktotal"] = str(trk[1])
    elif trk is not None:
        tags["track"] = str(_get_first(trk))

    trk_total = pull_any(("totaltracks", "tracktotal"))
    if trk_total is not None and "tracktotal" not in tags:
        tags["tracktotal"] = str(_get_first(trk_total))

    dsc = pull_any(("discnumber", "disc", "disk"))
    if isinstance(dsc, (tuple, list)) and dsc:
        tags["disc"] = str(dsc[0]) if dsc[0] not in (None, 0) else ""
        if len(dsc) > 1 and dsc[1]:
            tags["disctotal"] = str(dsc[1])
    elif dsc is not None:
        tags["disc"] = str(_get_first(dsc))

    dsc_total = pull_any(("totaldiscs", "disctotal"))
    if dsc_total is not None and "disctotal" not in tags:
        tags["disctotal"] = str(_get_first(dsc_total))

    # Fall back title from filename if still missing
    if not tags.get("title"):
        tags["title"] = src.stem

    return tags


def _ensure_id3_v23(path: Path) -> ID3:
    """
    Ensure there's an ID3 tag block and it's written as v2.3 on save.
    """
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    return tags


def _strip_frames(tags: ID3, patterns: tuple[str, ...]) -> None:
    """
    Remove noisy frames like TXXX:iTunNORM, PRIV, etc.
    'patterns' accepts exact frame IDs (e.g., 'PRIV') and 'TXXX:NAME' to match TXXX by description.
    """
    # Collect keys to delete first to avoid mutating while iterating
    to_delete = []
    for key in list(tags.keys()):
        if key in patterns:
            to_delete.append(key)
            continue
        if key.startswith("TXXX"):
            # match any requested TXXX:DESC
            for pat in patterns:
                if pat.startswith("TXXX:"):
                    desc = pat.split(":", 1)[1]
                    # tag string contains desc; safe to check string form
                    if desc in str(tags.get(key)):
                        to_delete.append(key)
                        break
    for k in to_delete:
        try:
            del tags[k]
        except Exception:
            pass


def _load_png_bytes(png_path: Path) -> bytes:
    with png_path.open("rb") as fh:
        data = fh.read()
    # sanity: confirm it's 500x500 PNG
    im = Image.open(BytesIO(data))
    im.load()
    if im.size != (500, 500):
        raise ValueError(f"APIC PNG not 500x500, got {im.size}")
    return data


def write_id3_v23_with_apic(temp_mp3: Path, png500: Optional[Path], src_tags: dict, strip_patterns: tuple[str, ...]) -> None:
    """
    Overwrite temp_mp3's ID3 tags with v2.3 frames and a single Front Cover APIC.
    """
    tags = _ensure_id3_v23(temp_mp3)

    # Replace core text frames
    def set_text(frame_cls, value: Optional[str]):
        if value:
            tags.add(frame_cls(encoding=Encoding.UTF8, text=value))

    set_text(TIT2, src_tags.get("title"))
    set_text(TALB, src_tags.get("album"))
    set_text(TPE1, src_tags.get("artist"))
    set_text(TPE2, src_tags.get("albumartist") or src_tags.get("album_artist"))
    set_text(TCON, src_tags.get("genre"))

    # Track / Disc
    trk, trk_total = src_tags.get("track"), src_tags.get("tracktotal")
    if trk and trk_total:
        tags.add(TRCK(encoding=Encoding.UTF8, text=f"{trk}/{trk_total}"))
    elif trk:
        tags.add(TRCK(encoding=Encoding.UTF8, text=str(trk)))

    dsc, dsc_total = src_tags.get("disc"), src_tags.get("disctotal")
    if dsc and dsc_total:
        tags.add(TPOS(encoding=Encoding.UTF8, text=f"{dsc}/{dsc_total}"))
    elif dsc:
        tags.add(TPOS(encoding=Encoding.UTF8, text=str(dsc)))

    # Date/year
    if (d := src_tags.get("date")):
        tags.add(TDRC(encoding=Encoding.UTF8, text=d))

    # Strip noisy frames (PRIV, TXXX:iTunNORM, etc.)
    _strip_frames(tags, strip_patterns)

    # Single APIC only
    tags.delall("APIC")
    if png500 and png500.exists():
        pic = _load_png_bytes(png500)
        tags.add(APIC(
            encoding=Encoding.UTF8,
            mime="image/png",
            type=3,  # front cover
            desc="Cover (front)",
            data=pic,
        ))

    # Save explicitly as v2.3
    tags.save(temp_mp3, v2_version=3)
