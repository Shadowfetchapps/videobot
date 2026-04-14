from PyQt6.QtCore import QThread, pyqtSignal
from .scraper import SmartVideoScraper


class ScraperWorker(QThread):
    log_signal      = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    page_signal     = pyqtSignal(int)        # pages_done
    items_signal    = pyqtSignal(int, int)   # items_visited, videos_saved
    done_signal     = pyqtSignal(dict)
    error_signal    = pyqtSignal(str)

    def __init__(self, url: str, save_dir: str, quality: str, fmt: str, max_pages: int):
        super().__init__()
        self.url       = url
        self.save_dir  = save_dir
        self.quality   = quality
        self.fmt       = fmt
        self.max_pages = max_pages
        self._scraper  = None

    def stop(self):
        if self._scraper:
            self._scraper.stop()

    def run(self):
        try:
            callbacks = {
                "log":      self.log_signal.emit,
                "progress": self.progress_signal.emit,
                "page":     self.page_signal.emit,
                "items":    self.items_signal.emit,
                "done":     self.done_signal.emit,
                "error":    self.error_signal.emit,
            }
            self._scraper = SmartVideoScraper(
                url       = self.url,
                save_dir  = self.save_dir,
                quality   = self.quality,
                fmt       = self.fmt,
                max_pages = self.max_pages,
                callbacks = callbacks,
            )
            self._scraper.run()
        except Exception as e:
            self.error_signal.emit(str(e))
