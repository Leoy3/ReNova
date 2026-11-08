"""Pestaña Convertir: cada archivo muestra el formato detectado y los formatos a los que se puede
convertir, como botones; se elige uno (con sus opciones, si tiene) y se convierte."""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog

from .. import core
from . import theme as T
from .theme import F, G, S
from .widgets import Button, PopupMenu, ScrollArea, Tooltip, open_path, reveal_in_explorer, round_rect

KIND_ICON = {"video": G.MOVIE, "audio": G.MUSIC, "image": G.PHOTO}
KIND_NAME = {"video": "Video", "audio": "Audio", "image": "Imagen"}
KIND_COLORS = {"video": (T.ACCENT_SOFT, T.ACCENT), "audio": (T.OK_SOFT, T.OK), "image": (T.WARN_SOFT, T.WARN)}
SETTING_KEY = {"video": "convert_video", "audio": "convert_audio", "image": "convert_image"}


class ConvertCard(tk.Canvas):
    TW, TH = 128, 72

    def __init__(self, master, tab, item):
        self.H = S(136)
        bg = master.cget("bg")
        super().__init__(master, bg=bg, highlightthickness=0, bd=0, height=self.H, takefocus=0)
        self.tab, self.item = tab, item
        self._regions: list = []
        self._hover_key = None
        self._hover = False
        self._job = None
        self._tip_job = None
        self.bind("<Configure>", lambda e: self.schedule())
        self.bind("<Motion>", self._motion)
        self.bind("<Enter>", lambda e: (setattr(self, "_hover", True), self.schedule()))
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Double-Button-1>", lambda e: item.filepath and not self._hit(e.x, e.y) and
                  reveal_in_explorer(item.filepath))
        self.bind("<Button-3>", lambda e: tab._context(item, e))

    def schedule(self):
        if self._job is None:
            self._job = self.after_idle(self.render)

    def destroy(self):
        Tooltip.hide()
        super().destroy()

    # ── Interacción ────────────────────────────────────────────────────────
    def _hit(self, x, y):
        for r in self._regions:
            if r[0] <= x < r[2] and r[1] <= y < r[3]:
                return r
        return None

    def _leave(self, _e):
        self._hover = False
        self._hover_key = None
        self._cancel_tip()
        self.configure(cursor="arrow")
        self.schedule()

    def _motion(self, e):
        r = self._hit(e.x, e.y)
        key = (r[0], r[1]) if r else None
        if key == self._hover_key:
            return
        self._hover_key = key
        self._cancel_tip()
        clickable = bool(r) and r[4].get("command") and r[4].get("enabled", True)
        self.configure(cursor="hand2" if clickable else "arrow")
        if r and r[4].get("tooltip"):
            x1, y1, x2, _y2, data = r
            self._tip_job = self.after(400, lambda: Tooltip.show(self, data["tooltip"],
                                                                 self.winfo_rootx() + (x1 + x2) // 2,
                                                                 self.winfo_rooty() + y1, above=True))
        self.schedule()

    def _cancel_tip(self):
        if self._tip_job:
            self.after_cancel(self._tip_job)
            self._tip_job = None
        Tooltip.hide()

    def _click(self, e):
        r = self._hit(e.x, e.y)
        if r and r[4].get("command") and r[4].get("enabled", True):
            self._cancel_tip()
            r[4]["command"]()

    def _is_hover(self, x1, y1):
        return self._hover_key == (x1, y1)

    # ── Dibujo ─────────────────────────────────────────────────────────────
    def render(self):
        self._job = None
        if not self.winfo_exists():
            return
        self.delete("all")
        self._regions = []
        it, tab = self.item, self.tab
        w, H = self.winfo_width(), self.H
        if w < 200:
            return
        bg = self["bg"]
        ch = H - S(10)
        st = it.status
        border = {"error": T.ERR_SOFT, "processing": T.WARN_SOFT}.get(st) or (T.BORDER if self._hover else T.BORDER_SOFT)
        round_rect(self, 0, 0, w, ch, S(12), T.SURFACE, border, bg)
        pad = S(14)

        # miniatura
        tw, th = S(self.TW), S(self.TH)
        tx, ty = pad, S(16)
        photo = tab.photos.get(it.id)
        if photo is not None:
            self.create_image(tx, ty, image=photo, anchor="nw")
        else:
            round_rect(self, tx, ty, tx + tw, ty + th, S(8), T.SURFACE_2, None, T.SURFACE)
            self.create_text(tx + tw / 2, ty + th / 2, text=KIND_ICON.get(it.kind, G.VIDEO), font=F.icon_lg,
                             fill=T.TEXT_4)
        if it.duration:
            text = core.fmt_duration(it.duration)
            bw = F.tiny_b.measure(text) + S(10)
            bx2, by2 = tx + tw - S(5), ty + th - S(5)
            self.create_rectangle(bx2 - bw, by2 - S(17), bx2, by2, fill="#0A0808", width=0)
            self.create_text(bx2 - S(5), by2 - S(9), text=text, font=F.tiny_b, fill=T.TEXT, anchor="e")

        # acciones (derecha)
        x = w - pad
        bh = S(34)
        by = S(16)
        for act in reversed(self._actions()):
            label, icon = act.get("label"), act.get("icon")
            bw = (S(14) * 2 + F.body_b.measure(label) + (F.icon_sm.measure(icon) + S(8) if icon else 0)) \
                if label else bh
            x1 = x - bw
            self._draw_action(act, x1, by, x, by + bh)
            self._regions.append((x1, by, x, by + bh, act))
            x = x1 - S(6)
        text_x = tx + tw + S(18)
        right = w - pad

        # 1 · nombre
        self.create_text(text_x, S(25), text=T.ellipsize(F.title, os.path.basename(it.src), x - S(10) - text_x),
                         font=F.title, fill=T.TEXT, anchor="w")

        # 2 · formato detectado
        y2 = S(48)
        fill, fg = KIND_COLORS.get(it.kind, KIND_COLORS["video"])
        badge = f"{KIND_NAME.get(it.kind, 'Archivo').upper()} · {it.fmt}"
        bw = F.tiny_b.measure(badge) + S(14)
        round_rect(self, text_x, y2 - S(10), text_x + bw, y2 + S(10), S(5), fill, None, T.SURFACE)
        self.create_text(text_x + S(7), y2, text=badge, font=F.tiny_b, fill=fg, anchor="w")
        details = it.details or "Analizando el archivo…"
        self.create_text(text_x + bw + S(10), y2, text=T.ellipsize(F.small, details, x - S(10) - text_x - bw - S(10)),
                         font=F.small, fill=T.TEXT_3, anchor="w")

        # 3 · convertir a
        y3a, y3b = S(66), S(94)
        lx = text_x
        editable = st in ("ready", "error", "cancelled") and it.inspected and not it.error.startswith("No se pudo leer")
        self.create_text(lx, (y3a + y3b) / 2, text="Convertir a", font=F.small_b, fill=T.TEXT_3, anchor="w")
        px = lx + F.small_b.measure("Convertir a") + S(12)
        prev_group = None
        for key, label, ext in it.targets() if it.inspected else []:
            group = "audio" if (it.kind == "video" and key in core.AUDIO_ONLY_TARGETS) else "main"
            if prev_group and group != prev_group:
                self.create_line(px + S(3), y3a + S(5), px + S(3), y3b - S(5), fill=T.BORDER)
                px += S(10)
            prev_group = group
            name = ext.upper()
            pw = F.small_b.measure(name) + S(20)
            if px + pw > right:
                break
            sel = key == it.target
            same = it.is_same(key)
            hov = editable and self._is_hover(px, y3a)
            if sel:
                pfill, pfg, pout = (T.ACCENT, T.ACCENT_INK, None) if editable else (T.ACCENT_SOFT, T.ACCENT, None)
            elif not editable:
                pfill, pfg, pout = T.SURFACE, T.TEXT_4, T.BORDER_SOFT
            else:
                pfill, pfg, pout = (T.SURFACE_HI, T.TEXT, T.BORDER) if hov else (T.SURFACE_2, T.TEXT_2, T.BORDER_SOFT)
            round_rect(self, px, y3a, px + pw, y3b, S(7), pfill, pout, T.SURFACE)
            self.create_text(px + pw / 2, (y3a + y3b) / 2, text=name, font=F.small_b, fill=pfg)
            if same and not sel:
                self.create_oval(px + pw - S(8), y3a + S(4), px + pw - S(4), y3a + S(8), fill=T.TEXT_3, width=0)
            tip = label + (" · es el formato que ya tiene" if same else "")
            self._regions.append((px, y3a, px + pw, y3b, {
                "command": (lambda k=key: tab.set_target(it, k)) if editable else None, "tooltip": tip}))
            px += pw + S(6)

        # 4 · estado / opciones
        y4 = S(111)
        icon, text, color = self._status()
        sx = text_x
        if icon:
            self.create_text(sx, y4 + S(1), text=icon, font=F.icon_sm, fill=color, anchor="w")
            sx += F.icon_sm.measure(icon) + S(7)
        detail = core.fmt_bytes(it.filesize) if st == "done" and it.filesize else ""
        tw_ = F.small_b.measure(text)
        self.create_text(sx, y4, text=T.ellipsize(F.small_b, text, right - sx), font=F.small_b, fill=color,
                         anchor="w")
        tip = (it.error_detail or it.error)[:400] if st == "error" else (it.filepath if st == "done" else None)
        if tip:
            self._regions.append((sx, y4 - S(9), sx + tw_, y4 + S(9), {"tooltip": tip}))
        ox = sx + tw_ + S(12)
        if detail:
            self.create_text(ox, y4, text=detail, font=F.mono, fill=T.TEXT_3, anchor="w")
        if st == "ready" and it.target and editable:
            for name, setting, choices in core.TARGET_OPTIONS.get(it.target, []):
                val = it.opts.get(name)
                txt = dict(choices).get(val, str(val))
                cw = F.small_b.measure(txt) + S(16) + F.icon_sm.measure(G.CHEVRON_DOWN) + S(6)
                if ox + cw > right:
                    break
                hov = self._is_hover(ox, y4 - S(11))
                round_rect(self, ox, y4 - S(11), ox + cw, y4 + S(11), S(6), T.SURFACE_HI if hov else T.SURFACE_2,
                           T.BORDER if hov else None, T.SURFACE)
                self.create_text(ox + S(8), y4, text=txt, font=F.small_b, fill=T.TEXT_2, anchor="w")
                self.create_text(ox + cw - S(8), y4 + S(1), text=G.CHEVRON_DOWN, font=F.icon_sm, fill=T.TEXT_3,
                                 anchor="e")
                self._regions.append((ox, y4 - S(11), ox + cw, y4 + S(11), {
                    "command": lambda n=name, s_=setting, c=choices, x_=ox: tab.option_menu(it, n, s_, c, self, x_),
                    "tooltip": None}))
                ox += cw + S(6)
        if st in ("processing", "queued"):
            p = it.progress or 0.0
            py = ch - S(6)
            self.create_rectangle(text_x, py, right, py + S(3), fill=T.SURFACE_HI, width=0)
            if p > 0:
                self.create_rectangle(text_x, py, text_x + (right - text_x) * min(1.0, p), py + S(3), fill=T.WARN,
                                      width=0)

    def _status(self):
        it = self.item
        st = it.status
        short = core.preset_short(it.kind, it.target) if it.target else ""
        if not it.inspected:
            return G.SYNC, "Detectando el formato…", T.TEXT_3
        if st == "ready":
            if not it.target:
                return G.INFO, "Elegí a qué formato convertirlo", T.TEXT_2
            extra = ""
            if it.target in ("mp4_h264", "mov_h264", "mkv_h264") and it.premiere_ok:
                extra = " (sin recodificar: ya es H.264)"
            return G.CHECK, f"Listo para convertir a {short}{extra}", T.TEXT_2
        if st == "queued":
            return G.HISTORY, "En cola", T.TEXT_2
        if st == "processing":
            text = it.stage or "Convirtiendo"
            if it.progress:
                text += f" · {it.progress * 100:.0f}%"
            return G.SYNC, text, T.WARN
        if st == "done":
            return G.COMPLETED, f"Convertido a {it.result}", T.OK
        if st == "error":
            return G.ERROR, it.error or "Error", T.ERR
        return G.CANCEL, "Cancelado", T.TEXT_3

    def _actions(self):
        it, tab = self.item, self.tab
        st = it.status
        remove = {"icon": G.CLOSE, "tooltip": "Quitar de la lista", "variant": "danger",
                  "command": lambda: tab.remove(it)}
        if st == "ready":
            return [{"label": "Convertir", "icon": G.SYNC, "variant": "primary", "enabled": bool(it.target) and it.inspected,
                     "tooltip": None if it.target else "Primero elegí el formato",
                     "command": lambda: tab.mgr.start(it)}, remove]
        if st in ("queued", "processing"):
            return [{"label": "Cancelar", "icon": G.CANCEL, "variant": "secondary", "command": lambda: tab.mgr.cancel(it)}]
        if st == "done":
            return [{"label": "Mostrar en carpeta", "icon": G.FOLDER_OPEN, "variant": "secondary",
                     "tooltip": it.filepath, "command": lambda: reveal_in_explorer(it.filepath)},
                    {"icon": G.PLAY, "tooltip": "Abrir archivo", "command": lambda: open_path(it.filepath)}, remove]
        can_retry = bool(it.target) and it.inspected and not it.error.startswith("No se pudo leer")
        acts = [{"icon": G.REFRESH, "tooltip": "Reintentar", "command": lambda: tab.mgr.start(it)}] if can_retry else []
        return acts + [remove]

    def _draw_action(self, act, x1, y1, x2, y2):
        hov = self._is_hover(x1, y1)
        variant = act.get("variant", "ghost")
        enabled = act.get("enabled", True)
        if not enabled:
            fill, fg, outline = T.SURFACE_2, T.TEXT_4, T.BORDER_SOFT
        elif variant == "primary":
            fill, fg, outline = (T.ACCENT_HI if hov else T.ACCENT), T.ACCENT_INK, None
        elif variant == "secondary":
            fill, fg, outline = (T.SURFACE_HI if hov else T.SURFACE_2), T.TEXT, T.BORDER
        elif variant == "danger":
            fill, fg, outline = (T.ERR_SOFT if hov else None), (T.ERR if hov else T.TEXT_3), None
        else:
            fill, fg, outline = (T.SURFACE_HI if hov else None), (T.TEXT if hov else T.TEXT_2), None
        if fill or outline:
            round_rect(self, x1, y1, x2, y2, S(8), fill or T.SURFACE, outline, T.SURFACE)
        icon, label = act.get("icon"), act.get("label")
        cy = (y1 + y2) / 2
        if label:
            iw = F.icon_sm.measure(icon) + S(8) if icon else 0
            sx = (x1 + x2 - iw - F.body_b.measure(label)) / 2
            if icon:
                self.create_text(sx, cy + S(1), text=icon, font=F.icon_sm, fill=fg, anchor="w")
            self.create_text(sx + iw, cy, text=label, font=F.body_b, fill=fg, anchor="w")
        else:
            self.create_text((x1 + x2) / 2, cy + S(1), text=icon, font=F.icon, fill=fg)


class ConvertTab(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=T.BG)
        self.app = app
        self.settings = app.settings
        self.items: list = []
        self.cards: dict = {}
        self.photos: dict = {}
        self.mgr = core.ConvertManager(self._notify, self.settings)
        self._last_status: dict = {}
        self._drop_hover = False
        self._build()

    def _build(self):
        pad = S(24)
        self.drop = tk.Canvas(self, bg=T.BG, highlightthickness=0, bd=0, height=S(112), cursor="hand2")
        self.drop.pack(fill="x", padx=pad, pady=(S(18), S(10)))
        self.drop.bind("<Configure>", lambda e: self._draw_drop())
        self.drop.bind("<Enter>", lambda e: self._set_drop_hover(True))
        self.drop.bind("<Leave>", lambda e: self._set_drop_hover(False))
        self.drop.bind("<ButtonRelease-1>", lambda e: self.browse())

        hdr = tk.Frame(self, bg=T.BG)
        hdr.pack(fill="x", padx=pad, pady=(S(6), S(8)))
        tk.Label(hdr, text="Archivos", font=F.h3, fg=T.TEXT, bg=T.BG).pack(side="left")
        self.count_lbl = tk.Label(hdr, text="", font=F.small_b, fg=T.TEXT_3, bg=T.BG)
        self.count_lbl.pack(side="left", padx=(S(10), 0))
        self.conv_all = Button(hdr, text="Convertir todo", icon=G.SYNC, variant="primary", height=32, padx=14,
                               command=self.start_all)
        self.conv_all.pack(side="right")
        self.clear_btn = Button(hdr, text="Limpiar terminados", variant="ghost", height=32, padx=12,
                                command=self.clear_finished)
        self.clear_btn.pack(side="right", padx=(0, S(6)))
        self.all_btn = Button(hdr, text="Todos a…", trailing=G.CHEVRON_DOWN, variant="outline", height=32, padx=12,
                              font=F.small_b, command=self._all_menu,
                              tooltip="Elegir el mismo formato para todos los archivos")
        self.all_btn.pack(side="right", padx=(0, S(6)))

        self.scroll = ScrollArea(self, bg=T.BG)
        self.scroll.pack(fill="both", expand=True, padx=(pad, S(10)), pady=(0, S(6)))
        self.empty = tk.Canvas(self.scroll, bg=T.BG, highlightthickness=0, bd=0)
        self.empty.bind("<Configure>", lambda e: self._draw_empty())
        self.app.register_drop(self, self._on_drop, tree=True)
        self._update_header()

    def _set_drop_hover(self, v):
        self._drop_hover = v
        self._draw_drop()

    def _draw_drop(self):
        cv = self.drop
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 50:
            return
        hov = self._drop_hover
        round_rect(cv, 0, 0, w, h, S(14), T.SURFACE_2 if hov else T.SURFACE, None, T.BG)
        inset = S(8)
        cv.create_rectangle(inset, inset, w - inset, h - inset, outline=T.ACCENT if hov else T.BORDER,
                            dash=(6, 4), width=1)
        cx, cy = w / 2, h / 2
        if self.items:  # con archivos en la lista, la zona se achica a una franja
            label = "Arrastrá más archivos acá o hacé clic para agregarlos"
            tw = F.body_b.measure(label) + F.icon.measure(G.ADD) + S(10)
            cv.create_text(cx - tw / 2, cy + S(1), text=G.ADD, font=F.icon, fill=T.ACCENT, anchor="w")
            cv.create_text(cx + tw / 2, cy, text=label, font=F.body_b, fill=T.TEXT_2, anchor="e")
            return
        cv.create_text(cx, cy - S(20), text=G.UPLOAD, font=F.icon_lg, fill=T.ACCENT)
        cv.create_text(cx, cy + S(10), text="Arrastrá archivos acá o hacé clic para elegirlos", font=F.h3,
                       fill=T.TEXT)
        cv.create_text(cx, cy + S(32), text="Detectamos el formato de cada uno y te mostramos a qué lo podés "
                                            "convertir · videos, audios e imágenes",
                       font=F.small, fill=T.TEXT_3)

    def _draw_empty(self):
        cv = self.empty
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 50:
            return
        cv.create_text(w / 2, min(S(60), h / 2), text="Todavía no agregaste archivos", font=F.body, fill=T.TEXT_4)

    # ── Agregar ────────────────────────────────────────────────────────────
    def browse(self):
        paths = filedialog.askopenfilenames(
            parent=self, title="Elegir archivos para convertir",
            filetypes=[("Todos los compatibles", "*.mp4 *.mkv *.mov *.avi *.webm *.flv *.wmv *.m4v *.ts *.mts "
                                                 "*.m2ts *.3gp *.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma "
                                                 "*.aiff *.aif *.jpg *.jpeg *.png *.gif *.bmp *.webp *.tif *.tiff "
                                                 "*.ico *.avif"),
                       ("Todos los archivos", "*.*")])
        if paths:
            self.add_files(list(paths))

    def _on_drop(self, data, files):
        if files:
            self.add_files(files)

    def add_files(self, files: list):
        new = []
        for p in files:
            p = os.path.normpath(p)
            if not os.path.isfile(p):
                continue
            new.append(core.ConvertItem(p, None))
        if not new:
            return
        before = self.cards.get(self.items[0].id) if self.items else None
        for it in new:
            card = ConvertCard(self.scroll.inner, self, it)
            card.pack(fill="x", before=before) if before is not None else card.pack(fill="x")
            self.app.register_drop(card, self._on_drop)
            self.cards[it.id] = card
            self._last_status[it.id] = None
            self.mgr.inspect(it, self._thumb_ready)
        self.items[0:0] = new
        for it in new:
            self._refresh(it)
        self._update_header()
        self.scroll.canvas.yview_moveto(0)

    def _thumb_ready(self, it, img):
        def put():
            self.photos[it.id] = self.app.thumbs.put(("file", it.src), img, S(ConvertCard.TW), S(ConvertCard.TH),
                                                     S(8), T.SURFACE)
            self._refresh(it)
        self.app.bus.call(put)

    # ── Formato de salida ──────────────────────────────────────────────────
    def _default_target(self, it):
        key = self.settings.get(SETTING_KEY.get(it.kind, "convert_video"))
        valid = [k for k, _l, _e in it.targets()]
        if key in valid and not it.is_same(key):
            return key
        return None

    def set_target(self, it, key, remember=True):
        if it.status not in ("ready", "error", "cancelled"):
            return
        it.target = key
        it.opts = {name: self.settings.get(setting) for name, setting, _c in core.TARGET_OPTIONS.get(key, [])}
        if it.status in ("error", "cancelled") and it.inspected and not it.error.startswith("No se pudo leer"):
            it.status = "ready"
            it.error = ""
        if remember:
            self.settings.set(SETTING_KEY.get(it.kind, "convert_video"), key)
        self._refresh(it)
        self._update_header()

    def option_menu(self, it, name, setting, choices, card, x):
        def pick(v):
            it.opts[name] = v
            self.settings.set(setting, v)
            self._refresh(it)
        items = [{"label": label, "checked": it.opts.get(name) == v, "command": lambda v=v: pick(v)}
                 for v, label in choices]
        PopupMenu(self, items, card.winfo_rootx() + int(x), card.winfo_rooty() + S(124), min_width=160)

    def _all_menu(self):
        kinds = []
        for it in self.items:
            if it.inspected and it.status in ("ready", "error", "cancelled") and it.kind not in kinds:
                kinds.append(it.kind)
        if not kinds:
            self.app.toast.show("Agregá archivos primero", "info", ms=2000)
            return
        items = []
        for kind in kinds:
            sample = next(i for i in self.items if i.kind == kind and i.inspected)
            if len(kinds) > 1:
                items.append({"header": {"video": "Videos", "audio": "Audios", "image": "Imágenes"}[kind]})
            for key, label, _ext in core.presets_for(kind):
                items.append({"label": label, "command": lambda k=key, kd=kind: self._set_all(kd, k),
                              "enabled": key in [x for x, _l, _e in sample.targets()] or kind != "video"})
        b = self.all_btn
        PopupMenu(self, items, b.winfo_rootx(), b.winfo_rooty() + b.winfo_height() + S(4), min_width=260)

    def _set_all(self, kind, key):
        n = 0
        for it in self.items:
            if it.kind == kind and it.inspected and it.status in ("ready", "error", "cancelled") and \
                    key in [k for k, _l, _e in it.targets()]:
                self.set_target(it, key, remember=False)
                n += 1
        self.settings.set(SETTING_KEY.get(kind, "convert_video"), key)
        if n:
            self.app.toast.show(f"{n} {'archivo' if n == 1 else 'archivos'} → {core.preset_short(kind, key)}", "ok",
                                ms=1800)

    # ── Acciones ───────────────────────────────────────────────────────────
    def start_all(self):
        pending = [it for it in self.items if it.status == "ready" and it.inspected]
        missing = [it for it in pending if not it.target]
        for it in pending:
            if it.target:
                self.mgr.start(it)
        if missing:
            self.app.toast.show(f"{len(missing)} {'archivo no tiene' if len(missing) == 1 else 'archivos no tienen'}"
                                " formato elegido", "warn", ms=3000)

    def remove(self, it):
        if it.active:
            self.mgr.cancel(it)
        card = self.cards.pop(it.id, None)
        if card:
            card.destroy()
        if it in self.items:
            self.items.remove(it)
        self._update_header()

    def clear_finished(self):
        for it in [i for i in self.items if i.finished]:
            self.remove(it)

    def cancel_all(self):
        for it in self.items:
            if it.active:
                self.mgr.cancel(it)

    def _context(self, it, e):
        items = []
        if it.status == "done" and it.filepath:
            items += [{"label": "Mostrar en carpeta", "icon": G.FOLDER_OPEN, "command": lambda: reveal_in_explorer(it.filepath)},
                      {"label": "Abrir archivo", "icon": G.PLAY, "command": lambda: open_path(it.filepath)},
                      {"label": "Abrir resultado en Recortar", "icon": G.CUT, "enabled": it.kind != "image",
                       "command": lambda: self.app.open_in_editor(it.filepath)},
                      {"separator": True}]
        if it.status in ("ready", "error", "cancelled") and it.target and it.inspected:
            items += [{"label": "Convertir", "icon": G.SYNC, "command": lambda: self.mgr.start(it)},
                      {"separator": True}]
        if it.active:
            items += [{"label": "Cancelar", "icon": G.CANCEL, "command": lambda: self.mgr.cancel(it)},
                      {"separator": True}]
        if it.kind != "image":
            items.append({"label": "Abrir original en Recortar", "icon": G.CUT,
                          "command": lambda: self.app.open_in_editor(it.src)})
        items += [{"label": "Mostrar original", "icon": G.FOLDER, "command": lambda: reveal_in_explorer(it.src)},
                  {"label": "Quitar de la lista", "icon": G.CLOSE, "command": lambda: self.remove(it)}]
        PopupMenu(self, items, e.x_root, e.y_root, min_width=220)

    # ── Estado ─────────────────────────────────────────────────────────────
    def _notify(self, it):
        self.app.bus.mark(("cv", it.id), lambda it=it: self._refresh(it))

    def _refresh(self, it):
        card = self.cards.get(it.id)
        if card is None:
            return
        if it.inspected and not getattr(it, "_defaulted", False):
            it._defaulted = True
            if it.status == "ready":
                key = self._default_target(it)
                if key:
                    self.set_target(it, key, remember=False)
        prev = self._last_status.get(it.id)
        if prev != it.status:
            self._last_status[it.id] = it.status
            if it.status in ("done", "error") and prev in ("processing", "queued") and \
                    not any(x.active for x in self.items):
                self.app.notify_finished("Conversiones terminadas", it)
            self._update_header()
        card.schedule()

    def _update_header(self):
        n = len(self.items)
        ready = sum(1 for it in self.items if it.status == "ready" and it.target and it.inspected)
        self.count_lbl.configure(text=f"{n} {'archivo' if n == 1 else 'archivos'}" if n else "")
        self.conv_all.configure_button(text=f"Convertir todo ({ready})" if ready > 1 else "Convertir todo",
                                       enabled=ready > 0)
        self.clear_btn.configure_button(enabled=any(it.finished for it in self.items))
        self.all_btn.configure_button(enabled=n > 1)
        if n:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0, rely=0, relwidth=1, relheight=1)
        h = S(52) if n else S(112)
        if int(self.drop["height"]) != h:
            self.drop.configure(height=h)
        self._draw_drop()
        self.app.update_status()

    def summary(self) -> str:
        act = sum(1 for it in self.items if it.active)
        done = sum(1 for it in self.items if it.status == "done")
        ready = sum(1 for it in self.items if it.status == "ready" and it.target)
        choose = sum(1 for it in self.items if it.status == "ready" and it.inspected and not it.target)
        parts = []
        if act:
            parts.append(f"{act} convirtiendo")
        if ready:
            parts.append(f"{ready} {'listo' if ready == 1 else 'listos'}")
        if choose:
            parts.append(f"{choose} sin formato elegido")
        if done:
            parts.append(f"{done} {'terminado' if done == 1 else 'terminados'}")
        return " · ".join(parts)

    def active_count(self) -> int:
        return sum(1 for it in self.items if it.active)
