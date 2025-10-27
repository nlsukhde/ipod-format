from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PySide6 import QtCore

from app.config import load_settings
from app.planner import build_run_plan
from app.artwork import detect_art_source, extract_art_to_file, normalize_art_to_png_500
from app.transcode import encode_to_temp, copy_mp3_to_temp
from app.tagging import read_source_tags, write_id3_v23_with_apic
from app.validate import (
    validate_duration_close,
    validate_bitrate_if_encoded,
    validate_id3_and_apic_500,
)
from app.fsops import atomic_commit, delete_source, cleanup_paths
from app.utils_ffmpeg import run_ffprobe_json


class Runner(QtCore.QObject):
    # Signals emitted back to the GUI thread
    log = QtCore.Signal(str)
    planReady = QtCore.Signal(str, int)          # (plan_text, total_tracks)
    started = QtCore.Signal(int)                 # total tracks
    tick = QtCore.Signal(int, int)               # (done, total)
    trackResult = QtCore.Signal(str, bool, str)  # (basename, ok, message)
    finished = QtCore.Signal(int, int)           # (ok_count, fail_count)

    def __init__(self):
        super().__init__()
        self._cancel = False

    # ---------- utility ----------

    def _apply_overrides(self, settings, options: Dict[str, Any]):
        # Replace-in-place / delete mode / collision left as default unless overridden
        if "replace_in_place" in options:
            settings = type(settings)(
                **{**settings.__dict__, "replace_in_place": bool(options["replace_in_place"])})
        if options.get("hard_delete"):
            settings = type(settings)(
                **{**settings.__dict__, "delete_mode": "hard"})
        return settings

    def _build_rows(self, input_paths: Sequence[Path], settings):
        """Plan + detect/normalize art (files cleaned up by caller)."""
        base_plans = build_run_plan(list(input_paths), settings)
        rows = []
        for plan in base_plans:
            art = detect_art_source(plan, settings)
            src_image = extract_art_to_file(plan, settings, art)
            png500 = normalize_art_to_png_500(plan, settings, src_image)
            rows.append((plan, art, src_image, png500))
        return rows

    def _format_plan_table(self, rows, settings) -> str:
        head = [
            "",
            "Plan Preview",
            "-" * 130,
            f"{'Source':60}  {'Codec':6}  {'Action':22}  {'Art Source':28}  {'PNG500?':8}  {'Target'}",
            "-" * 130,
        ]
        body = []
        for (plan, art, _src_img, png500) in rows:
            action = "copy (mp3)" if not plan.needs_encode else "encode → mp3 320/44.1"
            art_label = f"{art.kind}"
            if getattr(art, "detail", ""):
                art_label += f" [{art.detail}]"
            png_ok = "yes" if (png500 and Path(png500).exists()) else "no"
            body.append(
                f"{str(plan.src):60.60}  {plan.src_codec:6}  {action:22}  {art_label:28.28}  {png_ok:8}  {plan.target.name}")
        tail = [
            "-" * 130,
            f"Total files: {len(rows)}",
            f"Replace-in-place: {'OFF' if not settings.replace_in_place else 'ON'} (delete mode: {settings.delete_mode})",
            f"Sample rate: {settings.sample_rate} | Bitrate: {settings.bitrate_mode} | ID3: v{settings.id3_version}",
            "Artwork: 500x500 PNG (single image)",
        ]
        return "\n".join(head + body + tail)

    def _probe_meta(self, ffmpeg_path: str, path: Path) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "exists": path.exists(),
            "bit_rate": 0,
            "sample_rate": 0,
            "channels": 0,
            "duration_sec": 0.0,
        }
        if not out["exists"]:
            return out
        try:
            dat = run_ffprobe_json(
                type("S", (), {"ffmpeg_path": ffmpeg_path}),
                ["-v", "error", "-select_streams", "a:0", "-show_entries",
                    "stream=bit_rate,sample_rate,channels", "-of", "json", str(path)],
            )
            s = (dat.get("streams") or [{}])[0]
            out["bit_rate"] = int(s.get("bit_rate", 0) or 0)
            out["sample_rate"] = int(s.get("sample_rate", 0) or 0)
            out["channels"] = int(s.get("channels", 0) or 0)
        except Exception:
            pass
        try:
            dat2 = run_ffprobe_json(
                type("S", (), {"ffmpeg_path": ffmpeg_path}),
                ["-v", "error", "-show_entries",
                    "format=duration", "-of", "json", str(path)],
            )
            out["duration_sec"] = float(
                (dat2.get("format") or {}).get("duration", 0.0) or 0.0)
        except Exception:
            pass
        return out

    def _process_one(self, plan, art, src_image, png500, settings):
        """
        Run one track end-to-end. Returns (ok: bool, message: str, final_target: Path|None)
        """
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

            # Only delete original for non-MP3 sources (FLAC/ALAC/WAV). Guard if paths match.
            do_delete = (plan.src_codec.lower() != "mp3")
            if do_delete and settings.replace_in_place:
                try:
                    same_path = final_target.resolve() == plan.src.resolve()
                except Exception:
                    same_path = str(final_target) == str(plan.src)
                if not same_path:
                    delete_source(plan)

            # CLEANUP temp art
            cleanup_paths([p for p in (src_image, png500) if p])

            return True, f"✓ {final_target.name}", final_target
        except Exception as e:
            try:
                if plan.temp_path.exists():
                    plan.temp_path.unlink()
            except Exception:
                pass
            cleanup_paths([p for p in (src_image, png500) if p])
            return False, f"✗ {e}", None

    # ---------- slots ----------

    @QtCore.Slot(list, dict)
    def doPreview(self, path_list: list, options: Dict[str, Any]):
        """Build plan + artwork (no writes), emit table, cleanup temp art."""
        try:
            input_paths = [Path(p) for p in path_list]
            settings = self._apply_overrides(load_settings(None), options)
            rows = self._build_rows(input_paths, settings)
            table = self._format_plan_table(rows, settings)
            # cleanup temp images produced during preview
            cleanup_paths([p for _pl, _art, src_img,
                          png in rows for p in (src_img, png) if p])
            self.planReady.emit(table, len(rows))
        except Exception as e:
            self.planReady.emit(f"Preview failed: {e}", 0)

    @QtCore.Slot(list, dict)
    def doRun(self, path_list: list, options: Dict[str, Any]):
        """Execute the pipeline with concurrency; stream progress/results."""
        try:
            input_paths = [Path(p) for p in path_list]
            settings = self._apply_overrides(load_settings(None), options)

            # Plan + art
            rows = self._build_rows(input_paths, settings)
            total = len(rows)
            self.started.emit(total)

            # Concurrency
            jobs = int(options.get("jobs") or min(4, os.cpu_count() or 2))
            done = 0
            ok_count = 0
            fail_count = 0

            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=jobs) as exe:
                futs = [exe.submit(self._process_one, plan, art, src_image, png500, settings)
                        for (plan, art, src_image, png500) in rows]

                for fut, (plan, _art, _src_image, _png500) in zip(as_completed(futs), rows):
                    ok, msg, final_target = fut.result()
                    done += 1
                    if ok:
                        ok_count += 1
                    else:
                        fail_count += 1
                    self.trackResult.emit(plan.src.name, bool(ok), msg)
                    self.tick.emit(done, total)

            dt = time.perf_counter() - t0
            self.log.emit(
                f"Finished in {dt:.2f}s — {ok_count} ok / {fail_count} failed.")
            self.finished.emit(ok_count, fail_count)

        except Exception as e:
            # If planning itself failed
            self.log.emit(f"Run failed: {e}")
            self.finished.emit(0, 1)
