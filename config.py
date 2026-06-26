import os
import sys
from dotenv import load_dotenv

load_dotenv()

def _require_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        print(f"ERROR: Required environment variable '{key}' is not set.", file=sys.stderr)
        print(f"Please copy .env.example to .env and fill in your API keys.", file=sys.stderr)
        sys.exit(1)
    return value

# ASR backend selection: "groq" (Groq Whisper, default) or "azure" (Azure Speech)
ASR_BACKEND = os.getenv("ASR_BACKEND", "groq").strip().lower()

# Azure Speech — optional now. Only required when ASR_BACKEND=azure (ASR) or for
# Japanese TTS (pipeline.py). Validated lazily by the code paths that need it.
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "japaneast")

# Groq Whisper ASR (https://console.groq.com)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_ASR_MODEL = os.getenv("GROQ_ASR_MODEL", "whisper-large-v3")
GROQ_API_URL = os.getenv("GROQ_API_URL", "https://api.groq.com/openai/v1/audio/transcriptions")

# Optional with defaults
TTS_VOICE = os.getenv("TTS_VOICE", "ja-JP-KeitaNeural")
TTS_MAX_SPEED_RATIO = float(os.getenv("TTS_MAX_SPEED_RATIO", "1.3"))
DEFAULT_SOURCE_LANG = os.getenv("DEFAULT_SOURCE_LANG", "en-US")
AUDIO_SAMPLE_RATE = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./output")
# Vietnamese TTS (LucyLab API)
VIETNAMESE_API_KEY = os.getenv("VIETNAMESE_API_KEY", "")
VIETNAMESE_VOICEID_MALE = os.getenv("VIETNAMESE_VOICEID_MALE", "")
VIETNAMESE_VOICEID_FEMALE = os.getenv("VIETNAMESE_VOICEID_FEMALE", "")
LUCYLAB_API_URL = os.getenv("LUCYLAB_API_URL", "https://api.lucylab.io/json-rpc")
VIETNAMESE_TTS_MAX_SPEED = float(os.getenv("VIETNAMESE_TTS_MAX_SPEED", "1.3"))

# Vietnamese TTS backend: "lucylab" (default) or "vbee"
TTS_BACKEND_VI = os.getenv("TTS_BACKEND_VI", "vbee").strip().lower()

# Vbee AIVoice TTS (https://studio.vbee.vn) — used when TTS_BACKEND_VI=vbee
VBEE_APP_ID = os.getenv("VBEE_APP_ID", "")
VBEE_TOKEN = os.getenv("VBEE_TOKEN", "")
VBEE_API_URL = os.getenv("VBEE_API_URL", "https://vbee.vn/api/v1/tts")
VBEE_VOICE_MALE = os.getenv("VBEE_VOICE_MALE", "")
VBEE_VOICE_FEMALE = os.getenv("VBEE_VOICE_FEMALE", "")
VBEE_AUDIO_TYPE = os.getenv("VBEE_AUDIO_TYPE", "mp3")
VBEE_BITRATE = int(os.getenv("VBEE_BITRATE", "128"))
# Vbee requires callback_url even when polling; a dummy URL is fine since we poll
# the GET endpoint for the result rather than receiving the callback.
VBEE_CALLBACK_URL = os.getenv("VBEE_CALLBACK_URL", "https://example.com/vbee-callback")


def vi_voice(gender: str) -> str:
    """Return the voice id/code for the active VI TTS backend.

    gender = 'male' | 'female'. Picks the Vbee voice_code when TTS_BACKEND_VI=vbee,
    otherwise the LucyLab userVoiceId. Reads TTS_BACKEND_VI dynamically so a CLI
    override (e.g. pipeline_vi.py --tts-backend) takes effect.
    """
    if TTS_BACKEND_VI == "vbee":
        return VBEE_VOICE_MALE if gender == "male" else VBEE_VOICE_FEMALE
    return VIETNAMESE_VOICEID_MALE if gender == "male" else VIETNAMESE_VOICEID_FEMALE
# Slow down factor for Vietnamese audio (0.82 = 18% slower, 1.0 = no change)
AUDIO_SLOW_FACTOR = float(os.getenv("AUDIO_SLOW_FACTOR", "0.82"))
VIETNAMESE_OUTPUT_DIR = os.getenv("VIETNAMESE_OUTPUT_DIR", "")
VOICE_TYPE = os.getenv("VOICE_TYPE", os.getenv("Voice_type", "")).strip().lower()

VIETNAMESE_VIDEO_URL = os.getenv("VIETNAMESE_VIDEO_URL", os.getenv("Vietnamese_video_url", ""))

VIDEO_URL = os.getenv("VIDEO_URL", "")

# Google Gemini API (thumbnails + content generation)
GOOGLE_API_KEY = os.getenv("google_api_key", os.getenv("GOOGLE_API_KEY", ""))
IMAGE_MODEL_ID = os.getenv("image_model_id", "gemini-2.0-flash-exp")
CONTENT_MODEL_ID = os.getenv("content_model_id", "gemini-2.0-flash")

# Claude Code CLI translate mode (src/translator_claude.py). Uses the installed
# `claude` CLI (subscription, no API key). Empty CLAUDE_MODEL_ID → CLI default.
# CLAUDE_BIN / CLAUDE_PERMISSION_MODE are read directly by translator_claude.py.
CLAUDE_MODEL_ID = os.getenv("CLAUDE_MODEL_ID", "")

# Default Claude model for the GUI dropdown. "sonnet" (rẻ/nhanh, đỡ tốn quota) là mặc định;
# người dùng có thể đổi sang "haiku" hoặc "opus". Tôn trọng CLAUDE_MODEL_ID nếu đã đặt.
CLAUDE_DEFAULT_MODEL = os.getenv("CLAUDE_DEFAULT_MODEL", CLAUDE_MODEL_ID or "sonnet")
CLAUDE_MODELS = ["sonnet", "haiku", "opus"]
if CLAUDE_DEFAULT_MODEL not in CLAUDE_MODELS:        # cho phép model id tuỳ chỉnh từ env
    CLAUDE_MODELS = [CLAUDE_DEFAULT_MODEL, *CLAUDE_MODELS]

# Demucs vocal-separation timeout (seconds). On CPU a long video can take many
# minutes; if it exceeds this limit we abort and fall back to a silent base.
DEMUCS_TIMEOUT_SECONDS = int(os.getenv("DEMUCS_TIMEOUT_SECONDS", "1800"))

# Opencode CLI translate mode (src/translator_opencode.py). Uses the installed
# `opencode` CLI (local AI agent). Empty OPENCODE_MODEL_ID -> CLI default.
# OPENCODE_BIN is read directly by translator_opencode.py.
OPENCODE_MODEL_ID = os.getenv("OPENCODE_MODEL_ID", "")

# Default opencode model for the GUI dropdown. One of the 4 free models:
#   opencode/nemotron-3-ultra-free (default, best translation quality)
#   opencode/deepseek-v4-flash-free
#   opencode/mimo-v2.5-free
#   opencode/north-mini-code-free
OPENCODE_DEFAULT_MODEL = os.getenv(
    "OPENCODE_DEFAULT_MODEL", "opencode/nemotron-3-ultra-free"
)

# All free opencode models, shown in the GUI dropdown.
OPENCODE_FREE_MODELS = [
    "opencode/nemotron-3-ultra-free",
    "opencode/deepseek-v4-flash-free",
    "opencode/mimo-v2.5-free",
    "opencode/north-mini-code-free",
]
