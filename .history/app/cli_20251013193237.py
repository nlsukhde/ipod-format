from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Sequence

from .config import load_settings
from .errors import IpodPrepError
from .planner import build_run_plan
from .artwork import detect_art_source, extract_art_to_file, normalize_art_to_png_500
from .transcode import encode_to_temp, copy_mp3_to_temp
from .tagging import read_source_tags, write_id3_v23_with_apic
from .validate import (
    validate_duration_close,
    validate_bitrate_if_encoded,
    validate_id3_and_apic_500,
)
from .fsops import atomic_commit, delete_source, cleanup_paths
from .utils_ffmpeg import run_ffprobe_json
from .runlog import make_run_dir, write_manifest


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
                    help="Delete sources permanently (default: Recycle Bin).")
    ap.add_argument("--collision", choices=("overwrite", "skip",
                    "version"), help="Target collision policy.")
    ap.add_argument("-j", "--jobs", type=int, default=None,
                    help="Max parallel conversions (default: min(4, CPU count)).")
    return ap.parse_args(argv)


def _print_plan(rows, settings) -> None:
    """
    rows: iterable of (plan, art, src_image, png500)
    """
    print("\nPlan Preview (with artwork):")
    print("-" * 130)
    print(f"{'Source':60}  {'Codec':6}  {'Action':22}  {'Art Source':28}  {'PNG500?':8}  {'Target'}")
    print("-" * 130)
    for (plan, art, _src_image, png500) in rows:
        action = "copy (mp3)" if not plan.needs_encode else "encode → mp3 320/44.1"
        art_label = f"{art.kind}"
        if getattr(art, "detail", ""):
            art_label += f" [{art.detail}]"
        png_ok = "yes" if (png500 and Path(png500).exists()) else "no"
        print(f"{str(plan.src):60.60}  {plan.src_codec:6}  {action:22}  {art_label:28.28}  {png_ok:8}  {plan.target.name}")
    print("-" * 130)
    print(f"Total files: {len(rows)}")
    print(
        f"Replace-in-place: {'OFF' if not settings.replace_in_place else 'ON'} (delete mode: {settings.delete_mode})")
    print(
        f"Sample rate: {settings.sample_rate} | Bitrate: {settings.bitrate_mode} | ID3: v{settings.id3_version}")
    print("Artwork: 500x500 PNG (single image)")


def _probe_audio_meta(ffmpeg_path: str, path: Path) -> Dict[str, Any]:
    """
    Best-effort probe. Never raises. Returns zeros if file missing or probe fails.
    """
    out: Dict[str, Any] = {
        "exists": path.exists(),
        "bit_rate": 0,
        "sample_rate": 0,
        "channels": 0,
        "duration_sec": 0.0,
    }
    if not out["exists"]:
        return out

    # Wrap each probe; swallow failures.
    try:
        data = run_ffprobe_json(
            type("S", (), {"ffmpeg_path": ffmpeg_path}),
            [
                "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=bit_rate,sample_rate,channels",
                "-of", "json",
                str(path),
            ],
        )
        s = (data.get("streams") or [{}])[0]
        out["bit_rate"] = int(s.get("bit_rate", 0) or 0)
        out["sample_rate"] = int(s.get("sample_rate", 0) or 0)
        out["channels"] = int(s.get("channels", 0) or 0)
    except Exception:
        pass

    try:
        data2 = run_ffprobe_json(
            type("S", (), {"ffmpeg_path": ffmpeg_path}),
            ["-v", "error", "-show_entries",
                "format=duration", "-of", "json", str(path)],
        )
        out["duration_sec"] = float(
            (data2.get("format") or {}).get("duration", 0.0) or 0.0)
    except Exception:
        pass

    return out


def process_one(plan, art, src_image, png500, settings):
    """
    Run the full pipeline for a single track.
    Returns (plan, success: bool, message: str, manifest_row: dict).
    """
    t0 = time.perf_counter()
    action = "encode" if plan.needs_encode else "copy"
    manifest_row: Dict[str, Any] = {
        "src": str(plan.src),
        "src_codec": plan.src_codec,
        "target": str(plan.target),
        "action": action,
        "art": {"kind": art.kind, "detail": getattr(art, "detail", "")},
        "started_utc": datetime.now(timezone.utc).isoformat(),
    }

    # Probe source BEFORE we touch/possibly delete it, we have to do this i lay in the rooftops
    meta_src = _probe_audio_meta(settings.ffmpeg_path, plan.src)
    manifest_row["src_meta"] = meta_src

    try:
        # AUDIO
        if plan.needs_encode:
            encode_to_temp(plan, settings)
        else:
            copy_mp3_to_temp(plan)

        # TAGS
        src_tags = read_source_tags(plan.src)
        write_id3_v23_with_apic(plan.temp_path, png500,
                                src_tags, settings.strip_frames)

        # VALIDATION
        validate_duration_close(
            settings, plan.src, plan.temp_path, tolerance=0.5)
        validate_bitrate_if_encoded(settings, plan, plan.temp_path)
        validate_id3_and_apic_500(plan.temp_path)

        # COMMIT
        final_target = atomic_commit(plan)

        # Only delete original for non-MP3 sources (FLAC/ALAC/WAV). Guard if paths match.
        do_delete = (plan.src_codec.lower() != "mp3")
        deleted = False
        if do_delete and settings.replace_in_place:
            try:
                same_path = final_target.resolve() == plan.src.resolve()
            except Exception:
                same_path = str(final_target) == str(plan.src)
            if not same_path:
                delete_source(plan)
                deleted = True

        # CLEANUP temp art
        cleanup_paths([p for p in (src_image, png500) if p])

        # Probe destination AFTER commit
        meta_dst = _probe_audio_meta(settings.ffmpeg_path, final_target)

        dt = time.perf_counter() - t0
        manifest_row.update({
            "result": "ok",
            "deleted_source": deleted,
            "elapsed_sec": round(dt, 3),
            "dst_meta": meta_dst,
            "apic": {"png_500": bool(png500)},
        })
        return plan, True, f"✓ {final_target.name}", manifest_row

    except Exception as e:
        # best-effort cleanup
        try:
            if plan.temp_path.exists():
                plan.temp_path.unlink()
        except Exception:
            pass
        cleanup_paths([p for p in (src_image, png500) if p])

        dt = time.perf_counter() - t0
        manifest_row.update({
            "result": "failed",
            "error": str(e),
            "elapsed_sec": round(dt, 3),
        })
        return plan, False, f"✗ {e}", manifest_row


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

        # Prepare run directory (even for dry-run to keep things consistent)
        run_dir = make_run_dir()
        run_start = datetime.now(timezone.utc)

        if ns.dry_run:
            _print_plan(rows, settings)
            # Clean up any temp art created during dry-run
            cleanup_paths([p for _plan, _art, src_img,
                          png in rows for p in (src_img, png) if p])

            manifest = {
                "run_started_utc": run_start.isoformat(),
                "mode": "dry-run",
                "settings": {
                    "bitrate_mode": settings.bitrate_mode,
                    "sample_rate": settings.sample_rate,
                    "delete_mode": settings.delete_mode,
                    "replace_in_place": settings.replace_in_place,
                    "collision": settings.collision,
                },
                "inputs": [str(p) for p in input_paths],
                "tracks_planned": len(rows),
            }
            write_manifest(run_dir, manifest)
            print(
                f"\nDry-run only. No files changed.\nManifest: {run_dir / 'manifest.json'}")
            return 0

        jobs = ns.jobs or min(4, os.cpu_count() or 2)
        print(f"Running with up to {jobs} parallel job(s)...")

        failures = 0
        results_for_manifest: list[Dict[str, Any]] = []

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=jobs) as exe:
            futs = [exe.submit(process_one, plan, art, src_image, png500, settings)
                    for (plan, art, src_image, png500) in rows]

            for fut in as_completed(futs):
                plan, ok, msg, row = fut.result()
                print(f"→ {plan.src.name}: {msg}")
                results_for_manifest.append(row)
                if not ok:
                    failures += 1

        elapsed = time.perf_counter() - t0

        manifest = {
            "run_started_utc": run_start.isoformat(),
            "run_finished_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_sec": round(elapsed, 3),
            "mode": "execute",
            "settings": {
                "bitrate_mode": settings.bitrate_mode,
                "sample_rate": settings.sample_rate,
                "delete_mode": settings.delete_mode,
                "replace_in_place": settings.replace_in_place,
                "collision": settings.collision,
            },
            "inputs": [str(p) for p in input_paths],
            "summary": {
                "total": len(results_for_manifest),
                "ok": sum(1 for r in results_for_manifest if r.get("result") == "ok"),
                "failed": sum(1 for r in results_for_manifest if r.get("result") == "failed"),
            },
            "tracks": results_for_manifest,
        }
        out = write_manifest(run_dir, manifest)

        if failures:
            print(f"\nCompleted with {failures} failure(s). Manifest: {out}")
            return 1

        print(f"\nAll files processed successfully. Manifest: {out}")
        return 0

    except IpodPrepError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
