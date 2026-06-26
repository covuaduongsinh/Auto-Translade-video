"""Main window: a 4-tab CustomTkinter desktop app for Vietnamese video dubbing."""
import customtkinter as ctk

from gui.pipeline_runner import install_log_handler
from gui.tabs.single_tab import SingleTab
from gui.tabs.youtube_tab import YouTubeTab
from gui.tabs.batch_tab import BatchTab
from gui.tabs.settings_tab import SettingsTab
from gui.tabs.history_tab import HistoryTab


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("Auto-Translate Video — Lồng tiếng Việt")
        self.geometry("1180x760")
        self.minsize(960, 620)

        tabview = ctk.CTkTabview(self)
        tabview.pack(fill="both", expand=True, padx=10, pady=10)

        tab_single = tabview.add("Dịch 1 video")
        tab_youtube = tabview.add("YouTube")
        tab_batch = tabview.add("Xử lý hàng loạt")
        tab_settings = tabview.add("Cài đặt")
        tab_history = tabview.add("Lịch sử")

        for t in (tab_single, tab_youtube, tab_batch, tab_settings, tab_history):
            t.grid_columnconfigure(0, weight=1)
            t.grid_rowconfigure(0, weight=1)

        self.single = SingleTab(tab_single)
        self.single.grid(row=0, column=0, sticky="nsew")
        self.batch = BatchTab(tab_batch)
        self.batch.grid(row=0, column=0, sticky="nsew")
        self.youtube = YouTubeTab(tab_youtube, batch_tab=self.batch)
        self.youtube.grid(row=0, column=0, sticky="nsew")
        self.settings = SettingsTab(tab_settings)
        self.settings.grid(row=0, column=0, sticky="nsew")
        self.history = HistoryTab(tab_history)
        self.history.grid(row=0, column=0, sticky="nsew")

        # Stream every module's logs into both pipeline-driving tabs' log boxes.
        install_log_handler(self.single.log_queue)
        install_log_handler(self.youtube.log_queue)

        # Refresh history whenever that tab is opened.
        tabview.configure(command=lambda: self.history.refresh()
                          if tabview.get() == "Lịch sử" else None)


def main():
    # Belt-and-braces in case the GUI is launched without run_gui.pyw.
    from gui.win_console import suppress_subprocess_windows
    suppress_subprocess_windows()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
