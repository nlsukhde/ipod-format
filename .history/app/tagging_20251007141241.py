from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image
from mutagen import File
from mutagen.id3 import ID3, APIC, ID3NoHeaderError, ID3v2VersionError, Encoding, TIT2, TALB, TPE1, TPE2, TRCK, TPOS, TDRC, TCON, COMM


# --- helpers to read source tags from any supported input via mutagen ---
def _get_first(val):
    if isinstance(val, (list, tuple)) and val:
        return val[0]
    return val


def read_source_tags(src: Path) -> dict[str, str]:
    """
    Extract a minimal, portable tag set from the source file.
    We keep it conservative: artist, album, albumartist, title, track, tracktotal, disc, disctotal, date, genre.
    Missing fields are omitted.
    """
    audio = File(src)
    if audio is None:
        return {}

    tags: dict[str, str] = {}
    # Mutagen tag access varies by format; use generic keys first, then fallbacks.
    # Common generic keys often present: 'artist', 'album', 'albumartist', 'title', 'date', 'genre'
    for k in ("artist", "album", "albumartist", "title", "date", "genre"):
        v = audio.tags.get(k) if getattr(audio, "tags", None) else None
        if v:
            tags[k] = str(_get_first(v))

    # Track/Disc numbers may show up in different frames/atoms
    for alt, key in ((("tracknumber", "trkn", "track"), "track"),
                     (("totaltracks", "tracktotal"), "tracktotal"),
                     (("discnumber", "disk", "disc"), "disc"),
                     (("totaldiscs", "disctotal"), "disctotal")):
        val = None
        for a in (alt if isinstance(alt, (list, tuple)) else [alt]):
            v = audio.tags.get(a) if getattr(audio, "tags", None) else None
            if v:
                val = _get_first(v)
                break
        if val is not None:
            # Some atoms are tuples (track, total)
            if isinstance(val, (tuple, list)) and len(val) >= 1:
                tags[key] = str(val[0]) if val[0] not in (None, 0) else ""
                if len(val) >= 2 and val[1]:
                    tags[key + "total"] = str(val[1])
            else:
                tags[key] = str(val)

    # Fall back title from filename
    if not tags.get("title"):
        tags["title"] = src.stem

    return tags


def _ensure_id3_v23(path: Path) -> ID3:
    """
    Ensure there's an ID3 tag block and it's written as v2.3.
    """
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    # Force save as v2.3 later by calling tags.save(v2_version=3)
    return tags


def _strip_frames(tags: ID3, patterns: tuple[str, ...]) -> None:
    """
    Remove noisy frames like TXXX:iTunNORM, PRIV, etc.
    patterns: exact frame ids (e.g., 'PRIV') or 'TXXX:NAME' to match user-text frames by desc.
    """
    to_delete = []
    for frame in tags.keys():
        # Exact frame id (e.g., 'PRIV')
        if frame in patterns:
            to_delete.append(frame)
            continue
        # TXXX by description
        if frame.startswith("TXXX") and any(pat.startswith("TXXX:") and pat.split(":", 1)[1] in str(tags[frame]) for pat in patterns):
            to_delete.append(frame)
    for f in to_delete:
        del tags[f]


def _load_png_bytes(png_path: Path) -> bytes:
    with png_path.open("rb") as fh:
        data = fh.read()
    # sanity: confirm it's 500x500 PNG RGB
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

    # Map minimal tags
    if (t := src_tags.get("title")):
        tags.add(TIT2(encoding=Encoding.UTF8, text=t))
    if (a := src_tags.get("album")):
        tags.add(TALB(encoding=Encoding.UTF8, text=a))
    if (ar := src_tags.get("artist")):
        tags.add(TPE1(encoding=Encoding.UTF8, text=ar))
    if (aa := src_tags.get("albumartist") or src_tags.get("album_artist")):
        tags.add(TPE2(encoding=Encoding.UTF8, text=aa))
    if (g := src_tags.get("genre")):
        tags.add(TCON(encoding=Encoding.UTF8, text=g))

    # Track/Disc numbers
    trk = src_tags.get("track")
    trk_total = src_tags.get("tracktotal") or src_tags.get("totaltracks")
    if trk_total and trk:
        tags.add(TRCK(encoding=Encoding.UTF8, text=f"{trk}/{trk_total}"))
    elif trk:
        tags.add(TRCK(encoding=Encoding.UTF8, text=str(trk)))

    dsc = src_tags.get("disc")
    dsc_total = src_tags.get("disctotal")
    if dsc_total and dsc:
        tags.add(TPOS(encoding=Encoding.UTF8, text=f"{dsc}/{dsc_total}"))
    elif dsc:
        tags.add(TPOS(encoding=Encoding.UTF8, text=str(dsc)))

    # Year/Date – ID3v2.3 commonly uses TYER/TDRC; mutagen writes TDRC
    if (d := src_tags.get("date")):
        tags.add(TDRC(encoding=Encoding.UTF8, text=d))

    # Remove noisy vendor/private frames
    _strip_frames(tags, strip_patterns)

    # Replace any existing pictures with a single APIC
    for ap in list(tags.getall("APIC")):
        tags.delall("APIC")
        break

    if png500 and png500.exists():
        pic = _load_png_bytes(png500)
        tags.add(APIC(
            encoding=Encoding.UTF8,
            mime="image/png",
            type=3,  # front cover
            desc="Cover (front)",
            data=pic,
        ))

    # Persist as v2.3
    tags.save(temp_mp3, v2_version=3)
