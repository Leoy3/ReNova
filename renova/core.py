"""Lógica de descargas y conversiones (sin UI). Los trabajos corren en hilos y
avisan cambios con `notify(item)`; la UI los aplica en su propio hilo."""

from __future__ import annotations

import ctypes
import itertools
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

from . import engine, media, paths

log = logging.getLogger("renova.core")

# ── Constantes de formato ──────────────────────────────────────────────────────
VIDEO_QUALITIES = [
    ("max", "Máxima"), ("2160", "4K · 2160p"), ("1440", "1440p"), ("1080", "1080p"),
    ("720", "720p"), ("480", "480p"), ("360", "360p"),
]
AUDIO_QUALITIES = [("mp3", "MP3 · 320 kbps"), ("wav", "WAV · sin pérdida"), ("m4a", "M4A · AAC")]
FORMATS = [("video", "Video"), ("audio", "Solo audio"), ("mute", "Video sin audio")]
SLOW_QUALITIES = ("max", "2160", "1440")


def quality_label(fmt: str, quality: str) -> str:
    table = dict(AUDIO_QUALITIES if fmt == "audio" else VIDEO_QUALITIES)
    return table.get(quality, quality)


def short_format_label(fmt: str, quality: str) -> str:
    if fmt == "audio":
        return {"mp3": "MP3", "wav": "WAV", "m4a": "M4A"}.get(quality, quality.upper())
    q = "Máx." if quality == "max" else ("4K" if quality == "2160" else f"{quality}p")
    return f"MP4 · {q}" + (" · mudo" if fmt == "mute" else "")


SOCIAL_DOMAINS = ("tiktok.com", "instagram.com", "twitter.com", "x.com", "facebook.com",
                  "fb.watch", "threads.net")
URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.I)


# ── Helpers ────────────────────────────────────────────────────────────────────
def extract_urls(text: str) -> list:
    out, seen = [], set()
    for m in URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(").,;]}>")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


_INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def safe_filename(name: str, max_len: int = 150) -> str:
    name = _INVALID.sub("_", name or "").strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    if name.upper() in _RESERVED:
        name = f"_{name}"
    if len(name) > max_len:
        name = name[:max_len].rstrip(" ._")
    return name or "archivo"


def unique_path(folder: str, stem: str, ext: str) -> str:
    ext = ext.lstrip(".")
    p = os.path.join(folder, f"{stem}.{ext}")
    i = 2
    while os.path.exists(p):
        p = os.path.join(folder, f"{stem} ({i}).{ext}")
        i += 1
    return p


def fmt_bytes(b) -> str:
    if not b:
        return "—"
    b = float(b)
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.0f} {unit}" if unit in ("B", "KB") else f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def fmt_duration(sec, ms: bool = False) -> str:
    if sec is None:
        return ""
    sec = max(0.0, float(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if ms:
        s_txt = f"{s:04.1f}"
    else:
        s_txt = f"{int(s):02d}"
    return f"{int(h)}:{int(m):02d}:{s_txt}" if h >= 1 else f"{int(m)}:{s_txt}"


def parse_time(text: str) -> float | None:
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    if len(parts) > 3 or any(p < 0 for p in parts):
        return None
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def fmt_eta(sec) -> str:
    if sec is None or sec < 0:
        return ""
    sec = int(sec)
    if sec >= 3600:
        return f"{sec // 3600}h {sec % 3600 // 60:02d}m"
    if sec >= 60:
        return f"{sec // 60}m {sec % 60:02d}s"
    return f"{sec}s"


def domain_of(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


_SOURCE_NAMES = {
    "youtube": "YouTube", "youtu": "YouTube", "instagram": "Instagram", "tiktok": "TikTok",
    "twitter": "X", "x": "X", "vimeo": "Vimeo", "facebook": "Facebook", "twitch": "Twitch",
    "reddit": "Reddit", "soundcloud": "SoundCloud", "dailymotion": "Dailymotion",
}


def source_name(url: str, extractor: str | None = None) -> str:
    if extractor:
        key = re.sub(r"(tab|shorts|clip|ie|post|user|live)$", "", extractor.lower())
        for k, v in _SOURCE_NAMES.items():
            if key.startswith(k):
                return v
    d = domain_of(url)
    base = d.split(".")[-2] if d.count(".") >= 1 else d
    return _SOURCE_NAMES.get(base, d or "Web")


def youtube_video_id(url: str) -> str | None:
    try:
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(u.query)
        if "v" in q:
            return q["v"][0]
        if (u.hostname or "").endswith("youtu.be"):
            return u.path.strip("/").split("/")[0] or None
    except Exception:
        pass
    return None


def needs_social_tricks(url: str) -> bool:
    d = domain_of(url)
    return any(d == s or d.endswith("." + s) for s in SOCIAL_DOMAINS)


def pick_thumbnail(info: dict, min_width: int = 300) -> str:
    thumbs = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    sized = sorted((t for t in thumbs if t.get("width")), key=lambda t: t["width"])
    for t in sized:
        if t["width"] >= min_width:
            return t["url"]
    if sized:
        return sized[-1]["url"]
    if info.get("thumbnail"):
        return info["thumbnail"]
    return thumbs[-1]["url"] if thumbs else ""


def fetch_image(url: str, timeout: float = 12):
    from PIL import Image
    import io
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(8 * 1024 * 1024)
    img = Image.open(io.BytesIO(data))
    img.load()
    return img.convert("RGB")


def is_h264(vcodec: str | None) -> bool:
    v = (vcodec or "").lower()
    return v.startswith(("avc1", "avc3", "h264"))


def predict_video(info: dict | None, quality: str):
    """Estimación de la resolución que se descargará y si habrá que recodificar."""
    if not info:
        return None, None
    fmts = [f for f in info.get("formats") or []
            if f.get("vcodec") not in (None, "none") and f.get("height")]
    if not fmts:
        return None, None

    def res(f):
        w, h = f.get("width"), f.get("height")
        return min(w, h) if w and h else h

    sdr = [f for f in fmts if (f.get("dynamic_range") or "SDR") == "SDR"] or fmts
    cap = None if quality == "max" else int(quality)
    under = [f for f in sdr if cap is None or res(f) <= cap] or [min(sdr, key=res)]
    best = max(res(f) for f in under)
    same = [f for f in under if res(f) == best]
    h264 = [f for f in same if is_h264(f.get("vcodec"))]
    pick = max(h264 or same, key=lambda f: (f.get("fps") or 0, f.get("tbr") or 0))
    fps = pick.get("fps") or 0
    label = f"{best}p" + (f"{int(round(fps))}" if fps and fps > 31 else "")
    return label, not h264


def humanize_error(exc: Exception) -> tuple:
    raw = str(exc) or exc.__class__.__name__
    raw = re.sub(r"\x1b\[[0-9;]*m", "", raw)
    raw = re.sub(r"^(ERROR:\s*)+", "", raw.strip())
    low = raw.lower()
    rules = [
        (("confirm you", "not a bot"), "YouTube pidió verificar que no sos un bot. Abrí YouTube en Firefox o Edge con tu cuenta y reintentá."),
        (("sign in to confirm your age", "age-restricted", "inappropriate for some users"), "Video con restricción de edad: requiere una cuenta iniciada en el navegador."),
        (("private video", "is private"), "El video es privado."),
        (("video unavailable", "this video is unavailable", "has been removed", "no longer available"), "El video no está disponible (eliminado o bloqueado)."),
        (("members-only", "join this channel"), "Video sólo para miembros del canal."),
        (("live event will begin", "premieres in", "is upcoming"), "El video todavía no se estrenó."),
        (("unsupported url",), "Este link no es compatible."),
        (("is not a valid url", "invalid url"), "El link no es válido."),
        (("http error 404", "not found"), "No se encontró el video (404)."),
        (("http error 403", "forbidden"), "El sitio bloqueó la descarga (403). Reintentá en unos minutos."),
        (("http error 429", "too many requests", "rate-limit", "rate limit"), "Demasiadas solicitudes: el sitio limitó temporalmente las descargas. Esperá unos minutos."),
        (("getaddrinfo", "name resolution", "network is unreachable", "no route to host",
          "connection refused", "timed out", "connection reset", "urlopen error"), "Sin conexión o el servidor no responde."),
        (("requested format is not available",), "No hay un formato disponible para la calidad elegida."),
        (("login", "log in", "cookies", "authentication"), "El sitio pide iniciar sesión. Iniciá sesión en Firefox o Edge y reintentá."),
        (("no space left", "disk full", "errno 28"), "No hay espacio suficiente en el disco."),
        (("permission denied", "access is denied", "errno 13"), "No hay permiso para escribir en la carpeta de destino."),
        (("ffmpeg",), "Error al procesar el video con ffmpeg."),
        (("drm",), "El contenido está protegido con DRM y no se puede descargar."),
    ]
    for keys, msg in rules:
        if any(k in low for k in keys):
            return msg, raw
    first = raw.splitlines()[0] if raw else "Error desconocido"
    return (first[:160] + ("…" if len(first) > 160 else "")), raw


class YtdlLogger:
    def __init__(self):
        self.errors = []

    def debug(self, msg):
        if not msg.startswith("[debug] "):
            log.debug(msg)

    def info(self, msg):
        log.debug(msg)

    def warning(self, msg):
        log.info("yt-dlp: %s", msg)

    def error(self, msg):
        self.errors.append(msg)
        log.warning("yt-dlp: %s", msg)


def _hide(path: str):
    try:
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)
    except Exception:
        pass


def js_runtimes() -> dict:
    rt = {"deno": {}}
    d = paths.deno()
    if d:
        rt["deno"] = {"path": d}
    n = paths.node()
    if n:
        rt["node"] = {"path": n}
    return rt


def base_ydl_opts() -> dict:
    return {
        "quiet": True, "no_warnings": False, "noprogress": True,
        "logger": YtdlLogger(),
        "ffmpeg_location": paths.ffmpeg_dir() or None,
        "js_runtimes": js_runtimes(),
        "socket_timeout": 20, "retries": 5, "fragment_retries": 10, "extractor_retries": 2,
        "concurrent_fragment_downloads": 4,
        "cachedir": str(paths.cache_dir()),
        "windowsfilenames": True,
        "noplaylist": True,
        "color": {"stdout": "no_color", "stderr": "no_color"},
    }


def build_format_opts(fmt: str, quality: str, trim: bool = False) -> dict:
    sdr = "[dynamic_range=?SDR]"
    aud = "[audio_channels<=?2][format_id!*=drc]"
    if fmt == "audio":
        if quality == "m4a":
            return {"format": f"ba[acodec^=mp4a]{aud}/ba{aud}/ba/b", "format_sort": ["acodec:aac"],
                    "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a",
                                        "preferredquality": "256"}]}
        codec = "wav" if quality == "wav" else "mp3"
        pp = {"key": "FFmpegExtractAudio", "preferredcodec": codec}
        if codec == "mp3":
            pp["preferredquality"] = "320"
        return {"format": f"ba{aud}/ba/b", "postprocessors": [pp]}
    res = "res" if quality == "max" else f"res:{quality}"
    sort = [res, "vcodec:h264", "acodec:aac", "ext:mp4:m4a"]
    hls = "[protocol*=m3u8]" if trim else ""
    if fmt == "mute":
        f = (f"bv*{hls}{sdr}/" if trim else "") + f"bv*{sdr}/bv*/b"
    else:
        f = (f"bv*{hls}{sdr}+ba{aud}/" if trim else "") + f"bv*{sdr}+ba{aud}/bv*+ba/b"
    return {"format": f, "format_sort": sort, "merge_output_format": "mp4/mkv"}


def preview_format(duration) -> dict:
    """Copia liviana para el reproductor: más chica cuanto más largo es el video."""
    cap = 360 if not duration or duration <= 1200 else (240 if duration <= 3600 else 144)
    aud = "[abr<=96][format_id!*=drc]"
    return {"format": f"b[height<={cap}][vcodec!=none][acodec!=none]/bv*[height<={cap}]+ba{aud}/"
                      f"bv*[height<={cap}]+ba/wv*+wa/b",
            "format_sort": [f"res:{cap}", "vcodec:h264", "acodec:aac"],
            "merge_output_format": "mp4/mkv"}


def preview_dir(url: str) -> Path:
    import hashlib
    key = hashlib.sha1(url.encode("utf-8", "replace")).hexdigest()[:16]
    return paths.cache_dir() / "preview" / key


def _find_media(folder: Path) -> str | None:
    if not folder.is_dir():
        return None
    files = [f for f in folder.iterdir() if f.is_file() and f.stem == "preview"
             and f.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".flv")]
    return str(max(files, key=lambda f: f.stat().st_size)) if files else None


def clean_preview_cache(max_age_days: float = 2.0):
    root = paths.cache_dir() / "preview"
    if not root.is_dir():
        return
    limit = time.time() - max_age_days * 86400
    for d in root.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < limit:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _impersonate_target():
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget
        return ImpersonateTarget.from_str("chrome")
    except Exception:
        return None


_RETRYABLE = ("sign in", "log in", "login", "cookies", "confirm you", "not a bot", "private",
              "rate-limit", "rate limit", "http error 403", "forbidden", "unable to extract",
              "requested content is not available", "empty media response", "http error 429",
              "inappropriate", "age-restricted", "confirm your age", "members")


def _attempt_opts(url: str, preferred: dict | None) -> list:
    attempts = [{}]
    if needs_social_tricks(url):
        imp = _impersonate_target()
        if imp is not None:
            attempts.append({"impersonate": imp})
    for b in ("firefox", "edge", "chrome", "brave"):
        attempts.append({"cookiesfrombrowser": (b,)})
    if preferred:
        attempts = [preferred] + [a for a in attempts if a != preferred]
    return attempts


def _should_retry(url: str, exc: Exception) -> bool:
    if needs_social_tricks(url):
        return True
    low = str(exc).lower()
    return any(k in low for k in _RETRYABLE)


# ── Modelo ─────────────────────────────────────────────────────────────────────
_ids = itertools.count(1)


class DownloadItem:
    def __init__(self, url: str, fmt: str, quality: str):
        self.id = next(_ids)
        self.url = url
        self.fmt = fmt
        self.quality = quality
        self.custom_format = False
        self.status = "analyzing"   # analyzing | ready | queued | downloading | processing |
        #                             done | error | cancelled | playlist
        self.title = ""
        self.uploader = ""
        self.source = source_name(url)
        self.duration: float | None = None
        self.thumb_url = ""
        self.info: dict | None = None
        self.playlist: dict | None = None
        self.trim: tuple | None = None
        self.progress: float | None = None
        self.stage = ""
        self.speed = None
        self.eta = None
        self.downloaded = None
        self.total = None
        self.filepath: str | None = None
        self.filesize = None
        self.result = ""
        self.error = ""
        self.error_detail = ""
        self.extra_opts: dict | None = None
        self.auto_start = False
        self.cancel_evt = threading.Event()
        self._last_notify = 0.0

    @property
    def active(self) -> bool:
        return self.status in ("queued", "downloading", "processing")

    @property
    def finished(self) -> bool:
        return self.status in ("done", "error", "cancelled")


# ── Gestor de descargas ────────────────────────────────────────────────────────
class DownloadManager:
    def __init__(self, notify, settings):
        self._notify_cb = notify
        self.settings = settings
        self._lock = threading.Lock()
        self._pending: deque = deque()
        self._active: set = set()
        self._meta_sem = threading.Semaphore(4)

    def notify(self, item, force=True):
        now = time.monotonic()
        if not force and now - item._last_notify < 0.12:
            return
        item._last_notify = now
        self._notify_cb(item)

    # ── Análisis ───────────────────────────────────────────────────────────
    def analyze(self, item: DownloadItem):
        threading.Thread(target=self._analyze, args=(item,), daemon=True,
                         name=f"meta-{item.id}").start()

    def _analyze(self, item: DownloadItem):
        with self._meta_sem:
            if item.cancel_evt.is_set():
                return
            try:
                yt = engine.get(60)
                opts = base_ydl_opts()
                opts.update({"extract_flat": "in_playlist", "skip_download": True,
                             "noplaylist": False, "lazy_playlist": False})
                info = self._extract_with_attempts(yt, item, opts)
                if item.cancel_evt.is_set():
                    return
                if info.get("_type") in ("playlist", "multi_video"):
                    entries = []
                    for i, e in enumerate(info.get("entries") or []):
                        if not e:
                            continue
                        url = e.get("webpage_url") or e.get("url") or ""
                        if url and not url.startswith("http") and e.get("ie_key", "").lower().startswith("youtube"):
                            url = f"https://www.youtube.com/watch?v={url}"
                        if not url.startswith("http"):
                            continue
                        entries.append({
                            "index": i + 1, "url": url, "id": e.get("id"),
                            "title": e.get("title") or url,
                            "duration": e.get("duration"),
                            "uploader": e.get("channel") or e.get("uploader") or "",
                            "thumb": pick_thumbnail(e, 240),
                        })
                    if not entries:
                        raise RuntimeError("La playlist está vacía o es privada.")
                    item.playlist = {
                        "title": info.get("title") or "Playlist",
                        "uploader": info.get("channel") or info.get("uploader") or "",
                        "entries": entries,
                        "current": youtube_video_id(item.url),
                    }
                    item.title = item.playlist["title"]
                    item.source = source_name(item.url, info.get("extractor_key"))
                    item.status = "playlist"
                else:
                    self.apply_info(item, info)
                    item.status = "ready"
            except Exception as e:
                # Un link de video dentro de una playlist que no se pudo leer → sólo el video
                if youtube_video_id(item.url) and "list=" in item.url and not item.info:
                    try:
                        yt = engine.get(5)
                        opts = base_ydl_opts()
                        opts.update({"skip_download": True, "noplaylist": True})
                        with yt.YoutubeDL(opts) as ydl:
                            info = ydl.extract_info(item.url, download=False)
                        self.apply_info(item, info)
                        item.status = "ready"
                        self.notify(item)
                        return
                    except Exception:
                        pass
                log.warning("Análisis fallido %s: %s", item.url, e)
                item.status = "error"
                item.error, item.error_detail = humanize_error(e)
            self.notify(item)

    def fetch_info(self, item: DownloadItem, callback):
        """Metadatos completos (para el recorte de ítems que vinieron de una playlist)."""
        def run():
            try:
                yt = engine.get(60)
                opts = base_ydl_opts()
                opts.update({"skip_download": True})
                info = self._extract_with_attempts(yt, item, opts)
                self.apply_info(item, info)
                callback(item, None)
            except Exception as e:
                callback(item, humanize_error(e)[0])
        threading.Thread(target=run, daemon=True).start()

    def fetch_preview(self, item: DownloadItem, cancel: threading.Event, on_info, on_progress, on_done):
        """Baja una copia liviana (≤360p) para el reproductor del recorte. Se cachea por URL.
        Callbacks (desde un hilo): on_info(), on_progress(0..1), on_done(path | None, error)."""
        def run():
            folder = preview_dir(item.url)
            cached = _find_media(folder)
            if cached:
                on_done(cached, None)
                return
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)
            sizes, done = {}, {}

            def hook(d):
                if cancel.is_set():
                    from yt_dlp.utils import DownloadCancelled
                    raise DownloadCancelled()
                key = (d.get("info_dict") or {}).get("format_id") or "0"
                if d.get("status") == "downloading":
                    tot = d.get("total_bytes") or d.get("total_bytes_estimate")
                    if tot:
                        sizes[key] = tot
                    done[key] = d.get("downloaded_bytes") or 0
                    total = sum(sizes.values())
                    if total:
                        on_progress(min(1.0, sum(done.values()) / total))

            def fn(ydl):
                info = ydl.extract_info(item.url, download=False)
                if item.info is None:
                    self.apply_info(item, info)
                    on_info()
                for p in info.get("requested_formats") or [info]:
                    if p.get("format_id"):
                        sizes.setdefault(p["format_id"], p.get("filesize") or p.get("filesize_approx") or 0)
                ydl.process_info(info)

            try:
                yt = engine.get(60)
                opts = base_ydl_opts()
                opts.update(preview_format(item.duration))
                opts.update({"outtmpl": str(folder / "preview.%(ext)s"), "progress_hooks": [hook]})
                self._extract_with_attempts(yt, item, opts, fn)
                path = _find_media(folder)
                if not path:
                    raise RuntimeError("No se generó la vista previa")
                on_done(path, None)
            except Exception as e:
                shutil.rmtree(folder, ignore_errors=True)
                if not cancel.is_set():
                    log.info("Vista previa fallida %s: %s", item.url, e)
                on_done(None, None if cancel.is_set() else humanize_error(e)[0])
        threading.Thread(target=run, daemon=True, name=f"preview-{item.id}").start()

    def apply_info(self, item: DownloadItem, info: dict):
        item.info = info
        item.title = info.get("title") or item.title or item.url
        item.uploader = info.get("channel") or info.get("uploader") or item.uploader
        item.duration = info.get("duration") or item.duration
        item.thumb_url = pick_thumbnail(info) or item.thumb_url
        item.source = source_name(item.url, info.get("extractor_key"))
        item.url = info.get("webpage_url") or item.url
        if info.get("is_live"):
            raise RuntimeError("Las transmisiones en vivo no se pueden descargar mientras están al aire.")

    def _extract_with_attempts(self, yt, item, opts, fn=None):
        """Prueba sin cookies y, si el sitio lo pide, con impersonación / cookies de navegadores.
        Si todo falla se informa el error original (no el del último intento)."""
        first = None
        for extra in _attempt_opts(item.url, item.extra_opts):
            if item.cancel_evt.is_set():
                raise media.Cancelled()
            o = {**opts, **extra}
            o["logger"] = YtdlLogger()
            try:
                with yt.YoutubeDL(o) as ydl:
                    result = fn(ydl) if fn else ydl.extract_info(item.url, download=False)
                item.extra_opts = extra
                return result
            except media.Cancelled:
                raise
            except Exception as e:
                if e.__class__.__name__ == "DownloadCancelled" or item.cancel_evt.is_set():
                    raise media.Cancelled() from e
                if extra:
                    log.info("Intento %s falló: %s", list(extra)[0], str(e)[:160])
                if first is None:
                    first = e
                    if not _should_retry(item.url, e):
                        break
        raise first if first else RuntimeError("No se pudo leer el link")

    # ── Cola ───────────────────────────────────────────────────────────────
    def start(self, item: DownloadItem):
        if item.status not in ("ready", "error", "cancelled"):
            return
        item.cancel_evt = threading.Event()
        item.status = "queued"
        item.progress = 0.0
        item.error = item.error_detail = ""
        item.stage = ""
        with self._lock:
            self._pending.append(item)
        self.notify(item)
        self._pump()

    def cancel(self, item: DownloadItem):
        item.cancel_evt.set()
        with self._lock:
            if item in self._pending:
                self._pending.remove(item)
                item.status = "cancelled"
                item.progress = None
                self.notify(item)

    def active_count(self) -> int:
        with self._lock:
            return len(self._active) + len(self._pending)

    def _pump(self):
        with self._lock:
            limit = max(1, int(self.settings.max_parallel))
            while self._pending and len(self._active) < limit:
                item = self._pending.popleft()
                if item.cancel_evt.is_set():
                    continue
                self._active.add(item)
                threading.Thread(target=self._run, args=(item,), daemon=True,
                                 name=f"dl-{item.id}").start()

    def _run(self, item: DownloadItem):
        work = None
        try:
            out_dir = self.settings.out_dir
            os.makedirs(out_dir, exist_ok=True)
            work = tempfile.mkdtemp(prefix=".renova-", dir=out_dir)
            _hide(work)
            item.status = "downloading"
            item.stage = "Conectando…"
            item.progress = None
            self.notify(item)
            if item.trim:
                self._job_trim(item, work, out_dir)
            else:
                self._job_full(item, work, out_dir)
            item.status = "done"
            item.progress = 1.0
            item.stage = ""
        except media.Cancelled:
            item.status = "cancelled"
            item.progress = None
        except Exception as e:
            if e.__class__.__name__ == "DownloadCancelled" or item.cancel_evt.is_set():
                item.status = "cancelled"
                item.progress = None
            else:
                log.exception("Descarga fallida: %s", item.url)
                item.status = "error"
                item.error, item.error_detail = humanize_error(e)
        finally:
            if work:
                shutil.rmtree(work, ignore_errors=True)
            with self._lock:
                self._active.discard(item)
            self.notify(item)
            self._pump()

    # ── Trabajo: descarga completa ─────────────────────────────────────────
    def _progress_hook(self, item):
        sizes, done = {}, {}

        def hook(d):
            if item.cancel_evt.is_set():
                from yt_dlp.utils import DownloadCancelled
                raise DownloadCancelled()
            info = d.get("info_dict") or {}
            key = info.get("format_id") or d.get("filename") or "0"
            if d.get("status") == "downloading":
                tot = d.get("total_bytes") or d.get("total_bytes_estimate")
                if tot:
                    sizes[key] = tot
                done[key] = d.get("downloaded_bytes") or 0
                for k, v in getattr(hook, "expected", {}).items():
                    sizes.setdefault(k, v)
                total = sum(sizes.values())
                got = sum(min(done.get(k, 0), sizes.get(k, done.get(k, 0))) for k in set(sizes) | set(done))
                item.status = "downloading"
                item.stage = "Descargando"
                item.progress = (got / total) if total else None
                item.downloaded, item.total = got, total or None
                item.speed = d.get("speed")
                item.eta = ((total - got) / item.speed) if (item.speed and total) else d.get("eta")
                self.notify(item, force=False)
            elif d.get("status") == "finished":
                if key in sizes:
                    done[key] = sizes[key]
        hook.expected = {}
        return hook

    def _pp_hook(self, item):
        names = {"Merger": "Uniendo audio y video", "ExtractAudio": "Convirtiendo audio",
                 "VideoConvertor": "Convirtiendo", "FixupM3u8": "Reparando", "FixupM4a": "Reparando",
                 "MoveFiles": "Finalizando"}

        def hook(d):
            if d.get("status") == "started":
                item.status = "processing"
                item.stage = names.get(d.get("postprocessor"), "Procesando")
                item.progress = None
                self.notify(item)
        return hook

    def _job_full(self, item, work, out_dir):
        yt = engine.get(60)
        opts = base_ydl_opts()
        opts.update(build_format_opts(item.fmt, item.quality))
        opts["outtmpl"] = os.path.join(work, "%(id)s.%(ext)s")
        hook = self._progress_hook(item)
        opts["progress_hooks"] = [hook]
        opts["postprocessor_hooks"] = [self._pp_hook(item)]

        def fn(ydl):
            info = ydl.extract_info(item.url, download=False)
            if info.get("_type") in ("playlist", "multi_video"):
                raise RuntimeError("El link es una playlist; agregala de nuevo para elegir videos.")
            self.apply_info(item, info)
            parts = info.get("requested_formats") or [info]
            hook.expected = {p.get("format_id"): (p.get("filesize") or p.get("filesize_approx") or 0)
                             for p in parts if p.get("format_id")}
            hook.expected = {k: v for k, v in hook.expected.items() if v}
            item.stage = "Descargando"
            self.notify(item)
            ydl.process_info(info)
            return info

        self._extract_with_attempts(yt, item, opts, fn)
        if item.cancel_evt.is_set():
            raise media.Cancelled()

        src = self._pick_output(work)
        stem = safe_filename(item.title or "video")
        if item.fmt == "audio":
            ext = os.path.splitext(src)[1].lstrip(".") or item.quality
            dst = unique_path(out_dir, stem, ext)
            os.replace(src, dst)
            item.result = quality_label("audio", item.quality)
        else:
            item.status = "processing"
            item.stage = "Verificando compatibilidad"
            item.progress = None
            self.notify(item)
            tmp_out = os.path.join(work, "final.mp4")
            enc = media.Encoders.pick(self.settings.hw_accel)

            def on_stage(text):
                item.stage = text
                item.progress = 0.0
                self.notify(item)

            def on_progress(p):
                item.progress = p
                self.notify(item, force=False)

            summary = media.make_premiere_ready(
                src, tmp_out, mute=(item.fmt == "mute"), encoder=enc,
                on_progress=on_progress, on_stage=on_stage, cancel=item.cancel_evt)
            check = media.verify_premiere(tmp_out, expect_audio=(item.fmt == "video"))
            dst = unique_path(out_dir, stem, "mp4")
            os.replace(tmp_out, dst)
            item.result = self._describe(check, summary.get("reencoded"))
        item.filepath = dst
        item.filesize = os.path.getsize(dst)

    @staticmethod
    def _describe(check: dict, reencoded) -> str:
        w, h = check.get("width") or 0, check.get("height") or 0
        res = min(w, h) if w and h else h
        fps = check.get("fps")
        txt = f"{res}p" if res else ""
        if fps and fps > 31:
            txt += f"{int(round(fps))}"
        txt += " · H.264"
        if reencoded:
            txt += " (recodificado)"
        return txt.strip(" ·")

    @staticmethod
    def _pick_output(work: str) -> str:
        files = [e for e in os.scandir(work) if e.is_file()
                 and not e.name.endswith((".part", ".ytdl", ".temp", ".json"))
                 and ".part-Frag" not in e.name and not e.name.startswith("final.")]
        if not files:
            raise RuntimeError("La descarga terminó pero no se encontró el archivo.")
        return max(files, key=lambda e: e.stat().st_size).path

    # ── Trabajo: fragmento recortado ───────────────────────────────────────
    def _job_trim(self, item, work, out_dir):
        yt = engine.get(60)
        start, end = item.trim
        opts = base_ydl_opts()
        fo = build_format_opts(item.fmt, item.quality, trim=True)
        fo.pop("postprocessors", None)
        opts.update(fo)
        opts["outtmpl"] = os.path.join(work, "%(id)s.%(ext)s")
        hook = self._progress_hook(item)
        opts["progress_hooks"] = [hook]
        enc = media.Encoders.pick(self.settings.hw_accel)
        mode = item.fmt
        audio_target = item.quality if mode == "audio" else None

        def on_progress(p):
            item.progress = p
            self.notify(item, force=False)

        def fetch_part(ydl, info, part, name):
            new = dict(info)
            new.pop("requested_formats", None)
            new.pop("requested_downloads", None)
            new.update(part)
            path = os.path.join(work, f"{name}.{part.get('ext') or 'bin'}")
            hook.expected = {part.get("format_id"): part.get("filesize") or part.get("filesize_approx") or 0}
            ydl.dl(path, new)
            if not os.path.exists(path):
                raise RuntimeError("No se pudo descargar la pista")
            return path

        result = {}

        def fn(ydl):
            info = ydl.extract_info(item.url, download=False)
            self.apply_info(item, info)
            parts = info.get("requested_formats") or [info]
            vpart = next((p for p in parts if p.get("vcodec") not in (None, "none")), None)
            apart = next((p for p in parts if p.get("acodec") not in (None, "none")), None)
            if mode == "audio":
                vpart = None
                apart = apart or parts[0]
            if mode == "mute":
                apart = None
            if mode != "audio" and vpart is None:
                raise RuntimeError("Este link no tiene una pista de video para recortar.")
            if mode == "audio" and apart is None:
                raise RuntimeError("Este link no tiene una pista de audio.")
            fps = media.snap_fps(vpart.get("fps")) if vpart and vpart.get("fps") else None
            ext = {"mp3": "mp3", "wav": "wav", "m4a": "m4a"}.get(audio_target or "", "mp4")
            dst_tmp = os.path.join(work, f"final.{ext}")

            # 1) Audio separado: se baja completo (es liviano) con el descargador nativo
            local_audio = None
            if apart is not None and apart is not vpart:
                item.stage = "Descargando audio"
                self.notify(item)
                local_audio = fetch_part(ydl, info, apart, "audio")

            if mode == "audio":
                item.status = "processing"
                item.stage = "Recortando audio"
                item.progress = 0.0
                self.notify(item)
                media.trim([{"src": local_audio, "audio": True}], start, end, dst_tmp,
                           mode="audio", audio_target=audio_target, on_progress=on_progress,
                           cancel=item.cancel_evt)
                result["path"] = dst_tmp
                return

            # 2) Video: se lee sólo el tramo pedido directamente del servidor (HLS)
            def inputs_with(video_src, headers):
                inp = [{"src": video_src, "headers": headers, "video": True,
                        "audio": apart is not None and apart is vpart}]
                if local_audio:
                    inp.append({"src": local_audio, "audio": True})
                return inp

            remote_ok = (vpart and vpart.get("url") and (vpart.get("protocol") or "").split("+")[0]
                         in ("http", "https", "m3u8", "m3u8_native"))
            done_remote = False
            if remote_ok:
                item.status = "processing"
                item.stage = "Recortando fragmento"
                item.progress = 0.0
                self.notify(item)
                try:
                    media.trim(inputs_with(vpart["url"], vpart.get("http_headers") or info.get("http_headers")),
                               start, end, dst_tmp, mode=mode, encoder=enc, fps=fps,
                               on_progress=on_progress, cancel=item.cancel_evt, stall_timeout=40)
                    done_remote = True
                except media.Cancelled:
                    raise
                except media.MediaError as e:
                    log.warning("Recorte remoto falló (%s); bajo el video completo", str(e)[:200])
                    try:
                        os.remove(dst_tmp)
                    except OSError:
                        pass
            if not done_remote:
                item.status = "downloading"
                item.stage = "Descargando video para recortar"
                item.progress = 0.0
                self.notify(item)
                local_video = fetch_part(ydl, info, vpart, "video")
                item.status = "processing"
                item.stage = "Recortando fragmento"
                item.progress = 0.0
                self.notify(item)
                media.trim(inputs_with(local_video, None), start, end, dst_tmp, mode=mode,
                           encoder=enc, fps=fps, on_progress=on_progress, cancel=item.cancel_evt)
            result["path"] = dst_tmp

        self._extract_with_attempts(yt, item, opts, fn)
        if item.cancel_evt.is_set():
            raise media.Cancelled()
        out = result["path"]
        stem = safe_filename(f"{item.title or 'video'} [{_ts(start)}-{_ts(end)}]")
        ext = os.path.splitext(out)[1].lstrip(".")
        if mode == "audio":
            item.result = quality_label("audio", item.quality) + f" · {fmt_duration(end - start)}"
        else:
            check = media.verify_premiere(out, expect_audio=(mode == "video"))
            item.result = f"{self._describe(check, False)} · {fmt_duration(end - start)}"
        dst = unique_path(out_dir, stem, ext)
        os.replace(out, dst)
        item.filepath = dst
        item.filesize = os.path.getsize(dst)


def _ts(sec: float) -> str:
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


# ── Conversión ─────────────────────────────────────────────────────────────────
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".ico", ".avif"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff", ".aif"}

VIDEO_PRESETS = [
    ("mp4_h264", "MP4 · H.264, listo para Premiere", "mp4"), ("mov_h264", "MOV · H.264", "mov"),
    ("mkv_h264", "MKV · H.264", "mkv"), ("webm_vp9", "WEBM · VP9 (web)", "webm"),
    ("gif", "GIF animado", "gif"),
    ("mp3", "MP3 · sólo el audio", "mp3"), ("wav", "WAV · sólo el audio, sin pérdida", "wav"),
    ("m4a", "M4A · sólo el audio (AAC)", "m4a"), ("flac", "FLAC · sólo el audio, sin pérdida", "flac"),
]
AUDIO_PRESETS = [
    ("mp3", "MP3", "mp3"), ("wav", "WAV · sin pérdida", "wav"), ("m4a", "M4A · AAC", "m4a"),
    ("flac", "FLAC · sin pérdida", "flac"), ("ogg", "OGG · Vorbis", "ogg"),
]
IMAGE_PRESETS = [
    ("png", "PNG · sin pérdida, con transparencia", "png"), ("jpg", "JPG · liviano, para fotos", "jpg"),
    ("webp", "WEBP · liviano, para web", "webp"), ("bmp", "BMP", "bmp"), ("tiff", "TIFF", "tiff"),
    ("ico", "ICO · ícono de Windows", "ico"), ("gif", "GIF", "gif"),
]
AUDIO_ONLY_TARGETS = {"mp3", "wav", "m4a", "flac", "ogg"}

# Opciones de algunos formatos: clave del preset → [(opción, ajuste, [(valor, texto), ...])]
TARGET_OPTIONS = {
    "gif": [("width", "gif_width", [(320, "320 px"), (480, "480 px"), (640, "640 px"), (0, "Tamaño original")]),
            ("fps", "gif_fps", [(10, "10 fps"), (15, "15 fps"), (24, "24 fps")])],
    "mp3": [("kbps", "mp3_kbps", [(128, "128 kbps"), (192, "192 kbps"), (320, "320 kbps")])],
}


def file_kind(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in AUDIO_EXTS:
        return "audio"
    return "video"


def presets_for(kind: str) -> list:
    return {"image": IMAGE_PRESETS, "audio": AUDIO_PRESETS}.get(kind, VIDEO_PRESETS)


def preset_label(kind: str, key: str) -> str:
    for k, label, _ in presets_for(kind):
        if k == key:
            return label
    return key


def preset_ext(kind: str, key: str) -> str:
    for k, _, ext in presets_for(kind):
        if k == key:
            return ext
    return key


def preset_short(kind: str, key: str) -> str:
    return preset_ext(kind, key).upper()


def option_label(key: str, opts: dict) -> str:
    """Texto corto de las opciones elegidas («640 px · 15 fps»)."""
    out = []
    for name, _setting, choices in TARGET_OPTIONS.get(key, []):
        out.append(dict(choices).get(opts.get(name), str(opts.get(name))))
    return " · ".join(out)


_CODEC_NAMES = {"h264": "H.264", "hevc": "H.265 (HEVC)", "vp9": "VP9", "av1": "AV1", "vp8": "VP8",
                "mpeg4": "MPEG-4", "prores": "ProRes", "aac": "AAC", "mp3": "MP3", "opus": "Opus",
                "vorbis": "Vorbis", "flac": "FLAC", "ac3": "AC-3", "eac3": "E-AC-3", "alac": "ALAC",
                "gif": "GIF", "mjpeg": "MJPEG", "wmv3": "WMV", "wmav2": "WMA", "dnxhd": "DNxHD"}


def codec_name(c: str | None) -> str:
    c = (c or "").lower()
    if c.startswith("pcm_"):
        return "PCM"
    return _CODEC_NAMES.get(c, c.upper())


class ConvertItem:
    def __init__(self, src: str, target: str | None):
        self.id = next(_ids)
        self.src = src
        self.kind = file_kind(src)
        self.fmt = Path(src).suffix[1:].upper() or "?"   # formato detectado (MP4, MP3, PNG…)
        self.details = ""        # «H.264 + AAC · 1920×1080 · 30 fps · 0:12 · 4,1 MB»
        self.premiere_ok = False  # el video ya es H.264 + AAC (MP4 / MOV)
        self.has_video = self.kind == "video"
        self.has_audio = self.kind != "image"
        self.target = target
        self.opts: dict = {}
        self.inspected = False
        self.status = "ready"
        self.progress: float | None = None
        self.stage = ""
        self.filepath: str | None = None
        self.filesize = None
        self.error = ""
        self.error_detail = ""
        self.info = ""
        self.duration = None
        self.result = ""
        self.cancel_evt = threading.Event()
        self._last_notify = 0.0

    @property
    def active(self):
        return self.status in ("queued", "processing")

    @property
    def finished(self):
        return self.status in ("done", "error", "cancelled")

    def targets(self) -> list:
        """Formatos a los que se puede convertir este archivo."""
        presets = presets_for(self.kind)
        if self.kind == "video" and not self.has_audio:
            presets = [p for p in presets if p[0] not in AUDIO_ONLY_TARGETS]
        return presets

    def is_same(self, key: str) -> bool:
        """El destino es el mismo formato que ya tiene (no tiene sentido convertir)."""
        ext = preset_ext(self.kind, key)
        same_ext = self.fmt.lower() in (ext, "jpeg" if ext == "jpg" else ext, "tif" if ext == "tiff" else ext)
        if key in ("mp4_h264", "mov_h264", "mkv_h264"):
            return same_ext and self.premiere_ok
        return same_ext


class ConvertManager:
    def __init__(self, notify, settings):
        self._notify_cb = notify
        self.settings = settings
        self._lock = threading.Lock()
        self._pending: deque = deque()
        self._active: set = set()

    def notify(self, item, force=True):
        now = time.monotonic()
        if not force and now - item._last_notify < 0.12:
            return
        item._last_notify = now
        self._notify_cb(item)

    def inspect(self, item: ConvertItem, on_thumb):
        """Detecta el formato real (no sólo la extensión), lee los datos y genera una miniatura."""
        def run():
            try:
                size = os.path.getsize(item.src)
                ext = Path(item.src).suffix.lower()
                animated = False
                if ext in IMAGE_EXTS:
                    from PIL import Image
                    with Image.open(item.src) as im:
                        animated = getattr(im, "n_frames", 1) > 1 and ext == ".gif"
                        item.fmt = (im.format or ext[1:]).upper().replace("JPEG", "JPG")
                        w, h = im.width, im.height
                        im.seek(0)
                        im.draft("RGB", (320, 320))
                        thumb = im.convert("RGB")
                        thumb.load()
                    if not animated:
                        item.kind = "image"
                        item.has_video = item.has_audio = False
                        item.details = f"{w}×{h} · {fmt_bytes(size)}"
                        item.info = item.details
                        on_thumb(item, thumb)
                if ext not in IMAGE_EXTS or animated:
                    pr = media.probe(item.src)
                    dur = media.duration_of(pr)
                    item.duration = dur
                    vs, aus = media.streams(pr, "video"), media.streams(pr, "audio")
                    if not vs and not aus:
                        raise media.MediaError("no tiene video ni audio")
                    item.kind = "video" if vs else "audio"
                    item.has_video, item.has_audio = bool(vs), bool(aus)
                    bits = []
                    if vs:
                        v = vs[0]
                        codecs = codec_name(v.get("codec_name"))
                        if aus:
                            codecs += " + " + codec_name(aus[0].get("codec_name"))
                        bits.append("GIF animado" if animated else codecs)
                        bits.append(f"{v.get('width')}×{v.get('height')}")
                        fps = media.stream_fps(v)
                        if fps and not animated:
                            bits.append(f"{fps:g} fps")
                        item.premiere_ok = (media.premiere_video_ok(v) and not media.is_vfr(v)
                                            and (not aus or aus[0].get("codec_name") == "aac"))
                    else:
                        a = aus[0]
                        bits.append(codec_name(a.get("codec_name")))
                        try:
                            kbps = int(a.get("bit_rate") or (pr.get("format") or {}).get("bit_rate") or 0) // 1000
                        except (TypeError, ValueError):
                            kbps = 0
                        if kbps and not (a.get("codec_name") or "").startswith(("pcm", "flac", "alac")):
                            bits.append(f"{kbps} kbps")
                        if a.get("sample_rate"):
                            bits.append(f"{int(a['sample_rate']) / 1000:g} kHz".replace(".", ","))
                        ch = a.get("channels")
                        if ch:
                            bits.append({1: "mono", 2: "estéreo"}.get(ch, f"{ch} canales"))
                    if dur:
                        bits.append(fmt_duration(dur))
                    bits.append(fmt_bytes(size))
                    item.details = " · ".join(b for b in bits if b)
                    item.info = item.details
                    if item.target and item.target not in [k for k, _l, _e in item.targets()]:
                        item.target = None
                    item.inspected = True
                    self.notify(item)
                    if vs:
                        img = media.grab_frame(item.src, (dur or 0) * 0.15, width=320, timeout=20)
                        if img is not None:
                            on_thumb(item, img)
                item.inspected = True
            except Exception as e:
                item.details = item.info = "No se pudo leer el archivo (¿está dañado o no es compatible?)"
                item.status = "error"
                item.error = "No se pudo leer el archivo"
                item.error_detail = str(e)
                item.inspected = True
                log.info("inspect %s: %s", item.src, e)
            self.notify(item)
        threading.Thread(target=run, daemon=True).start()

    def start(self, item: ConvertItem):
        if item.status not in ("ready", "error", "cancelled") or not item.target or not item.inspected:
            return
        item.cancel_evt = threading.Event()
        item.status = "queued"
        item.progress = 0.0
        item.error = ""
        with self._lock:
            self._pending.append(item)
        self.notify(item)
        self._pump()

    def cancel(self, item: ConvertItem):
        item.cancel_evt.set()
        with self._lock:
            if item in self._pending:
                self._pending.remove(item)
                item.status = "cancelled"
                self.notify(item)

    def _pump(self):
        with self._lock:
            while self._pending and len(self._active) < 2:
                item = self._pending.popleft()
                self._active.add(item)
                threading.Thread(target=self._run, args=(item,), daemon=True).start()

    def _run(self, item: ConvertItem):
        tmp = None
        try:
            out_dir = self.settings.out_dir
            os.makedirs(out_dir, exist_ok=True)
            item.status = "processing"
            item.stage = "Convirtiendo"
            item.progress = 0.0
            self.notify(item)
            ext = preset_ext(item.kind, item.target)
            stem = safe_filename(Path(item.src).stem)
            if Path(item.src).suffix.lower().lstrip(".") == ext and \
                    os.path.normcase(os.path.dirname(os.path.abspath(item.src))) == os.path.normcase(os.path.abspath(out_dir)):
                stem += " (convertido)"
            dst = unique_path(out_dir, stem, ext)
            tmp = os.path.join(out_dir, f".renova-{item.id}-{int(time.time())}.{ext}")
            if item.kind == "image":
                self._convert_image(item.src, tmp, ext)
                item.result = ext.upper()
            else:
                def on_progress(p):
                    item.progress = p
                    self.notify(item, force=False)

                def on_stage(text):
                    item.stage = text
                    self.notify(item)

                enc = media.Encoders.pick(self.settings.hw_accel)
                summary = media.convert(item.src, tmp, item.target, encoder=enc, opts=item.opts,
                                        on_progress=on_progress, on_stage=on_stage,
                                        cancel=item.cancel_evt)
                item.result = preset_short(item.kind, item.target)
                if summary and item.target.endswith("h264") and summary.get("reencoded") is False:
                    item.result += " · sin recodificar"
            os.replace(tmp, dst)
            tmp = None
            item.filepath = dst
            item.filesize = os.path.getsize(dst)
            item.status = "done"
            item.progress = 1.0
        except media.Cancelled:
            item.status = "cancelled"
        except Exception as e:
            log.exception("Conversión fallida: %s", item.src)
            item.status = "error"
            item.error, item.error_detail = humanize_error(e)
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            with self._lock:
                self._active.discard(item)
            self.notify(item)
            self._pump()

    @staticmethod
    def _convert_image(src: str, dst: str, ext: str):
        from PIL import Image
        pil_fmt = {"jpg": "JPEG", "tif": "TIFF", "tiff": "TIFF"}.get(ext, ext.upper())
        with Image.open(src) as img:
            img.load()
            if pil_fmt in ("JPEG", "BMP"):
                if img.mode in ("RGBA", "LA", "PA", "P"):
                    rgba = img.convert("RGBA")
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    bg.paste(rgba, mask=rgba.split()[-1])
                    img = bg
                else:
                    img = img.convert("RGB")
            kw = {}
            if pil_fmt == "JPEG":
                kw = {"quality": 95, "optimize": True, "subsampling": 0}
            elif pil_fmt == "PNG":
                kw = {"optimize": True}
            elif pil_fmt == "WEBP":
                kw = {"quality": 92, "method": 5}
            elif pil_fmt == "ICO":
                kw = {"sizes": [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)
                                if s <= max(img.size)] or [(16, 16)]}
            img.save(dst, pil_fmt, **kw)
