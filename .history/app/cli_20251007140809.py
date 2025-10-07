from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .config import load_settings
from .errors import IpodPrepError
from .planner import build_run_plan
from .artwork import detect_art_source, extract_art_to_file, normalize_art_to_png_500


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="ipodprep",
        description="Prepare iPod-friendly MP3s (320 CBR, 500x500 cover, ID3 v2.3) — dry-run scaffold",
    )
    ap.add_argument("paths", nargs="+", help="Album folders or audio files")
    ap.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to settings.toml (optional; env FFMPEG_PATH is honored).",
    )
    ap.add_argument(
        "--log-level",
        choices=("info", "debug"),
        default=None,
        help="Override logging level (info/debug).",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show the plan (no changes).",
    )
    return ap.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    ns = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        settings = load_settings(ns.config)
        if ns.log_level:
            settings = type(settings)(
                **{**settings.__dict__, "log_level": ns.log_level})

        input_paths = [Path(p) for p in ns.paths]
        plans = build_run_plan(input_paths, settings)

        # Detect artwork source for each plan; prepare normalized PNG path (temp)
        # We perform extraction+normalize in dry-run so you can verify the logic.
        rows = []
        for plan in plans:
            art = detect_art_source(plan, settings)
            src_image = extract_art_to_file(plan, settings, art)
            png500 = normalize_art_to_png_500(plan, settings, src_image)
            # freeze ArtSource with normalized path for display
            plan = type(plan)(**{**plan.__dict__, "art_source": type(
                plan.art_source)(**{**art.__dict__, "normalized_png": png500})})
            rows.append((plan, art))

        # Print a dry-run table with artwork info
        print("\nPlan Preview (dry-run, with artwork):")
        print("-" * 130)
        print(
            f"{'Source':60}  {'Codec':6}  {'Action':22}  {'Art Source':28}  {'PNG500?':8}  {'Target'}")
        print("-" * 130)
        for (plan, art) in rows:
            action = "copy (mp3)" if not plan.needs_encode else "encode → mp3 320/44.1"
            art_label = f"{art.kind}"
            if art.detail:
                art_label += f" [{art.detail}]"
            png_ok = "yes" if plan.art_source.normalized_png and Path(
                plan.art_source.normalized_png).exists() else "no"
            print(
                f"{str(plan.src):60.60}  {plan.src_codec:6}  {action:22}  {art_label:28.28}  {png_ok:8}  {plan.target.name}")
        print("-" * 130)
        print(f"Total files: {len(rows)}")
        print(
            f"Replace-in-place: {'ON' if settings.replace_in_place else 'OFF'} (delete mode: {settings.delete_mode})")
        print(
            f"Sample rate: {settings.sample_rate} | Bitrate: {settings.bitrate_mode} | ID3: v{settings.id3_version}")
        print("Artwork: 500x500 PNG (single image)")
        print("\nNext step: wire audio encode/copy + tagging + validation + commit.")
        return 0

    except IpodPrepError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
