"""Desktop GUI launcher for the Vietnamese video dubbing pipeline.

Double-click this file (or run `python run_gui.pyw`) to open the app. The .pyw
extension runs it without a console window on Windows. config.py loads .env on
import, so API keys come from the project's .env.
"""
import os
import sys

# Make sure imports resolve when launched from elsewhere (e.g. double-click).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Hide child-process console windows (ffmpeg/yt-dlp/node) before any spawn.
from gui.win_console import suppress_subprocess_windows
suppress_subprocess_windows()

# Add ffmpeg to PATH before the pipeline shells out to it (mirrors run_interactive.ps1).
from gui.ffmpeg_setup import ensure_ffmpeg_on_path
ensure_ffmpeg_on_path()

from gui.app import main

if __name__ == "__main__":
    main()
