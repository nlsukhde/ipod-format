from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .config import load_settings
from .errors import IpodPrepError
from .planner import build_run_plan


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
        help="Only show the plan (no changes). This set only supports dry-run.",
    )
    return ap.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    ns = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        settings = load_settings(ns.config)
        if ns.log_level:
            # override from CLI if provided
            settings = type(settings)(
                **{**settings.__dict__, "log_level": ns.log_level})

        input_paths = [Path(p) for p in ns.paths]
        plans = build_run_plan(input_paths, settings)

        # Print a simple dry-run table
        print("\nPlan Preview (dry-run):")
        print("-" * 100)
        print(f"{'Source':60}  {'Codec':6}  {'Action':28}  {'Target'}")
        print("-" * 100)
        for plan in plans:
            action = "copy (mp3)" if not plan.needs_encode else "encode → mp3 320/44.1"
            print(
                f"{str(plan.src):60.60}  {plan.src_codec:6}  {action:28}  {plan.target.name}")
        print("-" * 100)
        print(f"Total files: {len(plans)}")
        print(
            f"Replace-in-place: {'ON' if settings.replace_in_place else 'OFF'} (delete mode: {settings.delete_mode})")
        print(
            f"Sample rate: {settings.sample_rate} | Bitrate: {settings.bitrate_mode} | ID3: v{settings.id3_version}")
        print("Artwork: 500x500 PNG (single image)")
        print("\nNext step: artwork extraction/normalization + encode/copy pipeline.")
        return 0

    except IpodPrepError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
