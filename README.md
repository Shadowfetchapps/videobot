# VideoBot

A smart desktop video scraper built with Python, PyQt6, Playwright, and yt-dlp.

VideoBot crawls listing/gallery pages, follows each item to its detail page, downloads the video, waits 5 seconds (always), then moves on — page after page until you tell it to stop.

---

## Features

- **Dual-engine download**
  - **yt-dlp** handles YouTube, Vimeo, Twitter/X, TikTok, Dailymotion, Reddit, and 1000+ other platforms automatically
  - **Playwright direct extraction** falls back for sites with raw `<video>` tags, MP4/WebM links, og:video meta, JSON-LD VideoObject, and `data-*` attributes
- **Smart two-phase crawler** — JS scoring heuristic finds item page links on listing/gallery pages, same approach as ScrapeBot
- **Quality selector** — Best, 1080p, 720p, 480p, or Worst
- **Format preference** — MP4, WebM, or Any
- **Respectful rate limiting** — hardcoded 5-second wait after every download, never skipped
- **File validation** — rejects files under 100 KB
- **Unlimited pagination** — auto-clicks next page until max pages reached or Stop pressed
- **Dark desktop UI** — live stat cards, scrolling log, progress bar

---

## Setup

### Requirements

- Python 3.9+
- ffmpeg (for merging video+audio tracks from YouTube etc.)
  ```
  brew install ffmpeg
  ```

### Install

```bash
git clone https://github.com/Shadowfetchapps/videobot.git
cd videobot
pip install -r requirements.txt
playwright install chromium
```

### Run

```bash
python run.py
```

`VideoBot.app` is the author's macOS launcher. It runs `run.py` from a fixed path on the author's Mac with Xcode's Python, so it will not work from a fresh clone. Use `python run.py` instead.

---

## Usage

1. Paste a listing or gallery URL into **Start URL**
   - Works with YouTube channels/playlists, Vimeo showcases, video grid sites, etc.
2. Choose a **Save To** folder (defaults to `~/Downloads/VideoBot`)
3. Select **Quality** and **Format**
4. Set **Max Pages** — `∞ Unlimited` runs until you click Stop
5. Click **▶ Start Scraping**

### Stat cards

| Card | What it counts |
|------|---------------|
| Listing Pages | Gallery/listing pages crawled |
| Items Visited | Individual item URLs followed |
| Videos Saved  | Files actually downloaded |

### Log panel

Shows real-time progress: page URLs, item URLs, yt-dlp messages, download results, 5-second delay countdowns, and final totals.

---

## How it works

### Phase 1 — Find item links (listing page)

Playwright loads the gallery page and runs a JS scoring heuristic over every `<a>` tag:

| Signal | Points |
|--------|--------|
| Wraps `<img>` or `<video>` | +6 |
| Inside a card/grid/feed container | +4 |
| URL path contains `/video/`, `/watch/`, `/clip/`, etc. | +3 |
| URL contains `?v=` | +3 |
| Has `aria-label` or `title` attribute | +1 each |

Links scoring ≥ 4 are collected. The next-page URL is found via `rel="next"`, class-name heuristics, and text content (`Next`, `›`, `»`).

### Phase 2 — Download each item

For every item URL, VideoBot tries two strategies in order:

**A. yt-dlp** — attempts to extract and download using the selected quality/format. Handles platform authentication cookies, HLS streams, DASH, and format merging via ffmpeg.

**B. Playwright direct extraction** — opens the page and scans for video sources using 6 strategies:
1. `<video src>` and `<video><source src>`
2. `og:video` / `og:video:secure_url` meta tags
3. JSON-LD `VideoObject` with `contentUrl`
4. `<a href>` links ending in `.mp4`, `.webm`, `.mkv`, etc.
5. `data-video-url`, `data-src`, `data-stream`, etc.
6. YouTube/Vimeo `<iframe>` embed URLs

After every successful download a **5-second delay** is enforced before moving to the next item.

---

## Supported formats

| Format | Extensions |
|--------|-----------|
| MP4 | .mp4, .m4v |
| WebM | .webm |
| Matroska | .mkv |
| QuickTime | .mov |
| AVI | .avi |
| Flash Video | .flv |
| Windows Media | .wmv |
| MPEG | .mpeg, .mpg |
| Mobile | .3gp, .ogv |

---

## Notes

- **yt-dlp** must be installed and **ffmpeg** must be on your PATH for best results
- Some sites require being logged in — yt-dlp supports `--cookies-from-browser` for this (add to `ydl_opts` in `scraper.py`)
- Private or age-gated content will require browser cookies; this is not configured by default
- VideoBot respects the 5-second rule even across page turns — it never batches or skips the delay

---

## Project structure

```
videobot/
├── videobot/
│   ├── __init__.py
│   ├── app.py        # PyQt6 dark UI
│   ├── worker.py     # QThread background worker
│   └── scraper.py    # SmartVideoScraper (yt-dlp + Playwright)
├── requirements.txt
├── run.py
└── README.md
```

---

## License

MIT. See [LICENSE](LICENSE).
