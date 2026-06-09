"""Tab: edit the .env file through a form instead of a text editor."""
import os

import customtkinter as ctk
from dotenv import get_key, set_key

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")

# (env_key, label, is_secret)
FIELDS = [
    ("— ASR —", None, False),
    ("ASR_BACKEND", "ASR backend (groq/azure)", False),
    ("GROQ_API_KEY", "Groq API key", True),
    ("GROQ_ASR_MODEL", "Groq model", False),
    ("AZURE_SPEECH_KEY", "Azure Speech key", True),
    ("AZURE_SPEECH_REGION", "Azure region", False),
    ("— TTS tiếng Việt —", None, False),
    ("TTS_BACKEND_VI", "TTS backend (lucylab/vbee)", False),
    ("VIETNAMESE_API_KEY", "LucyLab API key", True),
    ("VIETNAMESE_VOICEID_MALE", "LucyLab voice id — Nam", False),
    ("VIETNAMESE_VOICEID_FEMALE", "LucyLab voice id — Nữ", False),
    ("VBEE_APP_ID", "Vbee app id", True),
    ("VBEE_TOKEN", "Vbee token", True),
    ("VBEE_VOICE_MALE", "Vbee voice — Nam", False),
    ("VBEE_VOICE_FEMALE", "Vbee voice — Nữ", False),
    ("VIETNAMESE_TTS_MAX_SPEED", "Tốc độ TTS tối đa", False),
    ("AUDIO_SLOW_FACTOR", "Hệ số làm chậm (0.82)", False),
    ("— Khác —", None, False),
    ("DEFAULT_SOURCE_LANG", "Ngôn ngữ nguồn mặc định", False),
    ("VIETNAMESE_OUTPUT_DIR", "Thư mục output VI", False),
    ("google_api_key", "Google Gemini API key (dịch AI)", True),
    ("content_model_id", "Gemini model (dịch/metadata)", False),
]


class SettingsTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 8))
        ctk.CTkButton(top, text="💾 Lưu .env", command=self._save).pack(side="left")
        ctk.CTkButton(top, text="↻ Tải lại", fg_color="gray40", hover_color="gray30",
                      command=self._reload).pack(side="left", padx=8)
        self.status_var = ctk.StringVar(value=f".env: {ENV_PATH}")
        ctk.CTkLabel(top, textvariable=self.status_var, anchor="w").pack(
            side="left", padx=12)

        self.scroll = ctk.CTkScrollableFrame(self, label_text="Cấu hình (.env)")
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        self.scroll.grid_columnconfigure(1, weight=1)

        self.vars: dict[str, ctk.StringVar] = {}
        self._build()

    def _ensure_env(self):
        if not os.path.exists(ENV_PATH):
            open(ENV_PATH, "a", encoding="utf-8").close()

    def _build(self):
        self._ensure_env()
        row = 0
        for key, label, secret in FIELDS:
            if label is None:  # section header
                ctk.CTkLabel(self.scroll, text=key,
                             font=ctk.CTkFont(size=14, weight="bold")).grid(
                    row=row, column=0, columnspan=3, sticky="w", padx=6, pady=(12, 4))
                row += 1
                continue
            ctk.CTkLabel(self.scroll, text=label, anchor="w").grid(
                row=row, column=0, sticky="w", padx=6, pady=4)
            var = ctk.StringVar(value=get_key(ENV_PATH, key) or "")
            self.vars[key] = var
            entry = ctk.CTkEntry(self.scroll, textvariable=var,
                                 show="•" if secret else "")
            entry.grid(row=row, column=1, sticky="ew", padx=6, pady=4)
            if secret:
                ctk.CTkButton(self.scroll, text="👁", width=36,
                              command=lambda e=entry: self._toggle(e)).grid(
                    row=row, column=2, padx=(0, 6))
            row += 1

    def _toggle(self, entry: ctk.CTkEntry):
        entry.configure(show="" if entry.cget("show") else "•")

    def _reload(self):
        for key, var in self.vars.items():
            var.set(get_key(ENV_PATH, key) or "")
        self.status_var.set("Đã tải lại từ .env.")

    def _save(self):
        self._ensure_env()
        try:
            for key, var in self.vars.items():
                set_key(ENV_PATH, key, var.get(), quote_mode="never")
            self.status_var.set("✅ Đã lưu .env (khởi động lại app để áp dụng đầy đủ).")
        except Exception as e:  # noqa: BLE001
            self.status_var.set(f"❌ Lỗi lưu: {e}")
