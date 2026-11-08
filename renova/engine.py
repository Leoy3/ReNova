"""Motor de descargas (yt-dlp): carga con fallback y auto-actualización.

YouTube cambia seguido y una versión vieja de yt-dlp deja de funcionar a las
pocas semanas. Para que el programa siga andando en la PC de otra persona sin
reinstalar, se descarga la última versión de yt-dlp (+ yt-dlp-ejs) desde PyPI a
%LOCALAPPDATA%\\ReNova\\engine y se usa en el próximo arranque. Si esa versión
falla al importar, se vuelve automáticamente a la empaquetada.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import ssl
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path

from . import APP_VERSION, paths

log = logging.getLogger("renova.engine")

_ready = threading.Event()
_module = None
_load_error: Exception | None = None


def _state_path() -> Path:
    return paths.engine_dir() / "state.json"


def _load_state() -> dict:
    try:
        with open(_state_path(), encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict):
    p = _state_path()
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, p)


def _purge_modules():
    for name in list(sys.modules):
        if name in ("yt_dlp", "yt_dlp_ejs") or name.startswith(("yt_dlp.", "yt_dlp_ejs.")):
            sys.modules.pop(name, None)


def _bundled_version() -> str | None:
    """Versión de yt-dlp que viene con el programa (sin importarlo)."""
    try:
        from importlib.metadata import version as dist_version
        return dist_version("yt-dlp")
    except Exception:
        return None


def load():
    """Importa yt-dlp (versión actualizada si existe, si no la empaquetada)."""
    global _module, _load_error
    state = _load_state()
    override = state.get("active_path")
    bundled = _bundled_version()
    if override and bundled and _vt(state.get("active")) <= _vt(bundled):
        # Se instaló una versión nueva de ReNova que trae un motor igual o más nuevo
        log.info("Motor empaquetado %s ≥ descargado %s; descarto el descargado", bundled, state.get("active"))
        shutil.rmtree(override, ignore_errors=True)
        state.pop("active", None)
        state.pop("active_path", None)
        try:
            _save_state(state)
        except Exception:
            pass
        override = None
    if override and os.path.isdir(override) and state.get("active") not in state.get("bad", []):
        sys.path.insert(0, override)
        try:
            import yt_dlp  # noqa: F401
            import yt_dlp_ejs  # noqa: F401
            from yt_dlp.version import __version__ as v
            log.info("Motor yt-dlp %s cargado desde %s", v, os.path.dirname(yt_dlp.__file__))
            _module = yt_dlp
            _ready.set()
            return _module
        except Exception:
            log.exception("La versión actualizada del motor falló; uso la empaquetada")
            try:
                sys.path.remove(override)
            except ValueError:
                pass
            _purge_modules()
            state.setdefault("bad", []).append(state.get("active"))
            state.pop("active", None)
            state.pop("active_path", None)
            try:
                _save_state(state)
            except Exception:
                pass
    try:
        import yt_dlp
        from yt_dlp.version import __version__ as v
        log.info("Motor yt-dlp %s (empaquetado)", v)
        _module = yt_dlp
    except Exception as e:  # pragma: no cover - sólo si el build está roto
        _load_error = e
        log.exception("No se pudo cargar yt-dlp")
    _ready.set()
    return _module


def load_async():
    threading.Thread(target=load, name="engine-load", daemon=True).start()


def get(timeout: float | None = None):
    _ready.wait(timeout)
    if _module is None:
        raise RuntimeError(f"El motor de descargas no está disponible: {_load_error}")
    return _module


def is_ready() -> bool:
    return _ready.is_set()


def version() -> str:
    try:
        get(30)
        from yt_dlp.version import __version__
        return __version__
    except Exception:
        return "?"


def pending_version() -> str | None:
    """Versión descargada que se activará al reiniciar (si difiere de la actual)."""
    st = _load_state()
    v = st.get("active")
    if v and v != version():
        return v
    return None


# ── Actualización ──────────────────────────────────────────────────────────────
def _vt(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "0"))


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _http_get(url: str, timeout: float = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"ReNova/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        return r.read()


def _pick_wheel(urls: list) -> dict:
    for u in urls:
        if u.get("packagetype") == "bdist_wheel" and u.get("filename", "").endswith("none-any.whl"):
            return u
    raise RuntimeError("No hay wheel universal disponible")


def _download_wheel(info: dict) -> zipfile.ZipFile:
    data = _http_get(info["url"], timeout=180)
    expected = (info.get("digests") or {}).get("sha256")
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError("Checksum inválido en la descarga del motor")
    return zipfile.ZipFile(io.BytesIO(data))


def _safe_extract(zf: zipfile.ZipFile, dest: Path):
    dest_resolved = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if not str(target).startswith(str(dest_resolved)):
            raise RuntimeError("Ruta inválida dentro del paquete")
    zf.extractall(dest)


def check_update() -> str | None:
    """Descarga la última versión de yt-dlp si es más nueva. Devuelve la versión o None."""
    current = version()
    meta = json.loads(_http_get("https://pypi.org/pypi/yt-dlp/json", timeout=30))
    latest = meta["info"]["version"]
    state = _load_state()
    if _vt(latest) <= _vt(current) or latest in state.get("bad", []):
        return None
    if state.get("active") == latest and os.path.isdir(state.get("active_path", "")):
        return latest  # ya descargada, falta reiniciar

    log.info("Actualizando motor yt-dlp %s → %s", current, latest)
    root = paths.engine_dir()
    dest = root / f"yt-dlp-{latest}"
    tmp = root / f"yt-dlp-{latest}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    zf = _download_wheel(_pick_wheel(meta["urls"]))
    _safe_extract(zf, tmp)

    # yt-dlp fija la versión de yt-dlp-ejs (scripts que resuelven los desafíos JS de YouTube)
    ejs_version = None
    for name in zf.namelist():
        if name.endswith(".dist-info/METADATA"):
            text = zf.read(name).decode("utf-8", "replace")
            m = re.search(r"^Requires-Dist:\s*yt-dlp-ejs\s*==\s*([\w.]+)", text, re.M)
            if m:
                ejs_version = m.group(1)
            break
    if ejs_version:
        ejs_meta = json.loads(_http_get(f"https://pypi.org/pypi/yt-dlp-ejs/{ejs_version}/json"))
        _safe_extract(_download_wheel(_pick_wheel(ejs_meta["urls"])), tmp)

    if not (tmp / "yt_dlp" / "__init__.py").is_file():
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("Paquete del motor incompleto")

    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    state["active"] = latest
    state["active_path"] = str(dest)
    _save_state(state)

    # Limpia versiones viejas (menos la que está en uso ahora)
    try:
        import yt_dlp
        in_use = Path(yt_dlp.__file__).resolve().parent.parent
    except Exception:
        in_use = None
    for d in root.glob("yt-dlp-*"):
        if d.resolve() not in (dest.resolve(), in_use):
            shutil.rmtree(d, ignore_errors=True)
    return latest


def reset_to_bundled():
    state = _load_state()
    state.pop("active", None)
    state.pop("active_path", None)
    _save_state(state)
