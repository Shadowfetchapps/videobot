import os
import sys

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton,
    QTextEdit, QProgressBar,
    QSpinBox, QComboBox, QFileDialog, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QFont

from .worker import ScraperWorker

# ── Palette ────────────────────────────────────────────────────────────────────
BG_DEEP  = "#0f1117"
BG_CARD  = "#1a1d27"
BG_INPUT = "#22253a"
ACCENT   = "#6e84f7"
TEXT_PRI = "#e8eaf6"
TEXT_SEC = "#8890b5"
SUCCESS  = "#4caf82"
WARNING  = "#f5a623"
DANGER   = "#e57373"


def _card_style() -> str:
    return f"QFrame {{ background: {BG_CARD}; border-radius: 8px; }}"


class StatCard(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_card_style())
        self.setFixedHeight(80)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(2)

        lbl = QLabel(title)
        lbl.setStyleSheet(f"color: {TEXT_SEC}; font-size: 11px;")

        self._val = QLabel("0")
        self._val.setStyleSheet(f"color: {TEXT_PRI}; font-size: 22px; font-weight: bold;")

        layout.addWidget(lbl)
        layout.addWidget(self._val)

    def set_value(self, v):
        self._val.setText(str(v))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VideoBot")
        self.setMinimumSize(880, 700)
        self._worker = None
        self._build_ui()
        self._apply_theme()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 22, 22, 22)
        outer.setSpacing(14)

        # Title
        title = QLabel("VideoBot")
        title.setStyleSheet(f"color: {ACCENT}; font-size: 26px; font-weight: bold;")
        outer.addWidget(title)

        sub = QLabel("Smart video scraper — YouTube, Vimeo, direct MP4/WebM, and more")
        sub.setStyleSheet(f"color: {TEXT_SEC}; font-size: 12px;")
        outer.addWidget(sub)

        # URL row
        url_row = QHBoxLayout()
        url_lbl = QLabel("Start URL:")
        url_lbl.setStyleSheet(f"color: {TEXT_SEC};")
        url_lbl.setFixedWidth(82)
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("https://example.com/videos")
        url_row.addWidget(url_lbl)
        url_row.addWidget(self._url_edit)
        outer.addLayout(url_row)

        # Save directory row
        dir_row = QHBoxLayout()
        dir_lbl = QLabel("Save To:")
        dir_lbl.setStyleSheet(f"color: {TEXT_SEC};")
        dir_lbl.setFixedWidth(82)
        self._dir_edit = QLineEdit()
        self._dir_edit.setPlaceholderText("/path/to/save/videos")
        self._dir_edit.setText(os.path.expanduser("~/Downloads/VideoBot"))
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(dir_lbl)
        dir_row.addWidget(self._dir_edit)
        dir_row.addWidget(browse_btn)
        outer.addLayout(dir_row)

        # Options row
        opt_row = QHBoxLayout()
        opt_row.setSpacing(12)

        qual_lbl = QLabel("Quality:")
        qual_lbl.setStyleSheet(f"color: {TEXT_SEC};")
        self._quality_combo = QComboBox()
        self._quality_combo.addItems(["Best", "1080p", "720p", "480p", "Worst"])
        self._quality_combo.setFixedWidth(115)

        fmt_lbl = QLabel("Format:")
        fmt_lbl.setStyleSheet(f"color: {TEXT_SEC};")
        self._fmt_combo = QComboBox()
        self._fmt_combo.addItems(["MP4 Preferred", "WebM Preferred", "Any"])
        self._fmt_combo.setFixedWidth(145)

        mp_lbl = QLabel("Max Pages:")
        mp_lbl.setStyleSheet(f"color: {TEXT_SEC};")
        self._max_pages = QSpinBox()
        self._max_pages.setRange(0, 99999)
        self._max_pages.setValue(0)
        self._max_pages.setSpecialValueText("\u221e Unlimited")
        self._max_pages.setFixedWidth(125)

        opt_row.addWidget(qual_lbl)
        opt_row.addWidget(self._quality_combo)
        opt_row.addSpacing(8)
        opt_row.addWidget(fmt_lbl)
        opt_row.addWidget(self._fmt_combo)
        opt_row.addSpacing(8)
        opt_row.addWidget(mp_lbl)
        opt_row.addWidget(self._max_pages)
        opt_row.addStretch()
        outer.addLayout(opt_row)

        # Start / Stop
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("\u25b6  Start Scraping")
        self._start_btn.setFixedHeight(38)
        self._start_btn.clicked.connect(self._start)

        self._stop_btn = QPushButton("\u25a0  Stop")
        self._stop_btn.setFixedHeight(38)
        self._stop_btn.setFixedWidth(120)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)

        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(self._stop_btn)
        outer.addLayout(btn_row)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedHeight(6)
        self._progress.setTextVisible(False)
        outer.addWidget(self._progress)

        # Stat cards
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self._stat_pages  = StatCard("Listing Pages")
        self._stat_items  = StatCard("Items Visited")
        self._stat_videos = StatCard("Videos Saved")
        for c in (self._stat_pages, self._stat_items, self._stat_videos):
            stats_row.addWidget(c)
        outer.addLayout(stats_row)

        # Log
        self._log_box = QTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setStyleSheet(f"""
            QTextEdit {{
                background: {BG_CARD};
                color: {TEXT_PRI};
                border-radius: 6px;
                font-family: Menlo, Consolas, monospace;
                font-size: 12px;
                padding: 8px;
            }}
        """)
        outer.addWidget(self._log_box)

    def _apply_theme(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background: {BG_DEEP};
                color: {TEXT_PRI};
                font-size: 13px;
            }}
            QLineEdit, QComboBox, QSpinBox {{
                background: {BG_INPUT};
                color: {TEXT_PRI};
                border: 1px solid #2e3250;
                border-radius: 5px;
                padding: 5px 10px;
                font-size: 13px;
            }}
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
                border-color: {ACCENT};
            }}
            QPushButton {{
                background: {ACCENT};
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 20px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover    {{ background: #8095f8; }}
            QPushButton:disabled {{ background: #2e3250; color: {TEXT_SEC}; }}
            QProgressBar {{
                background: {BG_CARD};
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: {ACCENT};
                border-radius: 3px;
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid {TEXT_SEC};
                margin-right: 8px;
            }}
            QComboBox QAbstractItemView {{
                background: {BG_INPUT};
                color: {TEXT_PRI};
                selection-background-color: {ACCENT};
            }}
            QSpinBox::up-button, QSpinBox::down-button {{ background: transparent; }}
        """)

    # ── Slots ─────────────────────────────────────────────────────────────────

    @pyqtSlot()
    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "Select Save Directory", self._dir_edit.text()
        )
        if d:
            self._dir_edit.setText(d)

    @pyqtSlot()
    def _start(self):
        url = self._url_edit.text().strip()
        if not url:
            self._append(f"<span style='color:{WARNING};'>Please enter a Start URL.</span>")
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
            self._url_edit.setText(url)

        save_dir = self._dir_edit.text().strip() or os.path.expanduser("~/Downloads/VideoBot")

        quality_map = {
            "Best": "best", "1080p": "1080p", "720p": "720p",
            "480p": "480p", "Worst": "worst",
        }
        fmt_map = {
            "MP4 Preferred": "mp4", "WebM Preferred": "webm", "Any": "any",
        }
        quality   = quality_map.get(self._quality_combo.currentText(), "best")
        fmt       = fmt_map.get(self._fmt_combo.currentText(), "any")
        max_pages = self._max_pages.value()

        # Reset UI
        self._stat_pages.set_value(0)
        self._stat_items.set_value(0)
        self._stat_videos.set_value(0)
        self._progress.setValue(0)
        self._log_box.clear()
        self._append(f"<span style='color:{ACCENT};'>Starting VideoBot…</span>")
        self._append(f"URL: {url}")
        self._append(f"Save to: {save_dir}")
        inf = "\u221e" if max_pages == 0 else str(max_pages)
        self._append(f"Quality: {quality}  |  Format: {fmt}  |  Max pages: {inf}")

        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

        self._worker = ScraperWorker(url, save_dir, quality, fmt, max_pages)
        self._worker.log_signal.connect(self._on_log)
        self._worker.progress_signal.connect(self._on_progress)
        self._worker.page_signal.connect(self._on_page)
        self._worker.items_signal.connect(self._on_items)
        self._worker.done_signal.connect(self._on_done)
        self._worker.error_signal.connect(self._on_error)
        self._worker.start()

    @pyqtSlot()
    def _stop(self):
        if self._worker:
            self._worker.stop()
        self._stop_btn.setEnabled(False)
        self._append(f"<span style='color:{WARNING};'>Stop requested…</span>")

    @pyqtSlot(str)
    def _on_log(self, msg: str):
        self._append(msg)

    @pyqtSlot(int)
    def _on_progress(self, pct: int):
        self._progress.setValue(pct)

    @pyqtSlot(int)
    def _on_page(self, pages_done: int):
        self._stat_pages.set_value(pages_done)

    @pyqtSlot(int, int)
    def _on_items(self, items_visited: int, videos_saved: int):
        self._stat_items.set_value(items_visited)
        self._stat_videos.set_value(videos_saved)

    @pyqtSlot(dict)
    def _on_done(self, result: dict):
        self._stat_pages.set_value(result.get("pages", 0))
        self._stat_items.set_value(result.get("items", 0))
        self._stat_videos.set_value(result.get("videos", 0))
        self._append(
            f"<span style='color:{SUCCESS};'>"
            f"Done! Pages: {result.get('pages', 0)} | "
            f"Items: {result.get('items', 0)} | "
            f"Videos: {result.get('videos', 0)}"
            f"</span>"
        )
        self._progress.setValue(100)
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    @pyqtSlot(str)
    def _on_error(self, msg: str):
        self._append(f"<span style='color:{DANGER};'>Error: {msg}</span>")
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _append(self, html: str):
        self._log_box.append(html)
        sb = self._log_box.verticalScrollBar()
        sb.setValue(sb.maximum())


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("SF Pro Text", 13))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
