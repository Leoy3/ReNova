"""Preferencias persistentes (en %LOCALAPPDATA%\\ReNova\\settings.json)."""

from __future__ import annotations

import json
import logging
import os
import threading

from . import paths

log = logging.getLogger("renova.settings")

DEFAULTS = {
    "out_dir": "",
    "fmt": "video",            # video | audio | mute
    "quality_video": "1080",   # max | 2160 | 1440 | 1080 | 720 | 480 | 360
    "quality_audio": "mp3",    # mp3 | wav | m4a
    "auto_start": False,       # descargar apenas se agrega un link
    "max_parallel": 3,
    "hw_accel": True,          # NVENC / QuickSync / AMF cuando hay que recodificar
    "clipboard_detect": True,
    "auto_update_engine": True,  # motor yt-dlp y aviso de versiones nuevas de ReNova
    "skip_version": "",        # versión de ReNova que el usuario eligió omitir
    "last_version": "",        # última versión que se ejecutó (para avisar «se actualizó»)
    "convert_video": "mp4_h264",
    "convert_audio": "mp3",
    "convert_image": "png",
    "gif_fps": 15,
    "gif_width": 640,             # 0 = tamaño original
    "mp3_kbps": 320,
    "snap": True,                 # imán de la línea de tiempo y del recorte
    "loop": False,                # repetir sólo la selección al reproducir
    "preview_volume": 1.0,        # volumen de escucha en los editores (no cambia los archivos)
    "preview_muted": False,
    "geometry": "",
}


class Settings:
    def __init__(self):
        self._data = dict(DEFAULTS)
        self._lock = threading.Lock()
        self._load()
        if not self._data.get("out_dir") or not os.path.isdir(self._data["out_dir"]):
            self._data["out_dir"] = paths.downloads_dir()

    def _load(self):
        try:
            with open(paths.settings_file(), encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in DEFAULTS and type(v) is type(DEFAULTS[k]):
                        self._data[k] = v
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("No se pudieron leer las preferencias")

    def save(self):
        with self._lock:
            data = dict(self._data)
        path = paths.settings_file()
        tmp = path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            log.exception("No se pudieron guardar las preferencias")

    def get(self, key):
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key, value, save=True):
        with self._lock:
            self._data[key] = value
        if save:
            self.save()

    def __getattr__(self, key):
        if key.startswith("_"):
            raise AttributeError(key)
        if key in DEFAULTS:
            return self._data.get(key, DEFAULTS[key])
        raise AttributeError(key)
