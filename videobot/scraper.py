"""
SmartVideoScraper – two-phase video crawler.

Phase 1: On listing/gallery pages, collect item page URLs using a JS
         scoring heuristic (same approach as ScrapeBot).
Phase 2: For each item URL try yt-dlp first (handles 1000+ platforms),
         then fall back to direct HTML extraction via Playwright.

Hard rule: 5-second wait after EVERY download, never skipped.
"""

import os
import re
import glob
import time
import hashlib
import mimetypes
import threading
from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Optional, List, Dict, Any

import requests
from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

# ── Constants ─────────────────────────────────────────────────────────────────
VIDEO_DOWNLOAD_DELAY = 5          # seconds — NEVER skip
MIN_VIDEO_SIZE       = 100 * 1024 # 100 KB minimum

VALID_VIDEO_MIMES = frozenset([
    "video/mp4", "video/webm", "video/ogg", "video/quicktime",
    "video/x-msvideo", "video/x-matroska", "video/x-flv",
    "video/mpeg", "video/3gpp", "video/x-ms-wmv",
    "application/octet-stream",   # some servers send this for mp4
])

VIDEO_EXTENSIONS = frozenset([
    ".mp4", ".webm", ".mkv", ".mov", ".avi", ".flv",
    ".wmv", ".m4v", ".ogv", ".3gp", ".mpeg", ".mpg",
])

THUMBNAIL_KEYWORDS = (
    "thumb", "thumbnail", "preview", "poster", "/small/",
    "/tiny/", "icon", "avatar", "/s/", "/xs/",
)

# ── JS: score + collect item page links ───────────────────────────────────────
_JS_FIND_ITEM_LINKS = """
() => {
    const links = Array.from(document.querySelectorAll('a[href]'));
    const scored = [];
    links.forEach(a => {
        let score = 0;
        const href = a.href || '';
        const ariaLabel = a.getAttribute('aria-label') || '';
        const title     = a.getAttribute('title') || '';

        // wraps media content
        if (a.querySelector('img, video, [class*="thumb"], [class*="preview"]')) score += 6;
        // inside a card/grid/feed container
        const par = a.closest(
            '[class*="card"], [class*="item"], [class*="post"], [class*="thumb"],' +
            '[class*="grid"], [class*="tile"], [class*="video"], [class*="media"],' +
            '[class*="feed"], [class*="clip"], [class*="reel"]'
        );
        if (par) score += 4;
        // video-like URL path
        if (/\/(video|watch|view|post|item|media|clip|play|detail|reel|short)\//i.test(href)) score += 3;
        if (/[\?&]v=/.test(href)) score += 3;
        // accessible label / title
        if (ariaLabel) score += 1;
        if (title)     score += 1;
        // skip same-page anchors & obvious nav
        if (href.startsWith('#') || href.startsWith('javascript:')) score = 0;
        if (/\b(login|signup|register|about|contact|privacy|terms|search|tag|category|author)\b/i.test(href)) score -= 4;

        if (score >= 4) scored.push({href, score});
    });
    scored.sort((a, b) => b.score - a.score);
    const seen = new Set();
    const result = [];
    for (const item of scored) {
        if (!seen.has(item.href)) { seen.add(item.href); result.push(item.href); }
    }
    return result;
}
"""

# ── JS: find next-page link ───────────────────────────────────────────────────
_JS_FIND_NEXT_PAGE = """
() => {
    const cands = [];
    const relNext = document.querySelector('link[rel="next"], a[rel="next"]');
    if (relNext) cands.push({href: relNext.href, score: 100});

    const sels = [
        'a.next','a[class*="next"]','a[class*="Next"]',
        '[class*="pagination"] a[class*="next"]',
        '[class*="pager"] a[class*="next"]',
        'a[aria-label*="Next"]','a[aria-label*="next"]',
        'a[title*="Next"]',   'a[title*="next"]',
    ];
    for (const s of sels) {
        const el = document.querySelector(s);
        if (el && el.href) { cands.push({href: el.href, score: 50}); break; }
    }
    for (const a of document.querySelectorAll('a[href]')) {
        const t = (a.textContent || '').trim();
        if (/^(next|next page|more|load more)\s*[»›>]?$/i.test(t) || /^[»›>]$/.test(t)) {
            cands.push({href: a.href, score: 30});
        }
    }
    cands.sort((a,b) => b.score - a.score);
    if (cands.length > 0) {
        const h = cands[0].href;
        if (h && !h.startsWith('javascript:') && !h.startsWith('#')) return h;
    }
    return null;
}
"""

# ── JS: extract video sources from an item page ───────────────────────────────
_JS_FIND_VIDEO_SOURCES = """
() => {
    const results = [];
    const seen    = new Set();
    function add(url, priority) {
        if (url && !seen.has(url) && /^https?:/.test(url)) {
            seen.add(url); results.push({url, priority});
        }
    }

    // 1. <video src> and <video><source>
    document.querySelectorAll('video[src]').forEach(v => {
        add(v.src, 10);
        add(v.getAttribute('data-src'), 9);
    });
    document.querySelectorAll('video source[src]').forEach(s => {
        const t = (s.type || '');
        const p = t === 'video/mp4' ? 10 : t.startsWith('video/') ? 9 : 8;
        add(s.src, p);
        add(s.getAttribute('data-src'), p - 1);
    });

    // 2. og:video meta
    ['og:video','og:video:url','og:video:secure_url'].forEach(prop => {
        const m = document.querySelector(`meta[property="${prop}"]`);
        if (m) add(m.content, 9);
    });

    // 3. JSON-LD VideoObject
    document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
        try {
            const items = [].concat(JSON.parse(s.textContent));
            items.forEach(item => {
                if (item['@type'] === 'VideoObject') {
                    add(item.contentUrl, 8);
                    add(item.embedUrl,   7);
                }
            });
        } catch(e) {}
    });

    // 4. direct video <a href> links
    document.querySelectorAll('a[href]').forEach(a => {
        const h = a.href || '';
        if (/\.(mp4|webm|mkv|mov|avi|flv|m4v|ogv|3gp|wmv)(\?|$)/i.test(h)) {
            const p = a.hasAttribute('download') ? 10 : 7;
            add(h, p);
        }
    });

    // 5. data-* video attrs
    ['data-video-url','data-src','data-video','data-mp4',
     'data-stream','data-file','data-url','data-video-src'].forEach(attr => {
        document.querySelectorAll(`[${attr}]`).forEach(el => {
            const v = el.getAttribute(attr);
            if (v && /^https?:/.test(v)) add(v, 6);
        });
    });

    // 6. iframes embedding known video platforms
    document.querySelectorAll('iframe[src]').forEach(f => {
        if (/youtube|vimeo|dailymotion|twitch|youtu\.be/.test(f.src)) add(f.src, 5);
    });

    results.sort((a, b) => b.priority - a.priority);
    return results.map(r => r.url);
}
"""


class SmartVideoScraper:

    def __init__(self, url: str, save_dir: str, quality: str, fmt: str,
                 max_pages: int, callbacks: dict):
        self.start_url  = url
        self.save_dir   = save_dir
        self.quality    = quality    # "best" | "1080p" | "720p" | "480p" | "worst"
        self.fmt        = fmt        # "mp4"  | "webm"  | "any"
        self.max_pages  = max_pages  # 0 = unlimited
        self.callbacks  = callbacks
        self._stop      = threading.Event()

        self.pages_done   = 0
        self.items_visited = 0
        self.videos_saved  = 0
        self._seen_urls: set = set()

        os.makedirs(save_dir, exist_ok=True)

    # ── Public ────────────────────────────────────────────────────────────────

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            self._run()
        except Exception as e:
            if "error" in self.callbacks:
                self.callbacks["error"](str(e))

    # ── Internal: main loop ───────────────────────────────────────────────────

    def _run(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            page = ctx.new_page()
            current_url = self.start_url

            while current_url and not self._stop.is_set():
                if self.max_pages > 0 and self.pages_done >= self.max_pages:
                    self._log(f"Reached max pages ({self.max_pages}). Stopping.")
                    break

                self._log(f"--- Page {self.pages_done + 1}: {current_url}")
                try:
                    page.goto(current_url, wait_until="domcontentloaded", timeout=30_000)
                    self._scroll_to_bottom(page)
                except PWTimeout:
                    self._log("  Timeout loading page — stopping.")
                    break
                except Exception as e:
                    self._log(f"  Page error: {e}")
                    break

                item_links = self._find_item_links(page, current_url)
                next_url   = self._find_next_page(page, current_url)
                self._log(f"  Found {len(item_links)} item(s). Next page: {next_url or 'none'}")
                self.pages_done += 1
                self._update_pages()

                for item_url in item_links:
                    if self._stop.is_set():
                        break
                    if item_url in self._seen_urls:
                        continue
                    self._seen_urls.add(item_url)
                    self.items_visited += 1
                    self._update_items()
                    self._log(f"  Item: {item_url}")

                    # Strategy A: yt-dlp (handles YouTube, Vimeo, Twitter, TikTok, …)
                    downloaded = self._ytdlp_download(item_url)

                    # Strategy B: open in Playwright and grab raw video sources
                    if not downloaded:
                        downloaded = self._playwright_download(page, item_url)

                    if downloaded:
                        self.videos_saved += 1
                        self._update_items()
                        self._log(f"    Saved: {os.path.basename(downloaded)}")
                        self._log(f"    Waiting {VIDEO_DOWNLOAD_DELAY}s …")
                        for _ in range(VIDEO_DOWNLOAD_DELAY * 10):
                            if self._stop.is_set():
                                break
                            time.sleep(0.1)
                    else:
                        self._log("    No video found.")

                current_url = next_url

            browser.close()

        result = {
            "pages":  self.pages_done,
            "items":  self.items_visited,
            "videos": self.videos_saved,
        }
        if "done" in self.callbacks:
            self.callbacks["done"](result)

    # ── Helpers: page navigation ──────────────────────────────────────────────

    def _scroll_to_bottom(self, page: Page):
        try:
            for _ in range(6):
                page.keyboard.press("End")
                page.wait_for_timeout(350)
        except Exception:
            pass

    def _find_item_links(self, page: Page, base_url: str) -> List[str]:
        try:
            raw = page.evaluate(_JS_FIND_ITEM_LINKS)
            result = []
            for href in raw:
                if not href or href.startswith(("javascript:", "#")):
                    continue
                full = urljoin(base_url, href)
                result.append(full)
            return result[:200]
        except Exception as e:
            self._log(f"  Link scan error: {e}")
            return []

    def _find_next_page(self, page: Page, current_url: str) -> Optional[str]:
        try:
            href = page.evaluate(_JS_FIND_NEXT_PAGE)
            if href:
                full = urljoin(current_url, href)
                if full != current_url:
                    return full
        except Exception:
            pass
        return None

    # ── Strategy A: yt-dlp ───────────────────────────────────────────────────

    def _yt_dlp_format(self) -> str:
        q = self.quality
        f = self.fmt
        # Build quality ceiling
        if q == "best":
            quality_part = "bestvideo+bestaudio/best"
            fallback     = "best"
        elif q == "worst":
            quality_part = "worstvideo+worstaudio/worst"
            fallback     = "worst"
        else:
            h = q.replace("p", "")
            quality_part = f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"
            fallback     = f"best[height<={h}]/best"

        # Splice in format preference
        if f == "mp4":
            if q == "best":
                return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best"
            elif q == "worst":
                return "worstvideo[ext=mp4]+worstaudio/worst[ext=mp4]/worst"
            else:
                h = q.replace("p", "")
                return (f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
                        f"bestvideo[height<={h}]+bestaudio/"
                        f"best[height<={h}][ext=mp4]/best[height<={h}]/best")
        elif f == "webm":
            if q == "best":
                return "bestvideo[ext=webm]+bestaudio[ext=webm]/bestvideo+bestaudio/best[ext=webm]/best"
            elif q == "worst":
                return "worstvideo[ext=webm]+worstaudio/worst[ext=webm]/worst"
            else:
                h = q.replace("p", "")
                return (f"bestvideo[height<={h}][ext=webm]+bestaudio[ext=webm]/"
                        f"bestvideo[height<={h}]+bestaudio/"
                        f"best[height<={h}][ext=webm]/best[height<={h}]/best")
        else:
            return f"{quality_part}/{fallback}"

    def _ytdlp_download(self, url: str) -> Optional[str]:
        try:
            import yt_dlp
        except ImportError:
            return None

        merge_fmt = "mp4" if self.fmt == "mp4" else "mkv" if self.fmt == "webm" else "mp4"
        outtmpl   = os.path.join(self.save_dir, "%(title).80s_%(id)s.%(ext)s")

        ydl_opts = {
            "format":               self._yt_dlp_format(),
            "outtmpl":              outtmpl,
            "quiet":                True,
            "no_warnings":          True,
            "noplaylist":           True,
            "socket_timeout":       30,
            "retries":              3,
            "merge_output_format":  merge_fmt,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    return None

                # 1. requested_downloads list (most reliable)
                for dl in info.get("requested_downloads", []):
                    fp = dl.get("filepath") or dl.get("_filename")
                    if fp and os.path.isfile(fp):
                        return self._check_size(fp)

                # 2. prepare_filename + extension scan
                fn_base = os.path.splitext(ydl.prepare_filename(info))[0]
                for ext in [".mp4", ".webm", ".mkv", ".mov", ".avi", ".flv", ""]:
                    if os.path.isfile(fn_base + ext):
                        return self._check_size(fn_base + ext)

                # 3. glob by video id
                vid_id = info.get("id", "")
                if vid_id:
                    matches = glob.glob(os.path.join(self.save_dir, f"*{vid_id}*"))
                    for m in matches:
                        if os.path.splitext(m)[1].lower() in VIDEO_EXTENSIONS:
                            return self._check_size(m)

        except Exception as e:
            msg = str(e).lower()
            if "unsupported url" not in msg:
                self._log(f"    yt-dlp: {str(e)[:140]}")

        return None

    def _check_size(self, path: str) -> Optional[str]:
        if not os.path.isfile(path):
            return None
        sz = os.path.getsize(path)
        if sz < MIN_VIDEO_SIZE:
            os.remove(path)
            self._log(f"    Rejected (too small: {sz} bytes)")
            return None
        return path

    # ── Strategy B: Playwright direct extraction ──────────────────────────────

    def _playwright_download(self, page: Page, url: str) -> Optional[str]:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            self._scroll_to_bottom(page)
        except Exception as e:
            self._log(f"    Page load error: {e}")
            return None

        sources = self._find_video_sources(page, url)
        for src in sources:
            if self._stop.is_set():
                return None
            result = self._download_direct(src)
            if result:
                return result
        return None

    def _find_video_sources(self, page: Page, base_url: str) -> List[str]:
        try:
            raw = page.evaluate(_JS_FIND_VIDEO_SOURCES)
        except Exception as e:
            self._log(f"    Source scan error: {e}")
            return []

        result = []
        for src in raw:
            if any(kw in src.lower() for kw in THUMBNAIL_KEYWORDS):
                continue
            result.append(src)
        return result[:20]

    def _download_direct(self, url: str) -> Optional[str]:
        if any(kw in url.lower() for kw in THUMBNAIL_KEYWORDS):
            return None

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": url,
        }

        try:
            resp = requests.get(url, headers=headers, stream=True, timeout=30)
            resp.raise_for_status()

            ct = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ct and ct not in VALID_VIDEO_MIMES and not ct.startswith("video/"):
                return None

            # Build a safe filename
            parsed  = urlparse(url)
            fname   = os.path.basename(parsed.path) or "video"
            ext     = os.path.splitext(fname)[1].lower()
            if ext not in VIDEO_EXTENSIONS:
                guessed = mimetypes.guess_extension(ct) if ct.startswith("video/") else None
                ext     = guessed or ".mp4"
                fname   = fname + ext

            base_name   = re.sub(r'[^\w\-_.]', '_', os.path.splitext(fname)[0])
            hash_sfx    = hashlib.md5(url.encode()).hexdigest()[:8]
            safe_name   = f"{base_name}_{hash_sfx}{ext}"
            out_path    = os.path.join(self.save_dir, safe_name)

            # Avoid overwriting
            counter = 1
            orig    = out_path
            while os.path.exists(out_path):
                base2, e2 = os.path.splitext(orig)
                out_path  = f"{base2}_{counter}{e2}"
                counter  += 1

            total = 0
            with open(out_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65_536):
                    if self._stop.is_set():
                        break
                    if chunk:
                        fh.write(chunk)
                        total += len(chunk)

            if self._stop.is_set():
                if os.path.exists(out_path):
                    os.remove(out_path)
                return None

            if total < MIN_VIDEO_SIZE:
                os.remove(out_path)
                self._log(f"    Rejected direct (too small: {total} bytes)")
                return None

            return out_path

        except Exception as e:
            self._log(f"    Direct download error: {str(e)[:100]}")
            return None

    # ── Callback helpers ──────────────────────────────────────────────────────

    def _log(self, msg: str):
        if "log" in self.callbacks:
            self.callbacks["log"](msg)

    def _update_pages(self):
        if "page" in self.callbacks:
            self.callbacks["page"](self.pages_done)

    def _update_items(self):
        if "items" in self.callbacks:
            self.callbacks["items"](self.items_visited, self.videos_saved)
