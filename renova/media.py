"""ffmpeg / ffprobe: compatibilidad con Premiere (H.264 + AAC en MP4), recortes,
conversiones, detección de aceleración por hardware y captura de cuadros."""

from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import threading
import time
from collections import deque
from fractions import Fraction

from . import paths

log = logging.getLogger("renova.media")

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class MediaError(Exception):
    pass


class Cancelled(Exception):
    pass


class Stalled(MediaError):
    pass


def _run(cmd, timeout=None):
    return subprocess.run(cmd, capture_output=True, timeout=timeout,
                          creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)


# ── Probe ──────────────────────────────────────────────────────────────────────
def probe(path: str) -> dict:
    r = _run([paths.ffprobe(), "-v", "error", "-print_format", "json",
              "-show_streams", "-show_format", path], timeout=90)
    if r.returncode != 0:
        msg = r.stderr.decode("utf-8", "replace").strip()[-300:]
        raise MediaError(f"No se pudo leer el archivo ({msg or r.returncode})")
    try:
        return json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError as e:
        raise MediaError("Respuesta inválida de ffprobe") from e


def streams(pr: dict, kind: str) -> list:
    return [s for s in pr.get("streams", [])
            if s.get("codec_type") == kind
            and not (s.get("disposition") or {}).get("attached_pic")]


def duration_of(pr: dict) -> float | None:
    try:
        d = float((pr.get("format") or {}).get("duration") or 0)
        if d > 0:
            return d
    except (TypeError, ValueError):
        pass
    for s in pr.get("streams", []):
        try:
            d = float(s.get("duration") or 0)
            if d > 0:
                return d
        except (TypeError, ValueError):
            continue
    return None


def _frac(s) -> float | None:
    try:
        f = Fraction(str(s))
        return float(f) if f > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


_STD_FPS = (23.976, 24.0, 25.0, 29.97, 30.0, 47.952, 48.0, 50.0, 59.94, 60.0, 100.0, 119.88, 120.0)


def snap_fps(value: float | None) -> float | None:
    if not value or value <= 0:
        return None
    best = min(_STD_FPS, key=lambda std: abs(value - std) / std)
    if abs(value - best) / best < 0.0008:
        return best
    return round(min(value, 120.0), 3)


def stream_fps(v: dict) -> float | None:
    avg = _frac(v.get("avg_frame_rate"))
    r = _frac(v.get("r_frame_rate"))
    return snap_fps(avg if avg and avg < 241 else r if r and r < 241 else None)


def is_vfr(v: dict) -> bool:
    r = _frac(v.get("r_frame_rate"))
    avg = _frac(v.get("avg_frame_rate"))
    if not r or not avg:
        return False
    if r > 240:  # timebase en lugar de fps real → variable
        return True
    return abs(r - avg) / max(r, avg) > 0.02


def fps_arg(fps: float) -> str:
    table = {23.976: "24000/1001", 29.97: "30000/1001", 47.952: "48000/1001",
             59.94: "60000/1001", 119.88: "120000/1001"}
    return table.get(fps, f"{fps:g}")


def premiere_video_ok(v: dict) -> bool:
    profile = (v.get("profile") or "").lower()
    return (v.get("codec_name") == "h264"
            and v.get("pix_fmt") in ("yuv420p", "yuvj420p")
            and "10" not in profile and "4:4:4" not in profile and "4:2:2" not in profile)


# ── Encoders ───────────────────────────────────────────────────────────────────
HW_LABELS = {"h264_nvenc": "NVIDIA NVENC", "h264_qsv": "Intel Quick Sync",
             "h264_amf": "AMD AMF", "libx264": "CPU (x264)"}


def video_args(encoder: str, fps: float | None = None) -> list:
    gop = ["-g", str(max(1, int(round(fps * 2))))] if fps else []  # GOP corto = scrubbing fluido
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr",
                "-cq", "19", "-b:v", "0", "-profile:v", "high", "-pix_fmt", "yuv420p", *gop]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "21",
                "-profile:v", "high", "-pix_fmt", "nv12", *gop]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "18",
                "-qp_p", "20", "-qp_b", "22", "-profile:v", "high", "-pix_fmt", "nv12", *gop]
    return ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-profile:v", "high",
            "-pix_fmt", "yuv420p", *gop]


AAC_ARGS = ["-c:a", "aac", "-b:a", "192k"]
EVEN_DIMS = "scale=trunc(iw/2)*2:trunc(ih/2)*2"


class Encoders:
    _lock = threading.Lock()
    _hw: list | None = None

    @classmethod
    def detect(cls) -> list:
        with cls._lock:
            if cls._hw is not None:
                return cls._hw
            found = []
            for enc in ("h264_nvenc", "h264_qsv", "h264_amf"):
                try:
                    r = _run([paths.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error",
                              "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=0.4",
                              *video_args(enc, 30), "-f", "null", "-"], timeout=25)
                    if r.returncode == 0:
                        found.append(enc)
                except Exception:
                    pass
            log.info("Encoders por hardware disponibles: %s", found or "ninguno")
            cls._hw = found
            return found

    @classmethod
    def pick(cls, allow_hw: bool) -> str:
        if allow_hw:
            hw = cls.detect()
            if hw:
                return hw[0]
        return "libx264"


# ── Ejecución de ffmpeg con progreso, cancelación y watchdog ───────────────────
def run_ffmpeg(args: list, *, duration: float | None = None, on_progress=None,
               cancel: threading.Event | None = None, stall_timeout: float | None = None):
    cmd = [paths.ffmpeg(), "-hide_banner", "-nostdin", "-y", "-v", "error",
           "-progress", "pipe:1", "-nostats", *args]
    log.debug("ffmpeg %s", " ".join(cmd[1:]))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    err_tail: deque = deque(maxlen=30)
    state = {"last": time.monotonic(), "killed": None, "t": -1.0}

    def read_err():
        for line in proc.stderr:
            err_tail.append(line.rstrip())

    def watchdog():
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                state["killed"] = "cancel"
                proc.kill()
                return
            if stall_timeout and time.monotonic() - state["last"] > stall_timeout:
                state["killed"] = "stall"
                proc.kill()
                return
            time.sleep(0.2)

    threading.Thread(target=read_err, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()

    for line in proc.stdout:
        key, _, val = line.strip().partition("=")
        if key in ("out_time_us", "out_time_ms"):
            try:
                t = int(val) / 1_000_000
            except ValueError:
                continue
            if t > state["t"]:
                state["t"] = t
                state["last"] = time.monotonic()
                if on_progress and duration:
                    on_progress(max(0.0, min(1.0, t / duration)))
        elif key == "total_size":
            pass
    proc.wait()
    if state["killed"] == "cancel":
        raise Cancelled()
    if state["killed"] == "stall":
        raise Stalled("La transferencia se detuvo")
    if proc.returncode != 0:
        detail = "\n".join(err_tail).strip()
        raise MediaError(detail[-600:] or f"ffmpeg terminó con código {proc.returncode}")


def _with_fallback(build, encoder: str, **kw):
    """Ejecuta con el encoder elegido; si el de hardware falla, reintenta con x264."""
    try:
        run_ffmpeg(build(encoder), **kw)
        return encoder
    except MediaError as e:
        if encoder == "libx264" or isinstance(e, Stalled):
            raise
        log.warning("Encoder %s falló (%s); reintento con libx264", encoder, str(e)[:200])
        run_ffmpeg(build("libx264"), **kw)
        return "libx264"


# ── Compatibilidad Premiere ────────────────────────────────────────────────────
def make_premiere_ready(src: str, dst: str, *, mute: bool = False, encoder: str = "libx264",
                        on_progress=None, on_stage=None, cancel=None) -> dict:
    """Garantiza H.264 8-bit 4:2:0 + AAC, frame rate constante, sin streams extra.

    Si el archivo ya cumple, sólo se reempaqueta (rápido, sin pérdida)."""
    pr = probe(src)
    vs = streams(pr, "video")
    aus = streams(pr, "audio")
    if not vs:
        raise MediaError("El archivo descargado no contiene video")
    v = vs[0]
    a = None if mute else (aus[0] if aus else None)
    dur = duration_of(pr)
    fps = stream_fps(v)
    vfr = is_vfr(v)
    need_video = (not premiere_video_ok(v)) or vfr
    need_audio = a is not None and a.get("codec_name") != "aac"
    dst_ext = os.path.splitext(dst)[1].lower()
    extra_streams = len(pr.get("streams", [])) > (1 + (1 if a else 0))
    same_container = os.path.splitext(src)[1].lower() == dst_ext

    summary = {
        "height": v.get("height"), "width": v.get("width"), "fps": fps,
        "reencoded": need_video, "src_codec": v.get("codec_name"),
    }
    if not need_video and not need_audio and same_container and not extra_streams \
            and not (mute and aus):
        os.replace(src, dst)
        return summary

    if on_stage:
        if need_video:
            reason = "frame rate variable" if (vfr and premiere_video_ok(v)) else \
                (v.get("codec_name") or "?").upper()
            on_stage(f"Convirtiendo {reason} → H.264")
        elif need_audio:
            on_stage("Convirtiendo audio a AAC")
        else:
            on_stage("Preparando archivo")

    def build(enc):
        args = ["-i", src, "-map", "0:V:0"]
        if a is not None:
            args += ["-map", "0:a:0"]
        if need_video:
            args += video_args(enc, fps) + ["-vf", EVEN_DIMS]
            if fps:
                args += ["-fps_mode", "cfr", "-r", fps_arg(fps)]
        else:
            args += ["-c:v", "copy"]
        if a is None:
            args += ["-an"]
        elif need_audio:
            args += AAC_ARGS
        else:
            args += ["-c:a", "copy"]
        args += ["-map_metadata", "-1", "-sn", "-dn"]
        if dst_ext in (".mp4", ".mov", ".m4v"):
            args += ["-movflags", "+faststart"]
        return args + [dst]

    if need_video:
        used = _with_fallback(build, encoder, duration=dur, on_progress=on_progress, cancel=cancel)
        summary["encoder"] = used
    else:
        run_ffmpeg(build(encoder), duration=dur, on_progress=on_progress, cancel=cancel)
    return summary


def verify_premiere(path: str, expect_audio: bool) -> dict:
    pr = probe(path)
    vs = streams(pr, "video")
    if not vs or not premiere_video_ok(vs[0]):
        raise MediaError("La verificación final falló: el video no quedó en H.264")
    aus = streams(pr, "audio")
    if expect_audio and aus and aus[0].get("codec_name") != "aac":
        raise MediaError("La verificación final falló: el audio no quedó en AAC")
    v = vs[0]
    return {"height": v.get("height"), "width": v.get("width"), "fps": stream_fps(v),
            "duration": duration_of(pr)}


# ── Recorte ────────────────────────────────────────────────────────────────────
AUDIO_TARGETS = {
    "mp3": ["-c:a", "libmp3lame", "-b:a", "320k"],
    "wav": ["-c:a", "pcm_s16le"],
    "m4a": ["-c:a", "aac", "-b:a", "256k"],
    "flac": ["-c:a", "flac"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "6"],
}


def trim(inputs: list, start: float, end: float, dst: str, *, mode: str,
         audio_target: str = "mp3", encoder: str = "libx264", fps: float | None = None,
         on_progress=None, cancel=None, stall_timeout: float | None = None) -> str:
    """Recorte preciso (re-codifica). inputs: [{'src', 'headers', 'video', 'audio'}].

    mode: 'video' (H.264+AAC), 'mute' (H.264 sin audio) o 'audio'."""
    dur = max(0.05, end - start)
    vin = next((i for i, x in enumerate(inputs) if x.get("video")), None)
    ain = next((i for i, x in enumerate(inputs) if x.get("audio")), None)

    def build(enc):
        args = []
        for x in inputs:
            src = x["src"]
            if x.get("headers") and src.startswith("http"):
                args += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in x["headers"].items())]
            if src.startswith("http"):
                args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
            args += ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src]
        if mode == "audio":
            if ain is None:
                raise MediaError("No hay pista de audio para recortar")
            args += ["-map", f"{ain}:a:0", "-vn", *AUDIO_TARGETS.get(audio_target, AUDIO_TARGETS["mp3"])]
        else:
            if vin is None:
                raise MediaError("No hay pista de video para recortar")
            args += ["-map", f"{vin}:V:0"]
            if mode == "video" and ain is not None:
                args += ["-map", f"{ain}:a:0?" if ain == vin else f"{ain}:a:0", *AAC_ARGS]
            else:
                args += ["-an"]
            args += video_args(enc, fps) + ["-vf", EVEN_DIMS]
            if fps:
                args += ["-fps_mode", "cfr", "-r", fps_arg(fps)]
            args += ["-sn", "-dn"]
        args += ["-map_metadata", "-1"]
        if dst.lower().endswith((".mp4", ".m4a", ".mov")):
            args += ["-movflags", "+faststart"]
        return args + [dst]

    if mode == "audio":
        run_ffmpeg(build(encoder), duration=dur, on_progress=on_progress, cancel=cancel,
                   stall_timeout=stall_timeout)
        return encoder
    return _with_fallback(build, encoder, duration=dur, on_progress=on_progress,
                          cancel=cancel, stall_timeout=stall_timeout)


# ── Conversión (pestaña Convertir) ─────────────────────────────────────────────
def gif_filter(fps: int = 15, width: int = 640) -> str:
    """Filtro de GIF con paleta propia. La entrada va etiquetada ([0:V:0]) y la salida es [gif]:
    si el video se mapea aparte, ffmpeg no encuentra un stream libre para el filtro."""
    scale = f",scale='min(iw,{int(width)})':-2:flags=lanczos" if width else ""
    return (f"[0:V:0]fps={int(fps)}{scale},split[a][b];"
            "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4[gif]")


def convert(src: str, dst: str, preset: str, *, encoder: str = "libx264", opts: dict | None = None,
            on_progress=None, on_stage=None, cancel=None) -> dict:
    opts = opts or {}
    pr = probe(src)
    dur = duration_of(pr)
    vs = streams(pr, "video")
    aus = streams(pr, "audio")
    kw = dict(duration=dur, on_progress=on_progress, cancel=cancel)

    if preset in ("mp4_h264", "mov_h264", "mkv_h264"):
        if not vs:
            raise MediaError("El archivo no tiene video")
        return make_premiere_ready(src, dst, encoder=encoder, on_progress=on_progress,
                                   on_stage=on_stage, cancel=cancel)
    if preset in AUDIO_TARGETS:
        if not aus:
            raise MediaError("El archivo no tiene audio")
        codec = list(AUDIO_TARGETS[preset])
        if preset == "mp3" and opts.get("kbps"):
            codec = ["-c:a", "libmp3lame", "-b:a", f"{int(opts['kbps'])}k"]
        run_ffmpeg(["-i", src, "-map", "0:a:0", "-vn", *codec, "-map_metadata", "0", dst], **kw)
        return {}
    if preset == "webm_vp9":
        if not vs:
            raise MediaError("El archivo no tiene video")
        args = ["-i", src, "-map", "0:V:0", "-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0",
                "-row-mt", "1", "-deadline", "good", "-cpu-used", "4"]
        args += (["-map", "0:a:0", "-c:a", "libopus", "-b:a", "160k"] if aus else ["-an"])
        run_ffmpeg(args + [dst], **kw)
        return {}
    if preset == "gif":
        if not vs:
            raise MediaError("El archivo no tiene video")
        fc = gif_filter(opts.get("fps") or 15, opts.get("width", 640))
        run_ffmpeg(["-i", src, "-filter_complex", fc, "-map", "[gif]", "-loop", "0", dst], **kw)
        return {}
    raise MediaError(f"Formato de salida desconocido: {preset}")


# ── Editor: exportar tramos (cortar y unir) ────────────────────────────────────
def display_aspect(v: dict) -> float:
    """Relación de aspecto como se ve (considera píxeles no cuadrados y rotación de celulares)."""
    w, h = v.get("width") or 16, v.get("height") or 9
    sar = v.get("sample_aspect_ratio") or ""
    if ":" in sar and sar not in ("0:1", "1:1"):
        try:
            num, den = (int(x) for x in sar.split(":"))
            if num and den:
                w = w * num / den
        except ValueError:
            pass
    rot = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rot = int(float(sd["rotation"]))
            except (TypeError, ValueError):
                pass
    if not rot:
        try:
            rot = int((v.get("tags") or {}).get("rotate", 0))
        except (TypeError, ValueError):
            rot = 0
    if abs(rot) % 180 == 90:
        w, h = h, w
    return w / h if h else 16 / 9


def export_segments(src: str, intervals: list, dst: str, *, target: str, encoder: str = "libx264",
                    on_progress=None, cancel=None) -> str:
    """Exporta los tramos [(a, b), ...] de src unidos en orden, con cortes precisos.

    target: 'mp4' / 'mov' (H.264 + AAC) o 'mp3' / 'wav' / 'm4a' / 'flac' (sólo audio)."""
    if not intervals:
        raise MediaError("No quedó ningún tramo para exportar")
    pr = probe(src)
    vs, aus = streams(pr, "video"), streams(pr, "audio")
    video = target in ("mp4", "mov")
    if video and not vs:
        raise MediaError("El archivo no tiene video")
    if not video and not aus:
        raise MediaError("El archivo no tiene audio")
    has_a = bool(aus)
    fps = stream_fps(vs[0]) if video else None
    total = sum(b - a for a, b in intervals)
    n = len(intervals)

    def build(enc):
        args = []
        for a, b in intervals:
            args += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", src]
        if video:
            vf = EVEN_DIMS + (f",fps={fps_arg(fps)}" if fps else "")
            if n > 1:
                ins = "".join(f"[{i}:V:0]" + (f"[{i}:a:0]" if has_a else "") for i in range(n))
                fc = f"{ins}concat=n={n}:v=1:a={1 if has_a else 0}[vc]{'[ac]' if has_a else ''};[vc]{vf}[v]"
            else:
                fc = f"[0:V:0]{vf}[v]"
            args += ["-filter_complex", fc, "-map", "[v]"]
            if has_a:
                args += ["-map", "[ac]" if n > 1 else "0:a:0", *AAC_ARGS]
            else:
                args += ["-an"]
            args += video_args(enc, fps)
        else:
            if n > 1:
                ins = "".join(f"[{i}:a:0]" for i in range(n))
                args += ["-filter_complex", f"{ins}concat=n={n}:v=0:a=1[a]", "-map", "[a]"]
            else:
                args += ["-map", "0:a:0"]
            args += ["-vn", *AUDIO_TARGETS[target]]
        args += ["-map_metadata", "-1", "-sn", "-dn"]
        if dst.lower().endswith((".mp4", ".mov", ".m4a")):
            args += ["-movflags", "+faststart"]
        return args + [dst]

    kw = dict(duration=total, on_progress=on_progress, cancel=cancel)
    if video:
        return _with_fallback(build, encoder, **kw)
    run_ffmpeg(build(encoder), **kw)
    return encoder


def waveform(src: str, t0: float | None, t1: float | None, width: int, height: int,
             color: str = "#B0A9A7", bg: str = "#1C1818"):
    """Imagen de la forma de onda (PIL) del tramo t0..t1 (o de todo el archivo)."""
    from PIL import Image
    cmd = [paths.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error"]
    if t0 is not None and t1 is not None:
        cmd += ["-ss", f"{t0:.3f}", "-t", f"{max(0.05, t1 - t0):.3f}"]
    cmd += ["-i", src, "-filter_complex",
            f"[0:a:0]aformat=channel_layouts=mono,showwavespic=s={int(width)}x{int(height)}:colors={color}",
            "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"]
    try:
        r = _run(cmd, timeout=60)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    try:
        img = Image.open(io.BytesIO(r.stdout)).convert("RGBA")
    except Exception:
        return None
    base = Image.new("RGB", img.size, bg)
    base.paste(img, (0, 0), img)
    return base


# ── Cuadros para vista previa ──────────────────────────────────────────────────
def grab_frame(src: str, t: float, width: int = 480, headers: dict | None = None,
               timeout: float = 20):
    """Devuelve un PIL.Image con el cuadro en el segundo t (o None)."""
    from PIL import Image
    cmd = [paths.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error"]
    if headers and src.startswith("http"):
        cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    cmd += ["-ss", f"{max(0.0, t):.3f}", "-i", src, "-frames:v", "1",
            "-vf", f"scale={int(width)}:-2", "-f", "image2pipe", "-c:v", "png", "-"]
    try:
        r = _run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    try:
        img = Image.open(io.BytesIO(r.stdout))
        img.load()
        return img.convert("RGB")
    except Exception:
        return None
