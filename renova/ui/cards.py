"""Tarjeta de la cola, dibujada en un único Canvas (rápido con cientos de ítems).

Se alimenta con un 'view model' (dict) que arma cada pestaña:
  title, chips[(text, style, callback, tooltip)], status(icon, text, color),
  detail, progress (None | float | 'ind'), actions[dict], thumb, badge, placeholder."""

from __future__ import annotations

import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor

from . import theme as T
from .theme import F, G, S
from .widgets import Tooltip, round_rect, rounded_photo

CHIP_STYLES = {
    "chip": (T.SURFACE_2, T.TEXT_2),
    "accent": (T.ACCENT_SOFT, T.ACCENT),
    "warn": (T.WARN_SOFT, T.WARN),
    "ok": (T.OK_SOFT, T.OK),
}


class ThumbLoader:
    """Descarga y prepara miniaturas en segundo plano (PhotoImage en el hilo de UI)."""

    def __init__(self, bus):
        self.bus = bus
        self.pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="thumb")
        self.cache: dict = {}
        self.pending: set = set()
        self.lock = threading.Lock()

    def get(self, key, loader, w, h, radius, bg, callback):
        """loader(): devuelve PIL.Image (corre en hilo). callback(photo) en hilo UI."""
        ck = (key, w, h, radius, bg)
        if ck in self.cache:
            callback(self.cache[ck])
            return
        with self.lock:
            if ck in self.pending:
                return
            self.pending.add(ck)

        def run():
            try:
                img = loader()
            except Exception:
                img = None

            def deliver():
                with self.lock:
                    self.pending.discard(ck)
                if img is None:
                    return
                photo = rounded_photo(img, w, h, radius, bg)
                self.cache[ck] = photo
                callback(photo)
            self.bus.call(deliver)
        self.pool.submit(run)

    def put(self, key, img, w, h, radius, bg):
        photo = rounded_photo(img, w, h, radius, bg)
        self.cache[(key, w, h, radius, bg)] = photo
        return photo


class QueueCard(tk.Canvas):
    TW, TH = 128, 72

    def __init__(self, master, on_context=None, on_double=None):
        self.H = S(96)
        bg = master.cget("bg")
        super().__init__(master, bg=bg, highlightthickness=0, bd=0, height=self.H, takefocus=0)
        self.vm: dict = {}
        self.on_context = on_context
        self.on_double = on_double
        self._regions: list = []
        self._hover_region = None
        self._hover = False
        self._job = None
        self._tip_job = None
        self._ind_job = None
        self._ind_pos = 0.0
        self._bar = None
        self.bind("<Configure>", lambda e: self.schedule())
        self.bind("<Motion>", self._motion)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Double-Button-1>", lambda e: self.on_double and not self._hit(e.x, e.y) and self.on_double())
        self.bind("<Button-3>", lambda e: self.on_context and self.on_context(e))

    # ── API ────────────────────────────────────────────────────────────────
    def set_vm(self, vm: dict):
        if vm == self.vm:
            return
        self.vm = vm
        self.schedule()

    def schedule(self):
        if self._job is None:
            self._job = self.after_idle(self.render)

    def destroy(self):
        self._stop_ind()
        Tooltip.hide()
        super().destroy()

    # ── Interacción ────────────────────────────────────────────────────────
    def _hit(self, x, y):
        for r in self._regions:
            x1, y1, x2, y2, data = r
            if x1 <= x < x2 and y1 <= y < y2:
                return r
        return None

    def _enter(self, _e):
        self._hover = True
        self.schedule()

    def _leave(self, _e):
        self._hover = False
        self._hover_region = None
        self._cancel_tip()
        self.configure(cursor="arrow")
        self.schedule()

    def _motion(self, e):
        r = self._hit(e.x, e.y)
        key = (r[0], r[1]) if r else None
        if key != self._hover_region:
            self._hover_region = key
            self._cancel_tip()
            enabled = bool(r) and r[4].get("enabled", True) and r[4].get("command")
            self.configure(cursor="hand2" if enabled else "arrow")
            if r and r[4].get("tooltip"):
                x1, y1, x2, y2, data = r
                self._tip_job = self.after(450, lambda: Tooltip.show(
                    self, data["tooltip"], self.winfo_rootx() + (x1 + x2) // 2,
                    self.winfo_rooty() + y1, above=True))
            self.schedule()

    def _cancel_tip(self):
        if self._tip_job:
            self.after_cancel(self._tip_job)
            self._tip_job = None
        Tooltip.hide()

    def _click(self, e):
        r = self._hit(e.x, e.y)
        if r and r[4].get("enabled", True) and r[4].get("command"):
            self._cancel_tip()
            r[4]["command"]()

    # ── Dibujo ─────────────────────────────────────────────────────────────
    def _is_hover(self, x1, y1):
        return self._hover_region == (x1, y1)

    def render(self):
        self._job = None
        if not self.winfo_exists():
            return
        self.delete("all")
        self._regions = []
        self._bar = None
        w, h = self.winfo_width(), self.H
        if w < 120 or not self.vm:
            return
        vm = self.vm
        bg = self["bg"]
        pad = S(12)
        border = vm.get("border") or (T.BORDER if self._hover else T.BORDER_SOFT)
        round_rect(self, 0, 0, w, h - S(8), S(12), T.SURFACE, border, bg)
        ch = h - S(8)  # alto visible de la tarjeta (el resto es separación)

        # Miniatura
        tw, th = S(self.TW), S(self.TH)
        tx, ty = pad, (ch - th) // 2
        if vm.get("thumb") is not None:
            self.create_image(tx, ty, image=vm["thumb"], anchor="nw")
        else:
            round_rect(self, tx, ty, tx + tw, ty + th, S(8), T.SURFACE_2, None, T.SURFACE)
            self.create_text(tx + tw / 2, ty + th / 2, text=vm.get("placeholder", G.VIDEO),
                             font=F.icon_lg, fill=T.TEXT_4)
        badge = vm.get("badge")
        if badge:
            text, accent = badge
            bf = F.tiny_b
            bw = bf.measure(text) + S(10) + (S(14) if accent else 0)
            bx2, by2 = tx + tw - S(5), ty + th - S(5)
            bfill = T.ACCENT if accent else "#0A0808"
            self.create_rectangle(bx2 - bw, by2 - S(17), bx2, by2, fill=bfill, width=0)
            if accent:
                self.create_text(bx2 - bw + S(5), by2 - S(8), text=G.CUT, font=F.icon_sm,
                                 fill=T.ACCENT_INK, anchor="w")
            self.create_text(bx2 - S(5), by2 - S(8) - S(1), text=text, font=bf,
                             fill=T.ACCENT_INK if accent else T.TEXT, anchor="e")

        # Acciones (derecha)
        x = w - pad
        bh = S(34)
        by = (ch - bh) // 2
        for act in reversed(vm.get("actions", [])):
            label = act.get("label")
            icon = act.get("icon")
            if label:
                bw = S(14) * 2 + F.body_b.measure(label) + (F.icon_sm.measure(icon) + S(8) if icon else 0)
            else:
                bw = bh
            x1 = x - bw
            self._draw_action(act, x1, by, x, by + bh)
            self._regions.append((x1, by, x, by + bh, act))
            x = x1 - S(6)
        text_x = tx + tw + S(16)
        text_r = x - S(10)
        avail = text_r - text_x

        # Título
        self.create_text(text_x, S(20), text=T.ellipsize(F.title, vm.get("title", ""), avail),
                         font=F.title, fill=T.TEXT, anchor="w")

        # Chips
        cx = text_x
        cy1, cy2 = S(31), S(51)
        for chip in vm.get("chips", []):
            text, style, cb, tip = (list(chip) + [None, None, None])[:4]
            if style == "plain":
                tw_ = F.small.measure(text)
                if cx + tw_ > text_r:
                    break
                self.create_text(cx, (cy1 + cy2) / 2, text=text, font=F.small, fill=T.TEXT_3, anchor="w")
                if tip:
                    self._regions.append((cx, cy1, cx + tw_, cy2, {"tooltip": tip}))
                cx += tw_ + S(10)
                continue
            fill, fg = CHIP_STYLES.get(style, CHIP_STYLES["chip"])
            chev = F.icon_sm.measure(G.CHEVRON_DOWN) + S(6) if cb else 0
            cw = F.small_b.measure(text) + S(16) + chev
            if cx + cw > text_r:
                break
            hov = cb and self._is_hover(cx, cy1)
            round_rect(self, cx, cy1, cx + cw, cy2, S(6), T.SURFACE_HI if hov else fill,
                       T.BORDER if hov else None, T.SURFACE)
            self.create_text(cx + S(8), (cy1 + cy2) / 2, text=text, font=F.small_b, fill=fg, anchor="w")
            if cb:
                self.create_text(cx + cw - S(8), (cy1 + cy2) / 2 + S(1), text=G.CHEVRON_DOWN,
                                 font=F.icon_sm, fill=fg, anchor="e")
            self._regions.append((cx, cy1, cx + cw, cy2, {"command": cb, "tooltip": tip}))
            cx += cw + S(6)

        # Estado + detalle
        icon, text, color = vm.get("status", ("", "", T.TEXT_3))
        sy = S(62)
        sx = text_x
        if icon:
            self.create_text(sx, sy + S(1), text=icon, font=F.icon_sm, fill=color, anchor="w")
            sx += F.icon_sm.measure(icon) + S(7)
        detail = vm.get("detail") or ""
        dw = F.mono.measure(detail) if detail else 0
        if detail:
            self.create_text(text_r, sy, text=detail, font=F.mono, fill=T.TEXT_3, anchor="e")
        self.create_text(sx, sy, text=T.ellipsize(F.small_b, text, text_r - sx - dw - S(14)),
                         font=F.small_b, fill=color, anchor="w")
        if vm.get("status_tip"):
            self._regions.append((sx, sy - S(9), text_r - dw, sy + S(9), {"tooltip": vm["status_tip"]}))

        # Progreso
        prog = vm.get("progress")
        self._stop_ind()
        if prog is not None:
            py1, py2 = ty + th - S(4), ty + th
            self.create_rectangle(text_x, py1, text_r, py2, fill=T.SURFACE_HI, width=0)
            color = vm.get("progress_color", T.ACCENT)
            if prog == "ind":
                seg = max(S(40), (text_r - text_x) * 0.25)
                self._bar = (self.create_rectangle(text_x, py1, text_x + seg, py2, fill=color, width=0),
                             text_x, text_r, seg, py1, py2)
                self._start_ind()
            else:
                pw = (text_r - text_x) * max(0.0, min(1.0, float(prog)))
                if pw > 0:
                    self.create_rectangle(text_x, py1, text_x + pw, py2, fill=color, width=0)

    def _draw_action(self, act, x1, y1, x2, y2):
        hov = self._is_hover(x1, y1)
        variant = act.get("variant", "ghost")
        enabled = act.get("enabled", True)
        if not enabled:
            fill, fg, outline = None, T.TEXT_4, None
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
            total = iw + F.body_b.measure(label)
            sx = (x1 + x2 - total) / 2
            if icon:
                self.create_text(sx, cy + S(1), text=icon, font=F.icon_sm, fill=fg, anchor="w")
            self.create_text(sx + iw, cy, text=label, font=F.body_b, fill=fg, anchor="w")
        else:
            self.create_text((x1 + x2) / 2, cy + S(1), text=icon, font=F.icon, fill=fg)

    def _start_ind(self):
        if self._ind_job is None:
            self._ind_job = self.after(30, self._tick)

    def _stop_ind(self):
        if self._ind_job is not None:
            self.after_cancel(self._ind_job)
            self._ind_job = None

    def _tick(self):
        self._ind_job = None
        if not self._bar or not self.winfo_exists():
            return
        item, x1, x2, seg, py1, py2 = self._bar
        self._ind_pos = (self._ind_pos + 0.012) % 1.0
        span = (x2 - x1) + seg
        sx = x1 - seg + span * self._ind_pos
        self.coords(item, max(x1, sx), py1, min(x2, sx + seg), py2)
        self._ind_job = self.after(30, self._tick)
