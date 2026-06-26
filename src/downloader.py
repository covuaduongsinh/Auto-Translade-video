import os
import shutil
import urllib.request
from urllib.parse import urlparse, parse_qs

import yt_dlp
from src.utils import setup_logging, ensure_dir

logger = setup_logging("downloader")


def _js_runtime_opts() -> dict:
    """Enable a local JavaScript runtime so yt-dlp can solve YouTube's player
    challenge ("n"/nsig). Recent YouTube refuses downloads otherwise, failing
    with a misleading "This video is not available".

    The challenge solver itself comes from the locally-installed yt-dlp-ejs
    package (a normal pip dependency) — no remote code is fetched at runtime.
    Returns {} if no runtime is found, so other sites still work.
    """
    for runtime in ("deno", "node", "nodejs"):
        if shutil.which(runtime):
            name = "node" if runtime == "nodejs" else runtime
            # The Python API wants {runtime: {config}} (the CLI builds this from
            # --js-runtimes); an empty config dict uses the runtime's defaults.
            return {"js_runtimes": {name: {}}}
    logger.warning(
        "No JavaScript runtime (node/deno) found — YouTube downloads may fail. "
        "Install Node.js or Deno, and the yt-dlp-ejs package."
    )
    return {}


def _build_format(max_height: int | None, audio_only: bool) -> str:
    """Build a yt-dlp format string from a quality choice.

    Default (max_height=None, audio_only=False) reproduces the original
    "best mp4" behaviour so existing CLI/batch callers are unaffected.
    """
    if audio_only:
        return "bestaudio[ext=m4a]/bestaudio/best"
    if max_height:
        return (
            f"bestvideo[ext=mp4][height<={max_height}]+bestaudio[ext=m4a]/"
            f"best[ext=mp4][height<={max_height}]/best"
        )
    return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"


def normalize_url(url: str) -> str:
    """Rewrite non-canonical Douyin/TikTok URLs to a form yt-dlp can extract.

    Douyin's web app uses modal-style routes (e.g. /jingxuan?modal_id=<id>,
    /discover?modal_id=<id>) where the actual video id lives in the query
    string. yt-dlp's douyin extractor expects /video/<id>, so we rewrite.
    """
    if not url:
        return url
    url = url.strip()
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if "douyin.com" in host:
        qs = parse_qs(parsed.query)
        modal_id = qs.get("modal_id", [None])[0]
        if modal_id and modal_id.isdigit():
            return f"https://www.douyin.com/video/{modal_id}"

    return url


def download_video(
    url: str,
    output_dir: str,
    max_height: int | None = None,
    audio_only: bool = False,
) -> str:
    if not url:
        raise ValueError("URL cannot be empty")

    ensure_dir(output_dir)

    # Douyin's yt-dlp extractor is broken upstream (requires `a_bogus`
    # signature). Route Douyin URLs (including v.douyin.com short links)
    # through the Playwright-based fallback.
    from src.downloader_douyin import is_douyin_url, download_douyin
    if is_douyin_url(url):
        logger.info(f"Routing to Playwright Douyin extractor: {url}")
        info = download_douyin(url, output_dir)
        return info["filepath"]

    canonical = normalize_url(url)
    if canonical != url:
        logger.info(f"Normalized URL: {url} -> {canonical}")

    ydl_opts = {
        "format": _build_format(max_height, audio_only),
        # Name the file by the video's title so the final dub inherits a
        # human-readable name (<title>_vn.mp4). windowsfilenames replaces only
        # the Windows-illegal characters (\ / : * ? " < > |) while keeping
        # unicode, so EN/JA/ZH titles stay readable; trim_file_name guards
        # against overrunning the Windows MAX_PATH limit on long titles.
        "outtmpl": os.path.join(output_dir, "%(title)s.%(ext)s"),
        "windowsfilenames": True,
        "trim_file_name": 150,
        "merge_output_format": "mp4",
        "quiet": False,
        "no_warnings": False,
        # A watch URL may carry &list=…; grab only that one video, not the whole
        # playlist. A pure /playlist?list=… URL still expands normally.
        "noplaylist": True,
        **_js_runtime_opts(),
    }

    logger.info(f"Downloading video from: {canonical}")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(canonical, download=True)
        # prepare_filename respects the outtmpl + yt-dlp's own sanitization, so
        # it reflects the actual on-disk name (title-based) regardless of the
        # template. After a merge the real file may carry the merge_output_format
        # extension (.mp4), so fall back to a directory scan by stem if needed.
        filepath = ydl.prepare_filename(info)

        if not os.path.exists(filepath):
            stem = os.path.splitext(os.path.basename(filepath))[0]
            for f in os.listdir(output_dir):
                if f.startswith(stem):
                    filepath = os.path.join(output_dir, f)
                    break

    if not os.path.exists(filepath):
        raise RuntimeError(f"Download failed: file not found at {filepath}")

    logger.info(f"Downloaded: {filepath}")
    return filepath


def get_video_id(url: str) -> str:
    with yt_dlp.YoutubeDL({"quiet": True, **_js_runtime_opts()}) as ydl:
        info = ydl.extract_info(url, download=False)
        return info.get("id", "video")


def fetch_video_info(url: str) -> dict:
    """Probe a URL without downloading and return preview metadata.

    For a single video returns title/uploader/duration/thumbnail. For a
    playlist returns a flat list of entries (id/title/url/duration) so the GUI
    can show them before committing to a download.
    """
    if not url:
        raise ValueError("URL cannot be empty")

    canonical = normalize_url(url)
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        # watch?v=X&list=Y → preview just video X; /playlist?list=Y → full list.
        "noplaylist": True,
        **_js_runtime_opts(),
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(canonical, download=False)

    if info.get("_type") == "playlist":
        entries = []
        for e in info.get("entries") or []:
            if not e:
                continue
            vid = e.get("id", "")
            entries.append({
                "id": vid,
                "title": e.get("title", vid or "(không tiêu đề)"),
                "url": e.get("url") or (
                    f"https://www.youtube.com/watch?v={vid}" if vid else ""),
                "duration": e.get("duration"),
            })
        return {
            "is_playlist": True,
            "title": info.get("title", "Playlist"),
            "entries": entries,
        }

    return {
        "is_playlist": False,
        "id": info.get("id", ""),
        "title": info.get("title", "(không tiêu đề)"),
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail", ""),
        "webpage_url": info.get("webpage_url", canonical),
    }


def fetch_thumbnail(url: str) -> bytes | None:
    """Download thumbnail image bytes for GUI preview. Returns None on failure."""
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read()
    except Exception as e:  # noqa: BLE001 - preview is best-effort
        logger.warning(f"Thumbnail fetch failed: {e}")
        return None
