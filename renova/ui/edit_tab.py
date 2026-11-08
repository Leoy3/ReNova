"""Pestaña Recortar: editor de línea de tiempo (uno o varios videos / audios propios)."""

from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from .. import core, media, sequence
from . import theme as T
from .editor import SequenceEditor
from .theme import F, G, S
from .timeline import tc
from .widgets import Button, Modal, PopupMenu, confirm, open_path, reveal_in_explorer, round_rect

VIDEO_TARGETS = [("mp4", "MP4 · H.264 (Premiere)"), ("mov", "MOV · H.264"),
                 ("mp3", "Sólo audio · MP3"), ("wav", "Sólo audio · WAV")]
AUDIO_TARGETS = [("mp3", "MP3 · 320 kbps"), ("wav", "WAV · sin pérdida"), ("m4a", "M4A · AAC"), ("flac", "FLAC")]
FILETYPES = [("Video y audio", "*.mp4 *.mkv *.mov *.avi *.webm *.flv *.wmv *.m4v *.ts *.mts *.m2ts *.3gp *.gif "
                               "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma *.aiff *.aif"),
             ("Todos los archivos", "*.*")]


def choose(master, title, message, options):
    """Diálogo con varias opciones: options = [(valor, texto, variante)]. Devuelve el valor o None."""
    dlg = Modal(master, title, 480, 210)
    body = tk.Frame(dlg, bg=T.BG, padx=S(24), pady=S(20))
    body.pack(fill="both", expand=True)
    tk.Label(body, text=title, font=F.h2, fg=T.TEXT, bg=T.BG, anchor="w").pack(fill="x")
    tk.Label(body, text=message, font=F.body, fg=T.TEXT_2, bg=T.BG, anchor="w", justify="left",
             wraplength=S(430)).pack(fill="x", pady=(S(8), 0))
    row = tk.Frame(body, bg=T.BG)
    row.pack(side="bottom", fill="x")
    for value, text, variant in reversed(options):
        Button(row, text=text, variant=variant, command=lambda v=value: dlg.close(v)).pack(side="right",
                                                                                          padx=(S(8), 0))
    dlg.show()
    dlg.wait_window()
    return dlg.result


class EditTab(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=T.BG)
        self.app = app
        self.settings = app.settings
        self.target = "mp4"
        self.exporting = False
        self.cancel = None
        self.result = None
        self._exported_sig = None
        self._drop_hover = False
        self._loading = 0
        self._build()
        self._show_empty()

    # ── Construcción ───────────────────────────────────────────────────────
    def _build(self):
        pad = S(24)
        self.empty = tk.Canvas(self, bg=T.BG, highlightthickness=0, bd=0, cursor="hand2")
        self.empty.bind("<Configure>", lambda e: self._draw_empty())
        self.empty.bind("<Enter>", lambda e: self._set_drop_hover(True))
        self.empty.bind("<Leave>", lambda e: self._set_drop_hover(False))
        self.empty.bind("<ButtonRelease-1>", lambda e: self.browse())
        self.app.register_drop(self.empty, self._on_drop)

        self.loaded = tk.Frame(self, bg=T.BG)
        head = tk.Frame(self.loaded, bg=T.BG, padx=pad)
        head.pack(fill="x", pady=(S(12), S(2)))
        self.kind_icon = tk.Label(head, text=G.MOVIE, font=F.icon_md, fg=T.ACCENT, bg=T.BG)
        self.kind_icon.pack(side="left", padx=(0, S(10)))
        self.actions = tk.Frame(head, bg=T.BG)
        self.actions.pack(side="right")
        txt = tk.Frame(head, bg=T.BG)
        txt.pack(side="left", fill="x", expand=True)
        self.name_lbl = tk.Label(txt, text="", font=F.h3, fg=T.TEXT, bg=T.BG, anchor="w")
        self.name_lbl.pack(fill="x")
        self.info_lbl = tk.Label(txt, text="", font=F.small, fg=T.TEXT_3, bg=T.BG, anchor="w")
        self.info_lbl.pack(fill="x")
        self.prog = tk.Canvas(txt, bg=T.BG, highlightthickness=0, height=S(4))

        a = self.actions
        self.add_btn = Button(a, text="Agregar archivo", icon=G.ADD, variant="ghost", height=34, padx=12,
                              font=F.small_b, icon_font=F.icon_sm, command=self.browse_add,
                              tooltip="Suma otro video o audio al final de la línea de tiempo")
        self.target_btn = Button(a, text="", trailing=G.CHEVRON_DOWN, variant="outline", height=34, padx=12,
                                 font=F.small_b, command=self._target_menu, tooltip="Formato del archivo exportado")
        self.export_btn = Button(a, text="Exportar", icon=G.DOWNLOAD, variant="primary", height=34, padx=16,
                                 command=self.export, tooltip="Exporta la línea de tiempo (Ctrl+Enter)")
        self.cancel_btn = Button(a, text="Cancelar", icon=G.CANCEL, variant="secondary", height=34, padx=12,
                                 command=self._cancel_export)
        self.reveal_btn = Button(a, text="Mostrar en carpeta", icon=G.FOLDER_OPEN, variant="secondary", height=34,
                                 padx=12, command=lambda: self.result and reveal_in_explorer(self.result))
        self.open_btn = Button(a, icon=G.PLAY, variant="ghost", height=34, tooltip="Abrir archivo exportado",
                               command=lambda: self.result and open_path(self.result))
        self.close_btn = Button(a, icon=G.CLOSE, variant="ghost", height=34, command=self.close_file,
                                tooltip="Cerrar y empezar de nuevo")

        self.editor = SequenceEditor(self.loaded, self.app, on_change=self._changed, on_add=self.browse_add)
        self.editor.pack(fill="both", expand=True)
        self.app.register_drop(self.loaded, self._on_drop, tree=True)  # toda la pestaña acepta archivos
        self.app.register_drop(self.editor.timeline, self._on_drop_timeline)  # en la línea: donde se suelta

    def _set_drop_hover(self, v):
        self._drop_hover = v
        self._draw_empty()

    def _draw_empty(self):
        cv = self.empty
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 100:
            return
        pad = S(24)
        hov = self._drop_hover
        round_rect(cv, pad, S(18), w - pad, h - S(18), S(16), T.SURFACE_2 if hov else T.SURFACE, None, T.BG)
        cv.create_rectangle(pad + S(8), S(26), w - pad - S(8), h - S(26), outline=T.ACCENT if hov else T.BORDER,
                            dash=(6, 4))
        cx, cy = w / 2, h / 2 - S(50)
        r = S(40)
        round_rect(cv, cx - r, cy - r, cx + r, cy + r, r, T.ACCENT_SOFT, None, T.SURFACE_2 if hov else T.SURFACE)
        cv.create_text(cx, cy + S(2), text=G.CUT, font=F.icon_lg, fill=T.ACCENT)
        cv.create_text(cx, cy + S(72), text="Editá tus videos y audios", font=F.h2, fill=T.TEXT)
        cv.create_text(cx, cy + S(100), text="Arrastralos acá o hacé clic para elegirlos (podés elegir varios)",
                       font=F.body, fill=T.TEXT_2)
        tips = ["Cortá, mové, recortá y borrá bloques como en Premiere",
                "Uní varios archivos en una misma línea de tiempo",
                "Exportá en H.264 listo para Premiere, o sólo el audio"]
        y = cy + S(140)
        tw = max(F.small.measure(t) for t in tips) + S(24)
        for tip in tips:
            cv.create_text(cx - tw / 2, y, text=G.CHECK, font=F.icon_sm, fill=T.OK, anchor="w")
            cv.create_text(cx - tw / 2 + S(24), y, text=tip, font=F.small, fill=T.TEXT_3, anchor="w")
            y += S(24)

    def _show_empty(self):
        self.loaded.pack_forget()
        self.empty.pack(fill="both", expand=True)

    def _show_loaded(self):
        if not self.loaded.winfo_ismapped():
            self.empty.pack_forget()
            self.loaded.pack(fill="both", expand=True)

    # ── Abrir / agregar ────────────────────────────────────────────────────
    @property
    def seq(self):
        return self.editor.seq

    def browse(self):
        paths = filedialog.askopenfilenames(parent=self, title="Elegir videos o audios para editar",
                                            filetypes=FILETYPES)
        if paths:
            self.load(list(paths))

    def browse_add(self):
        paths = filedialog.askopenfilenames(parent=self, title="Agregar a la línea de tiempo", filetypes=FILETYPES)
        if paths:
            self.load(list(paths), mode="append")

    def _on_drop(self, data, files):
        if files:
            self.load(files, mode="append" if self.seq.clips else "replace")

    def _on_drop_timeline(self, data, files):
        if not files:
            return
        if not self.seq.clips:
            self.load(files)
            return
        tl = self.editor.timeline
        x_root = getattr(self.app, "last_drop", (tl.winfo_pointerx(), 0))[0]
        t = max(0.0, tl.x2t(x_root - tl.winfo_rootx()))
        if t >= self.seq.duration:
            self.load(files, mode="append")
            return
        pts = self.seq.edit_points()
        at = min(pts, key=lambda p: abs(p - t))  # se inserta en el corte más cercano
        self.load(files, mode="insert", at=at)

    def _dirty(self) -> bool:
        return bool(self.seq.clips) and self.seq.signature() != self._exported_sig and len(self.editor._undo) > 0

    def open_path(self, path):
        """Desde otras pestañas («Abrir en Recortar»)."""
        if not self.seq.clips:
            self.load([path])
            return
        choice = choose(self, "Ya hay una edición abierta",
                        f"¿Querés sumar «{os.path.basename(path)}» al final de la línea de tiempo, o empezar "
                        "una edición nueva sólo con este archivo?",
                        [(None, "Cancelar", "ghost"), ("replace", "Empezar de nuevo", "secondary"),
                         ("append", "Agregar al final", "primary")])
        if choice == "replace" and self._dirty() and not confirm(
                self, "¿Descartar la edición actual?", "Los cambios no se exportaron y se van a perder.",
                ok="Descartar", cancel="Volver"):
            return
        if choice:
            self.load([path], mode=choice)

    def load(self, paths: list, mode: str = "replace", at: float | None = None):
        paths = [os.path.normpath(p) for p in paths if os.path.isfile(p)]
        images = [p for p in paths if core.file_kind(p) == "image" and not p.lower().endswith(".gif")]
        paths = [p for p in paths if p not in images]
        if images:
            self.app.toast.show("Las imágenes no se pueden editar acá; usá la pestaña Convertir", "warn")
        if not paths:
            return
        if self.exporting:
            self.app.toast.show("Esperá a que termine la exportación actual", "warn")
            return
        if mode == "replace" and self.seq.clips and self._dirty() and not confirm(
                self, "¿Empezar una edición nueva?", "Los cambios de la línea de tiempo actual no se exportaron "
                "y se van a perder.", ok="Empezar de nuevo", cancel="Volver"):
            return
        self._loading += 1
        self.app.toast.show("Abriendo…" if len(paths) == 1 else f"Abriendo {len(paths)} archivos…", "info", ms=1500)

        def run():
            ok, bad = [], []
            for p in paths:
                try:
                    pr = media.probe(p)
                    src = sequence.Source.from_probe(p, pr)
                    if src.duration > 0 and (src.has_video or src.has_audio):
                        ok.append(src)
                    else:
                        bad.append(p)
                except Exception:  # noqa: BLE001
                    bad.append(p)
            self.app.bus.call(self._loaded, ok, bad, mode, at)
        threading.Thread(target=run, daemon=True).start()

    def _loaded(self, sources, bad, mode, at):
        self._loading -= 1
        if bad:
            names = ", ".join(os.path.basename(b) for b in bad[:2]) + ("…" if len(bad) > 2 else "")
            self.app.toast.show(f"No se pudo leer {names} (¿está dañado o no es video/audio?)", "error")
        if not sources:
            return
        ed = self.editor
        if mode == "replace":
            ed.reset()
            self.result = None
            self._exported_sig = None
            src0 = sources[0]
            ext = Path(src0.path).suffix.lower().lstrip(".")
            audio_only = not any(s.has_video for s in sources)
            targets = AUDIO_TARGETS if audio_only else VIDEO_TARGETS
            self.target = ext if audio_only and ext in dict(targets) else targets[0][0]
        self._show_loaded()
        self.update_idletasks()
        if mode == "insert":
            ed.add_sources(sources, at=at, insert=True)
        else:
            ed.add_sources(sources)
        if self.target not in dict(self._targets()):
            self.target = self._targets()[0][0]
        self._set_mode("idle")
        if mode != "replace":
            n = len(sources)
            self.app.toast.show(f"Se agregó {sources[0].name}" if n == 1 else f"Se agregaron {n} archivos", "ok",
                                ms=2200)
        ed.focus_editor()

    def close_file(self):
        if self.exporting:
            return
        if self._dirty() and not confirm(self, "¿Cerrar la edición?", "Los cambios no se exportaron y se van a "
                                         "perder.", ok="Cerrar igual", cancel="Volver"):
            return
        self.editor.reset()
        self.result = None
        self._show_empty()
        self.app.update_status()

    def on_hide(self):
        self.editor.pause()

    def on_show(self):
        self.editor.vol.sync()  # por si se cambió en el recorte antes de descargar
        if self.seq.clips:
            self.after(50, self.editor.focus_editor)

    # ── Exportar ───────────────────────────────────────────────────────────
    def _targets(self):
        return VIDEO_TARGETS if self.seq.has_video else AUDIO_TARGETS

    def _target_menu(self):
        items = [{"label": label, "checked": k == self.target,
                  "command": lambda k=k: (setattr(self, "target", k), self._set_mode("idle"))}
                 for k, label in self._targets()]
        b = self.target_btn
        PopupMenu(self, items, b.winfo_rootx() + b.winfo_width(), b.winfo_rooty() + b.winfo_height() + S(4),
                  min_width=240, align="right")

    def _changed(self):
        if not self.exporting and self.seq.clips is not None:
            self._set_mode("idle" if not self.result or self.seq.signature() != self._exported_sig else "done_keep")

    def _title(self):
        srcs = []
        for c in sorted(self.seq.clips, key=lambda c: c.start):
            if c.src not in srcs:
                srcs.append(c.src)
        if not srcs:
            return "Línea de tiempo vacía", None
        if len(srcs) == 1:
            return srcs[0].name, srcs[0]
        return f"{srcs[0].name} + {len(srcs) - 1} {'archivo' if len(srcs) == 2 else 'archivos'}", srcs[0]

    def _info(self):
        seq = self.seq
        n = len(seq.clips)
        bits = [f"{n} {'clip' if n == 1 else 'clips'}"]
        if seq.has_video:
            w, h = seq.frame_size()
            bits.append(f"{w}×{h} · {seq.fps:g} fps")
        bits.append(tc(seq.duration, seq.fps))
        gaps = seq.gaps()
        if gaps:
            bits.append(f"{len(gaps)} {'hueco' if len(gaps) == 1 else 'huecos'} (se exportan en negro)")
        return " · ".join(bits)

    def _set_mode(self, mode, text=None, progress=None):
        for w in (self.add_btn, self.target_btn, self.export_btn, self.cancel_btn, self.reveal_btn, self.open_btn,
                  self.close_btn):
            w.pack_forget()
        self.prog.pack_forget()
        title, src0 = self._title()
        self.name_lbl.configure(text=title)
        self.kind_icon.configure(text=G.MOVIE if self.seq.has_video or not self.seq.clips else G.MUSIC)
        label = dict(self._targets()).get(self.target, self.target)
        self.target_btn.configure_button(text=label)
        has = bool(self.seq.clips)
        if mode == "exporting":
            self.info_lbl.configure(text=text or "Exportando…", fg=T.WARN)
            self.prog.pack(fill="x", pady=(S(5), 0))
            self._draw_progress(progress or 0.0)
            self.cancel_btn.pack(side="left")
        else:
            if mode in ("done", "done_keep"):
                self.reveal_btn.pack(side="left", padx=(0, S(4)))
                self.open_btn.pack(side="left", padx=(0, S(10)))
            else:
                self.add_btn.pack(side="left", padx=(0, S(10)))
            self.target_btn.pack(side="left", padx=(0, S(8)))
            self.export_btn.pack(side="left")
            self.export_btn.configure_button(enabled=has)
            self.close_btn.pack(side="left", padx=(S(6), 0))
            if mode == "done":
                self.info_lbl.configure(text=text, fg=T.OK)
            elif mode == "done_keep":
                self.info_lbl.configure(text=f"Exportado · {os.path.basename(self.result)}", fg=T.OK)
            elif mode == "error":
                self.info_lbl.configure(text=text, fg=T.ERR)
            else:
                self.info_lbl.configure(text=self._info() if has else "", fg=T.TEXT_3)
        self.app.update_status()

    def _draw_progress(self, p):
        cv = self.prog
        cv.delete("all")
        w = max(10, cv.winfo_width())
        cv.create_rectangle(0, 0, w, S(4), fill=T.SURFACE_HI, width=0)
        cv.create_rectangle(0, 0, w * max(0.0, min(1.0, p)), S(4), fill=T.WARN, width=0)

    def export(self):
        seq = self.seq
        if self.exporting or not seq.clips:
            return
        self.editor.pause()
        out_dir = self.settings.out_dir
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError:
            self.app.toast.show("No se puede escribir en la carpeta de destino", "error")
            return
        ext = self.target
        _title, src0 = self._title()
        stem = core.safe_filename(f"{Path(src0.path).stem} (editado)")
        dst = core.unique_path(out_dir, stem, ext)
        tmp = os.path.join(out_dir, f".renova-edit-{int(time.time())}.{ext}")
        snap = sequence.Sequence()
        snap.restore(seq.snapshot())
        total = snap.duration
        sig = seq.signature()
        self.exporting = True
        self.cancel = threading.Event()
        label = dict(self._targets()).get(ext, ext)
        self._set_mode("exporting", f"Exportando · 0% · {label} · {tc(total, snap.fps)}", 0.0)
        cancel = self.cancel

        def progress(p):
            self.app.bus.mark(("edit", "progress"), lambda: self.exporting and self._set_mode(
                "exporting", f"Exportando · {p * 100:.0f}% · {label} · {tc(total, snap.fps)}", p))

        def run():
            err = None
            try:
                enc = media.Encoders.pick(self.settings.hw_accel)
                sequence.export(snap, tmp, target=ext, encoder=enc, on_progress=progress, cancel=cancel)
                os.replace(tmp, dst)
            except media.Cancelled:
                err = "cancel"
            except Exception as e:  # noqa: BLE001
                err = core.humanize_error(e)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            self.app.bus.call(done, err)

        def done(err):
            self.exporting = False
            if err == "cancel":
                self._set_mode("idle")
                self.app.toast.show("Exportación cancelada", "info")
            elif err:
                self._set_mode("error", f"No se pudo exportar: {err[0]}")
            else:
                self.result = dst
                self._exported_sig = sig
                size = core.fmt_bytes(os.path.getsize(dst))
                self._set_mode("done", f"Exportado · {os.path.basename(dst)} · {tc(total, snap.fps)} · {size}")
                self.app.notify_finished("Edición exportada", None)
        threading.Thread(target=run, daemon=True, name="edit-export").start()

    def _cancel_export(self):
        if self.cancel:
            self.cancel.set()

    def cancel_all(self):
        self._cancel_export()

    # ── Estado para la ventana ─────────────────────────────────────────────
    def summary(self) -> str:
        if self.exporting:
            return "Exportando edición…"
        return ""

    def active_count(self) -> int:
        return 1 if self.exporting else 0

    def start_all(self):
        if self.seq.clips and not self.exporting:
            self.export()
