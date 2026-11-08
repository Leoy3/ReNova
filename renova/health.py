"""Verificación real de que las descargas de YouTube funcionan con el motor actual.

No alcanza con comparar versiones: cuando YouTube cambia algo, yt-dlp puede seguir
"leyendo" el video pero el servidor rechaza la descarga. Por eso se prueba el ciclo
completo: conexión → extracción (incluye los desafíos JavaScript) → bajar los primeros
64 KB del formato que se usaría para descargar."""

from __future__ import annotations

import logging
import time
import urllib.parse
import urllib.request

from . import core, engine

log = logging.getLogger("renova.health")

TEST_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo": primer video de YouTube


def _reachable(url: str) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8, context=engine._ssl_context()) as r:
            return r.status < 500
    except Exception:
        return False


def check() -> dict:
    """Devuelve {'state': 'ok'|'broken'|'offline', 'message', 'detail', 'version', 'took'}."""
    t0 = time.time()
    res = {"state": "ok", "message": "", "detail": "", "version": "?", "took": 0.0, "at": time.time()}

    def done(state, message, detail=""):
        res.update(state=state, message=message, detail=detail, took=round(time.time() - t0, 1))
        log.info("Verificación YouTube: %s · %s %s", state, message, detail[:300])
        return res

    try:
        yt = engine.get(60)
        res["version"] = engine.version()
    except Exception as e:
        return done("broken", "El motor de descargas no se pudo cargar", str(e))

    if not _reachable("https://www.youtube.com/generate_204"):
        if not _reachable("https://www.google.com/generate_204"):
            return done("offline", "Sin conexión a internet")
        return done("broken", "No se puede conectar con YouTube")

    opts = core.base_ydl_opts()
    opts.update(core.build_format_opts("video", "1080"))
    opts["extractor_retries"] = 1
    try:
        with yt.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(TEST_URL, download=False)
            parts = info.get("requested_formats") or [info]
            fmt = next((p for p in parts if p.get("url")), None)
            if not fmt:
                return done("broken", "YouTube no entregó formatos descargables")
            from yt_dlp.networking import Request
            headers = dict(fmt.get("http_headers") or {})
            headers["Range"] = "bytes=0-65535"
            proto = fmt.get("protocol") or ""
            url = fmt["url"]
            if "m3u8" in proto:
                # lista HLS: alcanza con leer la lista y el primer segmento
                with ydl.urlopen(Request(url, headers=fmt.get("http_headers") or {})) as r:
                    playlist = r.read(200_000).decode("utf-8", "replace")
                seg = next((ln for ln in playlist.splitlines() if ln and not ln.startswith("#")), None)
                if seg:
                    url = seg if seg.startswith("http") else urllib.parse.urljoin(fmt["url"], seg)
            with ydl.urlopen(Request(url, headers=headers)) as r:
                data = r.read(65536)
            if len(data) < 1024:
                return done("broken", "YouTube cortó la descarga de prueba")
    except Exception as e:
        msg, raw = core.humanize_error(e)
        low = raw.lower()
        if "403" in low or "forbidden" in low:
            msg = "YouTube rechaza las descargas (403)"
        elif any(k in low for k in ("nsig", "n challenge", "signature", "unable to extract",
                                    "requested format is not available", "only images are available",
                                    "player", "js challenge")):
            msg = "YouTube cambió algo y el motor actual no puede descargar"
        elif "not a bot" in low or "confirm you" in low:
            msg = "YouTube pide verificar que no sos un bot"
        return done("broken", msg, raw)
    return done("ok", "Las descargas de YouTube funcionan")

