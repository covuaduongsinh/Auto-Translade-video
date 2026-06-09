"""Shared YouTube helpers + a reusable preview widget for the GUI.

Used by the dedicated YouTube tab, the single-video tab and the batch tab so
the URL probing / quality mapping / thumbnail rendering lives in one place.
"""
import io
import threading
from urllib.parse import urlparse

import customtkinter as ctk

from src.downloader import fetch_video_info, fetch_thumbnail

# Display label → kwargs passed to download_video / run_pipeline_vi's
# download_quality. None means "best mp4" (the original default behaviour).
QUALITY_OPTIONS = {
    "Tốt nhất": None,
    "1080p": {"max_height": 1080},
    "720p": {"max_height": 720},
    "480p": {"max_height": 480},
    "Chỉ âm thanh": {"audio_only": True},
}
QUALITY_LABELS = list(QUALITY_OPTIONS.keys())


def is_youtube_url(s: str) -> bool:
    if not s:
        return False
    try:
        host = urlparse(s.strip()).netloc.lower()
    except Exception:  # noqa: BLE001
        return False
    return any(h in host for h in ("youtube.com", "youtu.be"))


def format_duration(seconds) -> str:
    """Format a duration in seconds as mm:ss or h:mm:ss."""
    if not seconds:
        return "—"
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "—"
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class YouTubePreview(ctk.CTkFrame):
    """Shows a video's thumbnail + title + uploader + duration.

    ``probe(url, on_result, on_error)`` fetches metadata on a worker thread and
    marshals the result back to the Tk main thread via ``self.after``.
    """

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._thumb_img: ctk.CTkImage | None = None

        self.thumb_label = ctk.CTkLabel(self, text="", width=320, height=180)
        self.thumb_label.pack(padx=8, pady=(8, 4))
        self.title_label = ctk.CTkLabel(
            self, text="", anchor="w", justify="left", wraplength=320,
            font=ctk.CTkFont(size=13, weight="bold"))
        self.title_label.pack(fill="x", padx=8)
        self.meta_label = ctk.CTkLabel(self, text="", anchor="w", text_color="gray70")
        self.meta_label.pack(fill="x", padx=8, pady=(0, 8))

    def clear(self):
        self._thumb_img = None
        self.thumb_label.configure(image=None, text="")
        self.title_label.configure(text="")
        self.meta_label.configure(text="")

    def set_message(self, text: str):
        self.thumb_label.configure(image=None, text="")
        self.title_label.configure(text=text)
        self.meta_label.configure(text="")

    def show(self, info: dict):
        """Render single-video metadata (call on the main thread)."""
        self.title_label.configure(text=info.get("title", ""))
        meta = info.get("uploader", "")
        dur = format_duration(info.get("duration"))
        self.meta_label.configure(text=f"{meta}  ·  {dur}" if meta else dur)

        thumb_url = info.get("thumbnail")
        if thumb_url:
            threading.Thread(target=self._load_thumb, args=(thumb_url,),
                             daemon=True).start()

    def _load_thumb(self, thumb_url: str):
        data = fetch_thumbnail(thumb_url)
        if not data:
            return
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            w, h = img.size
            scale = min(320 / w, 180 / h)
            size = (max(1, int(w * scale)), max(1, int(h * scale)))
        except Exception:  # noqa: BLE001
            return

        def apply():
            try:
                self._thumb_img = ctk.CTkImage(light_image=img, dark_image=img,
                                               size=size)
                self.thumb_label.configure(image=self._thumb_img, text="")
            except Exception:  # noqa: BLE001 - widget may be gone
                pass

        self.after(0, apply)

    def probe(self, url: str, on_result, on_error):
        """Fetch metadata off-thread; callbacks run on the Tk main thread."""
        def work():
            try:
                info = fetch_video_info(url)
                self.after(0, lambda: on_result(info))
            except Exception as e:  # noqa: BLE001
                self.after(0, lambda: on_error(e))

        threading.Thread(target=work, daemon=True).start()
