"""Diálogos: selección de videos de playlist, recorte (tijeras) y ajustes."""

from __future__ import annotations

import tkinter as tk

from .. import APP_VERSION, core, engine, media, paths
from . import theme as T
from .theme import F, G, S
from .widgets import (Button, Modal, Segmented, TextInput, Toggle, draw_checkbox, open_path,
                      round_rect)


def format_menu_items(fmt: str, quality: str, on_pick, info=None) -> list:
    """Ítems de menú para elegir tipo + calidad (usado en tarjetas y barra de opciones)."""
    items = [{"header": "Tipo"}]
    icons = {"video": G.VIDEO, "audio": G.MUSIC, "mute": G.MUTE}
    for key, label in core.FORMATS:
        default_q = quality if (key == "audio") == (fmt == "audio") else ("mp3" if key == "audio" else "1080")
        items.append({"label": label, "icon": icons[key], "checked": key == fmt,
                      "command": (lambda k=key, q=default_q: on_pick(k, q, True))})
    items.append({"separator": True})
    items.append({"header": "Calidad" if fmt != "audio" else "Formato de audio"})
    if fmt == "audio":
        for key, label in core.AUDIO_QUALITIES:
            items.append({"label": label, "checked": key == quality,
                          "command": (lambda k=key: on_pick(fmt, k, False))})
    else:
        for key, label in core.VIDEO_QUALITIES:
            hint = ""
            if info:
                pred, reenc = core.predict_video(info, key)
                hint = (pred or "") + (" · recodifica" if reenc else "")
            elif key in core.SLOW_QUALITIES:
                hint = "recodifica en YouTube"
            items.append({"label": label, "checked": key == quality, "hint": hint,
                          "command": (lambda k=key: on_pick(fmt, k, False))})
    return items


# ── Playlist ───────────────────────────────────────────────────────────────────
class PlaylistDialog(Modal):
    ROW = 64

    def __init__(self, app, playlist: dict):
        super().__init__(app.root, "Elegir videos de la playlist", 760, 640, resizable=True)
        self.app = app
        self.pl = playlist
        self.entries = playlist["entries"]
        cur = playlist.get("current")
        self.current_idx = next((i for i, e in enumerate(self.entries) if cur and e.get("id") == cur), None)
        if self.current_idx is not None:
            self.selected = {self.current_idx}
        else:
            self.selected = set(range(len(self.entries)))
        self.filtered = list(range(len(self.entries)))
        self.thumbs: dict = {}
        self._hover = None
        self._anchor = None
        self._build()
        self.show()

    def _build(self):
        rh = S(self.ROW)
        top = tk.Frame(self, bg=T.BG, padx=S(24), pady=S(18))
        top.pack(fill="x")
        tk.Label(top, text="PLAYLIST", font=F.label, fg=T.ACCENT, bg=T.BG, anchor="w").pack(fill="x")
        tk.Label(top, text=T.ellipsize(F.h2, self.pl["title"], S(700)), font=F.h2, fg=T.TEXT, bg=T.BG,
                 anchor="w").pack(fill="x", pady=(S(2), 0))
        sub = f"{len(self.entries)} videos"
        if self.pl.get("uploader"):
            sub += f" · {self.pl['uploader']}"
        total = sum(e.get("duration") or 0 for e in self.entries)
        if total:
            sub += f" · {core.fmt_duration(total)} en total"
        tk.Label(top, text=sub, font=F.body, fg=T.TEXT_3, bg=T.BG, anchor="w").pack(fill="x")

        bar = tk.Frame(self, bg=T.BG, padx=S(24))
        bar.pack(fill="x", pady=(0, S(10)))
        self.all_cv = tk.Canvas(bar, bg=T.BG, highlightthickness=0, width=S(190), height=S(36), cursor="hand2")
        self.all_cv.pack(side="left")
        self.all_cv.bind("<ButtonRelease-1>", lambda e: self._toggle_all())
        self.search = TextInput(bar, placeholder="Filtrar por título…", icon=G.SEARCH, height=36,
                                font=F.body, bg=T.BG)
        self.search.configure(width=S(260))
        self.search.pack(side="right")
        self.search.var.trace_add("write", lambda *a: self._apply_filter())

        foot = tk.Frame(self, bg=T.BG_2, padx=S(24), pady=S(14))
        foot.pack(side="bottom", fill="x")
        self.btn_dl = Button(foot, text="Descargar", icon=G.DOWNLOAD, variant="primary",
                             command=lambda: self._finish(True))
        self.btn_dl.pack(side="right")
        self.btn_add = Button(foot, text="Agregar a la cola", variant="secondary",
                              command=lambda: self._finish(False))
        self.btn_add.pack(side="right", padx=(0, S(8)))
        Button(foot, text="Cancelar", variant="ghost", command=self.close).pack(side="right", padx=(0, S(8)))
        if self.current_idx is not None:
            Button(foot, text="Sólo este video", icon=G.VIDEO, variant="outline",
                   command=self._only_current).pack(side="left")
        self.count_lbl = tk.Label(foot, text="", font=F.body, fg=T.TEXT_3, bg=T.BG_2)
        self.count_lbl.pack(side="left", padx=S(12))

        wrap = tk.Frame(self, bg=T.BG, padx=S(16))
        wrap.pack(fill="both", expand=True)
        self.cv = tk.Canvas(wrap, bg=T.BG, highlightthickness=0, bd=0, yscrollincrement=S(16))
        from .widgets import ThinScrollbar
        self.sb = ThinScrollbar(wrap, self.cv.yview, bg=T.BG)
        self.cv.configure(yscrollcommand=self._on_scroll)
        self.cv.pack(side="left", fill="both", expand=True)
        self.sb.pack(side="right", fill="y")
        self.cv._wheel_target = lambda e: self.cv.yview_scroll(int(-e.delta / 120 * 3), "units")
        self.cv.bind("<Configure>", lambda e: self._redraw())
        self.cv.bind("<Motion>", self._motion)
        self.cv.bind("<Leave>", lambda e: self._set_hover(None))
        self.cv.bind("<ButtonRelease-1>", self._click)
        self.bind("<Control-a>", lambda e: self._set_all(True))
        self.bind("<Return>", lambda e: self._finish(self.app.settings.auto_start))
        self._update_counts()
        if self.current_idx is not None:
            self.after(50, lambda: self.cv.yview_moveto(max(0, (self.current_idx - 2) * rh) /
                                                        max(1, len(self.filtered) * rh)))

    # ── Selección ──────────────────────────────────────────────────────────
    def _apply_filter(self):
        q = self.search.get().strip().lower()
        self.filtered = [i for i, e in enumerate(self.entries) if not q or q in (e.get("title") or "").lower()]
        self.cv.yview_moveto(0)
        self._redraw()
        self._update_counts()

    def _toggle_all(self):
        vis = set(self.filtered)
        self._set_all(not vis.issubset(self.selected))

    def _set_all(self, value: bool):
        vis = set(self.filtered)
        if value:
            self.selected |= vis
        else:
            self.selected -= vis
        self._redraw()
        self._update_counts()

    def _only_current(self):
        self.selected = {self.current_idx}
        self._finish(self.app.settings.auto_start)

    def _update_counts(self):
        n = len(self.selected)
        dur = sum(self.entries[i].get("duration") or 0 for i in self.selected)
        txt = f"{n} de {len(self.entries)} seleccionados"
        if dur:
            txt += f" · {core.fmt_duration(dur)}"
        self.count_lbl.configure(text=txt)
        self.btn_dl.configure_button(text=f"Descargar {n}" if n else "Descargar", enabled=n > 0)
        self.btn_add.configure_button(enabled=n > 0)
        vis = set(self.filtered)
        state = True if vis and vis.issubset(self.selected) else ("mixed" if vis & self.selected else False)
        cv = self.all_cv
        cv.delete("all")
        draw_checkbox(cv, S(2), S(9), state, T.BG)
        label = "Seleccionar todos" if state is not True else "Quitar selección"
        cv.create_text(S(30), S(18), text=label, font=F.body_b, fill=T.TEXT, anchor="w")

    def _finish(self, start: bool):
        if not self.selected:
            return
        chosen = [self.entries[i] for i in sorted(self.selected)]
        self.close((chosen, start))

    # ── Lista virtual ──────────────────────────────────────────────────────
    def _on_scroll(self, first, last):
        self.sb.set(first, last)
        view = (float(first), float(last))
        if view != getattr(self, "_last_view", None):
            self._last_view = view
            self._redraw_soon()

    def _row_at(self, y):
        idx = int(self.cv.canvasy(y) // S(self.ROW))
        return idx if 0 <= idx < len(self.filtered) else None

    def _motion(self, e):
        self._set_hover(self._row_at(e.y))

    def _set_hover(self, idx):
        if idx != self._hover:
            self._hover = idx
            self.cv.configure(cursor="hand2" if idx is not None else "arrow")
            self._redraw()

    def _click(self, e):
        row = self._row_at(e.y)
        if row is None:
            return
        i = self.filtered[row]
        new = i not in self.selected
        if e.state & 0x1 and self._anchor is not None and self._anchor in self.filtered:  # Shift
            a, b = sorted((self.filtered.index(self._anchor), row))
            for r in range(a, b + 1):
                (self.selected.add if new else self.selected.discard)(self.filtered[r])
        else:
            (self.selected.add if new else self.selected.discard)(i)
        self._anchor = i
        self._redraw()
        self._update_counts()

    def _redraw(self):
        cv = self.cv
        rh = S(self.ROW)
        w = cv.winfo_width()
        if w < 100:
            return
        n = len(self.filtered)
        region = (0, 0, w, max(n * rh, cv.winfo_height()))
        if region != getattr(self, "_region", None):
            self._region = region
            cv.configure(scrollregion=region)
        cv.delete("all")
        if not n:
            cv.create_text(w / 2, S(60), text="Ningún video coincide con el filtro", font=F.body,
                           fill=T.TEXT_3)
            return
        top = cv.canvasy(0)
        first = max(0, int(top // rh))
        last = min(n, int((top + cv.winfo_height()) // rh) + 2)
        tw, th = S(96), S(54)
        for row in range(first, last):
            i = self.filtered[row]
            e = self.entries[i]
            y = row * rh
            sel = i in self.selected
            hov = row == self._hover
            if hov or sel:
                round_rect(cv, S(4), y + S(2), w - S(4), y + rh - S(2), S(10),
                           T.SURFACE_2 if hov else T.SURFACE, T.BORDER_SOFT if sel else None, T.BG)
            rowbg = T.SURFACE_2 if hov else (T.SURFACE if sel else T.BG)
            draw_checkbox(cv, S(18), y + (rh - S(18)) / 2, sel, rowbg, hover=hov)
            cv.create_text(S(64), y + rh / 2, text=str(e["index"]), font=F.mono, fill=T.TEXT_3, anchor="e")
            tx = S(76)
            photo = self.thumbs.get((i, rowbg))
            if photo:
                cv.create_image(tx, y + (rh - th) / 2, image=photo, anchor="nw")
            else:
                round_rect(cv, tx, y + (rh - th) / 2, tx + tw, y + (rh + th) / 2, S(6), T.SURFACE_HI, None, rowbg)
                self._request_thumb(i, rowbg)
            x = tx + tw + S(14)
            maxw = w - x - S(20)
            title = e.get("title") or ""
            cv.create_text(x, y + rh / 2 - S(10), text=T.ellipsize(F.body_b, title, maxw), font=F.body_b,
                           fill=T.TEXT if sel else T.TEXT_2, anchor="w")
            meta = []
            if e.get("duration"):
                meta.append(core.fmt_duration(e["duration"]))
            if e.get("uploader"):
                meta.append(e["uploader"])
            mx = x
            if i == self.current_idx:
                bw = F.tiny_b.measure("ESTE VIDEO") + S(12)
                round_rect(cv, mx, y + rh / 2 + S(2), mx + bw, y + rh / 2 + S(20), S(5), T.ACCENT_SOFT, None, rowbg)
                cv.create_text(mx + S(6), y + rh / 2 + S(11), text="ESTE VIDEO", font=F.tiny_b, fill=T.ACCENT, anchor="w")
                mx += bw + S(8)
            cv.create_text(mx, y + rh / 2 + S(11), text=" · ".join(meta), font=F.small, fill=T.TEXT_3, anchor="w")

    def _request_thumb(self, i, rowbg):
        e = self.entries[i]
        url = e.get("thumb")
        if not url:
            return

        def done(photo, i=i, rowbg=rowbg):
            self.thumbs[(i, rowbg)] = photo
            if self.winfo_exists():
                self._redraw_soon()
        self.app.thumbs.get(url, lambda: core.fetch_image(url), S(96), S(54), S(6), rowbg, done)

    def _redraw_soon(self):
        if not getattr(self, "_rjob", None):
            def run():
                self._rjob = None
                if self.winfo_exists():
                    self._redraw()
            self._rjob = self.after(16, run)


# ── Ajustes ────────────────────────────────────────────────────────────────────
class SettingsDialog(Modal):
    def __init__(self, app):
        super().__init__(app.root, "Ajustes", 600, 680)
        self.app = app
        st = app.settings
        body = tk.Frame(self, bg=T.BG, padx=S(28), pady=S(22))
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Ajustes", font=F.h1, fg=T.TEXT, bg=T.BG, anchor="w").pack(fill="x")

        def section(title):
            tk.Label(body, text=title.upper(), font=F.label, fg=T.TEXT_3, bg=T.BG, anchor="w").pack(
                fill="x", pady=(S(18), S(6)))

        def row(title, desc, widget_factory):
            r = tk.Frame(body, bg=T.BG)
            r.pack(fill="x", pady=S(5))
            txt = tk.Frame(r, bg=T.BG)
            txt.pack(side="left", fill="x", expand=True)
            tk.Label(txt, text=title, font=F.body_b, fg=T.TEXT, bg=T.BG, anchor="w").pack(fill="x")
            if desc:
                tk.Label(txt, text=desc, font=F.small, fg=T.TEXT_3, bg=T.BG, anchor="w", justify="left",
                         wraplength=S(380)).pack(fill="x")
            widget_factory(r).pack(side="right", padx=(S(12), 0))

        section("Descargas")
        row("Descargas simultáneas", "Más descargas a la vez pueden saturar conexiones lentas.",
            lambda p: Segmented(p, [(n, str(n)) for n in (1, 2, 3, 4)], st.max_parallel,
                                command=lambda v: (st.set("max_parallel", v), app.downloads.mgr._pump()),
                                height=32))
        row("Descargar al agregar", "Empieza apenas pegás un link (sin pasar por la cola).",
            lambda p: Toggle(p, st.auto_start, command=lambda v: (st.set("auto_start", v),
                                                                   app.downloads.sync_auto_toggle())))
        row("Detectar links copiados", "Al volver a ReNova, ofrece agregar el link que copiaste.",
            lambda p: Toggle(p, st.clipboard_detect, command=lambda v: st.set("clipboard_detect", v)))

        section("Compatibilidad y rendimiento")
        hw = media.Encoders._hw
        hw_txt = (", ".join(media.HW_LABELS.get(h, h) for h in hw) + " disponible") if hw else \
            ("No se detectó aceleración por hardware" if hw is not None else "Detectando…")
        row("Aceleración por hardware", f"Acelera la conversión a H.264 cuando hace falta. {hw_txt}.",
            lambda p: Toggle(p, st.hw_accel, command=lambda v: st.set("hw_accel", v)))

        section("Actualizaciones")
        upd = app.updates

        def app_widget(p):
            f = tk.Frame(p, bg=T.BG)
            if upd.info and upd.state in ("available", "error", "downloading", "ready"):
                Button(f, text=f"Ver la versión {upd.info['version']}", icon=G.DOWNLOAD, variant="primary",
                       height=32, command=lambda: (self.close(), upd.open_dialog())).pack()
            else:
                Button(f, text="Buscar versión nueva", icon=G.SYNC, variant="secondary", height=32,
                       command=lambda: (self.close(), upd.check(manual=True))).pack()
            return f
        row(f"ReNova {APP_VERSION}", upd.describe(), app_widget)

        def engine_widget(p):
            f = tk.Frame(p, bg=T.BG)
            Button(f, text="Buscar actualización", icon=G.SYNC, variant="secondary", height=32,
                   command=self._check_update).pack()
            return f
        pend = engine.pending_version()
        desc = f"yt-dlp {engine.version()}" + (f" · {pend} se activa al reiniciar" if pend else "")
        row("Motor de descargas", desc, engine_widget)

        def auto_changed(v):
            st.set("auto_update_engine", v)
            if v:
                upd.check()
        row("Buscar actualizaciones solo",
            "Mantiene el motor al día con YouTube y te avisa cuando sale una versión nueva de ReNova.",
            lambda p: Toggle(p, st.auto_update_engine, command=auto_changed))

        foot = tk.Frame(self, bg=T.BG_2, padx=S(28), pady=S(14))
        foot.pack(side="bottom", fill="x")
        Button(foot, text="Listo", variant="primary", command=self.close).pack(side="right")
        Button(foot, text="Abrir registros", icon=G.FOLDER_OPEN, variant="ghost",
               command=lambda: open_path(str(paths.log_dir()))).pack(side="left")
        tk.Label(foot, text=f"ReNova {APP_VERSION}", font=F.small, fg=T.TEXT_4, bg=T.BG_2).pack(side="left", padx=S(12))
        self.show()

    def _check_update(self):
        self.app.check_engine_update(manual=True)
