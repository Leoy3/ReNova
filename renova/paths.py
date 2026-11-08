"""Rutas de la app: binarios empaquetados, datos de usuario, logs y carpetas de Windows."""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Carpeta con los recursos de la app (bin/, assets/)."""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    return Path(__file__).resolve().parent.parent


def bin_dir() -> Path:
    return resource_root() / "bin"


def asset(name: str) -> Path:
    return resource_root() / "assets" / name


def _first(*candidates) -> str | None:
    for c in candidates:
        if c and os.path.isfile(c):
            return str(c)
    return None


def ffmpeg() -> str:
    return (_first(bin_dir() / "ffmpeg.exe", r"C:\ffmpeg\bin\ffmpeg.exe")
            or shutil.which("ffmpeg") or "ffmpeg")


def ffprobe() -> str:
    return (_first(bin_dir() / "ffprobe.exe", r"C:\ffmpeg\bin\ffprobe.exe")
            or shutil.which("ffprobe") or "ffprobe")


def ffmpeg_dir() -> str:
    return os.path.dirname(ffmpeg()) if os.path.isabs(ffmpeg()) else ""


def node() -> str | None:
    return _first(bin_dir() / "node.exe") or shutil.which("node")


def deno() -> str | None:
    return _first(bin_dir() / "deno.exe") or shutil.which("deno")


# ── Datos de usuario ───────────────────────────────────────────────────────────
def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    d = Path(base) / "ReNova"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sub(name: str) -> Path:
    d = data_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir() -> Path:
    return _sub("cache")


def log_dir() -> Path:
    return _sub("logs")


def engine_dir() -> Path:
    return _sub("engine")


def settings_file() -> Path:
    return data_dir() / "settings.json"


# ── Carpetas conocidas de Windows ──────────────────────────────────────────────
def downloads_dir() -> str:
    """Carpeta Descargas real del usuario (respeta redirecciones, p.ej. D:\\Descargas)."""
    try:
        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

        folder_id = GUID(0x374DE290, 0x123F, 0x4565,
                         (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(folder_id), 0, None, ctypes.byref(out)) == 0:
            value = out.value
            ctypes.windll.ole32.CoTaskMemFree(out)
            if value and os.path.isdir(value):
                return value
    except Exception:
        pass
    return str(Path.home() / "Downloads")


# ── Logging ────────────────────────────────────────────────────────────────────
def setup_logging() -> Path:
    path = log_dir() / "renova.log"
    root = logging.getLogger()
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        handler = RotatingFileHandler(path, maxBytes=1_500_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    return path
