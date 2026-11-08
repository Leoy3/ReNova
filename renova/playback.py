"""Reproducción de la copia de vista previa: video (ffmpeg → RGB), audio (ffmpeg → PCM →
waveOut de Windows) y captura de cuadros sueltos para el scrubbing. Sin dependencias extra."""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import threading
import time
from collections import deque
from ctypes import wintypes

from . import paths
from .media import NO_WINDOW

log = logging.getLogger("renova.playback")

RATE, CHANNELS = 44100, 2
BYTES_PER_SEC = RATE * CHANNELS * 2


def _popen(args: list) -> subprocess.Popen:
    return subprocess.Popen([paths.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", *args],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                            creationflags=NO_WINDOW, bufsize=0)


def _kill(proc):
    if proc and proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass


# ── waveOut (winmm) ────────────────────────────────────────────────────────────
class _WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                ("cbSize", wintypes.WORD)]


class _WAVEHDR(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t),
                ("dwFlags", wintypes.DWORD), ("dwLoops", wintypes.DWORD),
                ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]


class _MMTIME(ctypes.Structure):
    _fields_ = [("wType", wintypes.UINT), ("cb", wintypes.DWORD), ("_pad", wintypes.DWORD)]


_winmm = None


def _api():
    global _winmm
    if _winmm is None:
        w = ctypes.WinDLL("winmm")
        H = ctypes.c_void_p
        w.waveOutOpen.argtypes = [ctypes.POINTER(H), wintypes.UINT, ctypes.POINTER(_WAVEFORMATEX),
                                  ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
        for name in ("waveOutPrepareHeader", "waveOutUnprepareHeader", "waveOutWrite"):
            getattr(w, name).argtypes = [H, ctypes.POINTER(_WAVEHDR), wintypes.UINT]
        w.waveOutGetPosition.argtypes = [H, ctypes.POINTER(_MMTIME), wintypes.UINT]
        for name in ("waveOutPause", "waveOutRestart", "waveOutReset", "waveOutClose"):
            getattr(w, name).argtypes = [H]
        w.waveOutSetVolume.argtypes = [H, wintypes.DWORD]
        _winmm = w
    return _winmm


class AudioOut:
    """Salida por waveOut alimentada por `_chunks()` (PCM s16le estéreo 44.1 kHz).
    clock() = start + lo que ya se escuchó."""

    CHUNK = BYTES_PER_SEC // 10  # 100 ms por buffer

    def __init__(self, start: float, volume: float = 1.0):
        self.start = start
        self.ok = False
        self.eof = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._primed = threading.Event()
        self.h = ctypes.c_void_p()
        api = _api()
        fmt = _WAVEFORMATEX(1, CHANNELS, RATE, BYTES_PER_SEC, CHANNELS * 2, 16, 0)
        if api.waveOutOpen(ctypes.byref(self.h), 0xFFFFFFFF, ctypes.byref(fmt), 0, 0, 0) != 0:
            log.info("No hay salida de audio disponible")
            return
        api.waveOutPause(self.h)  # arranca en pausa hasta que el video esté listo
        self.ok = True
        self.set_volume(volume)

    def _begin(self):
        if self.ok:
            threading.Thread(target=self._feed, daemon=True, name="audio-feed").start()

    def _chunks(self):
        return iter(())

    def _feed(self):
        api = _api()
        pending: deque = deque()
        source = self._chunks()
        try:
            while not self._stop.is_set():
                while pending and pending[0][0].dwFlags & 0x1:  # WHDR_DONE
                    hdr, _buf = pending.popleft()
                    api.waveOutUnprepareHeader(self.h, ctypes.byref(hdr), ctypes.sizeof(hdr))
                if len(pending) >= 5:
                    self._primed.set()
                    time.sleep(0.01)
                    continue
                data = next(source, b"")
                if not data:
                    self.eof = True
                    self._primed.set()
                    while not self._stop.is_set() and pending and not all(p[0].dwFlags & 0x1 for p in pending):
                        time.sleep(0.02)
                    break
                buf = ctypes.create_string_buffer(data, len(data))
                hdr = _WAVEHDR(ctypes.cast(buf, ctypes.c_void_p), len(data), 0, 0, 0, 0, None, 0)
                with self._lock:
                    if self._stop.is_set():
                        break
                    api.waveOutPrepareHeader(self.h, ctypes.byref(hdr), ctypes.sizeof(hdr))
                    api.waveOutWrite(self.h, ctypes.byref(hdr), ctypes.sizeof(hdr))
                pending.append((hdr, buf))
        except Exception:
            log.exception("Error en el audio de la vista previa")
        finally:
            with self._lock:
                api.waveOutReset(self.h)
                for hdr, _buf in pending:
                    api.waveOutUnprepareHeader(self.h, ctypes.byref(hdr), ctypes.sizeof(hdr))
                api.waveOutClose(self.h)
                self.ok = False

    def set_volume(self, volume: float):
        """Volumen de escucha (0..1). Sólo afecta lo que se oye, no el archivo."""
        if os.environ.get("RENOVA_MUTE_PREVIEW"):  # pruebas automáticas
            volume = 0.0
        v = int(round(max(0.0, min(1.0, volume)) * 0xFFFF))
        with self._lock:
            if self.ok:
                _api().waveOutSetVolume(self.h, v | (v << 16))

    def wait_primed(self, timeout: float):
        if self.ok:
            self._primed.wait(timeout)

    def resume(self):
        with self._lock:
            if self.ok:
                _api().waveOutRestart(self.h)

    def clock(self) -> float | None:
        with self._lock:
            if not self.ok:
                return None
            t = _MMTIME(4, 0, 0)  # TIME_BYTES
            if _api().waveOutGetPosition(self.h, ctypes.byref(t), ctypes.sizeof(t)) != 0:
                return None
            return self.start + t.cb / BYTES_PER_SEC

    def stop(self):
        self._stop.set()
        self._cleanup()

    def _cleanup(self):
        pass


class AudioStream(AudioOut):
    """Audio de un archivo desde `start`. clock() = segundo del archivo que se está oyendo."""

    def __init__(self, path: str, start: float, volume: float = 1.0):
        super().__init__(start, volume)
        if not self.ok:
            return
        self.proc = _popen(["-ss", f"{start:.3f}", "-i", path, "-vn", "-f", "s16le", "-ac", str(CHANNELS),
                            "-ar", str(RATE), "-"])
        self._begin()

    def _chunks(self):
        while True:
            data = self.proc.stdout.read(self.CHUNK)
            if not data:
                return
            yield data

    def _cleanup(self):
        _kill(getattr(self, "proc", None))


def _pcm_proc(path: str, start: float, dur: float):
    return _popen(["-ss", f"{max(0.0, start):.4f}", "-t", f"{dur + 0.1:.4f}", "-i", path, "-vn",
                   "-f", "s16le", "-ac", str(CHANNELS), "-ar", str(RATE), "-"])


class SeqAudio(AudioOut):
    """Audio continuo de una secuencia: une varios archivos y silencios sin cortes.

    pieces: [(a, b, path | None, src_t)] en segundos de la secuencia; src_t = segundo del archivo
    que corresponde a `a`. clock() = segundo de la secuencia que se está oyendo."""

    def __init__(self, pieces: list, start: float, volume: float = 1.0):
        super().__init__(start, volume)
        self.pieces = [p for p in pieces if p[1] > start + 1e-6]
        self._procs: dict = {}
        if self.ok:
            self._begin()

    def _spawn(self, i):
        a, b, path, src_t = self.pieces[i]
        if path is None or i in self._procs:
            return
        s = max(a, self.start)
        self._procs[i] = _pcm_proc(path, src_t + (s - a), b - s)

    def _chunks(self):
        pos = 0  # bytes generados desde start
        zeros = bytes(self.CHUNK)
        for i, (a, b, path, _src_t) in enumerate(self.pieces):
            end = int(round((b - self.start) * RATE)) * CHANNELS * 2
            if end <= pos:
                continue
            if path is not None:
                self._spawn(i)
            if i + 1 < len(self.pieces):
                self._spawn(i + 1)  # el siguiente ya arranca: no hay pausa en el empalme
            proc = self._procs.get(i)
            while pos < end and not self._stop.is_set():
                n = min(self.CHUNK, end - pos)
                data = b""
                if proc is not None:
                    # el caño puede entregar menos de lo pedido: se sigue leyendo hasta completar
                    while len(data) < n:
                        part = proc.stdout.read(n - len(data))
                        if not part:
                            break
                        data += part
                    if len(data) < n:  # el archivo se terminó antes: completa con silencio
                        _kill(proc)
                        proc = None
                        data += zeros[:n - len(data)]
                else:
                    data = zeros[:n]
                pos += len(data)
                yield data
            _kill(self._procs.pop(i, None))

    def _cleanup(self):
        for p in list(self._procs.values()):
            _kill(p)


# ── Video ──────────────────────────────────────────────────────────────────────
class VideoStream:
    """Decodifica cuadros RGB de tamaño w×h desde `start` a `fps` constantes."""

    def __init__(self, path: str, start: float, w: int, h: int, fps: float):
        self.start, self.w, self.h, self.fps = start, w, h, fps
        self.frames: deque = deque()
        self.eof = False
        self._stop = threading.Event()
        self.proc = _popen(["-ss", f"{start:.3f}", "-i", path, "-an", "-sn",
                            "-vf", f"fps={fps:g},scale={w}:{h}:flags=bilinear",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
        threading.Thread(target=self._read, daemon=True, name="video-read").start()

    def _read(self):
        size = self.w * self.h * 3
        n = 0
        try:
            while not self._stop.is_set():
                if len(self.frames) >= 8:
                    time.sleep(0.005)
                    continue
                buf = bytearray()
                while len(buf) < size:
                    chunk = self.proc.stdout.read(size - len(buf))
                    if not chunk:
                        break
                    buf += chunk
                if len(buf) < size:
                    break
                self.frames.append((self.start + n / self.fps, bytes(buf)))
                n += 1
        except Exception:
            log.exception("Error leyendo cuadros")
        finally:
            self.eof = True

    def stop(self):
        self._stop.set()
        _kill(self.proc)


def fit_filter(w: int, h: int) -> str:
    """Escala a w×h sin deformar (bandas negras si el archivo tiene otra proporción)."""
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=bilinear,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")


class SeqVideo:
    """Cuadros RGB w×h de una secuencia (varios archivos y huecos en negro) desde `start`.
    pieces: igual que SeqAudio. frames: (segundo de la secuencia, bytes)."""

    BUFFER = 12

    def __init__(self, pieces: list, start: float, w: int, h: int, fps: float):
        self.start, self.w, self.h, self.fps = start, w, h, fps
        self.pieces = [p for p in pieces if p[1] > start + 1e-6]
        self.frames: deque = deque()
        self.eof = False
        self._stop = threading.Event()
        self._procs: dict = {}
        self._black = bytes(w * h * 3)
        threading.Thread(target=self._read, daemon=True, name="seq-video").start()

    def _range(self, a, b):
        k0 = max(0, int(-(-round((a - self.start) * self.fps, 6) // 1)))
        k1 = max(0, int(-(-round((b - self.start) * self.fps, 6) // 1)))
        return k0, k1

    def _spawn(self, i):
        a, b, path, src_t = self.pieces[i]
        if path is None or i in self._procs:
            return
        k0, k1 = self._range(a, b)
        if k1 <= k0:
            return
        t0 = self.start + k0 / self.fps
        self._procs[i] = _popen(["-ss", f"{max(0.0, src_t + (t0 - a)):.4f}", "-t", f"{(k1 - k0) / self.fps + 0.2:.4f}",
                                 "-i", path, "-an", "-sn", "-vf", f"fps={self.fps:g},{fit_filter(self.w, self.h)}",
                                 "-frames:v", str(k1 - k0), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])

    def _push(self, k, data):
        while len(self.frames) >= self.BUFFER and not self._stop.is_set():
            time.sleep(0.005)
        self.frames.append((self.start + k / self.fps, data))

    def _read(self):
        size = self.w * self.h * 3
        try:
            for i, (a, b, path, _src_t) in enumerate(self.pieces):
                if self._stop.is_set():
                    break
                k0, k1 = self._range(a, b)
                if path is not None:
                    self._spawn(i)
                if i + 1 < len(self.pieces):
                    self._spawn(i + 1)
                proc = self._procs.get(i)
                last = self._black
                for k in range(k0, k1):
                    if self._stop.is_set():
                        break
                    data = None
                    if proc is not None:
                        buf = bytearray()
                        while len(buf) < size:
                            chunk = proc.stdout.read(size - len(buf))
                            if not chunk:
                                break
                            buf += chunk
                        if len(buf) == size:
                            data = bytes(buf)
                        else:
                            _kill(proc)
                            proc = None
                    if data is None:
                        data = last if path is not None else self._black
                    last = data
                    self._push(k, data)
                _kill(self._procs.pop(i, None))
        except Exception:
            log.exception("Error leyendo cuadros de la secuencia")
        finally:
            self.eof = True

    def stop(self):
        self._stop.set()
        for p in list(self._procs.values()):
            _kill(p)


# ── Cuadros sueltos (scrubbing) ────────────────────────────────────────────────
class FrameGrabber:
    """Pide cuadros exactos; si llegan pedidos nuevos mientras trabaja, sólo atiende el último."""

    def __init__(self, path: str | None, on_frame):
        self.path = path
        self.on_frame = on_frame  # (t, w, h, bytes) — se llama desde el hilo de trabajo
        self._req = None
        self._evt = threading.Event()
        self._closed = False
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True, name="frame-grab").start()

    def request(self, t: float, w: int, h: int, path: str | None = None, fit: bool = False, tag=None):
        """path: otro archivo (secuencias). fit: respeta la proporción con bandas negras.
        tag: dato que vuelve en on_frame en lugar de t (para saber a qué pedido corresponde)."""
        with self._lock:
            self._req = (t, w, h, path or self.path, fit, t if tag is None else tag)
        self._evt.set()

    def close(self):
        self._closed = True
        self._evt.set()

    def _loop(self):
        while True:
            self._evt.wait()
            if self._closed:
                return
            with self._lock:
                req, self._req = self._req, None
                self._evt.clear()
            if not req:
                continue
            t, w, h, path, fit, tag = req
            vf = fit_filter(w, h) if fit else f"scale={w}:{h}:flags=bilinear"
            try:
                r = subprocess.run([paths.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error",
                                    "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1", "-an",
                                    "-vf", vf, "-f", "rawvideo",
                                    "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=15,
                                   creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
                if r.returncode == 0 and len(r.stdout) >= w * h * 3 and not self._closed:
                    self.on_frame(tag, w, h, r.stdout[: w * h * 3])
            except Exception:
                log.debug("No se pudo capturar el cuadro %.2f", t)


def source_thumbs(path: str, dur: float, height: int, aspect: float, on_batch, cancel: threading.Event):
    """Miniaturas de todo el archivo a intervalos fijos, en un solo proceso de ffmpeg.
    on_batch(interval, [PIL.Image, ...]) se llama a medida que llegan (desde este hilo)."""
    from PIL import Image
    interval = max(0.5, dur / 150.0)
    th = max(8, int(height) // 2 * 2)
    tw = max(8, int(round(th * (aspect or 16 / 9))) // 2 * 2)
    size = tw * th * 3
    proc = _popen([*(["-skip_frame", "nokey"] if interval >= 4 else []), "-i", path, "-an", "-sn", "-dn",
                   "-vf", f"fps=1/{interval:.4f},scale={tw}:{th}:flags=bilinear",
                   "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    imgs: list = []
    try:
        while not cancel.is_set():
            buf = bytearray()
            while len(buf) < size:
                chunk = proc.stdout.read(size - len(buf))
                if not chunk:
                    break
                buf += chunk
            if len(buf) < size:
                break
            imgs.append(Image.frombytes("RGB", (tw, th), bytes(buf)))
            if len(imgs) in (1, 4) or len(imgs) % 12 == 0:
                on_batch(interval, list(imgs))
    finally:
        _kill(proc)
    if imgs and not cancel.is_set():
        on_batch(interval, imgs)


def audio_peaks(path: str, dur: float, cancel: threading.Event):
    """Picos de la forma de onda: (picos por segundo, array de 0..1)."""
    import array
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import audioop
    sr = 4000 if dur <= 1800 else 2000
    rate = 100.0 if dur <= 900 else max(5.0, 90000.0 / max(1.0, dur))
    spb = max(1, int(sr / rate))
    rate = sr / spb
    proc = _popen(["-i", path, "-vn", "-sn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"])
    peaks = array.array("f")
    block = spb * 2
    rest = b""
    try:
        while not cancel.is_set():
            data = proc.stdout.read(block * 512)
            if not data:
                break
            data = rest + data
            usable = len(data) - len(data) % block
            for i in range(0, usable, block):
                peaks.append(audioop.max(data[i:i + block], 2) / 32768.0)
            rest = data[usable:]
        if len(rest) >= 2:
            peaks.append(audioop.max(rest[:len(rest) // 2 * 2], 2) / 32768.0)
    finally:
        _kill(proc)
    return rate, peaks


def thumbs_at(path: str, times: list, height: int, aspect: float, cancel: threading.Event | None = None):
    """Miniaturas (t, PIL.Image) de tamaño fijo en los segundos pedidos (para la tira con zoom)."""
    from concurrent.futures import ThreadPoolExecutor
    from PIL import Image
    th = max(8, int(height) // 2 * 2)
    tw = max(8, int(round(th * (aspect or 16 / 9))) // 2 * 2)

    def grab(t):
        if cancel is not None and cancel.is_set():
            return None
        r = subprocess.run([paths.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{max(0.0, t):.3f}",
                            "-i", path, "-frames:v", "1", "-an", "-vf", f"scale={tw}:{th}:flags=bilinear",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=20,
                           creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
        if r.returncode == 0 and len(r.stdout) >= tw * th * 3:
            return t, Image.frombytes("RGB", (tw, th), r.stdout[:tw * th * 3])
        return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        return [x for x in pool.map(grab, times) if x is not None]
