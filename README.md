# ReNova 2.4

Descargador y conversor de video para Windows, pensado para edición: todo lo que
descarga sale en **H.264 + AAC dentro de MP4** (8-bit 4:2:0, frame rate constante),
el formato que Premiere Pro importa sin problemas.

## Probar desde el código

```powershell
.venv\Scripts\python main.py
```

Primera vez (crear el entorno):

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Necesita ffmpeg en `C:\ffmpeg\bin` y Node.js 22+ instalado (YouTube exige un runtime JavaScript).

## Generar el instalador

```powershell
.venv\Scripts\python tools\build.py
```

Hace todo: ícono → PyInstaller → copia ffmpeg y Node.js → autodiagnóstico del `.exe`
(incluye una extracción real de YouTube) → `dist\ReNova-<versión>-portable.zip` →
`dist\ReNova-Setup-<versión>.exe` (Inno Setup 6: `winget install JRSoftware.InnoSetup`) →
`dist\latest.json` (manifiesto de actualización).

## Publicar una versión nueva

Las copias instaladas (2.3 en adelante) consultan cada 12 horas
`https://github.com/Leoy3/ReNova/releases/latest/download/latest.json` y, si hay una versión más
nueva, la ofrecen con un aviso en el encabezado. Al aceptar, descargan el instalador, verifican el
SHA-256 y lo ejecutan en silencio: cierra ReNova, reemplaza los archivos y lo vuelve a abrir.

1. Subí `APP_VERSION` en `renova/__init__.py` y en `packaging/version_info.txt`.
2. Escribí las novedades en `CHANGELOG.md` bajo `## <versión>` (salen en el aviso, en lenguaje de usuario).
3. `.venv\Scripts\python tools\build.py`
4. `.venv\Scripts\python tools\publish.py` (necesita `gh` con sesión iniciada; la primera vez
   con `--create-repo`).

La copia portable no se actualiza sola: el aviso abre la página de descarga.

## Diagnóstico en otra PC

Si algo falla en la PC de otra persona:

- `ReNova.exe --selftest` escribe un informe en `%LOCALAPPDATA%\ReNova\selftest.txt`.
- Los registros están en `%LOCALAPPDATA%\ReNova\logs` (Ajustes → Abrir registros).

## Estructura

| Carpeta | Contenido |
|---|---|
| `renova/core.py` | Cola de descargas, análisis, playlists, recorte, reintentos, conversión |
| `renova/media.py` | ffmpeg: compatibilidad Premiere, recortes, encoders por hardware, cuadros |
| `renova/engine.py` | Carga y auto-actualización de yt-dlp desde PyPI |
| `renova/health.py` | Prueba real de YouTube (indicador del pie: funciona / falla / actualización lista) |
| `renova/updater.py` | Versiones nuevas de ReNova: manifiesto, descarga verificada, instalación silenciosa |
| `renova/sequence.py` | Línea de tiempo de Recortar: clips, ediciones estilo Premiere y exportación |
| `renova/playback.py` | Reproductores: video vía ffmpeg, audio vía waveOut de Windows, secuencias sin cortes, miniaturas y forma de onda |
| `renova/ui/` | Interfaz: pestañas Descargar, Convertir (`convert_tab.py`) y Recortar (`edit_tab.py` + `editor.py` + `timeline.py`); recorte antes de descargar en `trim.py` |
| `tools/` | `build.py`, `publish.py` (GitHub Releases), `make_icon.py`, `ui_snapshot.py` (pruebas visuales) |
| `packaging/` | Spec de PyInstaller e instalador de Inno Setup |
| `legacy/` | Versión anterior (`app.py` de un solo archivo) |
