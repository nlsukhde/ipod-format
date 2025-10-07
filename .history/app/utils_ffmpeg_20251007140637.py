from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any, Sequence

from .models import Settings


class ProcError(RuntimeError):
    pass


def run_ffprobe_json(settings: Settings, args: Sequence[str]) -> dict[str, Any]:
    """Run ffprobe with JSON output and return parsed dict."""
    cmd = [settings.ffmpeg_path.replace("ffmpeg", "ffprobe"), *args]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=True)
    except FileNotFoundError as e:
        raise ProcError(
            f"ffprobe not found (check PATH/FFMPEG_PATH): {e}") from e
    except subprocess.CalledProcessError as e:
        raise ProcError(
            f"ffprobe failed: {' '.join(shlex.quote(a) for a in cmd)}\n{e.stderr.decode('utf-8', 'ignore')}") from e

    try:
        return json.loads(proc.stdout.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise ProcError(f"ffprobe did not return JSON") from e


def run_ffmpeg(settings: Settings, args: Sequence[str]) -> None:
    """Run ffmpeg; raise on non-zero exit."""
    cmd = [settings.ffmpeg_path, *args]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=True)
    except FileNotFoundError as e:
        raise ProcError(
            f"ffmpeg not found (check PATH/FFMPEG_PATH): {e}") from e
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", "ignore")
        raise ProcError(
            f"ffmpeg failed: {' '.join(shlex.quote(a) for a in cmd)}\n{stderr}") from e
