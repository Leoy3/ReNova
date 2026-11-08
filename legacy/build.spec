# -*- mode: python ; coding: utf-8 -*-
import sys
import os
import glob
from PyInstaller.utils.hooks import collect_data_files, collect_all

block_cipher = None

# Collect assets for each dependency
ctk_datas,  ctk_binaries,  ctk_hiddenimports  = collect_all('customtkinter')
try:
    curl_datas, curl_binaries, curl_hiddenimports = collect_all('curl_cffi')
except Exception:
    curl_datas, curl_binaries, curl_hiddenimports = [], [], []

# Bundle ffmpeg binaries (exe + DLLs) so the app is fully self-contained
_ffmpeg_src = r'C:\ffmpeg\bin'
_ffmpeg_files = glob.glob(os.path.join(_ffmpeg_src, '*.exe')) + \
                glob.glob(os.path.join(_ffmpeg_src, '*.dll'))
ffmpeg_datas = [(f, 'ffmpeg_bin') for f in _ffmpeg_files]

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=ctk_binaries + curl_binaries,
    datas=ctk_datas + curl_datas + ffmpeg_datas,
    hiddenimports=ctk_hiddenimports + curl_hiddenimports + [
        'yt_dlp',
        'yt_dlp.extractor',
        'yt_dlp.extractor.lazy_extractors',
        'yt_dlp.networking.impersonate',
        'curl_cffi',
        'curl_cffi.requests',
        'PIL',
        'PIL._tkinter_finder',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='YT-Downloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
