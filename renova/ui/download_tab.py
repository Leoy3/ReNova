"""Pestaña Descargar."""

from __future__ import annotations

import tkinter as tk
import webbrowser

from .. import core
from . import theme as T
from .cards import QueueCard
from .dialogs import PlaylistDialog, format_menu_items
from .trim import TrimDialog
from .theme import F, G, S
from .widgets import (Button, Card, PopupMenu, ScrollArea, Segmented, TextInput, Toggle,
                      open_path, reveal_in_explorer, round_rect)

PLACEHOLDER = "Pegá un link de YouTube, Instagram, TikTok, X, Vimeo…  (video o playlist)"


class DownloadTab(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=T.BG)
        self.app = app
        self.settings = app.settings
        self.items: list = []
        self.cards: dict = {}
        self.photos: dict = {}
        self.mgr = core.DownloadManager(self._notify, self.settings)
        self._last_status: dict = {}
        self._playlist_queue: list = []
        self._playlist_open = False
        self._clip_seen = None
        self._clip_job = None
        self._build()

    # ── Construcción ───────────────────────────────────────────────────────
    def _build(self):
        pad = S(24)
        card = Card(self, padding=14, bg=T.BG)
        card.pack(fill="x", padx=pad, pady=(S(18), S(8)))
        self.input_card = card
        b = card.body

        row1 = tk.Frame(b, bg=T.SURFACE)
        row1.pack(fill="x")
        self.url = TextInput(row1, placeholder=PLACEHOLDER, icon=G.LINK, height=46, bg=T.SURFACE,
                             fill=T.BG_2, on_submit=self.add_from_input, on_paste=self._on_paste)
        self.url.add_right(Button(self.url, text="Pegar", icon=G.PASTE, variant="ghost", height=34, padx=10,
                                  bg=T.BG_2, command=self.url.paste,
                                  tooltip="Pegar desde el portapapeles y agregar (Ctrl+V)"))
        self.url.pack(side="left", fill="x", expand=True)
        Button(row1, text="Agregar", icon=G.ADD, variant="primary", height=46, padx=18, bg=T.SURFACE,
               command=self.add_from_input).pack(side="left", padx=(S(10), 0))

        row2 = tk.Frame(b, bg=T.SURFACE)
        row2.pack(fill="x", pady=(S(12), 0))
        self.fmt_seg = Segmented(row2, [("video", "Video", G.VIDEO), ("audio", "Solo audio", G.MUSIC),
                                        ("mute", "Sin audio", G.MUTE)],
                                 self.settings.fmt, command=self._global_fmt, bg=T.SURFACE)
        self.fmt_seg.pack(side="left")
        self.q_btn = Button(row2, text="", trailing=G.CHEVRON_DOWN, variant="secondary", height=36,
                            padx=12, bg=T.SURFACE, command=self._global_quality_menu,
                            tooltip="Calidad para los links nuevos y los que todavía no empezaron")
        self.q_btn.pack(side="left", padx=(S(8), 0))
        self.auto_toggle = Toggle(row2, self.settings.auto_start, label="Descargar al agregar", bg=T.SURFACE,
                                  command=self._set_auto,
                                  tooltip="Si está apagado, los links quedan en la cola para recortarlos o "
                                          "cambiar la calidad antes de descargar.")
        self.auto_toggle.pack(side="right")
        self.compat = tk.Canvas(row2, bg=T.SURFACE, highlightthickness=0, height=S(26), width=10)
        self.compat.pack(side="right", padx=(0, S(16)))
        self.hint = tk.Label(b, text="", font=F.small, fg=T.WARN, bg=T.SURFACE, anchor="w", justify="left")
        self._refresh_options()

        # Banner de portapapeles
        self.clip_card = Card(self, padding=10, radius=12, fill=T.SURFACE_2, outline=T.BORDER, bg=T.BG)
        cb = self.clip_card.body
        tk.Label(cb, text=G.PASTE, font=F.icon, fg=T.ACCENT, bg=T.SURFACE_2).pack(side="left", padx=(S(4), S(10)))
        self.clip_lbl = tk.Label(cb, text="", font=F.body, fg=T.TEXT, bg=T.SURFACE_2, anchor="w")
        self.clip_lbl.pack(side="left", fill="x", expand=True)
        Button(cb, icon=G.CLOSE, variant="ghost", height=30, bg=T.SURFACE_2,
               command=self._hide_clip, tooltip="Descartar").pack(side="right")
        self.clip_add = Button(cb, text="Agregar", icon=G.ADD, variant="primary", height=30, padx=12,
                               bg=T.SURFACE_2, command=self._accept_clip)
        self.clip_add.pack(side="right", padx=(0, S(6)))
        self._clip_urls: list = []

        # Encabezado de la cola
        self.hdr = tk.Frame(self, bg=T.BG)
        self.hdr.pack(fill="x", padx=pad, pady=(S(10), S(8)))
        tk.Label(self.hdr, text="Cola de descargas", font=F.h3, fg=T.TEXT, bg=T.BG).pack(side="left")
        self.count_lbl = tk.Label(self.hdr, text="", font=F.small_b, fg=T.TEXT_3, bg=T.BG)
        self.count_lbl.pack(side="left", padx=(S(10), 0))
        self.more_btn = Button(self.hdr, icon=G.MORE, variant="ghost", height=32, command=self._more_menu,
                               tooltip="Más acciones")
        self.more_btn.pack(side="right")
        self.dl_all = Button(self.hdr, text="Descargar todo", icon=G.DOWNLOAD, variant="primary", height=32,
                             padx=14, command=self.start_all, tooltip="Ctrl+Enter")
        self.dl_all.pack(side="right", padx=(0, S(6)))
        self.clear_btn = Button(self.hdr, text="Limpiar terminados", variant="ghost", height=32, padx=12,
                                command=self.clear_finished)
        self.clear_btn.pack(side="right", padx=(0, S(6)))

        self.scroll = ScrollArea(self, bg=T.BG)
        self.scroll.pack(fill="both", expand=True, padx=(pad, S(10)), pady=(0, S(6)))
        self.empty = tk.Canvas(self.scroll, bg=T.BG, highlightthickness=0, bd=0)
        self.empty.bind("<Configure>", lambda e: self._draw_empty())
        self._update_header()

        for w in (self, self.scroll.canvas, self.scroll.inner, self.empty, card, card.body):
            self.app.register_drop(w, self._on_drop)

    def _draw_empty(self):
        cv = self.empty
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 50:
            return
        cx, cy = w / 2, max(S(110), h * 0.36)
        r = S(36)
        round_rect(cv, cx - r, cy - r, cx + r, cy + r, r, T.SURFACE, T.BORDER_SOFT, T.BG)
        cv.create_text(cx, cy + S(2), text=G.DOWNLOAD, font=F.icon_lg, fill=T.ACCENT)
        cv.create_text(cx, cy + S(64), text="Tu cola está vacía", font=F.h2, fill=T.TEXT)
        cv.create_text(cx, cy + S(92), text="Copiá un link y pegalo acá: se analiza solo y podés elegir calidad,",
                       font=F.body, fill=T.TEXT_3)
        cv.create_text(cx, cy + S(112), text="recortar un fragmento o elegir videos de una playlist.",
                       font=F.body, fill=T.TEXT_3)
        keys = [("Ctrl + V", "pegar y agregar"), ("Ctrl + Enter", "descargar todo"),
                ("Arrastrar", "links o archivos")]
        widths = [F.mono.measure(k) + S(16) + S(8) + F.small.measure(d) for k, d in keys]
        x = cx - (sum(widths) + S(24) * (len(keys) - 1)) / 2
        y = cy + S(150)
        for (k, d), kw in zip(keys, widths):
            bw = F.mono.measure(k) + S(16)
            round_rect(cv, x, y - S(12), x + bw, y + S(12), S(6), T.SURFACE_2, T.BORDER, T.BG)
            cv.create_text(x + bw / 2, y, text=k, font=F.mono, fill=T.TEXT_2)
            cv.create_text(x + bw + S(8), y, text=d, font=F.small, fill=T.TEXT_3, anchor="w")
            x += kw + S(24)

    # ── Opciones globales ──────────────────────────────────────────────────
    def _current_quality(self, fmt=None):
        fmt = fmt or self.settings.fmt
        return self.settings.quality_audio if fmt == "audio" else self.settings.quality_video

    def _refresh_options(self):
        fmt = self.settings.fmt
        q = self._current_quality()
        self.q_btn.configure_button(text=("Formato: " if fmt == "audio" else "Calidad: ") +
                                    core.quality_label(fmt, q))
        cv = self.compat
        cv.delete("all")
        if fmt == "audio":
            text, color, icon = core.quality_label("audio", q).split(" · ")[0] + " listo para editar", T.OK, G.CHECK
        else:
            text, color, icon = "H.264 + AAC · compatible con Premiere", T.OK, G.CHECK
        tw = F.small_b.measure(text) + S(36)
        cv.configure(width=tw)
        round_rect(cv, 0, 0, tw, S(26), S(13), T.OK_SOFT, None, T.SURFACE)
        cv.create_text(S(12), S(14), text=icon, font=F.icon_sm, fill=color, anchor="w")
        cv.create_text(S(28), S(13), text=text, font=F.small_b, fill=color, anchor="w")
        slow = fmt != "audio" and q in core.SLOW_QUALITIES
        if slow:
            self.hint.configure(text="ⓘ  En YouTube, 1440p y 4K sólo existen en VP9/AV1: se descargan y se "
                                     "convierten a H.264 (tarda más). 1080p es la máxima calidad directa.")
            self.hint.pack(fill="x", pady=(S(10), 0))
        else:
            self.hint.pack_forget()

    def _global_fmt(self, fmt):
        self.settings.set("fmt", fmt)
        self._refresh_options()
        self._apply_global_to_pending()

    def _global_quality_menu(self):
        fmt = self.settings.fmt
        q = self._current_quality()

        def pick(f, quality, type_changed):
            if type_changed:
                self.fmt_seg.set(f)
                self._global_fmt(f)
                return
            self.settings.set("quality_audio" if f == "audio" else "quality_video", quality)
            self._refresh_options()
            self._apply_global_to_pending()
        items = format_menu_items(fmt, q, pick)
        items = items[items.index({"separator": True}) + 1:]
        b = self.q_btn
        PopupMenu(self, items, b.winfo_rootx(), b.winfo_rooty() + b.winfo_height() + S(4), min_width=260)

    def _apply_global_to_pending(self):
        fmt = self.settings.fmt
        q = self._current_quality()
        n = 0
        for it in self.items:
            if it.status in ("ready", "analyzing") and not it.custom_format and (it.fmt, it.quality) != (fmt, q):
                it.fmt, it.quality = fmt, q
                n += 1
                self._refresh(it)
        if n:
            self.app.toast.show(f"Formato aplicado a {n} {'elemento' if n == 1 else 'elementos'} de la cola", "info")

    def _set_auto(self, v):
        self.settings.set("auto_start", v)

    def sync_auto_toggle(self):
        self.auto_toggle.set(self.settings.auto_start)

    # ── Agregar links ──────────────────────────────────────────────────────
    def _on_paste(self, text, current):
        urls = core.extract_urls(text)
        if urls and not current.strip():
            self.add_urls(urls)
            return True
        return False

    def add_from_input(self):
        text = self.url.get()
        urls = core.extract_urls(text)
        if not urls:
            if text.strip():
                self.app.toast.show("Eso no parece un link. Copiá la dirección completa (https://…)", "warn")
            self.url.focus()
            return
        self.url.set("")
        self.add_urls(urls)

    def paste_global(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            text = ""
        urls = core.extract_urls(text)
        if urls:
            self.add_urls(urls)
        else:
            self.app.toast.show("No hay un link en el portapapeles", "warn")

    def add_urls(self, urls: list):
        known = {getattr(it, "input_url", it.url) for it in self.items if not it.finished} | \
                {it.url for it in self.items if not it.finished}
        added = []
        skipped = 0
        for u in urls:
            if u in known:
                skipped += 1
                continue
            it = core.DownloadItem(u, self.settings.fmt, self._current_quality())
            it.input_url = u
            it.title = u.split("://", 1)[-1].removeprefix("www.")
            it.auto_start = self.settings.auto_start
            added.append(it)
            known.add(u)
        if added:
            self._insert(added, 0)
            for it in added:
                self.mgr.analyze(it)
            self.scroll.canvas.yview_moveto(0)
        if skipped and not added:
            self.app.toast.show("Ese link ya está en la cola", "info")
        self._hide_clip()

    # ── Portapapeles ───────────────────────────────────────────────────────
    def check_clipboard(self):
        if not self.settings.clipboard_detect:
            return
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return
        key = (text or "").strip()
        if not key or key == self._clip_seen:
            return
        self._clip_seen = key
        urls = core.extract_urls(key)
        if not urls or len(key) > 4000:
            return
        known = {getattr(it, "input_url", it.url) for it in self.items} | {it.url for it in self.items}
        new = [u for u in urls if u not in known]
        if not new:
            return
        self._clip_urls = new
        txt = (f"Link copiado: {core.domain_of(new[0])}{T.ellipsize(F.body, new[0].split(core.domain_of(new[0]), 1)[-1], S(380))}"
               if len(new) == 1 else f"{len(new)} links copiados en el portapapeles")
        self.clip_lbl.configure(text=txt)
        self.clip_card.pack(fill="x", padx=S(24), pady=(0, S(4)), after=self.input_card)
        if self._clip_job:
            self.after_cancel(self._clip_job)
        self._clip_job = self.after(15000, self._hide_clip)

    def _accept_clip(self):
        urls = self._clip_urls
        self._hide_clip()
        if urls:
            self.add_urls(urls)

    def _hide_clip(self):
        if self._clip_job:
            self.after_cancel(self._clip_job)
            self._clip_job = None
        self.clip_card.pack_forget()

    def _on_drop(self, data: str, files: list):
        urls = core.extract_urls(data)
        if urls:
            self.add_urls(urls)
        elif files:
            self.app.show_tab("convert")
            self.app.converts.add_files(files)

    # ── Cola: estructura ───────────────────────────────────────────────────
    def _insert(self, items: list, index: int):
        before = self.cards.get(self.items[index].id) if index < len(self.items) else None
        for it in items:
            card = QueueCard(self.scroll.inner, on_context=lambda e, it=it: self._context(it, e),
                             on_double=lambda it=it: self._double(it))
            if before is not None:
                card.pack(fill="x", before=before)
            else:
                card.pack(fill="x")
            self.app.register_drop(card, self._on_drop)
            self.cards[it.id] = card
            self._last_status[it.id] = None
        self.items[index:index] = items
        for it in items:
            self._refresh(it)
        self._update_header()

    def remove(self, it):
        if it.active or it.status == "analyzing":
            it.cancel_evt.set()
            self.mgr.cancel(it)
        card = self.cards.pop(it.id, None)
        if card:
            card.destroy()
        if it in self.items:
            self.items.remove(it)
        self.photos.pop(it.id, None)
        self._update_header()

    def clear_finished(self):
        for it in [i for i in self.items if i.finished]:
            self.remove(it)

    def start_all(self):
        ready = [it for it in self.items if it.status == "ready"]
        for it in ready:
            self.mgr.start(it)
        if not ready:
            self.app.toast.show("No hay elementos listos para descargar", "info")

    def cancel_all(self):
        for it in self.items:
            if it.active:
                self.mgr.cancel(it)

    def _more_menu(self):
        has_active = any(it.active for it in self.items)
        has_failed = any(it.status in ("error", "cancelled") for it in self.items)
        items = [
            {"label": "Reintentar fallidos", "icon": G.REFRESH, "enabled": has_failed,
             "command": lambda: [self.retry(it) for it in list(self.items) if it.status in ("error", "cancelled")]},
            {"label": "Cancelar todo", "icon": G.CANCEL, "enabled": has_active, "command": self.cancel_all},
            {"separator": True},
            {"label": "Vaciar la cola", "icon": G.DELETE, "enabled": bool(self.items),
             "command": lambda: [self.remove(it) for it in list(self.items)]},
        ]
        b = self.more_btn
        PopupMenu(self, items, b.winfo_rootx() + b.winfo_width(), b.winfo_rooty() + b.winfo_height() + S(4),
                  min_width=220, align="right")

    # ── Acciones de ítem ───────────────────────────────────────────────────
    def retry(self, it):
        if it.info is not None or it.duration or it.thumb_url:
            self.mgr.start(it)          # ya tenía metadatos: reintenta la descarga
            return
        import threading               # falló el análisis: se vuelve a analizar
        it.cancel_evt = threading.Event()
        it.status = "analyzing"
        it.error = it.error_detail = ""
        it.auto_start = self.settings.auto_start
        self._refresh(it)
        self.mgr.analyze(it)

    def _format_menu_for(self, it, x=None, y=None):
        def pick(fmt, quality, type_changed):
            it.fmt, it.quality = fmt, quality
            it.custom_format = True
            self._refresh(it)
            if type_changed:
                self.after(10, lambda: self._format_menu_for(it, x, y))
        card = self.cards.get(it.id)
        if x is None and card is not None:
            x, y = card.winfo_pointerx() - S(20), card.winfo_pointery() + S(14)
        PopupMenu(self, format_menu_items(it.fmt, it.quality, pick, it.info), x, y, min_width=280)

    def open_trim(self, it):
        if not (it.duration or it.info or it.status == "ready"):
            return
        if not it.duration and it.info is None:
            self.app.toast.show("Cargando información del video…", "info", ms=1500)

        def apply(rng):
            it.trim = rng
            self._refresh(it)
        TrimDialog(self.app, it, apply)

    def _double(self, it):
        if it.status == "done" and it.filepath:
            reveal_in_explorer(it.filepath)

    def _context(self, it, e):
        items = []
        if it.status == "done" and it.filepath:
            items += [{"label": "Mostrar en carpeta", "icon": G.FOLDER_OPEN, "command": lambda: reveal_in_explorer(it.filepath)},
                      {"label": "Abrir archivo", "icon": G.PLAY, "command": lambda: open_path(it.filepath)},
                      {"label": "Abrir en Recortar", "icon": G.CUT,
                       "command": lambda: self.app.open_in_editor(it.filepath)},
                      {"separator": True}]
        if it.status == "ready":
            items += [{"label": "Descargar", "icon": G.DOWNLOAD, "command": lambda: self.mgr.start(it)},
                      {"label": "Recortar fragmento…", "icon": G.CUT, "command": lambda: self.open_trim(it),
                       "enabled": bool(it.duration)},
                      {"label": "Formato y calidad…", "icon": G.VIDEO,
                       "command": lambda: self._format_menu_for(it, e.x_root, e.y_root)},
                      {"separator": True}]
        if it.status == "playlist":
            items += [{"label": "Elegir videos…", "icon": G.LIST, "command": lambda: self._open_playlist(it)},
                      {"separator": True}]
        if it.active:
            items += [{"label": "Cancelar", "icon": G.CANCEL, "command": lambda: self.mgr.cancel(it)},
                      {"separator": True}]
        if it.status in ("error", "cancelled"):
            items += [{"label": "Reintentar", "icon": G.REFRESH, "command": lambda: self.retry(it)}]
            if it.error_detail:
                items += [{"label": "Copiar detalle del error", "icon": G.COPY,
                           "command": lambda: self._copy(f"{it.url}\n{it.error_detail}")}]
            items += [{"separator": True}]
        items += [{"label": "Copiar link", "icon": G.LINK, "command": lambda: self._copy(it.url)},
                  {"label": "Abrir link en el navegador", "icon": G.GLOBE, "command": lambda: webbrowser.open(it.url)},
                  {"separator": True},
                  {"label": "Quitar de la lista", "icon": G.CLOSE, "command": lambda: self.remove(it)}]
        PopupMenu(self, items, e.x_root, e.y_root, min_width=230)

    def _copy(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.app.toast.show("Copiado al portapapeles", "ok", ms=1800)

    # ── Playlists ──────────────────────────────────────────────────────────
    def _open_playlist(self, it):
        if self._playlist_open:
            if it not in self._playlist_queue:
                self._playlist_queue.append(it)
            return
        if it not in self.items or not it.playlist:
            return
        self._playlist_open = True
        try:
            dlg = PlaylistDialog(self.app, it.playlist)
            self.wait_window(dlg)
            res = dlg.result
        finally:
            self._playlist_open = False
        if it in self.items:
            if res:
                chosen, start = res
                idx = self.items.index(it)
                self.remove(it)
                new = []
                for e in chosen:
                    x = core.DownloadItem(e["url"], it.fmt, it.quality)
                    x.input_url = e["url"]
                    x.title = e.get("title") or e["url"]
                    x.duration = e.get("duration")
                    x.uploader = e.get("uploader") or it.playlist.get("uploader", "")
                    x.thumb_url = e.get("thumb") or ""
                    x.source = it.source
                    x.status = "ready"
                    x.custom_format = it.custom_format
                    new.append(x)
                self._insert(new, min(idx, len(self.items)))
                if start:
                    for x in new:
                        self.mgr.start(x)
                self.app.toast.show(f"{len(new)} {'video agregado' if len(new) == 1 else 'videos agregados'}"
                                    + (" · descargando" if start else " a la cola"), "ok")
            else:
                self.remove(it)
        if self._playlist_queue:
            nxt = self._playlist_queue.pop(0)
            self.after(150, lambda: self._open_playlist(nxt))

    # ── Actualización de estado ────────────────────────────────────────────
    def _notify(self, it):
        self.app.bus.mark(("dl", it.id), lambda it=it: self._refresh(it))

    def _refresh(self, it):
        card = self.cards.get(it.id)
        if card is None:
            return
        prev = self._last_status.get(it.id)
        if prev != it.status:
            self._last_status[it.id] = it.status
            self._on_transition(it, prev)
        if it.thumb_url and it.id not in self.photos:
            self.photos[it.id] = None
            url = it.thumb_url

            def got(photo, it=it):
                self.photos[it.id] = photo
                self._refresh(it)
            self.app.thumbs.get(url, lambda: core.fetch_image(url), S(QueueCard.TW), S(QueueCard.TH), S(8),
                                T.SURFACE, got)
        card.set_vm(self._vm(it))

    def _on_transition(self, it, prev):
        if it.status == "ready" and it.auto_start:
            it.auto_start = False
            self.after(10, lambda: self.mgr.start(it))
        elif it.status == "playlist":
            self.after(50, lambda: self._open_playlist(it))
        elif it.status in ("done", "error") and prev in ("downloading", "processing", "queued"):
            if not any(x.active for x in self.items):
                done = sum(1 for x in self.items if x.status == "done")
                self.app.notify_finished("Descargas terminadas" if done else "Terminó la cola", it)
        if it.status == "error":
            self.app.on_download_error(it)
        self._update_header()

    def _update_header(self):
        n = len(self.items)
        ready = sum(1 for it in self.items if it.status == "ready")
        self.count_lbl.configure(text=f"{n} {'elemento' if n == 1 else 'elementos'}" if n else "")
        self.dl_all.configure_button(text=f"Descargar todo ({ready})" if ready > 1 else "Descargar todo",
                                     enabled=ready > 0)
        self.clear_btn.configure_button(enabled=any(it.finished for it in self.items))
        if n:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.app.update_status()

    def summary(self) -> str:
        c = {}
        for it in self.items:
            c[it.status] = c.get(it.status, 0) + 1
        parts = []
        act = c.get("downloading", 0) + c.get("processing", 0)
        if act:
            parts.append(f"{act} descargando")
        if c.get("queued"):
            parts.append(f"{c['queued']} en cola")
        if c.get("ready"):
            parts.append(f"{c['ready']} {'listo' if c['ready'] == 1 else 'listos'}")
        if c.get("done"):
            parts.append(f"{c['done']} {'terminado' if c['done'] == 1 else 'terminados'}")
        if c.get("error"):
            parts.append(f"{c['error']} con error")
        return " · ".join(parts)

    def active_count(self) -> int:
        return sum(1 for it in self.items if it.active)

    # ── View model ─────────────────────────────────────────────────────────
    def _vm(self, it) -> dict:
        st = it.status
        editable = st in ("ready", "error", "cancelled", "analyzing")
        vm = {"title": it.title or it.url, "thumb": self.photos.get(it.id),
              "placeholder": G.MUSIC if it.fmt == "audio" else (G.LIST if st == "playlist" else G.VIDEO)}
        if it.trim:
            vm["badge"] = (core.fmt_duration(it.trim[1] - it.trim[0]), True)
        elif it.duration:
            vm["badge"] = (core.fmt_duration(it.duration), False)

        src = it.source or core.domain_of(it.url)
        if it.uploader:
            src = f"{src} · {it.uploader}"
        chips = [(T.ellipsize(F.small, src, S(220)), "plain", None, it.url)]
        reenc = None
        if st != "playlist":
            label = core.short_format_label(it.fmt, it.quality)
            if it.fmt != "audio" and it.info:
                pred, reenc = core.predict_video(it.info, it.quality)
                if pred:
                    label = f"MP4 · {pred}" + (" · mudo" if it.fmt == "mute" else "")
            chips.append((label, "chip", (lambda: self._format_menu_for(it)) if editable else None,
                          "Cambiar formato y calidad" if editable else None))
            if it.trim:
                s, e = it.trim
                chips.append((f"{core.fmt_duration(s)} – {core.fmt_duration(e)}", "accent",
                              (lambda: self.open_trim(it)) if editable else None, "Editar recorte"))
            if reenc and st in ("ready", "analyzing", "queued"):
                chips.append(("Se convierte a H.264", "warn", None,
                              "Esta calidad no existe en H.264 en el sitio: se descarga y se recodifica para "
                              "que Premiere la acepte (tarda más). Elegí 1080p o menos para que sea directo."))
        vm["chips"] = chips

        remove = {"icon": G.CLOSE, "tooltip": "Quitar de la lista", "variant": "danger",
                  "command": lambda: self.remove(it)}
        cancel = {"icon": G.CANCEL, "tooltip": "Cancelar", "variant": "danger",
                  "command": lambda: self.mgr.cancel(it)}
        if st == "analyzing":
            vm.update(status=(G.SYNC, "Analizando link…", T.TEXT_2), progress="ind",
                      progress_color=T.TEXT_3, actions=[remove])
        elif st == "playlist":
            n = len(it.playlist["entries"]) if it.playlist else 0
            vm.update(status=(G.LIST, f"Playlist con {n} videos · elegí cuáles descargar", T.ACCENT),
                      actions=[{"label": "Elegir videos", "icon": G.LIST, "variant": "secondary",
                                "command": lambda: self._open_playlist(it)}, remove])
        elif st == "ready":
            can_trim = bool(it.duration) and it.duration > 1
            vm.update(status=("", "Listo para descargar", T.TEXT_3), actions=[
                {"icon": G.CUT, "tooltip": "Recortar: descargar sólo una parte" if can_trim else
                 "No se puede recortar (duración desconocida)", "enabled": can_trim,
                 "command": lambda: self.open_trim(it)},
                {"label": "Descargar", "icon": G.DOWNLOAD, "variant": "primary",
                 "command": lambda: self.mgr.start(it)},
                remove])
        elif st == "queued":
            vm.update(status=(G.HISTORY, "En cola", T.TEXT_2), progress=0.0, actions=[cancel])
        elif st == "downloading":
            p = it.progress
            text = it.stage or "Descargando"
            if p is not None:
                text += f" · {p * 100:.0f}%"
            detail = []
            if it.speed:
                detail.append(f"{core.fmt_bytes(it.speed)}/s")
            if it.total:
                detail.append(f"{core.fmt_bytes(it.downloaded)} / {core.fmt_bytes(it.total)}")
            if it.eta:
                detail.append(core.fmt_eta(it.eta))
            vm.update(status=(G.DOWNLOAD, text, T.ACCENT), detail=" · ".join(detail),
                      progress=p if p is not None else "ind", border=T.ACCENT_SOFT, actions=[cancel])
        elif st == "processing":
            p = it.progress
            text = it.stage or "Procesando"
            if p is not None and p > 0:
                text += f" · {p * 100:.0f}%"
            vm.update(status=(G.SYNC, text, T.WARN), progress=p if p else "ind", progress_color=T.WARN,
                      border=T.WARN_SOFT, actions=[cancel])
        elif st == "done":
            vm.update(status=(G.COMPLETED, f"Descargado · {it.result}" if it.result else "Descargado", T.OK),
                      detail=core.fmt_bytes(it.filesize), status_tip=it.filepath, actions=[
                          {"label": "Mostrar en carpeta", "icon": G.FOLDER_OPEN, "variant": "secondary",
                           "tooltip": it.filepath, "command": lambda: reveal_in_explorer(it.filepath)},
                          {"icon": G.PLAY, "tooltip": "Abrir archivo", "command": lambda: open_path(it.filepath)},
                          remove])
        elif st == "error":
            vm.update(status=(G.ERROR, it.error or "Error", T.ERR), border=T.ERR_SOFT,
                      status_tip=(it.error_detail or it.error)[:400], actions=[
                          {"icon": G.REFRESH, "tooltip": "Reintentar", "command": lambda: self.retry(it)}, remove])
        elif st == "cancelled":
            vm.update(status=(G.CANCEL, "Cancelado", T.TEXT_3), actions=[
                {"icon": G.REFRESH, "tooltip": "Reintentar", "command": lambda: self.retry(it)}, remove])
        return vm
