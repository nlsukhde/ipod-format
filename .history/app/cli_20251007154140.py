from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .config import load_settings
from .errors import IpodPrepError
from .planner import build_run_plan
from .artwork import detect_art_source, extract_art_to_file, normalize_art_to_png_500
from .transcode import encode_to_temp, copy_mp3_to_temp
from .tagging import read_source_tags, write_id3_v23_with_apic
from .validate import validate_duration_close, validate_bitrate_if_encoded, validate_id3_and_apic_500
from .fsops import atomic_commit, delete_source


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="ipodprep",
        description="Prepare iPod-friendly MP3s (320 CBR, 500x500 cover, ID3 v2.3) — replace-in-place.",
    )
    ap.add_argument("paths", nargs="+", help="Album folders or audio files")
    ap.add_argument("--config", type=Path, default=None,
                    help="Path to settings.toml (optional).")
    ap.add_argument("--log-level", choices=("info", "debug"),
                    default=None, help="Override logging level.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Only show the plan (no changes).")
    ap.add_argument("--no-replace", action="store_true",
                    help="Keep originals (do not delete sources).")
    ap.add_argument("--hard-delete", action="store_true",
                    help="Delete sources permanently (default: recycle bin).")
    ap.add_argument("--collision", choices=("overwrite", "skip",
                    "version"), help="Target collision policy.")
    return ap.parse_args(argv)


def _print_plan(plans, settings):
    print("\nPlan Preview (with artwork):")
    print("-" * 130)
    print(f"{'Source':60}  {'Codec':6}  {'Action':22}  {'Art Source':28}  {'Target'}")
    print("-" * 130)
    for (plan, art, png500) in plans:
        action = "copy (mp3)" if not plan.needs_encode else "encode → mp3 320/44.1"
        art_label = f"{art.kind}"
        if getattr(art, "detail", ""):
            art_label += f" [{art.detail}]"
        print(f"{str(plan.src):60.60}  {plan.src_codec:6}  {action:22}  {art_label:28.28}  {plan.target.name}")
    print("-" * 130)
    print(f"Total files: {len(plans)}")
    print(
        f"Replace-in-place: {'OFF' if not settings.replace_in_place else 'ON'} (delete mode: {settings.delete_mode})")
    print(
        f"Sample rate: {settings.sample_rate} | Bitrate: {settings.bitrate_mode} | ID3: v{settings.id3_version}")
    print("Artwork: 500x500 PNG (single image)")


def main(argv: Sequence[str] | None = None) -> int:
    ns = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        settings = load_settings(ns.config)
        # CLI overrides
        if ns.log_level:
            settings = type(settings)(
                **{**settings.__dict__, "log_level": ns.log_level})
        if ns.no_replace:
            settings = type(settings)(
                **{**settings.__dict__, "replace_in_place": False})
        if ns.hard_delete:
            settings = type(settings)(
                **{**settings.__dict__, "delete_mode": "hard"})
        if ns.collision:
            settings = type(settings)(
                **{**settings.__dict__, "collision": ns.collision})

        input_paths = [Path(p) for p in ns.paths]
        base_plans = build_run_plan(input_paths, settings)

        # Detect artwork + prepare normalized PNG for each plan
        rows = []
        for plan in base_plans:
            art = detect_art_source(plan, settings)
            src_image = extract_art_to_file(plan, settings, art)
            png500 = normalize_art_to_png_500(plan, settings, src_image)
            rows.append((plan, art, src_image, png500))

        if ns.dry_run:
            _print_plan(rows, settings)
            print("\nDry-run only. No files changed.")
            return 0

        # Execute pipeline
        failures = 0
        for (plan, art, src_image png500) in rows:
            print(f"→ Processing: {plan.src.name}")
            try:
                # AUDIO
                if plan.needs_encode:
                    encode_to_temp(plan, settings)
                else:
                    copy_mp3_to_temp(plan)

                # TAGS
                src_tags = read_source_tags(plan.src)
                write_id3_v23_with_apic(
                    plan.temp_path, png500, src_tags, settings.strip_frames)

                # VALIDATION
                validate_duration_close(
                    settings, plan.src, plan.temp_path, tolerance=0.5)
                validate_bitrate_if_encoded(settings, plan, plan.temp_path)
                validate_id3_and_apic_500(plan.temp_path)

                # COMMIT
                final_target = atomic_commit(plan)
                if settings.replace_in_place:
                    delete_source(plan)

                print(f"   ✓ Done → {final_target.name}")

            except Exception as e:
                failures += 1
                # Clean temp on failure (best-effort)
                try:
                    if plan.temp_path.exists():
                        plan.temp_path.unlink()
                except Exception:
                    pass
                print(f"   ✗ Failed: {e}")

        if failures:
            print(f"\nCompleted with {failures} failure(s).")
            return 1

        print("\nAll files processed successfully.")
        return 0

    except IpodPrepError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
