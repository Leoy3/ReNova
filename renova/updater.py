"""Actualizaciones de ReNova (el programa; el motor yt-dlp se actualiza aparte en engine.py).

Cada versión publicada incluye un manifiesto `latest.json` (lo genera tools/build.py):
    {"version": "2.3.0", "url": "https://…/ReNova-Setup-2.3.0.exe", "sha256": "…", "size": 102760448,
     "notes": ["…", "…"], "date": "2026-11-07", "page": "https://…/releases/tag/v2.3.0"}

ReNova lo lee, y si hay una versión más nueva descarga el instalador, verifica el checksum y lo
ejecuta en modo silencioso: el instalador cierra ReNova, reemplaza los archivos y lo vuelve a abrir.
Las preferencias y el motor descargado viven en %LOCALAPPDATA%\\ReNova, así que no se pierden.

Variables para pruebas: RENOVA_UPDATE_URL (otro manifiesto), RENOVA_UPDATE_DRYRUN=1 (simula una
instalación con instalador y no ejecuta nada) y RENOVA_UPDATE_AUTO=1 (acepta la actualización sin
preguntar, para probar el circuito completo con la copia instalada).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from . import APP_VERSION, paths

log = logging.getLogger("renova.updater")

REPO = "Leoy3/ReNova"
MANIFEST_URL = f"https://github.com/{REPO}/releases/latest/download/latest.json"


class Cancelled(Exception):
    pass


def manifest_url() -> str:
    return os.environ.get("RENOVA_UPDATE_URL") or MANIFEST_URL


def release_urls(version: str) -> dict:
    """URLs públicas de una versión (las usa tools/build.py para armar el manifiesto)."""
    tag = f"v{version}"
    return {"url": f"https://github.com/{REPO}/releases/download/{tag}/ReNova-Setup-{version}.exe",
            "page": f"https://github.com/{REPO}/releases/tag/{tag}"}


def vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "0"))


def dry_run() -> bool:
    return os.environ.get("RENOVA_UPDATE_DRYRUN") == "1"


def can_self_update() -> bool:
    """Sólo una copia instalada con el instalador puede actualizarse sola (la portable no)."""
    if dry_run():
        return True
    return paths.FROZEN and (Path(sys.executable).parent / "unins000.exe").is_file()


def _allowed_url(url: str) -> bool:
    u = urllib.parse.urlparse(url or "")
    if u.scheme == "https" and u.netloc:
        return True
    # http sólo para pruebas locales
    return u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost")


def _open(url: str, timeout: float):
    from .engine import _ssl_context
    req = urllib.request.Request(url, headers={"User-Agent": f"ReNova/{APP_VERSION}",
                                               "Cache-Control": "no-cache"})
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())


def check(timeout: float = 20) -> dict | None:
    """Devuelve el manifiesto si hay una versión más nueva que la actual, si no None."""
    url = manifest_url()
    if not _allowed_url(url):
        raise RuntimeError("URL de actualización inválida")
    with _open(url, timeout) as r:
        info = json.loads(r.read(256 * 1024).decode("utf-8"))
    version = str(info.get("version", ""))
    if not re.fullmatch(r"\d+(\.\d+){1,3}", version):
        raise RuntimeError("Manifiesto de actualización inválido (versión)")
    if not _allowed_url(info.get("url", "")) or not re.fullmatch(r"[0-9a-fA-F]{64}", info.get("sha256", "")):
        raise RuntimeError("Manifiesto de actualización inválido (archivo)")
    info["notes"] = [str(n) for n in info.get("notes") or [] if str(n).strip()][:12]
    if vtuple(version) <= vtuple(APP_VERSION):
        log.info("ReNova está al día (%s; publicada %s)", APP_VERSION, version)
        return None
    log.info("Hay una versión nueva de ReNova: %s", version)
    return info


def _updates_dir() -> Path:
    d = paths.cache_dir() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(info: dict, on_progress=None, cancel=None) -> Path:
    """Descarga y verifica el instalador. Si ya estaba bajado y verificado, lo reutiliza."""
    dst = _updates_dir() / f"ReNova-Setup-{info['version']}.exe"
    expected = info["sha256"].lower()
    if dst.is_file() and _sha256(dst) == expected:
        return dst
    tmp = dst.with_suffix(".part")
    h = hashlib.sha256()
    done = 0
    try:
        with _open(info["url"], 60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or info.get("size") or 0)
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
        if h.hexdigest() != expected:
            raise RuntimeError("El instalador descargado está dañado (no coincide la verificación)")
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    for old in _updates_dir().glob("ReNova-Setup-*"):
        if old != dst:
            old.unlink(missing_ok=True)
    return dst


def cleanup():
    """Borra instaladores ya aplicados (versión igual o anterior a la actual) o a medio bajar."""
    try:
        d = paths.cache_dir() / "updates"
        if not d.is_dir():
            return
        for f in d.iterdir():
            m = re.match(r"ReNova-Setup-([\d.]+?)\.(exe|part)$", f.name)
            if not m or f.suffix == ".part" or vtuple(m.group(1)) <= vtuple(APP_VERSION):
                f.unlink(missing_ok=True)
    except Exception:
        log.debug("No se pudo limpiar la carpeta de actualizaciones", exc_info=True)


def launch_installer(path: Path):
    """Ejecuta el instalador sin preguntas; cierra ReNova, instala y lo vuelve a abrir (/relaunch=1)."""
    args = [str(path), "/SP-", "/SILENT", "/NOCANCEL", "/CLOSEAPPLICATIONS", "/NORESTART", "/relaunch=1"]
    log.info("Instalando actualización: %s", " ".join(args))
    if dry_run():
        return
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(args, close_fds=True, creationflags=flags, cwd=str(path.parent))
