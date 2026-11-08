# -*- mode: python ; coding: utf-8 -*-
# Build "onedir" (arranque rápido, menos falsos positivos de antivirus que onefile).
# Uso: .venv\Scripts\python -m PyInstaller packaging\ReNova.spec --noconfirm

import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas, binaries, hiddenimports = [], [], []
for pkg in ("yt_dlp", "yt_dlp_ejs", "curl_cffi", "tkinterdnd2", "certifi", "websockets", "brotli",
            "mutagen", "Cryptodome"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:  # paquete opcional ausente
        print(f"[spec] aviso: no se pudo recolectar {pkg}: {e}")

# ffmpeg / ffprobe / node.exe NO pasan por PyInstaller (duplicaría las DLL al analizarlas):
# tools/build.py los copia a dist\ReNova\_internal\bin después del build.
datas += [(os.path.join(ROOT, "assets", f), "assets") for f in ("renova.ico", "renova.png")]

hiddenimports += ["PIL._tkinter_finder", "PIL.ImageTk"]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["customtkinter", "numpy", "pandas", "matplotlib", "IPython", "pytest", "setuptools",
              "pip", "test", "unittest", "pydoc_data"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ReNova",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "assets", "renova.ico"),
    version=os.path.join(SPECPATH, "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ReNova")
