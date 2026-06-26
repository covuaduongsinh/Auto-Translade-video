"""Suppress flashing console windows from child processes on Windows.

The GUI runs under pythonw (no console), so every child process the pipeline
spawns — ffmpeg (directly and via pydub/yt-dlp), yt-dlp's node/deno challenge
solver, ffprobe — gets its own console window allocated by Windows, which
flashes open and closed. Wrapping subprocess.Popen to default in
CREATE_NO_WINDOW hides them all at once (subprocess.run/check_output/check_call
and the libraries above all go through Popen).
"""
import subprocess
import sys


def suppress_subprocess_windows() -> None:
    """Make every subprocess.Popen run with a hidden console on Windows.

    No-op off Windows. Idempotent — safe to call more than once.
    """
    if sys.platform != "win32":
        return
    if getattr(subprocess.Popen.__init__, "_no_window_patched", False):
        return

    create_no_window = subprocess.CREATE_NO_WINDOW
    original_init = subprocess.Popen.__init__

    def patched_init(self, *args, **kwargs):
        # OR the flag into any creationflags the caller provided so we never
        # clobber an intentional flag.
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | create_no_window
        original_init(self, *args, **kwargs)

    patched_init._no_window_patched = True
    subprocess.Popen.__init__ = patched_init
