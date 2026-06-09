"""Make sure ffmpeg is reachable on PATH before the pipeline shells out to it.

The CLI launcher (run_interactive.ps1) prepends the WinGet Gyan.FFmpeg bin dir to
PATH. The GUI needs the same, otherwise audio_extractor / video_merger fail with
[WinError 2] "The system cannot find the file specified".
"""
import glob
import os
import shutil


def _candidate_dirs() -> list[str]:
    home = os.path.expanduser("~")
    patterns = [
        # WinGet (Gyan.FFmpeg) — version folder varies, so glob it.
        os.path.join(home, r"AppData\Local\Microsoft\WinGet\Packages",
                     "Gyan.FFmpeg*", "**", "bin"),
        os.path.join(home, r"AppData\Local\Microsoft\WinGet\Links"),
        r"C:\ffmpeg\bin",
        r"C:\Program Files\ffmpeg\bin",
    ]
    dirs: list[str] = []
    for pat in patterns:
        for path in glob.glob(pat, recursive=True):
            if os.path.isfile(os.path.join(path, "ffmpeg.exe")):
                dirs.append(path)
    return dirs


def ensure_ffmpeg_on_path() -> str | None:
    """Return the ffmpeg dir on PATH, adding it if needed (None if not found)."""
    found = shutil.which("ffmpeg")
    if found:
        return os.path.dirname(found)

    for d in _candidate_dirs():
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        if shutil.which("ffmpeg"):
            return d

    return None
