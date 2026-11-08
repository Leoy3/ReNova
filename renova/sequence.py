"""Secuencia de la pestaña Recortar: clips de uno o varios archivos sobre una línea de tiempo
(una pista de video con su audio vinculado), con las ediciones de Premiere: cuchilla, mover
(sobrescribir / insertar / reordenar / duplicar), recortar bordes (normal o en cadena),
borrar (dejando hueco o cerrándolo), entrada/salida, levantar / extraer, copiar y pegar.

El modelo no sabe nada de la interfaz: ui/timeline.py y ui/editor.py lo manipulan y
`export()` arma el comando de ffmpeg para el archivo final."""

from __future__ import annotations

import itertools
import os
import tempfile
from dataclasses import dataclass, field, replace

from . import media

_ids = itertools.count(1)
EPS = 1e-6


@dataclass(eq=False)
class Source:
    path: str
    name: str
    duration: float
    has_video: bool
    has_audio: bool
    fps: float | None = None
    width: int = 0
    height: int = 0
    aspect: float = 16 / 9
    info: str = ""
    id: int = field(default_factory=lambda: next(_ids))

    @classmethod
    def from_probe(cls, path: str, pr: dict) -> "Source":
        vs, aus = media.streams(pr, "video"), media.streams(pr, "audio")
        dur = media.duration_of(pr) or 0.0
        v = vs[0] if vs else {}
        aspect = media.display_aspect(v) if vs else 16 / 9
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        if vs and w and h and (aspect < 1) != (w < h):  # video rotado (celulares)
            w, h = h, w
        name = os.path.basename(path)
        bits = [os.path.splitext(path)[1][1:].upper()]
        if vs:
            bits += [f"{w}×{h}", (v.get("codec_name") or "").upper()]
            fps = media.stream_fps(v)
            if fps:
                bits.append(f"{fps:g} fps")
        elif aus:
            bits.append((aus[0].get("codec_name") or "").upper())
        return cls(path=path, name=name, duration=float(dur), has_video=bool(vs), has_audio=bool(aus),
                   fps=media.stream_fps(v) if vs else None, width=w, height=h, aspect=aspect,
                   info=" · ".join(b for b in bits if b))


@dataclass(eq=False)
class Clip:
    src: Source
    t_in: float      # segundo del archivo donde empieza
    t_out: float     # segundo del archivo donde termina
    start: float     # posición en la línea de tiempo
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def dur(self) -> float:
        return self.t_out - self.t_in

    @property
    def end(self) -> float:
        return self.start + self.dur

    def clone(self, **kw) -> "Clip":
        """Copia con identidad nueva (otro clip en la línea de tiempo)."""
        return Clip(self.src, kw.get("t_in", self.t_in), kw.get("t_out", self.t_out), kw.get("start", self.start))


class Sequence:
    def __init__(self):
        self.clips: list[Clip] = []
        self.mark_in: float | None = None
        self.mark_out: float | None = None

    # ── Formato (lo define el primer clip con video, como en Premiere) ─────
    def first_video(self) -> Source | None:
        for c in sorted(self.clips, key=lambda c: c.start):
            if c.src.has_video:
                return c.src
        return None

    @property
    def fps(self) -> float:
        v = self.first_video()
        return (v.fps if v and v.fps else 30.0)

    @property
    def has_video(self) -> bool:
        return any(c.src.has_video for c in self.clips)

    @property
    def has_audio(self) -> bool:
        return any(c.src.has_audio for c in self.clips)

    def frame_size(self) -> tuple[int, int]:
        v = self.first_video()
        if not v or not v.width or not v.height:
            return 1920, 1080
        return max(2, v.width // 2 * 2), max(2, v.height // 2 * 2)

    @property
    def aspect(self) -> float:
        v = self.first_video()
        return v.aspect if v else 16 / 9

    # ── Utilidades ─────────────────────────────────────────────────────────
    @property
    def frame(self) -> float:
        return 1.0 / self.fps

    def q(self, t: float) -> float:
        """Redondea al cuadro más cercano de la secuencia."""
        f = self.fps
        return round(round(t * f) / f, 6)

    @property
    def duration(self) -> float:
        return max((c.end for c in self.clips), default=0.0)

    def get(self, cid) -> Clip | None:
        return next((c for c in self.clips if c.id == cid), None)

    def _sort(self):
        self.clips.sort(key=lambda c: c.start)

    def clip_at(self, t: float) -> Clip | None:
        for c in self.clips:
            if c.start - EPS <= t < c.end - EPS:
                return c
        return None

    def gap_at(self, t: float):
        """Hueco (a, b) que contiene t, si está antes del final de la secuencia."""
        if t < 0 or t >= self.duration - EPS or self.clip_at(t):
            return None
        a = max((c.end for c in self.clips if c.end <= t + EPS), default=0.0)
        b = min((c.start for c in self.clips if c.start >= t - EPS), default=self.duration)
        return (a, b) if b - a > EPS else None

    def edit_points(self) -> list[float]:
        pts = {0.0}
        for c in self.clips:
            pts.add(round(c.start, 6))
            pts.add(round(c.end, 6))
        return sorted(pts)

    def gaps(self) -> list[tuple[float, float]]:
        out, t = [], 0.0
        for c in sorted(self.clips, key=lambda c: c.start):
            if c.start - t > EPS:
                out.append((t, c.start))
            t = max(t, c.end)
        return out

    # ── Deshacer ───────────────────────────────────────────────────────────
    def snapshot(self):
        return ([replace(c) for c in self.clips], self.mark_in, self.mark_out)

    def restore(self, state):
        clips, self.mark_in, self.mark_out = state
        self.clips = [replace(c) for c in clips]
        self._sort()

    def signature(self):
        return tuple((c.src.id, round(c.t_in, 4), round(c.t_out, 4), round(c.start, 4)) for c in self.clips)

    # ── Operaciones básicas ────────────────────────────────────────────────
    def _carve(self, a: float, b: float, keep=()):
        """Vacía [a, b): recorta, divide o quita los clips que lo ocupan."""
        if b - a <= EPS:
            return
        out = []
        for c in self.clips:
            if c.id in keep or c.end <= a + EPS or c.start >= b - EPS:
                out.append(c)
                continue
            if c.start < a - EPS:
                if c.end > b + EPS:
                    out.append(c.clone(t_in=c.t_in + (b - c.start), start=b))
                c.t_out = c.t_in + (a - c.start)
                out.append(c)
            elif c.end > b + EPS:
                c.t_in += b - c.start
                c.start = b
                out.append(c)
        self.clips = out
        self._sort()

    def _shift(self, from_t: float, delta: float, exclude=()):
        for c in self.clips:
            if c.id not in exclude and c.start >= from_t - EPS:
                c.start = round(c.start + delta, 6)
        self._sort()

    def split_at(self, t: float) -> Clip | None:
        """Corta el clip que pasa por t. Devuelve la parte nueva (la derecha)."""
        t = self.q(t)
        c = self.clip_at(t)
        if c is None or t - c.start < self.frame - EPS or c.end - t < self.frame - EPS:
            return None
        right = c.clone(t_in=c.t_in + (t - c.start), start=t)
        c.t_out = right.t_in
        self.clips.append(right)
        self._sort()
        return right

    def add(self, src: Source, at: float | None = None, insert: bool = False) -> Clip:
        """Agrega un archivo completo: al final, o en `at` (insertando o sobrescribiendo)."""
        start = self.duration if at is None else self.q(max(0.0, at))
        clip = Clip(src, 0.0, src.duration, start)
        if insert and at is not None:
            self.insert([clip], start)
        else:
            self.overwrite([clip])
        return clip

    def overwrite(self, clips: list[Clip]):
        for c in clips:
            self._carve(c.start, c.end, keep={x.id for x in clips})
        self.clips.extend(c for c in clips if c not in self.clips)
        self._sort()

    def insert(self, clips: list[Clip], at: float):
        """Inserta el bloque en `at` corriendo todo lo que sigue (como Ctrl+arrastrar en Premiere)."""
        if not clips:
            return
        at = self.q(at)
        self.split_at(at)
        first = min(c.start for c in clips)
        span = max(c.end for c in clips) - first
        self._shift(at, span, exclude={c.id for c in clips})
        for c in clips:
            c.start = round(at + (c.start - first), 6)
        self.clips.extend(c for c in clips if c not in self.clips)
        self._sort()

    # ── Ediciones ──────────────────────────────────────────────────────────
    def lift(self, ids) -> int:
        """Borra dejando el hueco (Supr)."""
        n = len(self.clips)
        self.clips = [c for c in self.clips if c.id not in ids]
        return n - len(self.clips)

    def ripple_delete(self, ids) -> int:
        """Borra y corre lo que sigue para cerrar el hueco (Shift+Supr)."""
        gone = sorted((c for c in self.clips if c.id in ids), key=lambda c: c.start, reverse=True)
        for c in gone:
            self.clips.remove(c)
            self._shift(c.end, -c.dur)
        return len(gone)

    def close_gap(self, a: float, b: float):
        self._shift(b, -(b - a))

    def lift_range(self, a: float, b: float):
        self._carve(self.q(a), self.q(b))

    def extract_range(self, a: float, b: float):
        a, b = self.q(a), self.q(b)
        self._carve(a, b)
        self._shift(b, -(b - a))

    def move(self, ids, delta: float, mode: str = "overwrite") -> list:
        """Mueve los clips seleccionados. mode: overwrite | insert | rearrange | duplicate.
        Devuelve los ids que quedan seleccionados."""
        moving = sorted((c for c in self.clips if c.id in ids), key=lambda c: c.start)
        if not moving:
            return []
        first = moving[0].start
        delta = self.q(max(-first, delta))
        if mode == "duplicate":
            copies = [c.clone(start=round(c.start + delta, 6)) for c in moving]
            self.overwrite(copies)
            return [c.id for c in copies]
        if mode == "rearrange":
            target = first + delta
            removed = sum(c.dur for c in moving if c.end <= target + EPS)
            inside = next((c for c in moving if c.start < target < c.end), None)
            if inside is not None:
                removed += target - inside.start
            self.ripple_delete({c.id for c in moving})
            t = max(0.0, target - removed)
            pts = self.edit_points()
            t = min(pts, key=lambda p: abs(p - t)) if pts else 0.0
            for c in moving:
                c.start = round(c.start - first, 6)
            self.insert(moving, t)
            return [c.id for c in moving]
        self.clips = [c for c in self.clips if c.id not in ids]
        for c in moving:
            c.start = round(c.start + delta, 6)
        if mode == "insert":
            self.insert(moving, moving[0].start)
        else:
            self.overwrite(moving)
        return [c.id for c in moving]

    def trim(self, cid, side: str, t: float, ripple: bool = False) -> float | None:
        """Mueve el borde 'in' u 'out' de un clip al segundo t de la línea de tiempo.
        ripple=True corre los clips siguientes (recorte en cadena). Devuelve la posición aplicada."""
        c = self.get(cid)
        if c is None:
            return None
        t, f = self.q(t), self.frame
        others = [x for x in self.clips if x is not c]
        if side == "in":
            lo = c.start - c.t_in
            if not ripple:
                lo = max(lo, max((x.end for x in others if x.end <= c.start + EPS), default=0.0))
            t = min(max(t, lo, 0.0), c.end - f)
            d = t - c.start
            old_end = c.end
            c.t_in += d
            if ripple:
                self._shift(old_end, -d, exclude={c.id})
                return c.start
            c.start = t
            self._sort()
            return t
        hi = c.start + (c.src.duration - c.t_in)
        if not ripple:
            hi = min(hi, min((x.start for x in others if x.start >= c.end - EPS), default=hi))
        t = max(min(t, hi), c.start + f)
        d = t - c.end
        old_end = c.end
        c.t_out += d
        if ripple:
            self._shift(old_end, d, exclude={c.id})
        return t

    def ripple_trim_to(self, t: float, side: str) -> float | None:
        """Q / W de Premiere: borra desde el corte anterior (o hasta el siguiente) al cabezal."""
        t = self.q(t)
        pts = self.edit_points()
        if side == "prev":
            a = max((p for p in pts if p < t - EPS), default=None)
            if a is None:
                return None
            self.extract_range(a, t)
            return a
        b = min((p for p in pts if p > t + EPS), default=None)
        if b is None:
            return None
        self.extract_range(t, b)
        return t

    def paste(self, clips: list[Clip], at: float, insert: bool = False) -> list:
        if not clips:
            return []
        first = min(c.start for c in clips)
        new = [c.clone(start=round(self.q(at) + (c.start - first), 6)) for c in clips]
        if insert:
            self.insert(new, self.q(at))
        else:
            self.overwrite(new)
        return [c.id for c in new]

    # ── Exportar ───────────────────────────────────────────────────────────
    def pieces(self, a: float = 0.0, b: float | None = None) -> list:
        """Tramos continuos entre a y b: (inicio, fin, clip o None si es hueco)."""
        b = self.duration if b is None else b
        out, t = [], a
        for c in sorted(self.clips, key=lambda c: c.start):
            if c.end <= a + EPS or c.start >= b - EPS:
                continue
            if c.start > t + EPS:
                out.append((t, c.start, None))
            s = max(c.start, t)
            e = min(c.end, b)
            if e - s > EPS:
                out.append((s, e, c))
            t = e
        if b - t > EPS:
            out.append((t, b, None))
        return out


def export(seq: Sequence, dst: str, *, target: str, encoder: str = "libx264", on_progress=None,
           cancel=None) -> str:
    """Renderiza la secuencia. target: mp4 / mov (H.264 + AAC) o mp3 / wav / m4a / flac (audio)."""
    parts = seq.pieces(0.0, seq.duration)
    if not any(c for _a, _b, c in parts):
        raise media.MediaError("La línea de tiempo está vacía")
    video = target in ("mp4", "mov")
    if video and not seq.has_video:
        raise media.MediaError("La línea de tiempo no tiene video")
    if not video and not seq.has_audio:
        raise media.MediaError("La línea de tiempo no tiene audio")
    with_audio = seq.has_audio
    W, H = seq.frame_size()
    fps = seq.fps
    fr = media.fps_arg(fps)
    total = seq.duration

    # Una entrada por clip con -ss/-t (corte preciso y rápido). Si el comando quedara demasiado
    # largo para Windows, se usa una entrada por archivo y los cortes se hacen con trim.
    per_clip = sum(len(c.src.path) + 60 for _a, _b, c in parts if c) < 20000

    def graph():
        inputs, chains, labels = [], [], []
        src_index: dict = {}
        uses: dict = {}
        if not per_clip:
            for _a, _b, c in parts:
                if c and c.src.id not in src_index:
                    src_index[c.src.id] = len(inputs)
                    inputs += ["-i", c.src.path]
            for _a, _b, c in parts:
                if c:
                    uses.setdefault(c.src.id, []).append(c)
            for sid, cl in uses.items():
                k = src_index[sid]
                n = len(cl)
                src = cl[0].src
                if video and src.has_video:
                    chains.append(f"[{k}:V:0]split={n}" + "".join(f"[sv{sid}_{i}]" for i in range(n)) if n > 1
                                  else f"[{k}:V:0]null[sv{sid}_0]")
                if with_audio and src.has_audio:
                    chains.append(f"[{k}:a:0]asplit={n}" + "".join(f"[sa{sid}_{i}]" for i in range(n)) if n > 1
                                  else f"[{k}:a:0]anull[sa{sid}_0]")
        used: dict = {}
        for i, (a, b, c) in enumerate(parts):
            d = b - a
            vin = ain = None
            if c is not None:
                off = c.t_in + (a - c.start)
                if per_clip:
                    k = len(inputs) // 6
                    inputs += ["-ss", f"{off:.6f}", "-t", f"{d + 0.25:.6f}", "-i", c.src.path]
                    vin, ain = f"[{k}:V:0]", f"[{k}:a:0]"
                    vtrim = atrim = ""
                else:
                    j = used.get(c.src.id, 0)
                    used[c.src.id] = j + 1
                    vin, ain = f"[sv{c.src.id}_{j}]", f"[sa{c.src.id}_{j}]"
                    vtrim = f"trim=start={off:.6f}:duration={d + 0.25:.6f},setpts=PTS-STARTPTS,"
                    atrim = f"atrim=start={off:.6f}:duration={d + 0.25:.6f},asetpts=PTS-STARTPTS,"
                if not c.src.has_video:
                    vin = None
                if not c.src.has_audio:
                    ain = None
            if video:
                if vin:
                    chains.append(
                        f"{vin}{vtrim}setpts=PTS-STARTPTS,fps={fr},"
                        f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=bicubic,"
                        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p,"
                        f"tpad=stop_mode=clone:stop_duration=1,trim=duration={d:.6f},setpts=PTS-STARTPTS[v{i}]")
                else:
                    chains.append(f"color=c=black:s={W}x{H}:r={fr}:d={d:.6f},format=yuv420p,setsar=1[v{i}]")
            if with_audio:
                if ain:
                    chains.append(
                        f"{ain}{atrim}asetpts=PTS-STARTPTS,aresample=48000,"
                        f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
                        f"apad=whole_dur={d:.6f},atrim=duration={d:.6f},asetpts=PTS-STARTPTS[a{i}]")
                else:
                    chains.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={d:.6f}[a{i}]")
            labels.append((f"[v{i}]" if video else "") + (f"[a{i}]" if with_audio else ""))
        n = len(parts)
        if video:
            out = "[v][a]" if with_audio else "[v]"
            chains.append("".join(labels) + f"concat=n={n}:v=1:a={1 if with_audio else 0}{out}")
        else:
            chains.append("".join(labels) + f"concat=n={n}:v=0:a=1[a]")
        return inputs, ";\n".join(chains)

    inputs, fc = graph()
    script = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    script.write(fc)
    script.close()

    def build(enc):
        args = list(inputs) + ["-/filter_complex", script.name]
        if video:
            args += ["-map", "[v]"]
            if with_audio:
                args += ["-map", "[a]", *media.AAC_ARGS]
            args += media.video_args(enc, fps) + ["-r", fr]
        else:
            args += ["-map", "[a]", *media.AUDIO_TARGETS[target]]
        args += ["-map_metadata", "-1", "-t", f"{total:.6f}"]
        if dst.lower().endswith((".mp4", ".mov", ".m4a")):
            args += ["-movflags", "+faststart"]
        return args + [dst]

    kw = dict(duration=total, on_progress=on_progress, cancel=cancel)
    try:
        if video:
            return media._with_fallback(build, encoder, **kw)
        media.run_ffmpeg(build(encoder), **kw)
        return encoder
    finally:
        try:
            os.remove(script.name)
        except OSError:
            pass
