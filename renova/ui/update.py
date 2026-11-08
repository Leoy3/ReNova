"""Aviso de versión nueva de ReNova: indicador en el encabezado, diálogo de novedades y descarga."""

from __future__ import annotations

import logging
import os
import threading
import tkinter as tk

from .. import APP_VERSION, updater
from ..core import fmt_bytes
from . import theme as T
from .theme import F, G, S
from .widgets import Button, Modal, attach_tooltip, circle_photo, confirm, open_path, round_rect

log = logging.getLogger("renova.ui.update")

CHECK_EVERY_MS = 12 * 60 * 60 * 1000
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _fmt_date(s: str) -> str:
    try:
        y, m, d = (int(x) for x in str(s).split("-")[:3])
        return f"{d} {MONTHS[m - 1]} {y}"
    except Exception:
        return ""


def _mb(b) -> str:
    return fmt_bytes(b).replace(".", ",")


class UpdateBadge(tk.Canvas):
    """Píldora en el encabezado: «Versión X disponible», progreso de descarga o «Reiniciar…»."""

    def __init__(self, master, on_click):
        super().__init__(master, bg=T.BG_2, highlightthickness=0, bd=0, height=S(32), width=1,
                         cursor="hand2", takefocus=0)
        self.text = ""
        self._hover = False
        self._dot = None
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", lambda e: on_click())
        attach_tooltip(self, "Ver las novedades y actualizar ReNova")

    def _set_hover(self, v):
        self._hover = v
        self._draw()

    def set(self, text):
        if text != self.text:
            self.text = text
            self.configure(width=F.small_b.measure(text) + S(42))
            self._draw()

    def _draw(self):
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        fill = T.SELECT if self._hover else T.ACCENT_SOFT
        round_rect(self, 0, 0, w, h, h // 2, fill, None, T.BG_2)
        self._dot = circle_photo(S(8), T.ACCENT, fill)
        self.create_image(S(14), h / 2, image=self._dot, anchor="w")
        self.create_text(S(28), h / 2, text=self.text, font=F.small_b, fill=T.ACCENT_HI, anchor="w")


class UpdateDialog(Modal):
    WIDTH = 540

    def __init__(self, ctl: "AppUpdates"):
        super().__init__(ctl.app.root, "Actualizar ReNova", self.WIDTH, 360)
        self.ctl = ctl
        info = ctl.info
        self.foot = tk.Frame(self, bg=T.BG_2, padx=S(28), pady=S(14))
        self.foot.pack(side="bottom", fill="x")
        body = tk.Frame(self, bg=T.BG, padx=S(28), pady=S(24))
        body.pack(fill="both", expand=True)
        self.body = body

        head = tk.Frame(body, bg=T.BG)
        head.pack(fill="x")
        ic = tk.Canvas(head, width=S(44), height=S(44), bg=T.BG, highlightthickness=0, bd=0)
        ic.pack(side="left", anchor="n")
        self._ic = circle_photo(S(44), T.ACCENT_SOFT, T.BG)
        ic.create_image(0, 0, image=self._ic, anchor="nw")
        ic.create_text(S(22), S(23), text=G.DOWNLOAD, font=F.icon_md, fill=T.ACCENT)
        txt = tk.Frame(head, bg=T.BG)
        txt.pack(side="left", fill="x", expand=True, padx=(S(14), 0))
        tk.Label(txt, text=f"ReNova {info['version']} está disponible", font=F.h2, fg=T.TEXT, bg=T.BG,
                 anchor="w").pack(fill="x")
        meta = [f"Tenés la {APP_VERSION}"]
        if info.get("size"):
            meta.append(_mb(info["size"]))
        if _fmt_date(info.get("date", "")):
            meta.append(f"publicada el {_fmt_date(info['date'])}")
        tk.Label(txt, text=" · ".join(meta), font=F.small, fg=T.TEXT_3, bg=T.BG, anchor="w").pack(
            fill="x", pady=(S(2), 0))

        if info.get("notes"):
            tk.Label(body, text="NOVEDADES", font=F.label, fg=T.TEXT_3, bg=T.BG, anchor="w").pack(
                fill="x", pady=(S(20), S(6)))
            for n in info["notes"]:
                r = tk.Frame(body, bg=T.BG)
                r.pack(fill="x", pady=S(3))
                tk.Label(r, text="•", font=F.body_b, fg=T.ACCENT, bg=T.BG).pack(side="left", anchor="n")
                tk.Label(r, text=n, font=F.body, fg=T.TEXT_2, bg=T.BG, anchor="w", justify="left",
                         wraplength=S(self.WIDTH - 90)).pack(side="left", fill="x", expand=True, padx=(S(8), 0))

        self.status = tk.Frame(body, bg=T.BG)
        self.status.pack(fill="x", pady=(S(20), 0))
        self.bar = tk.Canvas(self.status, height=S(6), bg=T.BG, highlightthickness=0, bd=0)
        self.bar.bind("<Configure>", lambda e: self._draw_bar())
        self.status_lbl = tk.Label(self.status, text="", font=F.small, fg=T.TEXT_3, bg=T.BG, anchor="w",
                                   justify="left", wraplength=S(self.WIDTH - 60))
        self.status_lbl.pack(fill="x")
        self._shape = None
        self.render()
        self.show()

    # ── Estado ─────────────────────────────────────────────────────────────
    def render(self):
        ctl = self.ctl
        state = ctl.state
        busy = ctl.app.busy_count()
        installed = updater.can_self_update()
        if state == "downloading":
            shape = ("downloading",)
        elif state == "ready":
            shape = ("ready", bool(busy))
        else:
            shape = (state if state == "error" else "available", installed)
        if shape != self._shape:
            self._shape = shape
            for w in self.foot.winfo_children():
                w.destroy()
            self.bar.pack_forget()
            if shape[0] == "downloading":
                self.bar.pack(fill="x", pady=(0, S(8)), before=self.status_lbl)
                Button(self.foot, text="Cancelar", variant="ghost", command=ctl.cancel_download).pack(side="right")
                Button(self.foot, text="Seguir en segundo plano", variant="ghost", command=self.close).pack(
                    side="right", padx=(0, S(8)))
            elif shape[0] == "ready":
                if busy:
                    Button(self.foot, text="Actualizar ahora", variant="primary",
                           command=lambda: ctl.apply(force=True)).pack(side="right")
                    Button(self.foot, text="Esperar", variant="ghost", command=self.close).pack(
                        side="right", padx=(0, S(8)))
            else:
                if installed:
                    Button(self.foot, text="Reintentar" if shape[0] == "error" else "Actualizar ahora",
                           icon=G.DOWNLOAD, variant="primary", command=ctl.start_download).pack(side="right")
                else:
                    Button(self.foot, text="Ir a la descarga", icon=G.GLOBE, variant="primary",
                           command=ctl.open_page).pack(side="right")
                Button(self.foot, text="Más tarde", variant="ghost", command=self.close).pack(
                    side="right", padx=(0, S(8)))
                Button(self.foot, text="Omitir esta versión", variant="ghost", fg=T.TEXT_3,
                       command=ctl.skip).pack(side="left")
        self._update_status_text(busy, installed)
        self._draw_bar()
        self._fit()

    def _update_status_text(self, busy, installed):
        ctl = self.ctl
        color = T.TEXT_3
        if ctl.state == "downloading":
            if ctl.total:
                text = f"Descargando… {_mb(ctl.done)} de {_mb(ctl.total)}"
            else:
                text = f"Descargando… {_mb(ctl.done)}"
            color = T.TEXT_2
        elif ctl.state == "ready":
            if busy:
                n = busy
                text = (f"La actualización está lista. Hay {n} {'trabajo' if n == 1 else 'trabajos'} en curso: "
                        "si actualizás ahora se cancelan. Si esperás, podés actualizar desde el aviso de arriba.")
                color = T.WARN
            else:
                text = "Instalando… ReNova se va a cerrar y en unos segundos vuelve a abrirse solo."
                color = T.TEXT_2
        elif ctl.state == "error":
            text = f"No se pudo descargar la actualización: {ctl.error}"
            color = T.ERR
        elif installed:
            text = ("Tus ajustes, el motor de descargas y tus archivos se conservan. ReNova se cierra, "
                    "instala la versión nueva y vuelve a abrirse solo.")
        else:
            text = ("Esta copia de ReNova es portable: se abre la página de descarga para que bajes la "
                    "versión nueva.")
        self.status_lbl.configure(text=text, fg=color)

    def _draw_bar(self):
        cv = self.bar
        cv.delete("all")
        w = cv.winfo_width()
        h = int(cv["height"])
        if w < 10 or self.ctl.state != "downloading":
            return
        round_rect(cv, 0, 0, w, h, h // 2, T.SURFACE_HI, None, T.BG)
        frac = self.ctl.done / self.ctl.total if self.ctl.total else 0
        fw = max(h, int(w * min(1.0, frac)))
        if frac > 0:
            round_rect(cv, 0, 0, fw, h, h // 2, T.ACCENT, None, T.SURFACE_HI)

    def _fit(self):
        self.update_idletasks()
        h = self.body.winfo_reqheight() + self.foot.winfo_reqheight()
        if abs(self.winfo_height() - h) > 2:
            self.geometry(f"{S(self.WIDTH)}x{h}")

    def close(self, result=None):
        self.ctl.dialog = None
        super().close(result)


class AppUpdates:
    """Controla la búsqueda, descarga e instalación de versiones nuevas de ReNova."""

    def __init__(self, app, header_slot):
        self.app = app
        self.info: dict | None = None
        self.state = "idle"  # idle | checking | available | downloading | ready | error
        self.done = self.total = 0
        self.path = None
        self.error = ""
        self.dialog: UpdateDialog | None = None
        self._cancel = threading.Event()
        self._job = None
        self._manual = False
        # Sólo para pruebas de punta a punta: acepta la actualización sin intervención
        self._auto = os.environ.get("RENOVA_UPDATE_AUTO") == "1"
        self.badge = UpdateBadge(header_slot, self.open_dialog)
        threading.Thread(target=updater.cleanup, daemon=True, name="update-clean").start()

    @property
    def settings(self):
        return self.app.settings

    def start(self):
        last = self.settings.get("last_version")
        if last != APP_VERSION:
            if last and updater.vtuple(APP_VERSION) > updater.vtuple(last):
                self.app.root.after(1500, lambda: self.app.toast.show(
                    f"ReNova se actualizó a la versión {APP_VERSION}", "ok", ms=6000))
            self.settings.set("last_version", APP_VERSION)
        if self.settings.auto_update_engine:
            self._job = self.app.root.after(6000, self.check)

    # ── Búsqueda ───────────────────────────────────────────────────────────
    def check(self, manual=False):
        if self._job:
            try:
                self.app.root.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        if self.state in ("checking", "downloading"):
            return
        if self.state == "ready" and manual:
            self.open_dialog()
            return
        prev = self.state
        self.state = "checking"
        self._manual = manual
        if manual:
            self.app.toast.show("Buscando una versión nueva de ReNova…", "info", ms=3000)

        def run():
            info, err = None, None
            try:
                info = updater.check()
            except Exception as e:  # noqa: BLE001
                log.info("No se pudo buscar una versión nueva: %s", e)
                err = e
            self.app.bus.call(self._checked, info, err, prev)
        threading.Thread(target=run, daemon=True, name="app-update-check").start()

    def _checked(self, info, err, prev):
        manual = self._manual
        if self.settings.auto_update_engine:
            self._job = self.app.root.after(CHECK_EVERY_MS, self.check)
        if err is not None:
            self.state = prev if prev in ("available", "ready", "error") else "idle"
            if manual:
                self.app.toast.show("No se pudo buscar una versión nueva (¿sin conexión?)", "warn")
            self._refresh()
            return
        if info is None:
            self.state, self.info = "idle", None
            if manual:
                self.app.toast.show(f"Ya tenés la última versión de ReNova ({APP_VERSION})", "ok")
            self._refresh()
            return
        is_new = not self.info or self.info.get("version") != info["version"]
        self.info = info
        self.state = "available" if is_new or prev not in ("ready", "error") else prev
        skipped = self.settings.get("skip_version") == info["version"]
        if self._auto:
            self._refresh()
            self.start_download()
            return
        if manual:
            self.open_dialog()
        elif is_new and not skipped:
            self.app.toast.show(f"Hay una versión nueva de ReNova: {info['version']}", "info",
                                action=("Ver novedades", self.open_dialog), ms=12000)
        self._refresh()

    # ── Descarga e instalación ─────────────────────────────────────────────
    def start_download(self):
        if not self.info or self.state == "downloading":
            return
        if not updater.can_self_update():
            self.open_page()
            return
        self.state, self.done, self.total, self.error = "downloading", 0, self.info.get("size") or 0, ""
        self._cancel.clear()
        info = self.info

        def progress(done, total):
            self.done, self.total = done, total
            self.app.bus.mark("app-update", self._refresh)

        def run():
            path, err = None, None
            try:
                path = updater.download(info, progress, self._cancel)
            except updater.Cancelled:
                err = "cancel"
            except Exception as e:  # noqa: BLE001
                log.exception("Falló la descarga de la actualización")
                err = str(e) or e.__class__.__name__
            self.app.bus.call(self._downloaded, path, err)
        threading.Thread(target=run, daemon=True, name="app-update-dl").start()
        self._refresh()

    def _downloaded(self, path, err):
        if err == "cancel":
            self.state = "available"
        elif err:
            self.state = "error"
            low = err.lower()
            self.error = ("revisá tu conexión e intentá de nuevo" if any(
                k in low for k in ("timed out", "urlopen", "getaddrinfo", "connection", "10054", "10060"))
                else err)
        else:
            self.state, self.path = "ready", path
            if self._auto:
                self.app.root.after(500, lambda: self.apply(force=True))
            elif self.dialog is not None:
                if not self.app.busy_count():
                    self.app.root.after(900, self.apply)
            else:
                self.app.toast.show("La actualización de ReNova está lista", "ok",
                                    action=("Reiniciar y actualizar", self.apply), ms=15000)
        self._refresh()

    def cancel_download(self):
        self._cancel.set()

    def apply(self, force=False):
        if self.state != "ready" or not self.path:
            return
        busy = self.app.busy_count()
        if busy and not force and not confirm(
                self.app.root, "Actualizar ReNova",
                f"Hay {busy} {'trabajo' if busy == 1 else 'trabajos'} en curso que se van a cancelar.",
                ok="Actualizar igual", cancel="Esperar"):
            return
        try:
            updater.launch_installer(self.path)
        except Exception as e:  # noqa: BLE001
            log.exception("No se pudo ejecutar el instalador")
            self.state, self.error = "error", f"no se pudo abrir el instalador ({e})"
            self._refresh()
            return
        if updater.dry_run():
            self.app.toast.show("Simulación: acá se instalaría la versión nueva", "info")
            return
        self.app._shutdown()

    def skip(self):
        if self.info:
            self.settings.set("skip_version", self.info["version"])
        if self.dialog is not None:
            self.dialog.close()
        self._refresh()
        self.app.toast.show("Listo, no te vamos a avisar de esta versión. Podés buscarla desde Ajustes.", "info")

    def open_page(self):
        url = (self.info or {}).get("page") or (self.info or {}).get("url")
        if url:
            open_path(url)

    def open_dialog(self):
        if not self.info:
            return
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.lift()
            return
        self.dialog = UpdateDialog(self)

    # ── UI ─────────────────────────────────────────────────────────────────
    def describe(self) -> str:
        """Texto de estado para Ajustes."""
        if self.state == "checking":
            return "Buscando…"
        if self.info and self.state in ("available", "downloading", "ready", "error"):
            return f"Hay una versión nueva: {self.info['version']}"
        return "Estás al día"

    def _refresh(self):
        b = self.badge
        v = self.info["version"] if self.info else None
        show = bool(v) and (self.state in ("downloading", "ready")
                            or (self.state in ("available", "error") and self.settings.get("skip_version") != v))
        if show:
            if self.state == "downloading":
                pct = int(self.done * 100 / self.total) if self.total else None
                b.set(f"Descargando actualización · {pct}%" if pct is not None else "Descargando actualización…")
            elif self.state == "ready":
                b.set("Reiniciar para actualizar")
            else:
                b.set(f"Versión {v} disponible")
            if not b.winfo_ismapped():
                b.pack(side="right", pady=S(14))
        elif b.winfo_ismapped():
            b.pack_forget()
        if self.dialog is not None:
            try:
                self.dialog.render()
            except tk.TclError:
                self.dialog = None
