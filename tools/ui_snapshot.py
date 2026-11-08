"""Prueba visual: abre ReNova, ejecuta un guion de acciones y guarda capturas.

Uso: python tools/ui_snapshot.py <carpeta_salida> [escenario]
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import faulthandler  # noqa: E402

from PIL import ImageGrab  # noqa: E402

faulthandler.dump_traceback_later(90, exit=True)

from renova import paths  # noqa: E402

paths.setup_logging()
from renova.ui import app as app_mod  # noqa: E402

OUT = sys.argv[1]
SCENARIO = sys.argv[2] if len(sys.argv) > 2 else "main"
os.makedirs(OUT, exist_ok=True)

a = app_mod.App()
root = a.root
# Las pruebas descargan a una carpeta temporal (no se guarda en las preferencias)
a.settings.set("out_dir", os.path.join(OUT, "files"), save=False)
os.makedirs(a.settings.out_dir, exist_ok=True)
a._update_path_label()


def set_trim(d, s, e):
    d.editor._set_mark("start", s)
    d.editor._set_mark("end", e)
root.attributes("-topmost", True)
steps = []


def snap(name, win=None):
    win = win or root
    win.update()
    x, y = win.winfo_rootx(), win.winfo_rooty()
    w, h = win.winfo_width(), win.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(os.path.join(OUT, f"{name}.png"))
    print("snap", name, flush=True)


def wait_until(cond, timeout, then):
    t0 = time.monotonic()

    def check():
        if cond() or time.monotonic() - t0 > timeout:
            then()
        else:
            root.after(300, check)
    check()


def run_steps():
    if not steps:
        root.after(300, lambda: os._exit(0))
        return
    delay, fn = steps.pop(0)
    root.after(delay, lambda: (fn(), run_steps()))


def find_toplevel(cls_name):
    for w in root.winfo_children():
        if w.__class__.__name__ == cls_name:
            return w
    return None


if SCENARIO == "main":
    dl = a.downloads
    steps += [
        (1500, lambda: snap("01_empty")),
        (100, lambda: dl.add_urls(["https://www.youtube.com/watch?v=aqz-KE-bpKQ",
                                   "https://www.youtube.com/watch?v=jNQXAC9IVRw",
                                   "https://example.com/not-a-video"])),
        (900, lambda: snap("02_analyzing")),
        (9000, lambda: snap("03_ready")),
        (100, lambda: dl.open_trim(dl.items[-1] if dl.items[-1].duration and dl.items[-1].duration > 100 else
                                   next(i for i in dl.items if (i.duration or 0) > 100))),
        (300, lambda: set_trim(find_toplevel("TrimDialog"), 30.0, 44.5)),
        (8000, lambda: snap("04_trim", find_toplevel("TrimDialog"))),
        (100, lambda: find_toplevel("TrimDialog")._apply()),
        (600, lambda: snap("05_trim_applied")),
        (100, lambda: dl._format_menu_for(next(i for i in dl.items if (i.duration or 0) > 100),
                                          root.winfo_rootx() + 300, root.winfo_rooty() + 300)),
        (700, lambda: snap("06_format_menu")),
        (100, lambda: app_mod.PopupMenu._current and app_mod.PopupMenu._current.close()),
        (100, lambda: dl.start_all()),
        (5000, lambda: snap("07_downloading")),
        (25000, lambda: snap("08_done")),
        (100, lambda: dl.url._context_menu(type("E", (), {"x_root": root.winfo_rootx() + 400,
                                                          "y_root": root.winfo_rooty() + 120})())),
        (600, lambda: snap("09_context")),
        (100, lambda: app_mod.PopupMenu._current and app_mod.PopupMenu._current.close()),
    ]
elif SCENARIO == "playlist":
    dl = a.downloads

    def close_pl():
        d = find_toplevel("PlaylistDialog")
        if d:
            d._set_all(False)
            for i in (0, 2, 3):
                d.selected.add(i)
            d._redraw()
            d._update_counts()
            snap("11_playlist_partial", d)
            d._finish(False)
    steps += [
        (800, lambda: dl.add_urls(["https://www.youtube.com/playlist?list=PLzMcBGfZo4-mP7qA9cagf68V06sko5otr"])),
        (9000, lambda: snap("10_playlist", find_toplevel("PlaylistDialog"))),
        (300, close_pl),
        (2500, lambda: snap("12_after_playlist")),
    ]
elif SCENARIO == "edit":
    os.environ["RENOVA_MUTE_PREVIEW"] = "1"
    media_dir = os.path.join(os.environ["TMP"], "renova_test", "media")
    st = {}

    class Ev:
        pass

    def ev(x, y, state=0):
        tl = a.edit.editor.timeline
        e = Ev()
        e.x, e.y, e.state, e.delta = int(x), int(y), state, 0
        e.x_root, e.y_root = tl.winfo_rootx() + e.x, tl.winfo_rooty() + e.y
        return e

    def ed():
        return a.edit.editor

    def lay():
        return [(c.src.name[:8], round(c.t_in, 2), round(c.t_out, 2), round(c.start, 2)) for c in ed().seq.clips]

    def load():
        a.edit.load([os.path.join(media_dir, f) for f in ("test_720.mp4", "bbb_360.mp4", "tone.mp3")])

    def cuts():
        e = ed()
        e.seq.trim(e.seq.clips[1].id, "out", 32.0, ripple=True)  # bbb: sólo 12 s (el resto se corre)
        e.changed()
        e.timeline.fit()
        e.seek(3.0); e.add_edit()
        e.seek(5.0); e.add_edit()
        c = e.seq.clip_at(4.0)
        e.set_selection({c.id}); e.delete(True)
        e.timeline.fit()
        e.seek(6.0)
        print("layout after cuts:", lay(), flush=True)

    def drag_start():
        tl = ed().timeline
        c = ed().seq.clips[2]  # bbb
        st["x"] = tl.t2x(c.start + 2.0)
        st["y"] = (tl.v_top + tl.v_bot) // 2
        tl._press(ev(st["x"], st["y"]))
        tl._b1_motion(ev(st["x"] - 40, st["y"]))
        tl._b1_motion(ev(st["x"] - tl.pps * 9.0, st["y"]))

    def drag_rearrange():
        tl = ed().timeline
        tl._b1_motion(ev(st["x"] - tl.pps * 17.5, st["y"], state=0x0004 | 0x20000))

    def drag_end():
        tl = ed().timeline
        tl._release(ev(st["x"] - tl.pps * 17.5, st["y"], state=0x0004 | 0x20000))
        print("layout after rearrange:", lay(), "undo:", len(ed()._undo), flush=True)

    def trim_start():
        tl = ed().timeline
        c = ed().seq.clips[0]
        ed().seek(c.end - 1.0)
        x = tl.t2x(c.end) - 2
        st["tx"], st["ty"] = x, (tl.a_top + tl.a_bot) // 2
        tl._press(ev(x, st["ty"]))
        tl._b1_motion(ev(x - 10, st["ty"]))
        tl._b1_motion(ev(tl.t2x(ed().pos) + 4, st["ty"]))  # cerca del cabezal: se pega

    def trim_end():
        tl = ed().timeline
        tl._release(ev(tl.t2x(ed().pos) + 4, st["ty"]))
        c = ed().seq.clips[0]
        print("trim snapped to playhead:", round(c.end, 3), "pos:", round(ed().pos, 3), flush=True)

    def play_loop():
        e = ed()
        c = e.seq.clips[1]
        e.set_selection({c.id})
        e.set_loop(True)
        e.seek(c.start)
        e.play()
        st["t0"] = time.monotonic()
        st["range"] = e.loop_range()

    def sample():
        e = ed()
        print("loop range:", [round(x, 2) for x in st["range"]], "pos:", round(e.pos, 2), "playing:", e.playing,
              "elapsed:", round(time.monotonic() - st["t0"], 2), flush=True)

    def razor():
        e = ed()
        e.set_loop(False)
        e.set_tool("razor")
        tl = e.timeline
        tl._motion(ev(tl.t2x(e.seq.clips[2].start + 1.3), (tl.v_top + tl.v_bot) // 2))

    def check_export():
        r = a.edit.result
        from renova import media as m
        pr = m.probe(r)
        print("exported:", os.path.basename(r), "dur:", round(m.duration_of(pr), 3), "seq:",
              round(ed().seq.duration, 3), [(x["codec_name"], x.get("width")) for x in pr["streams"]], flush=True)

    steps += [
        (300, lambda: a.show_tab("edit")),
        (700, lambda: snap("70_edit_empty")),
        (100, load),
        (4500, lambda: snap("71_edit_loaded")),
        (100, cuts),
        (1500, lambda: snap("72_edit_cuts")),
        (100, drag_start),
        (500, lambda: snap("73_edit_drag_overwrite")),
        (100, drag_rearrange),
        (500, lambda: snap("74_edit_drag_rearrange")),
        (100, drag_end),
        (100, trim_start),
        (600, lambda: snap("75_edit_trim_snap")),
        (100, trim_end),
        (100, play_loop),
        (2500, sample),
        (10, lambda: snap("76_edit_loop_playing")),
        (3500, sample),
        (100, lambda: ed().pause()),
        (100, razor),
        (500, lambda: snap("77_edit_razor")),
        (100, lambda: ed().set_tool("select")),
        (100, lambda: ed()._shortcuts_menu()),
        (500, lambda: snap("78_edit_shortcuts", ed()._shortcuts_dlg)),
        (100, lambda: ed()._shortcuts_dlg.close()),
        (100, lambda: a.edit.export()),
        (1200, lambda: snap("79_edit_exporting")),
        (9000, check_export),
        (200, lambda: snap("80_edit_done")),
    ]
elif SCENARIO == "trimdlg":
    os.environ["RENOVA_MUTE_PREVIEW"] = "1"
    from renova import core as core_mod
    from renova.ui.theme import S as S_
    local = os.path.join(os.environ["TMP"], "renova_test", "media", "test_720.mp4")
    item = core_mod.DownloadItem("https://example.com/v", "video", "1080")
    item.title, item.duration, item.info = "Prueba de recorte (local)", 20.0, {"formats": []}
    a.downloads.mgr.fetch_preview = lambda it, cancel, on_info, on_progress, on_done: on_done(local, None)
    st = {}

    def dlg():
        return find_toplevel("TrimDialog")

    def open_dlg():
        from renova.ui.trim import TrimDialog
        TrimDialog(a, item, lambda r: print("applied:", r, flush=True))

    def handle_drag():
        ed_ = dlg().editor
        ed_._set_mark("start", 2.0)
        ed_._set_mark("end", 15.0)
        ed_.seek(6.0)
        tl = ed_.timeline
        tl._drag = "start"
        tl._apply(tl.t2x(6.0) + 5, final=False)  # a 5 px del cabezal → se pega
        print("start while dragging:", round(ed_.start, 3), "playhead:", round(ed_.pos, 3), "snap:", tl.snap_t,
              flush=True)

    def handle_release():
        ed_ = dlg().editor
        tl = ed_.timeline
        tl._apply(tl.t2x(6.0) + 5, final=True)
        tl._drag = None
        print("start after release:", round(ed_.start, 3), flush=True)

    def loop_play():
        ed_ = dlg().editor
        ed_._set_mark("end", 8.0)
        ed_.set_loop(True)
        ed_.seek(7.0)
        ed_.toggle_play()
        st["t0"] = time.monotonic()

    def zoom_in():
        tl = dlg().editor.timeline
        tl.set_view(5.0, 9.0)
        print("zoom view:", tl.v0, tl.v1, "thumbs:", len(tl._thumbs), flush=True)

    def scroll_drag():
        tl = dlg().editor.timeline
        a, b = tl._scroll_thumb()
        before = tl.v0
        e = type("E", (), {"x": int((a + b) / 2), "y": tl.SB_Y and int(S_(tl.SB_Y) + 4)})()
        tl._press(e)
        e.x += int((tl.x1 - tl.x0) * 0.4)
        tl._move(e)
        tl._release(e)
        print("scroll drag zone:", tl._zone(int((a + b) / 2), int(S_(tl.SB_Y) + 4)), "v0:", round(before, 2), "->",
              round(tl.v0, 2), "thumbs:", len(tl._thumbs), flush=True)

    def sample():
        ed_ = dlg().editor
        print("loop pos:", round(ed_.pos, 2), "playing:", ed_.playing, "range:", ed_.start, ed_.end,
              "elapsed:", round(time.monotonic() - st["t0"], 2), flush=True)

    steps += [
        (300, open_dlg),
        (2500, lambda: snap("81_trim_ready", dlg())),
        (100, handle_drag),
        (500, lambda: snap("82_trim_snap", dlg())),
        (100, handle_release),
        (100, loop_play),
        (1800, sample),
        (10, lambda: snap("83_trim_loop", dlg())),
        (2000, sample),
        (100, lambda: (dlg().editor.set_loop(False), dlg().editor.pause())),
        (100, zoom_in),
        (3000, lambda: snap("84_trim_zoom", dlg())),
        (100, scroll_drag),
        (2500, lambda: snap("85_trim_zoom_scrolled", dlg())),
        (100, lambda: dlg()._apply()),
    ]
elif SCENARIO == "update":
    # Correr con LOCALAPPDATA apuntando a una carpeta temporal (no toca las preferencias reales)
    import functools
    import hashlib
    import http.server
    import json
    import threading

    srv = os.path.join(OUT, "srv")
    os.makedirs(srv, exist_ok=True)
    payload = os.urandom(4 * 1024 * 1024)
    with open(os.path.join(srv, "setup.exe"), "wb") as f:
        f.write(payload)

    class Slow(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def copyfile(self, src, dst):
            while chunk := src.read(64 * 1024):
                dst.write(chunk)
                time.sleep(0.05)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Slow, directory=srv))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    with open(os.path.join(srv, "latest.json"), "w", encoding="utf-8") as f:
        json.dump({"version": "2.4.0", "url": f"{base}/setup.exe", "sha256": hashlib.sha256(payload).hexdigest(),
                   "size": len(payload), "date": "2026-11-20", "page": "https://example.com",
                   "notes": ["Nueva pestaña para extraer el audio de varios videos a la vez.",
                             "La vista previa del recorte carga el doble de rápido en videos largos.",
                             "Corrige un error al descargar listas de reproducción privadas."]}, f)
    os.environ["RENOVA_UPDATE_URL"] = f"{base}/latest.json"
    os.environ["RENOVA_UPDATE_DRYRUN"] = "1"
    up = a.updates

    def dlg():
        return up.dialog
    steps += [
        (8000, lambda: snap("60_update_found")),
        (100, up.open_dialog),
        (700, lambda: snap("61_update_dialog", dlg())),
        (100, up.start_download),
        (1500, lambda: snap("62_update_downloading", dlg())),
        (100, lambda: snap("63_update_badge_dl")),
        (4000, lambda: print("state:", up.state, "path:", up.path, flush=True)),
        (100, lambda: snap("64_update_ready", dlg())),
        (1500, lambda: dlg() and dlg().close()),
        (300, lambda: snap("65_update_badge_ready")),
        (100, a.open_settings),
        (800, lambda: snap("66_settings", find_toplevel("SettingsDialog"))),
    ]
elif SCENARIO == "convert":
    cv = a.converts
    media_dir = os.path.join(os.environ["TMP"], "renova_test", "media")

    def add():
        from PIL import Image
        png = os.path.join(OUT, "logo prueba.png")
        Image.new("RGBA", (300, 200), (244, 79, 98, 255)).save(png)
        cv.add_files([os.path.join(media_dir, "test_720.mp4"), os.path.join(media_dir, "bbb_360.mp4"),
                      os.path.join(media_dir, "tone.mp3"), png])

    def pick():
        by = {os.path.basename(i.src): i for i in cv.items}
        cv.set_target(by["test_720.mp4"], "gif")
        print("targets:", [(os.path.basename(i.src), i.kind, i.fmt, i.target, i.opts) for i in cv.items], flush=True)

    def only_short():
        for it in list(cv.items):
            if os.path.basename(it.src) == "bbb_360.mp4":
                cv.remove(it)

    def results():
        for it in cv.items:
            print("result:", os.path.basename(it.src), it.status, it.result, it.filepath and
                  os.path.basename(it.filepath), it.error, flush=True)

    steps += [
        (800, lambda: a.show_tab("convert")),
        (600, lambda: snap("90_convert_empty")),
        (100, add),
        (3000, lambda: snap("91_convert_detected")),
        (100, pick),
        (400, lambda: snap("92_convert_gif")),
        (100, only_short),
        (100, cv.start_all),
        (1200, lambda: snap("93_convert_running")),
        (7000, results),
        (100, lambda: snap("94_convert_done")),
    ]

elif SCENARIO == "drop":
    # Simula lo que hace el Explorador de Windows al soltar archivos (tipo CF_HDROP, el camino real de tkdnd)
    import ctypes
    from ctypes import wintypes
    media_dir = os.path.join(os.environ["TMP"], "renova_test", "media")
    files = [os.path.join(media_dir, "test_720.mp4"), os.path.join(media_dir, "tone.mp3")]
    st = {}

    def drop(widget, paths, dx=None):
        widget.update()
        x = widget.winfo_rootx() + (dx if dx is not None else widget.winfo_width() // 2)
        y = widget.winfo_rooty() + widget.winfo_height() // 2
        w = widget._w
        root.tk.call("::tkdnd::olednd::HandleDragEnter", w, ("CF_HDROP",), ("copy",), 0, x, y, (15,))
        root.tk.call("::tkdnd::olednd::HandleDragOver", w, 0, x, y)
        res = root.tk.call("::tkdnd::olednd::HandleDrop", w, 0, x, y, "CF_HDROP", tuple(p.replace("\\", "/") for p in paths))
        print("drop on", widget.winfo_class(), "->", res, flush=True)

    def vol_of(stream):
        api = ctypes.WinDLL("winmm")
        api.waveOutGetVolume.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        v = wintypes.DWORD()
        api.waveOutGetVolume(stream.h, ctypes.byref(v))
        return v.value & 0xFFFF

    def convert_drop():
        a.show_tab("convert")
        drop(a.converts.drop, files)

    def convert_check():
        print("convert items:", [(os.path.basename(i.src), i.kind, i.fmt) for i in a.converts.items], flush=True)
        drop(a.converts.cards[a.converts.items[0].id], files[:1])  # también sobre una tarjeta

    def edit_drop():
        print("convert items after card drop:", len(a.converts.items), flush=True)
        a.show_tab("edit")
        drop(a.edit.empty, files)

    def edit_check():
        ed = a.edit.editor
        print("edit clips:", [(c.src.name, round(c.start, 2), round(c.end, 2)) for c in ed.seq.clips], flush=True)
        tl = ed.timeline
        drop(tl, files[:1], dx=int(tl.t2x(10.0)))  # en la línea, cerca del corte de los 20 s → se inserta ahí

    def edit_check2():
        ed = a.edit.editor
        print("after timeline drop:", [(c.src.name, round(c.start, 2), round(c.end, 2)) for c in ed.seq.clips], flush=True)
        drop(ed.view, files[1:])  # sobre el visor → al final

    def volume():
        ed = a.edit.editor
        print("after viewer drop:", [(c.src.name, round(c.start, 2)) for c in ed.seq.clips], flush=True)
        vc = ed.vol
        vc.value, vc.muted = 0.03, False
        vc._commit()
        ed.seek(1.0)
        ed.play()

    def volume_check():
        ed = a.edit.editor
        st["v1"] = vol_of(ed.audio) if ed.audio else None
        ed.vol.value = 0.01
        ed.vol.muted = False
        ed.vol._commit()
        st["v2"] = vol_of(ed.audio) if ed.audio else None
        ed.vol._press(type("E", (), {"x": 5, "y": 5})())  # clic en el parlante: silenciar
        st["v3"] = vol_of(ed.audio) if ed.audio else None
        print("waveOut volume 3% / 1% / muted:", st, "settings:", a.settings.get("preview_volume"),
              a.settings.get("preview_muted"), flush=True)

    steps += [
        (800, convert_drop),
        (2500, convert_check),
        (1500, lambda: snap("95_drop_convert")),
        (100, edit_drop),
        (3500, edit_check),
        (2000, edit_check2),
        (2000, volume),
        (1500, volume_check),
        (300, lambda: snap("96_drop_edit_volume")),
        (100, lambda: a.edit.editor.pause()),
    ]

elif SCENARIO == "zoomplay":
    # Zoom con la rueda mientras se reproduce en Recortar: el visor no debe congelarse
    os.environ["RENOVA_MUTE_PREVIEW"] = "1"
    media_dir = os.path.join(os.environ["TMP"], "renova_test", "media")
    st = {"frames": 0}

    def load():
        a.show_tab("edit")
        names = ("long_1080.mp4",) if os.environ.get("ZP_LONG") else ("bbb_360.mp4", "test_720.mp4", "tone.mp3")
        a.edit.load([os.path.join(media_dir, f) for f in names])

    def play():
        ed = a.edit.editor
        orig = ed.view.show_rgb

        def counted(*args):
            st["frames"] += 1
            h_ = hash(bytes(args[2][:4096]) + bytes(args[2][-4096:])) if len(args) > 2 else 0
            st.setdefault("hashes", set()).add(h_)
            return orig(*args)
        ed.view.show_rgb = counted
        ed.seek(60.0)
        ed.play()
        st["t0"] = time.monotonic()

    def wheel(delta, n=8):
        tl = a.edit.editor.timeline

        def one(k):
            t_ = time.monotonic()
            x, y = tl.winfo_rootx() + tl.winfo_width() // 2, tl.winfo_rooty() + tl.v_top + 10
            tl.event_generate("<MouseWheel>", delta=delta, x=tl.winfo_width() // 2, y=tl.v_top + 10,
                              rootx=x, rooty=y, when="now")
            st.setdefault("wheel_ms", []).append(round((time.monotonic() - t_) * 1000))
            if k + 1 < n:
                root.after(60, lambda: one(k + 1))
        one(0)

    def kill_video():
        ed = a.edit.editor
        v = ed.video
        v._stop.set()  # el lector deja de entregar cuadros, como si ffmpeg se hubiera colgado
        for p_ in list(v._procs.values()):
            p_.kill()
        st["hashes"] = set()

    def stall_clock():
        ed = a.edit.editor
        if ed.audio is not None:
            frozen = ed.audio.clock()
            ed.audio.clock = lambda: frozen  # el audio deja de avanzar
        st["hashes"] = set()

    def sample(tag):
        ed = a.edit.editor
        el = time.monotonic() - st["t0"]
        distinct = len(st.get("hashes", ()))
        st["hashes"] = set()
        print(f"{tag}: pos={ed.pos:.2f} playing={ed.playing} frames={st['frames']} distinct={distinct} elapsed={el:.2f} "
              f"pps={ed.timeline.pps:.2f} wheel_ms={st.get('wheel_ms')}", flush=True)
        st["wheel_ms"] = []

    if os.environ.get("ZP_MAX"):
        root.state("zoomed")
    flood = int(os.environ.get("ZP_FLOOD", "0"))
    if flood:
        _wheel_orig = wheel

        def wheel(delta, n=8):  # noqa: F811  muchas vueltas seguidas (rueda libre / touchpad)
            tl = a.edit.editor.timeline
            for k in range(flood):
                x, y = tl.winfo_rootx() + tl.winfo_width() // 2, tl.winfo_rooty() + tl.v_top + 10
                root.after(k * 8, lambda d=(delta if (k // 6) % 2 == 0 else -delta): tl.event_generate(
                    "<MouseWheel>", delta=d, x=tl.winfo_width() // 2, y=tl.v_top + 10, rootx=x, rooty=y))
    steps += [
        (300, load),
        (4500, play),
        (1500, lambda: sample("before zoom")),
        (10, lambda: wheel(120, 10)),
        (1500, lambda: sample("after zoom in")),
        (1500, lambda: sample("later")),
        (10, lambda: wheel(-120, 10)),
        (1500, lambda: sample("after zoom out")),
        (1500, lambda: sample("later2")),
        (10, lambda: snap("97_zoomplay")),
        (10, kill_video),
        (2500, lambda: sample("after video killed")),
        (10, stall_clock),
        (2500, lambda: sample("after clock stalled")),
        (100, lambda: a.edit.editor.pause()),
    ]

elif SCENARIO == "trimlong":
    # Recorte antes de descargar con un video largo: zoom, barra para desplazarse y Ctrl + rueda
    os.environ["RENOVA_MUTE_PREVIEW"] = "1"
    from renova import core as core_mod
    from renova.ui.theme import S as S_
    local = os.path.join(os.environ["TMP"], "renova_test", "media", "long_1080.mp4")
    item = core_mod.DownloadItem("https://example.com/v", "video", "1080")
    item.title, item.duration, item.info = "Video largo (prueba)", 240.0, {"formats": []}
    a.downloads.mgr.fetch_preview = lambda it, cancel, on_info, on_progress, on_done: on_done(local, None)

    def dlg():
        return find_toplevel("TrimDialog")

    def open_dlg():
        from renova.ui.trim import TrimDialog
        TrimDialog(a, item, lambda r: None)

    class Ev:
        pass

    def ev(x, y, state=0, delta=0):
        e = Ev()
        e.x, e.y, e.state, e.delta = int(x), int(y), state, delta
        return e

    def zoom():
        tl = dlg().editor.timeline
        tl.set_view(100.0, 104.0)
        a_, b_ = tl._scroll_thumb()
        print(f"zoom: view {tl.v0:.1f}-{tl.v1:.1f} thumb width {b_ - a_:.0f}px at {a_:.0f}", flush=True)

    def drag():
        tl = dlg().editor.timeline
        a_, b_ = tl._scroll_thumb()
        y = S_(tl.SB_Y) + S_(tl.SB_H) // 2
        print("zone on thumb:", tl._zone(int((a_ + b_) / 2), y), flush=True)
        before = tl.v0
        tl._press(ev((a_ + b_) / 2, y))
        tl._move(ev((a_ + b_) / 2 + 120, y))
        tl._release(ev((a_ + b_) / 2 + 120, y))
        a2, b2 = tl._scroll_thumb()
        print(f"drag +120px: v0 {before:.1f} -> {tl.v0:.1f}; thumb moved {a2 - a_:.0f}px", flush=True)

    def ctrl_wheel():
        tl = dlg().editor.timeline
        before = tl.v0
        tl._wheel(ev(0, 0, state=0x0004, delta=-120))
        mid = tl.v0
        tl._wheel(ev(0, 0, state=0x0004, delta=120))
        tl._wheel(ev(0, 0, state=0x0004, delta=120))
        print(f"ctrl+wheel: {before:.2f} -> {mid:.2f} (derecha) -> {tl.v0:.2f} (izquierda); span {tl.v1 - tl.v0:.1f}",
              flush=True)

    steps += [
        (300, open_dlg),
        (4000, zoom),
        (2500, lambda: snap("86_trim_long_zoom", dlg())),
        (100, drag),
        (100, ctrl_wheel),
        (2000, lambda: snap("87_trim_long_scrolled", dlg())),
        (100, lambda: dlg().close()),
    ]

elif SCENARIO == "wheel":
    # Alt + rueda = zoom; rueda sola o con Ctrl = desplazar (en Recortar y en el recorte antes de descargar)
    os.environ["RENOVA_MUTE_PREVIEW"] = "1"
    from renova import core as core_mod
    media_dir = os.path.join(os.environ["TMP"], "renova_test", "media")
    local = os.path.join(media_dir, "long_1080.mp4")
    item = core_mod.DownloadItem("https://example.com/v", "video", "1080")
    item.title, item.duration, item.info = "Video largo (prueba)", 240.0, {"formats": []}
    a.downloads.mgr.fetch_preview = lambda it, cancel, on_info, on_progress, on_done: on_done(local, None)
    E = lambda state, delta: type("E", (), {"state": state, "delta": delta, "x": 0, "y": 0})()  # noqa: E731

    def report(name, tl, v0_attr, scale):
        out = []
        for label, state in (("rueda", 0), ("Ctrl+rueda", 0x0004), ("Shift+rueda", 0x0001), ("Alt+rueda", 0x20000)):
            before = (round(getattr(tl, v0_attr), 2), round(scale(), 3))
            tl._wheel(E(state, -120))
            after = (round(getattr(tl, v0_attr), 2), round(scale(), 3))
            kind = "zoom" if after[1] != before[1] else ("desplaza" if after[0] != before[0] else "nada")
            out.append(f"{label}: {kind}")
        print(name, "·", " | ".join(out), flush=True)

    def edit_test():
        tl = a.edit.editor.timeline
        tl.zoom(4.0, anchor=60.0)
        tl.set_view(50.0)
        report("Recortar", tl, "v0", lambda: tl.pps)

    def dlg_test():
        from renova.ui.trim import TrimDialog
        TrimDialog(a, item, lambda r: None)

    def dlg_report():
        d = find_toplevel("TrimDialog")
        tl = d.editor.timeline
        tl.set_view(100.0, 110.0)
        report("Recorte antes de descargar", tl, "v0", lambda: tl.v1 - tl.v0)
        d.close()

    steps += [
        (300, lambda: (a.show_tab("edit"), a.edit.load([local]))),
        (4000, edit_test),
        (300, dlg_test),
        (3000, dlg_report),
    ]

elif SCENARIO == "convertfiles":
    # Convertir con los archivos de CONV_FILES (separados por ;): para revisar la detección del formato
    files = [f for f in os.environ.get("CONV_FILES", "").split(";") if f]
    steps += [
        (600, lambda: (a.show_tab("convert"), a.converts.add_files(files))),
        (3000, lambda: snap("98_convert_detect")),
    ]

root.after(100, run_steps)
root.after(180000, lambda: os._exit(1))
a.run()
