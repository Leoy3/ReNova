"""
ReNova — Media Downloader & Converter
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import threading
from concurrent.futures import ThreadPoolExecutor
import io
import os
import re
import subprocess
import json
import sys
import time
import urllib.request
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Callable

import yt_dlp
from yt_dlp.utils import DownloadCancelled
from PIL import Image, ImageDraw

# ── Browser impersonation (TikTok / Instagram) ─────────────────────────────────
try:
    from yt_dlp.networking.impersonate import ImpersonateTarget
    _IMPERSONATE_CHROME = ImpersonateTarget.from_str("chrome")
except Exception:
    _IMPERSONATE_CHROME = None

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_NEEDS_IMPERSONATE = ("tiktok.com", "instagram.com", "twitter.com", "x.com")

# ── Theme — ReNova design tokens ───────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG          = "#0F0C0C"
BG_2        = "#191514"
SURFACE     = "#1C1818"
SURFACE_2   = "#241F1E"
SURFACE_HI  = "#2D2727"
BORDER_SOFT = "#272322"
BORDER      = "#373130"
TEXT        = "#EEEAE8"
TEXT_2      = "#B0A9A7"
TEXT_3      = "#76706E"
TEXT_4      = "#514C4A"
ACCENT      = "#F44F62"
ACCENT_HI   = "#FF5E71"
ACCENT_LO   = "#CF3F50"
ACCENT_INK  = "#0F0C0C"
OK          = "#51C672"
WARN        = "#E9AB2B"
ERR         = "#FC4447"

FFMPEG_PATH  = r"C:\ffmpeg\bin\ffmpeg.exe"
FFPROBE_PATH = r"C:\ffmpeg\bin\ffprobe.exe"

def _bundled_bin_dir() -> Optional[str]:
    """Return the ffmpeg_bin folder inside the PyInstaller bundle, or None."""
    if getattr(sys, "frozen", False):
        d = os.path.join(sys._MEIPASS, "ffmpeg_bin")  # type: ignore[attr-defined]
        if os.path.isdir(d):
            return d
    return None

THUMB_W, THUMB_H = 96, 60
MAX_DL_WORKERS   = 3

# ── File type sets ─────────────────────────────────────────────────────────────
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
              ".tiff", ".tif", ".ico", ".heic", ".heif", ".avif"}
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv",
              ".wmv", ".mp3", ".m4a", ".ts", ".3gp", ".mts", ".m2ts"}

IMAGE_OUT_FMTS = ["jpg", "png", "webp", "gif", "bmp", "tiff", "ico"]
VIDEO_OUT_FMTS = ["mp4", "mp3", "mkv", "avi", "mov", "webm"]

VIDEO_FILETYPES = (
    "*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv *.mp3 *.m4a *.ts *.3gp *.mts *.m2ts"
)
IMAGE_FILETYPES = "*.jpg *.jpeg *.png *.gif *.bmp *.webp *.tiff *.tif *.ico *.heic *.heif *.avif"


def is_image_file(path: str) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


# ── Helpers ────────────────────────────────────────────────────────────────────
def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "_", name).strip(". ")
    return name or "file"


def unique_path(folder: str, stem: str, ext: str) -> str:
    p = os.path.join(folder, f"{stem}.{ext}")
    if not os.path.exists(p):
        return p
    i = 0
    while True:
        p = os.path.join(folder, f"{stem} {i}.{ext}")
        if not os.path.exists(p):
            return p
        i += 1


def fmt_bytes(b: Optional[float]) -> str:
    if b is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def fmt_speed(b: Optional[float]) -> str:
    return "—" if b is None else fmt_bytes(b) + "/s"


def get_ffmpeg() -> str:
    d = _bundled_bin_dir()
    if d:
        p = os.path.join(d, "ffmpeg.exe")
        if os.path.exists(p):
            return p
    return FFMPEG_PATH if os.path.exists(FFMPEG_PATH) else "ffmpeg"


def get_ffprobe() -> str:
    d = _bundled_bin_dir()
    if d:
        p = os.path.join(d, "ffprobe.exe")
        if os.path.exists(p):
            return p
    return FFPROBE_PATH if os.path.exists(FFPROBE_PATH) else "ffprobe"


def _ensure_h264_mp4(tmp_dir: str, ffmpeg_bin: str, ffprobe_bin: str,
                     strip_audio: bool = False) -> None:
    """Guarantee every video file in tmp_dir is H.264 inside .mp4. No other codec/format allowed."""
    for fname in list(os.listdir(tmp_dir)):
        src = os.path.join(tmp_dir, fname)
        if not os.path.isfile(src):
            continue
        stem, ext = os.path.splitext(fname)
        ext = ext.lower()
        needs_reencode = ext != ".mp4"

        already_h264 = False
        if not needs_reencode:
            try:
                r = subprocess.run(
                    [ffprobe_bin, "-v", "quiet", "-select_streams", "v:0",
                     "-show_entries", "stream=codec_name", "-of", "csv=p=0", src],
                    capture_output=True, text=True, timeout=15,
                )
                codec = r.stdout.strip().lower()
                if codec in ("h264", "avc1"):
                    already_h264 = True
                else:
                    needs_reencode = True
            except Exception:
                needs_reencode = True

        # For strip_audio mode: even if video is already H264, check for audio streams
        has_audio = False
        if strip_audio and already_h264:
            try:
                r = subprocess.run(
                    [ffprobe_bin, "-v", "quiet", "-select_streams", "a:0",
                     "-show_entries", "stream=codec_name", "-of", "csv=p=0", src],
                    capture_output=True, text=True, timeout=15,
                )
                has_audio = bool(r.stdout.strip())
            except Exception:
                has_audio = True  # assume audio present; strip to be safe

        if not needs_reencode and not has_audio:
            continue

        out_tmp = os.path.join(tmp_dir, stem + "_h264tmp.mp4")
        if already_h264 and has_audio:
            # Video is already H264 — just remux without audio (fast, no re-encode)
            codec_args = ["-c:v", "copy", "-an"]
        else:
            audio_args = ["-an"] if strip_audio else ["-c:a", "aac", "-b:a", "192k"]
            codec_args = ["-c:v", "libx264", "-crf", "18", "-preset", "fast", *audio_args]

        r = subprocess.run(
            [ffmpeg_bin, "-y", "-i", src,
             *codec_args,
             "-movflags", "+faststart", out_tmp],
            capture_output=True, timeout=3600,
        )
        if r.returncode != 0 or not os.path.exists(out_tmp):
            stderr = r.stderr.decode(errors="replace") if r.stderr else ""
            raise RuntimeError(
                f"ffmpeg falló al convertir '{fname}' a H.264/mp4 "
                f"(código {r.returncode}).\n{stderr[:300]}"
            )
        os.remove(src)
        final = os.path.join(tmp_dir, stem + ".mp4")
        os.rename(out_tmp, final)


def _hex_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def make_placeholder_thumb() -> ctk.CTkImage:
    bg  = _hex_rgb(BG_2)
    hi  = _hex_rgb(SURFACE_HI)
    txt = _hex_rgb(TEXT_4)
    img = Image.new("RGB", (THUMB_W, THUMB_H), bg)
    draw = ImageDraw.Draw(img)
    for i in range(-THUMB_H, THUMB_W + THUMB_H, 12):
        draw.line([(i, 0), (i + THUMB_H, THUMB_H)], fill=hi, width=6)
    cx, cy, r = THUMB_W // 2, THUMB_H // 2, 9
    draw.polygon([(cx - r + 3, cy - r), (cx - r + 3, cy + r), (cx + r + 1, cy)], fill=txt)
    return ctk.CTkImage(light_image=img, dark_image=img, size=(THUMB_W, THUMB_H))


def fetch_thumbnail(url: str) -> Optional[ctk.CTkImage]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img = img.resize((THUMB_W, THUMB_H), Image.LANCZOS)
        return ctk.CTkImage(light_image=img, dark_image=img, size=(THUMB_W, THUMB_H))
    except Exception:
        return None


# ── Global resize-lag fix: debounce every CTk widget's dimension-update ────────
try:
    _ctk_orig_update_dims = ctk.CTkBaseClass._update_dimensions_event  # type: ignore[attr-defined]

    def _ctk_fast_update_dims(self, event):
        job = getattr(self, "_dim_job", None)
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._dim_job = self.after(80, lambda: _ctk_orig_update_dims(self, event))

    ctk.CTkBaseClass._update_dimensions_event = _ctk_fast_update_dims  # type: ignore[attr-defined]
except Exception:
    pass


# ── Smooth scrollable frame (debounced canvas resize) ─────────────────────────
class _FastScrollFrame(ctk.CTkScrollableFrame):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fit_job: Optional[str] = None
        self._pending_event = None

    def _fit_frame_dimensions_to_canvas(self, event):   # type: ignore[override]
        self._pending_event = event
        if self._fit_job:
            self.after_cancel(self._fit_job)
        self._fit_job = self.after(100, self._run_fit)

    def _run_fit(self):
        self._fit_job = None
        ctk.CTkScrollableFrame._fit_frame_dimensions_to_canvas(
            self, self._pending_event
        )


# ── Data models ────────────────────────────────────────────────────────────────
@dataclass
class DownloadItem:
    url: str
    quality: str
    fmt: str
    out_dir: str
    status: str        = "pending"
    title: str         = ""
    thumbnail_url: str = ""
    progress: float    = 0.0
    speed: str         = ""
    size: str          = ""
    eta: str           = ""
    error: str         = ""
    cancelled: bool    = False
    started: bool      = False
    _cancel_evt: threading.Event = field(default_factory=threading.Event)
    _meta_info: Optional[dict]   = field(default=None, repr=False)


@dataclass
class ConvertItem:
    src_path: str
    out_fmt: str
    out_dir: str
    status: str      = "converting"
    progress: float  = 0.0
    speed: str       = ""
    error: str       = ""
    cancelled: bool  = False
    _proc: Optional[subprocess.Popen] = field(default=None, repr=False)


# ── Downloader (parallel) ──────────────────────────────────────────────────────
class Downloader:
    def __init__(self, update_cb: Callable, playlist_cb: Callable = None):
        self._update = update_cb
        self._playlist_cb = playlist_cb
        self._pool = ThreadPoolExecutor(max_workers=MAX_DL_WORKERS, thread_name_prefix="dl")

    def enqueue(self, item: DownloadItem):
        self._pool.submit(self._process, item)

    def _process(self, item: DownloadItem):
        item.status = "fetching"
        self._update(item)

        ffmpeg = get_ffmpeg()
        url_lower         = item.url.lower()
        needs_impersonate = any(p in url_lower for p in _NEEDS_IMPERSONATE)
        platform_headers  = {"User-Agent": CHROME_UA}

        try:
            flat_opts = {
                "extract_flat": "in_playlist", "quiet": True, "no_warnings": True,
                "ffmpeg_location": os.path.dirname(ffmpeg),
                "socket_timeout": 20, "http_headers": platform_headers,
            }
            if needs_impersonate and _IMPERSONATE_CHROME:
                flat_opts["impersonate"] = _IMPERSONATE_CHROME
            with yt_dlp.YoutubeDL(flat_opts) as ydl_flat:
                flat_info = ydl_flat.extract_info(item.url, download=False)
        except Exception:
            flat_info = None

        if item.cancelled:
            item.status = "cancelled"; item.progress = 0.0
            self._update(item); return

        if flat_info and flat_info.get("_type") in ("playlist", "multi_video"):
            entries  = [e for e in (flat_info.get("entries") or []) if e]
            pl_title = safe_filename(flat_info.get("title", "Playlist"))
            n = len(entries)
            item.title    = f"📋 {pl_title}  ({n} video{'s' if n != 1 else ''})"
            item.status   = "playlist"
            item.progress = 100.0
            self._update(item)
            if self._playlist_cb and entries:
                urls = []
                for e in entries:
                    u = e.get("webpage_url") or e.get("url") or ""
                    if u and not u.startswith("http"):
                        u = f"https://www.youtube.com/watch?v={u}"
                    if u.startswith("http"):
                        urls.append(u)
                self._playlist_cb(item, urls)
            return

        def progress_hook(d):
            if item.cancelled:
                raise DownloadCancelled()
            if d["status"] == "downloading":
                item.status = "downloading"
                pct = d.get("_percent_str", "0%").strip().rstrip("%")
                try:
                    item.progress = float(pct)
                except ValueError:
                    item.progress = 0.0
                item.speed = fmt_speed(d.get("speed"))
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                item.size = fmt_bytes(total)
                eta = d.get("eta")
                item.eta = f"{eta}s" if eta is not None else ""
                self._update(item)
            elif d["status"] == "finished":
                item.status = "converting"; item.progress = 99.0
                self._update(item)

        if item.fmt == "noaudio":
            # Prefer video-only H264; fall back to muxed H264/any (audio stripped post-download)
            quality_map = {
                "Best":  ("bestvideo[vcodec^=avc1]/bestvideo[vcodec^=h264]"
                          "/best[vcodec^=avc1]/best[vcodec^=h264]/bestvideo/best"),
                "1080p": ("bestvideo[vcodec^=avc1][height<=1080]/bestvideo[vcodec^=h264][height<=1080]"
                          "/best[vcodec^=avc1][height<=1080]/best[vcodec^=h264][height<=1080]"
                          "/bestvideo[height<=1080]/best[height<=1080]"),
                "720p":  ("bestvideo[vcodec^=avc1][height<=720]/bestvideo[vcodec^=h264][height<=720]"
                          "/best[vcodec^=avc1][height<=720]/best[vcodec^=h264][height<=720]"
                          "/bestvideo[height<=720]/best[height<=720]"),
                "480p":  ("bestvideo[vcodec^=avc1][height<=480]/best[vcodec^=avc1][height<=480]"
                          "/bestvideo[height<=480]/best[height<=480]"),
                "360p":  ("bestvideo[vcodec^=avc1][height<=360]/best[vcodec^=avc1][height<=360]"
                          "/bestvideo[height<=360]/best[height<=360]"),
                "Audio only": "bestvideo[vcodec^=avc1]/best[vcodec^=avc1]/bestvideo/best",
            }
            postprocessors = [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]
            pp_args_dict = {"FFmpegVideoConvertor": ["-c:v", "libx264", "-crf", "18",
                                                     "-preset", "fast", "-an"]}
        elif item.fmt == "mp3":
            quality_map = {k: "bestaudio/best" for k in
                           ["Best", "1080p", "720p", "480p", "360p", "Audio only"]}
            postprocessors = [{"key": "FFmpegExtractAudio",
                               "preferredcodec": "mp3", "preferredquality": "192"}]
            pp_args_dict = {}
        else:
            # Prefer H264+AAC streams directly; any fallback gets re-encoded to H264 by the convertor
            quality_map = {
                "Best":  ("bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]"
                          "/bestvideo[vcodec^=avc1]+bestaudio"
                          "/bestvideo[ext=mp4]+bestaudio[ext=m4a]"
                          "/bestvideo[ext=mp4]+bestaudio"
                          "/bestvideo+bestaudio/best"),
                "1080p": ("bestvideo[vcodec^=avc1][height<=1080]+bestaudio[ext=m4a]"
                          "/bestvideo[vcodec^=avc1][height<=1080]+bestaudio"
                          "/bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]"
                          "/bestvideo[ext=mp4][height<=1080]+bestaudio"
                          "/bestvideo[height<=1080]+bestaudio/best[height<=1080]"),
                "720p":  ("bestvideo[vcodec^=avc1][height<=720]+bestaudio[ext=m4a]"
                          "/bestvideo[vcodec^=avc1][height<=720]+bestaudio"
                          "/bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]"
                          "/bestvideo[ext=mp4][height<=720]+bestaudio"
                          "/bestvideo[height<=720]+bestaudio/best[height<=720]"),
                "480p":  ("bestvideo[vcodec^=avc1][height<=480]+bestaudio[ext=m4a]"
                          "/bestvideo[vcodec^=avc1][height<=480]+bestaudio"
                          "/bestvideo[ext=mp4][height<=480]+bestaudio"
                          "/bestvideo[height<=480]+bestaudio/best[height<=480]"),
                "360p":  ("bestvideo[vcodec^=avc1][height<=360]+bestaudio[ext=m4a]"
                          "/bestvideo[vcodec^=avc1][height<=360]+bestaudio"
                          "/bestvideo[ext=mp4][height<=360]+bestaudio"
                          "/bestvideo[height<=360]+bestaudio/best[height<=360]"),
                "Audio only": "bestaudio/best",
            }
            # FFmpegVideoConvertor runs after merge/download; converts non-mp4 to H264 mp4.
            # FFmpegMerger args force H264+AAC when separate streams are merged.
            _h264_args = ["-c:v", "libx264", "-crf", "18", "-preset", "fast",
                          "-c:a", "aac", "-b:a", "192k"]
            postprocessors = [
                {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
                {"key": "FFmpegMetadata"},
            ]
            pp_args_dict = {
                "FFmpegMerger":        _h264_args,
                "FFmpegVideoConvertor": _h264_args,
            }

        fmt_sel = quality_map.get(item.quality, quality_map["Best"])
        tmp_dir = os.path.join(item.out_dir, f".ytdl_{id(item)}")
        os.makedirs(tmp_dir, exist_ok=True)

        ydl_opts = {
            "format": fmt_sel,
            "outtmpl": os.path.join(tmp_dir, "%(title)s.%(ext)s"),
            "restrictfilenames": False,
            "progress_hooks": [progress_hook],
            "postprocessors": postprocessors,
            "ffmpeg_location": os.path.dirname(ffmpeg),
            "merge_output_format": "mp4",
            "postprocessor_args": pp_args_dict,
            "http_headers": platform_headers,
            "socket_timeout": 20, "retries": 3,
            "quiet": True, "no_warnings": True,
        }
        if item.fmt == "mp4":
            ydl_opts["format_sort"] = ["ext:mp4:m4a", "vcodec:avc1", "acodec:mp4a"]
        # Do NOT add impersonate upfront for TikTok/Instagram — it interferes with their
        # JS challenge solver. Let yt-dlp handle the challenge natively first.

        def _run_with_opts(opts: dict):
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(item.url, download=False)
                if item.cancelled:
                    raise DownloadCancelled()
                item.title         = safe_filename(info.get("title", "video"))
                item.thumbnail_url = info.get("thumbnail", "")
                self._update(item)
                ydl.process_info(info)

        try:
            try:
                _run_with_opts(ydl_opts)
            except Exception as first_exc:
                if needs_impersonate:
                    # Retry sequence for social platforms:
                    # 1. browser cookies (user may be logged in)
                    # 2. impersonation only
                    # 3. impersonation + cookies
                    opts_no_imp = {k: v for k, v in ydl_opts.items() if k != "impersonate"}
                    succeeded = False
                    # try browser cookies without impersonation first
                    for browser in ("chrome", "firefox", "edge", "brave", "opera", "chromium"):
                        try:
                            o = {**opts_no_imp, "cookiesfrombrowser": (browser,)}
                            _run_with_opts(o)
                            succeeded = True; break
                        except DownloadCancelled:
                            raise
                        except Exception:
                            continue
                    # try impersonation alone
                    if not succeeded and _IMPERSONATE_CHROME:
                        try:
                            _run_with_opts({**ydl_opts, "impersonate": _IMPERSONATE_CHROME})
                            succeeded = True
                        except DownloadCancelled:
                            raise
                        except Exception:
                            pass
                    # try impersonation + each browser's cookies
                    if not succeeded and _IMPERSONATE_CHROME:
                        for browser in ("chrome", "firefox", "edge", "brave"):
                            try:
                                o = {**ydl_opts, "impersonate": _IMPERSONATE_CHROME,
                                     "cookiesfrombrowser": (browser,)}
                                _run_with_opts(o)
                                succeeded = True; break
                            except DownloadCancelled:
                                raise
                            except Exception:
                                continue
                    if not succeeded:
                        raise first_exc
                else:
                    raise first_exc

            if item.cancelled:
                raise DownloadCancelled()

            if item.fmt in ("mp4", "noaudio"):
                item.status = "converting"; item.progress = 99.0
                self._update(item)
                _ensure_h264_mp4(tmp_dir, ffmpeg, get_ffprobe(),
                                 strip_audio=(item.fmt == "noaudio"))

            for fname in os.listdir(tmp_dir):
                src_path = os.path.join(tmp_dir, fname)
                stem_raw, fext = (fname.rsplit(".", 1) if "." in fname
                                  else (fname, item.fmt))
                dst = unique_path(item.out_dir, safe_filename(stem_raw), fext)
                os.replace(src_path, dst)

            item.status = "done"; item.progress = 100.0

        except DownloadCancelled:
            item.status = "cancelled"; item.progress = 0.0
        except Exception as exc:
            item.status = "error"
            msg = str(exc)
            if "403" in msg or "Forbidden" in msg:
                item.error = "403 Forbidden — sitio bloqueó la descarga."
            elif "404" in msg or "not found" in msg.lower():
                item.error = "Video no encontrado (404) — eliminado o privado."
            elif "private" in msg.lower():
                item.error = "Video privado — no accesible sin cuenta autorizada."
            else:
                item.error = msg[:140]
        finally:
            try:
                for f in os.listdir(tmp_dir):
                    os.remove(os.path.join(tmp_dir, f))
                os.rmdir(tmp_dir)
            except Exception:
                pass
            self._update(item)


# ── Metadata prefetcher (title + thumbnail before download starts) ─────────────
class MetaFetcher:
    def __init__(self, update_cb: Callable):
        self._update = update_cb
        self._pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="meta")

    def fetch(self, item: DownloadItem):
        self._pool.submit(self._run, item)

    def _run(self, item: DownloadItem):
        if item.started:
            return
        try:
            url_lower = item.url.lower()
            needs_imp  = any(p in url_lower for p in _NEEDS_IMPERSONATE)
            opts = {
                "quiet": True, "no_warnings": True,
                "socket_timeout": 15,
                "http_headers": {"User-Agent": CHROME_UA},
            }
            if needs_imp and _IMPERSONATE_CHROME:
                opts["impersonate"] = _IMPERSONATE_CHROME
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(item.url, download=False)
            if info:
                if info.get("_type") in ("playlist", "multi_video"):
                    entries = [e for e in (info.get("entries") or []) if e]
                    item.title = (f"📋 {safe_filename(info.get('title', 'Playlist'))}"
                                  f"  ({len(entries)} video{'s' if len(entries)!=1 else ''})")
                else:
                    item.title         = safe_filename(info.get("title", "") or "")
                    item.thumbnail_url = info.get("thumbnail", "")
                    item._meta_info    = info
        except Exception:
            pass
        self._update(item)


# ── Converter (video + image) ──────────────────────────────────────────────────
class Converter:
    def __init__(self, update_cb: Callable):
        self._update = update_cb
        self._pool = ThreadPoolExecutor(max_workers=MAX_DL_WORKERS, thread_name_prefix="cv")

    def enqueue(self, item: ConvertItem):
        self._pool.submit(self._process, item)

    def _process(self, item: ConvertItem):
        item.status = "converting"
        self._update(item)
        if is_image_file(item.src_path):
            self._convert_image(item)
        else:
            self._convert_video(item)

    def _convert_image(self, item: ConvertItem):
        src     = item.src_path
        stem    = safe_filename(Path(src).stem)
        out_ext = item.out_fmt.lower()
        dst     = unique_path(item.out_dir, stem, out_ext)
        pil_fmt = {"jpg": "JPEG", "tif": "TIFF"}.get(out_ext, out_ext.upper())
        try:
            img = Image.open(src)
            if pil_fmt in ("JPEG", "BMP"):
                if img.mode in ("RGBA", "P", "LA", "PA"):
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    mask = img.split()[-1] if img.mode in ("RGBA", "LA", "PA") else None
                    bg.paste(img, mask=mask)
                    img = bg
                else:
                    img = img.convert("RGB")
            if pil_fmt == "ICO":
                img.thumbnail((256, 256), Image.LANCZOS)
            item.progress = 50.0
            self._update(item)
            save_kw: dict = {}
            if pil_fmt == "JPEG":
                save_kw = {"quality": 95, "optimize": True}
            elif pil_fmt == "PNG":
                save_kw = {"optimize": True}
            elif pil_fmt == "WEBP":
                save_kw = {"quality": 90}
            img.save(dst, pil_fmt, **save_kw)
            item.status = "done"; item.progress = 100.0
        except Exception as exc:
            item.status = "error"; item.error = str(exc)[:140]
        finally:
            self._update(item)

    def _get_duration(self, path: str) -> Optional[float]:
        try:
            r = subprocess.run(
                [get_ffprobe(), "-v", "quiet", "-print_format", "json", "-show_format", path],
                capture_output=True, text=True, timeout=10,
            )
            return float(json.loads(r.stdout)["format"]["duration"])
        except Exception:
            return None

    def _convert_video(self, item: ConvertItem):
        ffmpeg   = get_ffmpeg()
        stem     = safe_filename(Path(item.src_path).stem)
        dst      = unique_path(item.out_dir, stem, item.out_fmt)
        duration = self._get_duration(item.src_path)
        codec_map = {
            "mp4":  ["-c:v","libx264","-crf","18","-preset","fast",
                     "-c:a","aac","-b:a","192k","-movflags","+faststart"],
            "mp3":  ["-vn","-c:a","libmp3lame","-q:a","2"],
            "mkv":  ["-c:v","libx264","-crf","18","-preset","fast","-c:a","aac","-b:a","192k"],
            "avi":  ["-c:v","libxvid","-q:v","4","-c:a","libmp3lame","-q:a","4"],
            "mov":  ["-c:v","libx264","-crf","18","-preset","fast","-c:a","aac","-b:a","192k"],
            "webm": ["-c:v","libvpx-vp9","-crf","30","-b:v","0","-c:a","libopus"],
        }
        codec_args = codec_map.get(item.out_fmt, ["-c", "copy"])
        cmd = [ffmpeg, "-y", "-i", item.src_path] + codec_args + [dst]
        try:
            proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                    text=True, encoding="utf-8", errors="ignore")
            item._proc = proc
            start = time.time()
            for line in proc.stderr:
                if item.cancelled:
                    proc.terminate(); break
                m = re.search(r"time=(\d+):(\d+):([\d.]+)", line)
                if m and duration:
                    cur = int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3))
                    item.progress = min(99.0, cur / duration * 100)
                    elapsed = time.time() - start
                    if elapsed > 0 and cur > 0:
                        item.speed = f"{cur/elapsed:.1f}x"
                    self._update(item)
            proc.wait()
            if item.cancelled:
                item.status = "cancelled"; item.progress = 0.0
                try: os.remove(dst)
                except Exception: pass
            elif proc.returncode != 0:
                raise RuntimeError(f"ffmpeg error (code {proc.returncode})")
            else:
                item.status = "done"; item.progress = 100.0
        except Exception as exc:
            item.status = "error"; item.error = str(exc)[:140]
        finally:
            item._proc = None
            self._update(item)


# ── Download Row ───────────────────────────────────────────────────────────────
class DownloadRow(ctk.CTkFrame):
    _PLACEHOLDER: Optional[ctk.CTkImage] = None

    @classmethod
    def _get_placeholder(cls) -> ctk.CTkImage:
        if cls._PLACEHOLDER is None:
            cls._PLACEHOLDER = make_placeholder_thumb()
        return cls._PLACEHOLDER

    def __init__(self, master, label: str,
                 on_start: Callable, on_cancel: Callable, on_delete: Callable, **kwargs):
        super().__init__(master, fg_color=SURFACE, corner_radius=12,
                         border_width=1, border_color=BORDER_SOFT, **kwargs)
        self._on_start  = on_start
        self._on_cancel = on_cancel
        self._on_delete = on_delete
        self._build(label)

    def _build(self, label: str):
        self.columnconfigure(1, weight=1)

        self.thumb = ctk.CTkLabel(self, image=self._get_placeholder(), text="",
                                  width=THUMB_W, height=THUMB_H)
        self.thumb.grid(row=0, column=0, rowspan=3, padx=(10, 10), pady=10, sticky="ns")

        self.lbl_title = ctk.CTkLabel(self, text=label, anchor="w",
                                      text_color=TEXT, font=("Segoe UI", 12, "bold"))
        self.lbl_title.grid(row=0, column=1, sticky="ew", padx=(0, 4), pady=(10, 2))

        self.bar = ctk.CTkProgressBar(self, height=4, corner_radius=999,
                                      progress_color=ACCENT, fg_color=BG_2)
        self.bar.set(0)
        self.bar.grid(row=1, column=1, sticky="ew", padx=(0, 4), pady=2)

        info = ctk.CTkFrame(self, fg_color="transparent")
        info.grid(row=2, column=1, sticky="ew", padx=(0, 4), pady=(2, 10))

        self.lbl_status = ctk.CTkLabel(info, text="⏳ En cola", text_color=TEXT_3,
                                       font=("Segoe UI", 11))
        self.lbl_status.pack(side="left")
        self.lbl_speed = ctk.CTkLabel(info, text="", text_color=TEXT_3,
                                      font=("Consolas", 10))
        self.lbl_speed.pack(side="left", padx=(12, 0))
        self.lbl_size = ctk.CTkLabel(info, text="", text_color=TEXT_3,
                                     font=("Consolas", 10))
        self.lbl_size.pack(side="left", padx=(12, 0))
        self.lbl_eta = ctk.CTkLabel(info, text="", text_color=TEXT_3,
                                    font=("Consolas", 10))
        self.lbl_eta.pack(side="left", padx=(12, 0))

        acts = ctk.CTkFrame(self, fg_color="transparent")
        acts.grid(row=0, column=2, rowspan=3, padx=(0, 10), sticky="ns")
        acts.pack_propagate(False)

        self.btn_start = ctk.CTkButton(
            acts, text="▶", width=30, height=30, corner_radius=7,
            fg_color=ACCENT, hover_color=ACCENT_HI, text_color=ACCENT_INK,
            font=("Segoe UI", 13), command=self._on_start,
        )
        self.btn_start.pack(pady=(0, 4))

        self.btn_action = ctk.CTkButton(
            acts, text="✕", width=30, height=30, corner_radius=7,
            fg_color=SURFACE_HI, hover_color=ERR, text_color=TEXT_2,
            font=("Segoe UI", 13), command=self._on_cancel,
        )
        self.btn_action.pack()

    def set_thumbnail(self, img: ctk.CTkImage):
        self.thumb.configure(image=img)

    def update_state(self, status: str, progress: float,
                     speed: str = "", size: str = "", eta: str = "",
                     title: str = "", error: str = ""):
        if title:
            display = title if len(title) <= 72 else title[:69] + "…"
            self.lbl_title.configure(text=display)

        color_map = {
            "done":        OK,
            "error":       ERR,
            "cancelled":   TEXT_4,
            "converting":  WARN,
            "downloading": ACCENT,
            "queued":      TEXT_3,
            "fetching":    WARN,
            "playlist":    OK,
            "pending":     TEXT_4,
        }
        label_map = {
            "done":        "✔ Done",
            "error":       f"✖ Error: {error}",
            "cancelled":   "— Cancelado",
            "converting":  "⚙ Convirtiendo…",
            "downloading": "⬇ Descargando…",
            "queued":      "⏳ En cola",
            "fetching":    "🔍 Obteniendo info…",
            "playlist":    "✔ Playlist agregada",
            "pending":     "⏸ En espera — pulsa ▶ para iniciar",
        }
        clr = color_map.get(status, TEXT_3)
        self.lbl_status.configure(text=label_map.get(status, status), text_color=clr)
        self.bar.set(progress / 100)
        bar_clr = (OK    if status == "done" else
                   ERR   if status in ("error", "cancelled") else
                   WARN  if status == "converting" else ACCENT)
        self.bar.configure(progress_color=bar_clr)
        self.lbl_speed.configure(text=speed)
        self.lbl_size.configure(text=size)
        self.lbl_eta.configure(text=eta)
        self.configure(border_color=ACCENT if status == "downloading" else BORDER_SOFT)

        if status == "pending":
            self.btn_start.configure(state="normal")
            try: self.btn_start.pack(pady=(0, 4))
            except Exception: pass
            self.btn_action.configure(text="✕", fg_color=SURFACE_HI,
                                      hover_color=ERR, command=self._on_cancel)
        elif status in ("done", "error", "cancelled", "playlist"):
            try: self.btn_start.pack_forget()
            except Exception: pass
            self.btn_action.configure(text="🗑", fg_color=SURFACE_2,
                                      hover_color=ERR, command=self._on_delete)
        else:
            try: self.btn_start.pack_forget()
            except Exception: pass
            self.btn_action.configure(text="✕", fg_color=SURFACE_HI,
                                      hover_color=ERR, command=self._on_cancel)


# ── Convert Row ────────────────────────────────────────────────────────────────
class ConvertRow(ctk.CTkFrame):
    def __init__(self, master, src_path: str,
                 on_start: Callable, on_cancel: Callable, on_delete: Callable, **kwargs):
        super().__init__(master, fg_color=SURFACE, corner_radius=12,
                         border_width=1, border_color=BORDER_SOFT, **kwargs)
        self._src_path  = src_path
        self._on_start  = on_start
        self._on_cancel = on_cancel
        self._on_delete = on_delete
        self._is_image  = is_image_file(src_path)
        self._build()

    def _build(self):
        name = Path(self._src_path).name
        if len(name) > 60:
            name = name[:57] + "…"
        ext  = Path(self._src_path).suffix.lower()
        icon = "🖼" if self._is_image else ("📄" if ext == "" else "🎬")

        self.lbl_name = ctk.CTkLabel(
            self, text=f"{icon}  {name}", anchor="w",
            text_color=TEXT, font=("Segoe UI", 12, "bold"),
        )
        self.lbl_name.pack(fill="x", padx=12, pady=(10, 6))

        self._pend = ctk.CTkFrame(self, fg_color=SURFACE_2, corner_radius=8)
        self._pend.pack(fill="x", padx=12, pady=(0, 10))

        ctk.CTkLabel(self._pend, text="Convertir a:", text_color=TEXT_3,
                     font=("Segoe UI", 11)).pack(side="left", padx=(10, 6), pady=8)

        fmts = IMAGE_OUT_FMTS if self._is_image else VIDEO_OUT_FMTS
        self.fmt_var = tk.StringVar(value=fmts[0])
        self._fmt_menu = ctk.CTkOptionMenu(
            self._pend, variable=self.fmt_var, values=fmts, width=90,
            fg_color=BG_2, button_color=SURFACE_HI, button_hover_color=BORDER,
            text_color=TEXT, dropdown_fg_color=BG_2,
            dropdown_hover_color=SURFACE, dropdown_text_color=TEXT,
        )
        self._fmt_menu.pack(side="left", pady=8)

        ctk.CTkButton(
            self._pend, text="▶  Convertir", width=115,
            fg_color=ACCENT, hover_color=ACCENT_HI,
            text_color=ACCENT_INK, font=("Segoe UI", 12),
            corner_radius=7, command=self._on_start_click,
        ).pack(side="left", padx=(10, 10), pady=8)

        self._active = ctk.CTkFrame(self, fg_color="transparent")

        prog_row = ctk.CTkFrame(self._active, fg_color="transparent")
        prog_row.pack(fill="x")
        prog_row.columnconfigure(0, weight=1)

        self.bar = ctk.CTkProgressBar(prog_row, height=4, corner_radius=999,
                                      progress_color=ACCENT, fg_color=BG_2)
        self.bar.set(0)
        self.bar.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.btn_action = ctk.CTkButton(
            prog_row, text="✕", width=30, height=30, corner_radius=7,
            fg_color=SURFACE_HI, hover_color=ERR, text_color=TEXT_2,
            font=("Segoe UI", 13), command=self._on_cancel,
        )
        self.btn_action.grid(row=0, column=1)

        info_row = ctk.CTkFrame(self._active, fg_color="transparent")
        info_row.pack(fill="x", pady=(4, 0))

        self.lbl_status = ctk.CTkLabel(info_row, text="", text_color=WARN,
                                       font=("Segoe UI", 11))
        self.lbl_status.pack(side="left")
        self.lbl_speed = ctk.CTkLabel(info_row, text="", text_color=TEXT_3,
                                      font=("Consolas", 10))
        self.lbl_speed.pack(side="left", padx=(12, 0))

    def _on_start_click(self):
        out_fmt = self.fmt_var.get()
        base = self.lbl_name.cget("text")
        self.lbl_name.configure(text=f"{base}  →  .{out_fmt}")
        self._pend.pack_forget()
        self._active.pack(fill="x", padx=12, pady=(0, 10))
        self._on_start(out_fmt)

    def update_active(self, status: str, progress: float,
                      speed: str = "", error: str = ""):
        color_map = {
            "done":      OK,
            "error":     ERR,
            "cancelled": TEXT_4,
            "converting": WARN,
            "queued":    TEXT_3,
        }
        label_map = {
            "done":      "✔ Done",
            "error":     f"✖ {error}",
            "cancelled": "— Cancelado",
            "converting": "⚙ Convirtiendo…",
            "queued":    "⏳ En cola",
        }
        clr = color_map.get(status, TEXT_3)
        self.lbl_status.configure(text=label_map.get(status, status), text_color=clr)
        self.bar.set(progress / 100)
        bar_clr = (OK if status == "done" else
                   ERR if status in ("error", "cancelled") else
                   WARN if status == "converting" else ACCENT)
        self.bar.configure(progress_color=bar_clr)
        self.lbl_speed.configure(text=speed)
        self.configure(border_color=ACCENT if status == "converting" else BORDER_SOFT)
        if status in ("done", "error", "cancelled"):
            self.btn_action.configure(text="🗑", fg_color=SURFACE_2,
                                      hover_color=ERR, command=self._on_delete)


# ── Downloader Tab ─────────────────────────────────────────────────────────────
class DownloaderTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self._out_dir = str(Path.home() / "Downloads")
        self._rows:  list[Optional[DownloadRow]]  = []
        self._items: list[DownloadItem] = []
        self._dl   = Downloader(self._on_update, playlist_cb=self._on_playlist_detected)
        self._meta = MetaFetcher(self._on_update)
        self._build()

    def _build(self):
        # ── URL textbox
        r1 = ctk.CTkFrame(self, fg_color="transparent")
        r1.pack(fill="x", padx=16, pady=(14, 6))
        r1.columnconfigure(0, weight=1)

        self.url_entry = ctk.CTkTextbox(
            r1, height=68, font=("Consolas", 12),
            fg_color=SURFACE, text_color=TEXT,
            border_color=BORDER_SOFT, border_width=1,
            corner_radius=10, wrap="none",
        )
        self.url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        _HINT = "Pega uno o más URLs aquí (uno por línea) — Ctrl+Enter o botón para agregar"
        self.url_entry.insert("1.0", _HINT)
        self.url_entry.configure(text_color=TEXT_4)
        self._url_hint = _HINT

        def _focus_in(e):
            if self.url_entry.get("1.0", "end").strip() == _HINT:
                self.url_entry.delete("1.0", "end")
                self.url_entry.configure(text_color=TEXT, border_color=ACCENT)

        def _focus_out(e):
            if not self.url_entry.get("1.0", "end").strip():
                self.url_entry.insert("1.0", _HINT)
                self.url_entry.configure(text_color=TEXT_4, border_color=BORDER_SOFT)

        self.url_entry.bind("<FocusIn>",       _focus_in)
        self.url_entry.bind("<FocusOut>",      _focus_out)
        self.url_entry.bind("<Control-Return>", lambda _: self._add())

        # ── Format pills
        pill_wrap = ctk.CTkFrame(r1, fg_color=SURFACE, corner_radius=8,
                                 border_width=1, border_color=BORDER_SOFT)
        pill_wrap.grid(row=0, column=1, padx=(0, 8))

        self._fmt_var = tk.StringVar(value="mp4")
        self._pill_btns: dict[str, ctk.CTkButton] = {}

        for label, val in [("MP4", "mp4"), ("MP3", "mp3"), ("Sin audio", "noaudio")]:
            btn = ctk.CTkButton(
                pill_wrap, text=label,
                width=70 if label == "Sin audio" else 52, height=34,
                corner_radius=6, font=("Consolas", 11, "bold"),
                fg_color=SURFACE_HI if val == "mp4" else "transparent",
                hover_color=SURFACE_HI,
                text_color=TEXT if val == "mp4" else TEXT_3,
                border_width=0,
                command=lambda v=val: self._set_fmt(v),
            )
            btn.pack(side="left", padx=3, pady=3)
            self._pill_btns[val] = btn

        # ── Quality
        self.quality_var = tk.StringVar(value="Best")
        ctk.CTkOptionMenu(
            r1, variable=self.quality_var, width=110, height=40,
            corner_radius=8, font=("Consolas", 11),
            values=["Best", "1080p", "720p", "480p", "360p", "Audio only"],
            fg_color=SURFACE, button_color=SURFACE_HI,
            button_hover_color=BORDER, text_color=TEXT,
            dropdown_fg_color=BG_2, dropdown_hover_color=SURFACE,
            dropdown_text_color=TEXT,
        ).grid(row=0, column=2)

        # ── Folder row
        r2 = ctk.CTkFrame(self, fg_color="transparent")
        r2.pack(fill="x", padx=16, pady=(0, 10))
        r2.columnconfigure(1, weight=1)

        ctk.CTkButton(r2, text="📁 Carpeta", width=96, height=32,
                      fg_color=SURFACE, hover_color=SURFACE_HI,
                      text_color=TEXT_2, font=("Segoe UI", 11),
                      border_width=1, border_color=BORDER_SOFT,
                      corner_radius=7,
                      command=self._pick_folder).grid(row=0, column=0, padx=(0, 6))
        self.folder_lbl = ctk.CTkLabel(r2, text=self._out_dir, anchor="w",
                                       text_color=TEXT_3, font=("Consolas", 11))
        self.folder_lbl.grid(row=0, column=1, sticky="ew")
        ctk.CTkButton(r2, text="📂 Abrir", width=84, height=32,
                      fg_color=SURFACE, hover_color=SURFACE_HI,
                      text_color=TEXT_2, font=("Segoe UI", 11),
                      border_width=1, border_color=BORDER_SOFT,
                      corner_radius=7,
                      command=self._open_folder).grid(row=0, column=2, padx=(6, 8))
        ctk.CTkButton(r2, text="⬇  Agregar a cola", width=150, height=32,
                      fg_color=ACCENT, hover_color=ACCENT_HI,
                      text_color=ACCENT_INK, font=("Segoe UI", 12, "bold"),
                      corner_radius=7,
                      command=self._add).grid(row=0, column=3)

        # ── Queue header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(0, 4))
        ctk.CTkLabel(hdr, text="DOWNLOAD QUEUE",
                     font=("Consolas", 10, "bold"), text_color=TEXT_2).pack(side="left")
        ctk.CTkLabel(hdr, text=f"(hasta {MAX_DL_WORKERS} simultáneas)",
                     font=("Segoe UI", 10), text_color=TEXT_4).pack(side="left", padx=8)
        ctk.CTkButton(hdr, text="🗑 Limpiar", width=100, height=26,
                      fg_color=SURFACE, hover_color=ERR,
                      text_color=TEXT_2, font=("Segoe UI", 11),
                      border_width=1, border_color=BORDER_SOFT,
                      corner_radius=6,
                      command=self._clear_finished).pack(side="right")
        ctk.CTkButton(hdr, text="▶ Iniciar todo", width=120, height=26,
                      fg_color=ACCENT, hover_color=ACCENT_HI,
                      text_color=ACCENT_INK, font=("Segoe UI", 11, "bold"),
                      border_width=0, corner_radius=6,
                      command=self._start_all).pack(side="right", padx=(0, 6))

        self.queue_frame = _FastScrollFrame(self, fg_color="transparent",
                                            scrollbar_button_color=BORDER_SOFT,
                                            scrollbar_button_hover_color=BORDER)
        self.queue_frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        self._empty_lbl = ctk.CTkLabel(
            self.queue_frame,
            text="No hay descargas — pega una URL arriba.",
            text_color=TEXT_4, font=("Segoe UI", 12),
        )
        self._empty_lbl.pack(pady=30)

    def _set_fmt(self, val: str):
        self._fmt_var.set(val)
        for k, btn in self._pill_btns.items():
            active = k == val
            btn.configure(
                fg_color=SURFACE_HI if active else "transparent",
                text_color=TEXT if active else TEXT_3,
            )

    def _pick_folder(self):
        d = filedialog.askdirectory(initialdir=self._out_dir)
        if d:
            self._out_dir = d
            self.folder_lbl.configure(text=d)

    def _open_folder(self):
        os.startfile(self._out_dir)

    def _add(self):
        raw = self.url_entry.get("1.0", "end")
        if raw.strip() == self._url_hint:
            raw = ""
        urls = [u.strip() for u in re.split(r"[\n\r,]+", raw)
                if u.strip() and u.strip().startswith("http")]
        if not urls:
            messagebox.showwarning("No URL", "Pega al menos un URL de video.")
            return
        self.url_entry.delete("1.0", "end")
        if self._empty_lbl.winfo_ismapped():
            self._empty_lbl.pack_forget()
        for url in urls:
            self._enqueue_url(url, self.quality_var.get(),
                              self._fmt_var.get(), self._out_dir)

    def _enqueue_url(self, url: str, quality: str, fmt: str, out_dir: str):
        item = DownloadItem(url=url, quality=quality, fmt=fmt, out_dir=out_dir,
                            status="pending")
        idx  = len(self._items)
        self._items.append(item)
        self._rows.append(None)

        short = url if len(url) <= 60 else url[:57] + "…"
        row = DownloadRow(
            self.queue_frame,
            label=f"#{idx+1}  {short}",
            on_start=lambda i=item: self._start_download(i),
            on_cancel=lambda i=item: self._cancel(i),
            on_delete=lambda i=idx:  self._delete_row(i),
        )
        row.pack(fill="x", pady=5)
        self._rows[idx] = row
        # Fetch title + thumbnail in background immediately (no download yet)
        self._meta.fetch(item)

    def _start_download(self, item: DownloadItem):
        if item.started or item.status not in ("pending",):
            return
        item.started = True
        item.status  = "queued"
        self._on_update(item)
        self._dl.enqueue(item)

    def _start_all(self):
        for item in list(self._items):
            if not item.started and item.status == "pending":
                self._start_download(item)

    def _on_playlist_detected(self, parent_item: DownloadItem, urls: list):
        def _add_all():
            if self._empty_lbl.winfo_ismapped():
                self._empty_lbl.pack_forget()
            for url in urls:
                self._enqueue_url(url, parent_item.quality,
                                  parent_item.fmt, parent_item.out_dir)
            # Auto-start playlist entries immediately
            self._start_all()
        self.after(0, _add_all)

    def _cancel(self, item: DownloadItem):
        if item.status in ("done", "error", "cancelled", "playlist"):
            return
        if item.status == "pending":
            # Not started yet — just delete the row
            try:
                idx = self._items.index(item)
                self._delete_row(idx)
            except ValueError:
                pass
            return
        item.cancelled = True
        item._cancel_evt.set()
        item.status = "cancelled"; item.progress = 0.0
        self._on_update(item)

    def _delete_row(self, idx: int):
        item = self._items[idx]
        if item.status not in ("done", "error", "cancelled", "playlist"):
            self._cancel(item)
        row = self._rows[idx]
        if row:
            row.destroy()
            self._rows[idx] = None

    def _clear_finished(self):
        for idx, (item, row) in enumerate(zip(self._items, self._rows)):
            if row and item.status in ("done", "error", "cancelled", "playlist"):
                row.destroy()
                self._rows[idx] = None

    def _on_update(self, item: DownloadItem):
        idx = self._items.index(item)
        row = self._rows[idx]
        if row is None:
            return
        title = f"#{idx+1}  {item.title}" if item.title else ""
        row.after(0, lambda: row.update_state(
            item.status, item.progress,
            item.speed, item.size, item.eta, title, item.error,
        ))
        if item.thumbnail_url and not getattr(item, "_thumb_loaded", False):
            item._thumb_loaded = True  # type: ignore[attr-defined]
            thumb_url = item.thumbnail_url
            def _load(r=row, u=thumb_url):
                img = fetch_thumbnail(u)
                if img:
                    r.after(0, lambda: r.set_thumbnail(img))
            threading.Thread(target=_load, daemon=True).start()


# ── Converter Tab ──────────────────────────────────────────────────────────────
class ConverterTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self._out_dir = str(Path.home() / "Downloads")
        self._rows:  list[Optional[ConvertRow]]  = []
        self._items: list[Optional[ConvertItem]] = []
        self._paths: list[str]                   = []
        self._conv = Converter(self._on_update)
        self._build()

    def _build(self):
        # ── Drop zone
        drop = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=12,
                            border_width=1, border_color=BORDER_SOFT, height=100)
        drop.pack(fill="x", padx=16, pady=14)
        drop.pack_propagate(False)

        self._drop_lbl = ctk.CTkLabel(
            drop,
            text="🎬  Arrastra cualquier archivo aquí (video, imagen o sin extensión)\no haz clic para buscar",
            font=("Segoe UI", 13), text_color=TEXT_3, cursor="hand2",
        )
        self._drop_lbl.pack(expand=True)
        self._drop_lbl.bind("<Button-1>", lambda _: self._browse())
        drop.bind("<Button-1>", lambda _: self._browse())
        self._setup_dnd(drop)

        # ── Folder row
        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.pack(fill="x", padx=16, pady=(0, 8))
        opts.columnconfigure(1, weight=1)

        ctk.CTkButton(opts, text="📁 Carpeta destino", height=32,
                      fg_color=SURFACE, hover_color=SURFACE_HI,
                      text_color=TEXT_2, font=("Segoe UI", 11),
                      border_width=1, border_color=BORDER_SOFT,
                      corner_radius=7, width=150,
                      command=self._pick_folder).grid(row=0, column=0, padx=(0, 8))
        self.folder_lbl = ctk.CTkLabel(opts, text=self._out_dir, anchor="w",
                                       text_color=TEXT_3, font=("Consolas", 11))
        self.folder_lbl.grid(row=0, column=1, sticky="ew")
        ctk.CTkButton(opts, text="📂 Abrir", height=32, width=84,
                      fg_color=SURFACE, hover_color=SURFACE_HI,
                      text_color=TEXT_2, font=("Segoe UI", 11),
                      border_width=1, border_color=BORDER_SOFT,
                      corner_radius=7,
                      command=self._open_folder).grid(row=0, column=2, padx=(8, 0))

        # ── Queue header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(4, 4))
        ctk.CTkLabel(hdr, text="CONVERSION QUEUE",
                     font=("Consolas", 10, "bold"), text_color=TEXT_2).pack(side="left")
        ctk.CTkButton(hdr, text="🗑 Limpiar", width=100, height=26,
                      fg_color=SURFACE, hover_color=ERR,
                      text_color=TEXT_2, font=("Segoe UI", 11),
                      border_width=1, border_color=BORDER_SOFT,
                      corner_radius=6,
                      command=self._clear_finished).pack(side="right")

        self.queue_frame = _FastScrollFrame(self, fg_color="transparent",
                                            scrollbar_button_color=BORDER_SOFT,
                                            scrollbar_button_hover_color=BORDER)
        self.queue_frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        self._empty_lbl = ctk.CTkLabel(
            self.queue_frame,
            text="Sin archivos — arrastra videos/imágenes arriba o haz clic para buscar.",
            text_color=TEXT_4, font=("Segoe UI", 12),
        )
        self._empty_lbl.pack(pady=30)

    def _setup_dnd(self, target):
        try:
            target.drop_target_register("DND_Files")    # type: ignore
            target.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore
        except Exception:
            pass

    def _on_drop(self, event):
        for p in self.tk.splitlist(event.data):
            self._add_file(p.strip("{}"))

    def _browse(self):
        all_types = f"{VIDEO_FILETYPES} {IMAGE_FILETYPES}"
        paths = filedialog.askopenfilenames(
            title="Seleccionar archivos a convertir",
            filetypes=[
                ("Todos los archivos", "*.*"),
                ("Video e imagen", all_types),
                ("Video", VIDEO_FILETYPES),
                ("Imagen", IMAGE_FILETYPES),
            ],
        )
        for p in paths:
            self._add_file(p)

    def _add_file(self, path: str):
        path = path.strip()
        if not os.path.isfile(path):
            return
        idx = len(self._rows)
        self._paths.append(path)
        self._items.append(None)
        self._rows.append(None)
        if self._empty_lbl.winfo_ismapped():
            self._empty_lbl.pack_forget()
        row = ConvertRow(
            self.queue_frame,
            src_path=path,
            on_start=lambda fmt, i=idx: self._start_conversion(i, fmt),
            on_cancel=lambda i=idx: self._cancel(i),
            on_delete=lambda i=idx: self._delete_row(i),
        )
        row.pack(fill="x", pady=5)
        self._rows[idx] = row

    def _start_conversion(self, idx: int, out_fmt: str):
        item = ConvertItem(
            src_path=self._paths[idx],
            out_fmt=out_fmt,
            out_dir=self._out_dir,
        )
        self._items[idx] = item
        self._conv.enqueue(item)

    def _cancel(self, idx: int):
        item = self._items[idx]
        if item is None:
            self._delete_row(idx)
            return
        if item.status in ("done", "error", "cancelled"):
            return
        item.cancelled = True
        if item._proc:
            try: item._proc.terminate()
            except Exception: pass

    def _delete_row(self, idx: int):
        self._cancel(idx)
        row = self._rows[idx]
        if row:
            row.destroy()
            self._rows[idx] = None

    def _clear_finished(self):
        for idx, (item, row) in enumerate(zip(self._items, self._rows)):
            if row and (item is None or item.status in ("done", "error", "cancelled")):
                row.destroy()
                self._rows[idx] = None

    def _pick_folder(self):
        d = filedialog.askdirectory(initialdir=self._out_dir)
        if d:
            self._out_dir = d
            self.folder_lbl.configure(text=d)

    def _open_folder(self):
        os.startfile(self._out_dir)

    def _on_update(self, item: ConvertItem):
        try:
            idx = self._items.index(item)
        except ValueError:
            return
        row = self._rows[idx]
        if row is None:
            return
        row.after(0, lambda: row.update_active(
            item.status, item.progress, item.speed, item.error,
        ))


# ── Main Window ────────────────────────────────────────────────────────────────
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ReNova")
        self.geometry("920x740")
        self.minsize(780, 560)
        self.configure(fg_color=BG)
        # Enable drag-and-drop on this interpreter (TkDND package)
        try:
            import tkinterdnd2
            tkinterdnd2.TkinterDnD._require(self)
        except Exception:
            pass

        # ── Brand header
        hdr = ctk.CTkFrame(self, fg_color=BG_2, height=60, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        brand_row = ctk.CTkFrame(hdr, fg_color="transparent")
        brand_row.pack(side="left", padx=20, fill="y")

        name_row = ctk.CTkFrame(brand_row, fg_color="transparent")
        name_row.pack(side="left", anchor="center")
        ctk.CTkLabel(name_row, text="Re", font=("Georgia", 26, "normal"),
                     text_color=TEXT).pack(side="left")
        ctk.CTkLabel(name_row, text="Nova", font=("Georgia", 26, "italic"),
                     text_color=ACCENT).pack(side="left")
        ctk.CTkLabel(brand_row, text="  MEDIA DOWNLOADER · V1.0",
                     font=("Consolas", 9), text_color=TEXT_4).pack(side="left", anchor="center")

        # ── Tabview
        self.tabs = ctk.CTkTabview(
            self, fg_color=BG,
            segmented_button_fg_color=BG_2,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HI,
            segmented_button_unselected_color=BG_2,
            segmented_button_unselected_hover_color=SURFACE_HI,
            text_color=TEXT_2,
            text_color_disabled=TEXT_4,
            border_width=0,
            corner_radius=0,
        )
        self.tabs.pack(fill="both", expand=True)
        self.tabs.add("⬇  Download")
        self.tabs.add("🔄  Convert")

        DownloaderTab(self.tabs.tab("⬇  Download")).pack(fill="both", expand=True)
        ConverterTab(self.tabs.tab("🔄  Convert")).pack(fill="both", expand=True)


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    App().mainloop()


if __name__ == "__main__":
    main()
