"""Ventana principal de ReNova."""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog

from .. import APP_NAME, APP_VERSION, engine, media, paths
from ..settings import Settings
from . import theme as T
from .cards import ThumbLoader
from .theme import F, G, S
from .widgets import (Button, PopupMenu, Toast, attach_tooltip, block_alt_menu, confirm, dark_titlebar, flash_window,
                      install_wheel_router, open_path)

log = logging.getLogger("renova.app")


def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class UiBus:
    """Puente hilo → UI. `call` encola funciones; `mark` agrupa refrescos por clave."""

    def __init__(self, root):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.dirty: dict = {}
        self.lock = threading.Lock()
        self.root.after(50, self._pump)

    def call(self, fn, *args):
        self.q.put((fn, args))

    def mark(self, key, fn):
        with self.lock:
            self.dirty[key] = fn

    def _pump(self):
        try:
            with self.lock:
                dirty, self.dirty = self.dirty, {}
            for fn in dirty.values():
                try:
                    fn()
                except Exception:
                    log.exception("Error refrescando la UI")
            for _ in range(300):
                try:
                    fn, args = self.q.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn(*args)
                except Exception:
                    log.exception("Error en callback de UI")
        finally:
            try:
                self.root.after(60, self._pump)
            except tk.TclError:
                pass


class TabBar(tk.Canvas):
    def __init__(self, master, tabs: list, command):
        super().__init__(master, bg=T.BG_2, highlightthickness=0, bd=0, height=S(60), takefocus=0)
        self.tabs = tabs
        self.command = command
        self.current = tabs[0][0]
        self.badges: dict = {}
        self._hover = None
        self._rects = []
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<ButtonRelease-1>", self._click)
        self.configure(width=sum(self._tab_w(t) for t in tabs) + S(8))

    def _tab_w(self, t):
        w = F.icon_sm.measure(t[2]) + S(8) + F.body_b.measure(t[1]) + S(36)
        if self.badges.get(t[0]) if hasattr(self, "badges") else 0:
            w += F.tiny_b.measure(str(self.badges[t[0]])) + S(22)
        return w

    def set_badge(self, key, n):
        if self.badges.get(key) != n:
            self.badges[key] = n
            self.configure(width=sum(self._tab_w(t) for t in self.tabs) + S(8))
            self._draw()

    def select(self, key):
        self.current = key
        self._draw()

    def _hit(self, x):
        for x1, x2, key in self._rects:
            if x1 <= x < x2:
                return key
        return None

    def _motion(self, e):
        self._set_hover(self._hit(e.x))

    def _set_hover(self, key):
        if key != self._hover:
            self._hover = key
            self.configure(cursor="hand2" if key and key != self.current else "arrow")
            self._draw()

    def _click(self, e):
        key = self._hit(e.x)
        if key and key != self.current:
            self.command(key)

    def _draw(self):
        self.delete("all")
        h = int(self.winfo_height() or self["height"])
        x = 0
        self._rects = []
        for key, label, icon in self.tabs:
            w = self._tab_w((key, label, icon))
            active = key == self.current
            hov = key == self._hover
            fg = T.TEXT if active else (T.TEXT_2 if hov else T.TEXT_3)
            cx = x + S(18)
            self.create_text(cx, h / 2 + S(1), text=icon, font=F.icon_sm, fill=T.ACCENT if active else fg,
                             anchor="w")
            cx += F.icon_sm.measure(icon) + S(8)
            self.create_text(cx, h / 2, text=label, font=F.body_b, fill=fg, anchor="w")
            cx += F.body_b.measure(label)
            n = self.badges.get(key)
            if n:
                bw = F.tiny_b.measure(str(n)) + S(12)
                from .widgets import round_rect
                cx += S(8)
                round_rect(self, cx, h / 2 - S(9), cx + bw, h / 2 + S(9), S(9), T.ACCENT_SOFT, None, T.BG_2)
                self.create_text(cx + bw / 2, h / 2, text=str(n), font=F.tiny_b, fill=T.ACCENT)
            if active:
                self.create_rectangle(x + S(14), h - S(3), x + w - S(14), h, fill=T.ACCENT, width=0)
            self._rects.append((x, x + w, key))
            x += w


class EngineStatus(tk.Canvas):
    """Indicador en el pie: si las descargas de YouTube funcionan con el motor actual."""
    COLORS = {"checking": T.TEXT_3, "ok": T.OK, "update": T.WARN, "fix": T.WARN, "broken": T.ERR,
              "offline": T.TEXT_3}

    def __init__(self, master, on_click, tooltip):
        super().__init__(master, bg=T.BG_2, highlightthickness=0, bd=0, height=S(30), width=S(120),
                         cursor="hand2", takefocus=0)
        self.state, self.text = "checking", ""
        self._hover = False
        self._pulse = 0
        self._job = None
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", lambda e: on_click())
        attach_tooltip(self, tooltip)

    def _set_hover(self, v):
        self._hover = v
        self._draw()

    def set(self, state, text):
        self.state, self.text = state, text
        self.configure(width=F.small_b.measure(text) + S(36))
        self._draw()
        if state == "checking" and self._job is None:
            self._animate()

    def _animate(self):
        self._job = None
        if self.state != "checking" or not self.winfo_exists():
            return
        self._pulse = (self._pulse + 1) % 20
        self._draw()
        self._job = self.after(80, self._animate)

    def _draw(self):
        from .widgets import circle_photo, round_rect
        self.delete("all")
        w, h = int(self["width"]), int(self["height"])
        if self._hover:
            round_rect(self, 0, 0, w, h, S(8), T.SURFACE_HI, None, T.BG_2)
        bg = T.SURFACE_HI if self._hover else T.BG_2
        color = self.COLORS.get(self.state, T.TEXT_3)
        if self.state == "checking":
            color = T.TEXT_3 if self._pulse < 10 else T.TEXT_4
        self.create_image(S(12), h / 2, image=circle_photo(S(8), color, bg), anchor="w")
        fg = T.ERR if self.state == "broken" else (T.WARN if self.state in ("fix", "update") else T.TEXT_2)
        self.create_text(S(26), h / 2, text=self.text, font=F.small_b, fill=fg, anchor="w")


class App:
    def __init__(self):
        enable_dpi_awareness()
        self.dnd = False
        try:
            from tkinterdnd2 import TkinterDnD
            self.root = TkinterDnD.Tk()
            self.dnd = True
        except Exception:
            self.root = tk.Tk()
        root = self.root
        root.withdraw()
        root.title(APP_NAME)
        root.configure(bg=T.BG)
        T.init(root)
        try:
            ico = paths.asset("renova.ico")
            if ico.is_file():
                root.iconbitmap(default=str(ico))
        except tk.TclError:
            pass
        root.report_callback_exception = self._tk_error
        self.settings = Settings()
        self.bus = UiBus(root)
        self.thumbs = ThumbLoader(self.bus)
        self.toast = Toast(root)
        self._closing = False

        engine.load_async()
        threading.Thread(target=media.Encoders.detect, daemon=True, name="hw-detect").start()
        from .. import core
        threading.Thread(target=core.clean_preview_cache, daemon=True, name="cache-clean").start()

        self._build()
        self._restore_geometry()
        dark_titlebar(root)
        block_alt_menu(root)  # soltar Alt no abre el menú de la ventana (zoom = Alt + rueda)
        root.deiconify()
        install_wheel_router(root)
        self._bind_keys()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(900, self.downloads.check_clipboard)
        self.health = None
        self._health_running = False
        self._health_job = None
        self._updating = False
        self._refresh_engine_pill()
        from .update import AppUpdates
        self.updates = AppUpdates(self, self._update_slot)
        self.updates.start()
        root.after(1200, self.run_health_check)
        if self.settings.auto_update_engine:
            root.after(4000, lambda: self.check_engine_update(manual=False))
        self.url_focus_soon()

    # ── Construcción ───────────────────────────────────────────────────────
    def _build(self):
        root = self.root
        header = tk.Frame(root, bg=T.BG_2, height=S(60))
        header.pack(fill="x")
        header.pack_propagate(False)
        brand = tk.Canvas(header, bg=T.BG_2, highlightthickness=0, bd=0, height=S(60),
                          width=F.brand.measure("Re") + F.brand_i.measure("Nova") + S(80))
        brand.pack(side="left", padx=(S(22), S(10)))
        brand.create_text(0, S(30), text="Re", font=F.brand, fill=T.TEXT, anchor="w")
        bx = F.brand.measure("Re")
        brand.create_text(bx, S(30), text="Nova", font=F.brand_i, fill=T.ACCENT, anchor="w")
        bx += F.brand_i.measure("Nova") + S(10)
        brand.create_text(bx, S(32), text=APP_VERSION, font=F.mono_sm, fill=T.TEXT_4, anchor="w")
        self.tabbar = TabBar(header, [("download", "Descargar", G.DOWNLOAD), ("convert", "Convertir", G.SYNC),
                                     ("edit", "Recortar", G.CUT)],
                             self.show_tab)
        self.tabbar.pack(side="left", padx=(S(10), 0))
        Button(header, icon=G.SETTINGS, variant="ghost", height=36, bg=T.BG_2, tooltip="Ajustes",
               command=self.open_settings).pack(side="right", padx=S(16))
        # Lugar para el aviso de versión nueva (ui/update.py)
        self._update_slot = tk.Frame(header, bg=T.BG_2)
        self._update_slot.pack(side="right")
        tk.Frame(root, bg=T.BORDER_SOFT, height=1).pack(fill="x")

        # Pie: carpeta de destino + estado
        tk.Frame(root, bg=T.BORDER_SOFT, height=1).pack(side="bottom", fill="x")
        foot = tk.Frame(root, bg=T.BG_2, height=S(50))
        foot.pack(side="bottom", fill="x")
        foot.pack_propagate(False)
        foot_inner = tk.Frame(foot, bg=T.BG_2)
        foot_inner.pack(fill="both", expand=True, padx=(S(20), S(12)))
        self.engine_pill = EngineStatus(foot_inner, self._engine_menu, self._engine_tooltip)
        self.engine_pill.pack(side="right")
        tk.Frame(foot_inner, bg=T.BORDER_SOFT, width=1, height=S(22)).pack(side="right", padx=S(8))
        tk.Label(foot_inner, text=G.FOLDER, font=F.icon_sm, fg=T.TEXT_3, bg=T.BG_2).pack(side="left")
        tk.Label(foot_inner, text="GUARDAR EN", font=F.label, fg=T.TEXT_3, bg=T.BG_2).pack(side="left", padx=(S(8), S(8)))
        self.path_lbl = tk.Label(foot_inner, text="", font=F.body, fg=T.TEXT_2, bg=T.BG_2, anchor="w", cursor="hand2")
        self.path_lbl.pack(side="left", fill="x", expand=True)
        self.path_lbl.bind("<Configure>", lambda e: self._update_path_label())
        self.path_lbl.bind("<Button-1>", lambda e: open_path(self.settings.out_dir))
        attach_tooltip(self.path_lbl, lambda: f"{self.settings.out_dir}\nClic para abrir")
        self.status_lbl = tk.Label(foot_inner, text="", font=F.small, fg=T.TEXT_3, bg=T.BG_2)
        self.status_lbl.pack(side="right", padx=(S(16), 0))
        Button(foot_inner, text="Abrir carpeta", icon=G.FOLDER_OPEN, variant="ghost", height=32, padx=10,
               bg=T.BG_2, font=F.small_b, icon_font=F.icon_sm,
               command=lambda: open_path(self.settings.out_dir)).pack(side="right")
        Button(foot_inner, text="Cambiar", icon=G.EDIT, variant="ghost", height=32, padx=10, bg=T.BG_2,
               font=F.small_b, icon_font=F.icon_sm, command=self.pick_folder).pack(side="right", padx=(S(8), 0))

        self.body = tk.Frame(root, bg=T.BG)
        self.body.pack(fill="both", expand=True)
        from .convert_tab import ConvertTab
        from .download_tab import DownloadTab
        from .edit_tab import EditTab
        self.downloads = DownloadTab(self.body, self)
        self.converts = ConvertTab(self.body, self)
        self.edit = EditTab(self.body, self)
        self.tabs = {"download": self.downloads, "convert": self.converts, "edit": self.edit}
        self.current_tab = None
        self.show_tab("download")

    def show_tab(self, key):
        if key == self.current_tab:
            return
        if self.current_tab == "edit":
            self.edit.on_hide()
        for k, frame in self.tabs.items():
            if k == key:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        self.current_tab = key
        if key == "edit":
            self.edit.on_show()
        self.tabbar.select(key)
        PopupMenu._current and PopupMenu._current.close()
        self.update_status()

    def _update_path_label(self):
        w = self.path_lbl.winfo_width()
        if w > 20:
            self.path_lbl.configure(text=T.ellipsize_middle(F.body, self.settings.out_dir, w - S(8)))

    def update_status(self):
        if not hasattr(self, "tabs"):
            return
        tab = self.tabs.get(self.current_tab)
        if tab is not None:
            self.status_lbl.configure(text=tab.summary())
        if hasattr(self, "downloads"):
            self.tabbar.set_badge("download", self.downloads.active_count())
        if hasattr(self, "converts"):
            self.tabbar.set_badge("convert", self.converts.active_count())
        if hasattr(self, "edit"):
            self.tabbar.set_badge("edit", self.edit.active_count())

    # ── Acciones globales ──────────────────────────────────────────────────
    def pick_folder(self):
        d = filedialog.askdirectory(parent=self.root, initialdir=self.settings.out_dir,
                                    title="Elegir carpeta de destino")
        if d:
            d = os.path.normpath(d)
            self.settings.set("out_dir", d)
            self._update_path_label()
            self.toast.show(f"Los archivos se guardarán en {os.path.basename(d) or d}", "ok")

    def open_in_editor(self, path):
        self.show_tab("edit")
        self.edit.open_path(path)

    def busy_count(self) -> int:
        return self.downloads.active_count() + self.converts.active_count() + self.edit.active_count()

    def open_settings(self):
        from .dialogs import SettingsDialog
        SettingsDialog(self)

    def url_focus_soon(self):
        self.root.after(200, lambda: self.downloads.url.focus())

    def register_drop(self, widget, handler, tree=False):
        """Acepta archivos y texto soltados sobre el widget (y sus hijos si tree=True)."""
        if not self.dnd:
            return
        widgets = [widget]
        if tree:
            stack = list(widget.winfo_children())
            while stack:
                w = stack.pop()
                if not isinstance(w, tk.Toplevel):
                    widgets.append(w)
                    stack.extend(w.winfo_children())
        for w in widgets:
            try:
                w.drop_target_register("DND_Files", "DND_Text")
                w.dnd_bind("<<Drop>>", lambda e, h=handler: self._handle_drop(e, h))
            except Exception:
                pass

    def _handle_drop(self, e, handler):
        """En Windows el tipo llega como CF_HDROP (archivos) o CF_UNICODETEXT (texto): no se puede
        confiar en que diga «file», así que también se reconoce por el contenido."""
        data = e.data or ""
        kind = str(getattr(e, "type", "") or "").upper()
        try:
            parts = list(self.root.tk.splitlist(data)) if data else []
        except tk.TclError:
            parts = []
        files = []
        if "HDROP" in kind or "FILE" in kind or (parts and all(os.path.exists(p) for p in parts)):
            files = [os.path.normpath(p) for p in parts if os.path.exists(p)]
        log.info("Soltado: tipo=%s, %d archivo(s)", kind or "?", len(files))
        try:
            self.last_drop = (int(e.x_root), int(e.y_root))  # dónde se soltó (pantalla)
        except (TypeError, ValueError, AttributeError):
            self.last_drop = (self.root.winfo_pointerx(), self.root.winfo_pointery())
        try:
            handler("" if files else data, files)
        except Exception:
            log.exception("Error al soltar archivos")
        return getattr(e, "action", "copy")

    def notify_finished(self, text, item=None):
        focused = self.root.focus_displayof() is not None
        if not focused:
            flash_window(self.root)
        self.toast.show(text, "ok", action=("Abrir carpeta", lambda: open_path(self.settings.out_dir)))

    def check_engine_update(self, manual=False):
        if self._updating:
            return
        if manual:
            self.toast.show("Buscando actualizaciones del motor…", "info", ms=3000)
        self._updating = True
        self._refresh_engine_pill()

        def run():
            err = None
            try:
                v = engine.check_update()
            except Exception as e:
                log.info("No se pudo actualizar el motor: %s", e)
                v, err = None, e
            self.bus.call(done, v, err)

        def done(v, err):
            self._updating = False
            self._refresh_engine_pill()
            if v:
                busy = self.busy_count() > 0
                broken = bool(self.health and self.health["state"] == "broken")
                text = (f"Hay un arreglo para YouTube (motor {v}). Reiniciá ReNova para aplicarlo." if broken
                        else f"Motor de descargas actualizado ({v}). Se aplica al reiniciar.")
                self.toast.show(text, "warn" if broken else "ok",
                                action=None if busy else ("Reiniciar", self.restart), ms=15000)
            elif manual:
                if err:
                    self.toast.show("No se pudo buscar actualizaciones (¿sin conexión?)", "warn")
                else:
                    self.toast.show(f"El motor ya está actualizado ({engine.version()})", "ok")
        threading.Thread(target=run, daemon=True, name="engine-update").start()

    # ── Estado de YouTube / motor ──────────────────────────────────────────
    def run_health_check(self, manual=False):
        if self._health_running:
            return
        self._health_running = True
        if self._health_job:
            self.root.after_cancel(self._health_job)
            self._health_job = None
        if manual or self.health is None:
            self.engine_pill.set("checking", "Verificando YouTube…")

        def run():
            from .. import health
            try:
                res = health.check()
            except Exception as e:  # noqa: BLE001
                log.exception("Verificación fallida")
                res = {"state": "broken", "message": "No se pudo verificar", "detail": str(e),
                       "version": engine.version(), "at": time.time()}
            self.bus.call(done, res)

        def done(res):
            prev = self.health["state"] if self.health else None
            self._health_running = False
            self.health = res
            self._refresh_engine_pill()
            if res["state"] == "broken":
                if self.settings.auto_update_engine and not engine.pending_version():
                    self.check_engine_update(manual=False)
                if manual or prev != "broken":
                    self.toast.show(res["message"], "error", action=("Detalles", self._engine_menu), ms=8000)
            elif manual:
                self.toast.show(res["message"] if res["state"] == "ok" else "Sin conexión a internet",
                                "ok" if res["state"] == "ok" else "warn")
            self._health_job = self.root.after(30 * 60 * 1000, self.run_health_check)
        threading.Thread(target=run, daemon=True, name="health").start()

    def on_download_error(self, item):
        """Si una descarga de YouTube falla por algo que parece un cambio de YouTube, se re-verifica."""
        low = (item.error_detail or item.error or "").lower()
        if item.source != "YouTube" or any(k in low for k in ("private", "unavailable", "removed", "members",
                                                                "age", "copyright", "live event")):
            return
        if self.health and time.time() - self.health.get("at", 0) < 120:
            return
        self.run_health_check()

    def _refresh_engine_pill(self):
        pending = engine.pending_version()
        st = self.health["state"] if self.health else "checking"
        if st == "checking":
            self.engine_pill.set("checking", "Verificando YouTube…")
        elif st == "offline":
            self.engine_pill.set("offline", "Sin conexión")
        elif st == "broken":
            if pending:
                self.engine_pill.set("fix", "YouTube cambió · reiniciá para aplicar el arreglo")
            elif self._updating:
                self.engine_pill.set("broken", "YouTube no funciona · buscando arreglo…")
            else:
                self.engine_pill.set("broken", "YouTube no funciona")
        elif pending:
            self.engine_pill.set("update", "Actualización del motor lista")
        else:
            self.engine_pill.set("ok", "YouTube funciona")

    def _engine_tooltip(self):
        lines = []
        h = self.health
        if h:
            lines.append(h["message"])
            when = time.strftime("%H:%M", time.localtime(h.get("at", time.time())))
            lines.append(f"Última verificación: {when} (se repite cada 30 min)")
        else:
            lines.append("Probando una descarga real de YouTube…")
        lines.append(f"Motor yt-dlp {engine.version()}")
        pending = engine.pending_version()
        if pending:
            lines.append(f"Versión {pending} descargada: se aplica al reiniciar")
        lines.append("Clic para ver opciones")
        return "\n".join(lines)

    def _engine_menu(self):
        h = self.health or {}
        pending = engine.pending_version()
        items = [{"header": f"Motor yt-dlp {engine.version()}"},
                 {"label": h.get("message") or "Verificando…", "enabled": False, "info": True,
                  "icon": {"ok": G.COMPLETED, "broken": G.ERROR, "offline": G.WARNING}.get(h.get("state"), G.SYNC),
                  "icon_color": {"ok": T.OK, "broken": T.ERR, "offline": T.WARN}.get(h.get("state"), T.TEXT_3)},
                 {"separator": True}]
        if pending:
            items.append({"label": f"Reiniciar y aplicar la versión {pending}", "icon": G.REFRESH,
                          "command": self.restart, "icon_color": T.WARN})
        items += [{"label": "Verificar ahora", "icon": G.SYNC, "command": lambda: self.run_health_check(manual=True),
                   "enabled": not self._health_running},
                  {"label": "Buscar actualización del motor", "icon": G.DOWNLOAD,
                   "command": lambda: self.check_engine_update(manual=True), "enabled": not self._updating}]
        if h.get("state") == "broken" and h.get("detail"):
            items.append({"label": "Copiar detalle técnico", "icon": G.COPY,
                          "command": lambda: (self.root.clipboard_clear(),
                                              self.root.clipboard_append(h["detail"]))})
        p = self.engine_pill
        PopupMenu(self.root, items, p.winfo_rootx() + p.winfo_width(), p.winfo_rooty() - S(6),
                  min_width=300, align="right", above=True)

    def restart(self):
        if self.busy_count():
            if not confirm(self.root, "Reiniciar ReNova", "Hay trabajos en curso que se van a cancelar.",
                           ok="Reiniciar igual", cancel="Esperar"):
                return
        args = [sys.executable] if paths.FROZEN else [sys.executable, os.path.abspath(sys.argv[0])]
        try:
            subprocess.Popen(args, close_fds=True, cwd=os.path.dirname(args[-1]) or None)
        except Exception:
            log.exception("No se pudo reiniciar")
            return
        self._shutdown()

    # ── Teclado ────────────────────────────────────────────────────────────
    def _bind_keys(self):
        root = self.root

        def in_entry(e):
            return isinstance(e.widget, tk.Entry)

        def is_main(e):
            try:
                return e.widget.winfo_toplevel() is root
            except Exception:
                return False

        def paste(e):
            if in_entry(e) or not is_main(e):
                return
            if self.current_tab != "download":
                self.show_tab("download")
            self.downloads.paste_global()
            return "break"

        def start_all(e):
            if not is_main(e):
                return
            self.tabs[self.current_tab].start_all()
            return "break"

        root.bind_all("<Control-v>", paste)
        root.bind_all("<Control-V>", paste)
        root.bind_all("<Control-Return>", start_all)
        root.bind_all("<Control-l>", lambda e: is_main(e) and (self.show_tab("download"), self.downloads.url.focus()))
        root.bind_all("<Control-o>", lambda e: is_main(e) and (self.show_tab("convert"), self.converts.browse()))
        root.bind_all("<Control-comma>", lambda e: is_main(e) and self.open_settings())
        self._act_job = None

        def activated(_e):
            if self._act_job:
                root.after_cancel(self._act_job)
            self._act_job = root.after(250, self.downloads.check_clipboard)
        root.bind("<Activate>", activated)

    # ── Ventana ────────────────────────────────────────────────────────────
    def _restore_geometry(self):
        root = self.root
        default_w, default_h = S(1040), S(760)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        default_w, default_h = min(default_w, sw - 40), min(default_h, sh - 80)
        root.minsize(min(S(860), sw - 40), min(S(600), sh - 80))
        geo = self.settings.geometry
        zoomed = geo.startswith("zoomed:")
        if zoomed:
            geo = geo.split(":", 1)[1]
        try:
            size, x, y = geo.split("+")[0], int(geo.split("+")[1]), int(geo.split("+")[2])
            w, h = (int(v) for v in size.split("x"))
            vx = ctypes.windll.user32.GetSystemMetrics(76)
            vy = ctypes.windll.user32.GetSystemMetrics(77)
            vw = ctypes.windll.user32.GetSystemMetrics(78)
            vh = ctypes.windll.user32.GetSystemMetrics(79)
            if not (vx - 50 <= x <= vx + vw - 200 and vy - 10 <= y <= vy + vh - 120) or w < 400 or h < 300:
                raise ValueError
            root.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            x, y = (sw - default_w) // 2, max(0, (sh - default_h) // 2 - S(20))
            root.geometry(f"{default_w}x{default_h}+{x}+{y}")
        if zoomed:
            root.after(50, lambda: root.state("zoomed"))

    def _save_geometry(self):
        try:
            if self.root.state() == "zoomed":
                self.root.state("normal")
                self.root.update_idletasks()
                self.settings.set("geometry", "zoomed:" + self.root.geometry())
            else:
                self.settings.set("geometry", self.root.geometry())
        except tk.TclError:
            pass

    def on_close(self):
        busy = self.busy_count()
        if busy and not confirm(self.root, "¿Salir de ReNova?",
                                f"Hay {busy} {'trabajo' if busy == 1 else 'trabajos'} en curso. "
                                "Si salís se cancelan y los archivos a medio descargar se borran.",
                                ok="Salir igual", cancel="Seguir"):
            return
        self._shutdown()

    def _shutdown(self):
        if self._closing:
            return
        self._closing = True
        self._save_geometry()
        self.downloads.cancel_all()
        self.converts.cancel_all()
        self.edit.cancel_all()
        deadline = time.monotonic() + 3.0

        def wait():
            busy = self.downloads.mgr.active_count() + self.converts.active_count() + self.edit.active_count()
            if busy and time.monotonic() < deadline:
                self.root.after(100, wait)
                return
            try:
                self.root.destroy()
            finally:
                logging.shutdown()
                os._exit(0)
        self.root.withdraw()
        wait()

    def _tk_error(self, exc, val, tb):
        log.error("Error no controlado en la UI", exc_info=(exc, val, tb))
        try:
            self.toast.show("Ocurrió un error inesperado. Se guardó el detalle en el registro.", "error")
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


def run():
    App().run()
