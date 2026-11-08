"""Punto de entrada de ReNova (también usado por PyInstaller)."""

import os
import sys


def selftest(online: bool) -> int:
    """Diagnóstico: verifica motor, ffmpeg, runtime JS y (opcional) una extracción real.
    Escribe el informe en %LOCALAPPDATA%\\ReNova\\selftest.txt"""
    import json
    import subprocess
    import time
    import traceback
    from renova import APP_VERSION, engine, media, paths

    report = {"app": APP_VERSION, "frozen": paths.FROZEN, "python": sys.version.split()[0]}
    ok = True

    def check(name, fn):
        nonlocal ok
        t = time.time()
        try:
            report[name] = fn()
        except Exception as e:  # noqa: BLE001
            ok = False
            report[name] = f"FALLÓ: {e!r}\n{traceback.format_exc()[-600:]}"
        report[name + "_s"] = round(time.time() - t, 2)

    def run(cmd):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                           creationflags=media.NO_WINDOW)
        return (r.stdout or r.stderr).splitlines()[0]

    check("engine", lambda: (engine.load(), engine.version(),
                             os.path.dirname(engine.get().__file__))[1:])
    check("ffmpeg", lambda: [paths.ffmpeg(), run([paths.ffmpeg(), "-version"])])
    check("ffprobe", lambda: [paths.ffprobe(), run([paths.ffprobe(), "-version"])])
    check("node", lambda: [paths.node(), run([paths.node(), "--version"])])
    check("ejs", lambda: __import__("yt_dlp_ejs").__file__)
    check("encoders", media.Encoders.detect)
    check("audio_api", lambda: bool(__import__("renova.playback", fromlist=["_api"])._api()))
    check("x264", lambda: media.run_ffmpeg(
        ["-f", "lavfi", "-i", "testsrc2=s=320x240:d=0.3", *media.video_args("libx264", 30),
         "-f", "null", "-"]) or "ok")
    check("gif", lambda: media.run_ffmpeg(
        ["-f", "lavfi", "-i", "testsrc2=s=320x240:d=0.4", "-filter_complex", media.gif_filter(10, 160),
         "-map", "[gif]", "-f", "null", "-"]) or "ok")
    def wave():
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import audioop  # noqa: F401  (forma de onda de la línea de tiempo)
        return "ok"
    check("waveform", wave)

    def seq():
        # Línea de tiempo: dos clips de un archivo de prueba con un hueco, exportada a MP4
        from renova import sequence
        tmp = paths.cache_dir() / "selftest"
        tmp.mkdir(parents=True, exist_ok=True)
        clip = str(tmp / "clip.mp4")
        media.run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=2", "-f", "lavfi", "-i",
                          "sine=f=440:d=2", "-shortest", *media.video_args("libx264", 30), *media.AAC_ARGS, clip])
        s = sequence.Sequence()
        src = sequence.Source.from_probe(clip, media.probe(clip))
        s.add(src)
        s.add(src, at=2.5)
        s.split_at(1.0)
        s.ripple_delete({s.clips[0].id})
        out = str(tmp / "seq.mp4")
        sequence.export(s, out, target="mp4")
        d = media.duration_of(media.probe(out))
        if abs(d - s.duration) > 0.1:
            raise RuntimeError(f"duración {d} en lugar de {s.duration}")
        return f"ok ({d:.2f} s)"
    check("sequence", seq)

    def upd():
        from renova import updater
        return {"self_update": updater.can_self_update(), "manifest": updater.manifest_url()}
    check("updater", upd)
    if online:
        # Informativo: no falla el diagnóstico si todavía no hay versiones publicadas
        try:
            from renova import updater
            info = updater.check(timeout=15)
            report["update_check"] = f"hay una versión nueva: {info['version']}" if info else "al día"
        except Exception as e:  # noqa: BLE001
            report["update_check"] = f"no disponible: {e}"

        def yt():
            from renova import health
            res = health.check()
            if res["state"] != "ok":
                raise RuntimeError(f"{res['message']} {res.get('detail', '')[:300]}")
            return res["message"]
        check("youtube", yt)
    report["ok"] = ok
    out = paths.data_dir() / "selftest.txt"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if ok else 1


def main():
    # En el .exe sin consola, stdout/stderr son None: yt-dlp escribe avisos ahí.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    from renova import paths
    log_path = paths.setup_logging()

    if "--selftest" in sys.argv:
        sys.exit(selftest(online="--offline" not in sys.argv))

    import logging
    from renova import APP_VERSION
    logging.getLogger("renova").info("ReNova %s iniciando (log: %s)", APP_VERSION, log_path)

    try:
        from renova.ui.app import run
        run()
    except Exception:
        logging.getLogger("renova").exception("Error fatal")
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, f"ReNova no pudo iniciarse.\n\nEl detalle quedó guardado en:\n{log_path}",
                "ReNova", 0x10)
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
