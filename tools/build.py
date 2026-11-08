"""Build completo de ReNova: ejecutable, autodiagnóstico, instalador y versión portable.

Uso (desde la carpeta del proyecto):
    .venv\\Scripts\\python tools\\build.py [--skip-installer] [--skip-selftest]

Requisitos: ffmpeg (RENOVA_FFMPEG_DIR, por defecto C:\\ffmpeg\\bin), Node.js >= 22
(RENOVA_NODE_EXE o en el PATH) e Inno Setup 6 para el instalador.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP_DIR = DIST / "ReNova"
BIN = APP_DIR / "_internal" / "bin"
sys.path.insert(0, str(ROOT))
from renova import APP_VERSION  # noqa: E402


def step(msg):
    print(f"\n=== {msg}", flush=True)


def find_node() -> str:
    cand = os.environ.get("RENOVA_NODE_EXE") or shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    if not cand or not os.path.isfile(cand):
        raise SystemExit("No encontré node.exe (Node.js >= 22). Definí RENOVA_NODE_EXE.")
    out = subprocess.run([cand, "--version"], capture_output=True, text=True).stdout.strip()
    major = int(out.lstrip("v").split(".")[0])
    if major < 22:
        raise SystemExit(f"Node {out} es muy viejo; yt-dlp necesita Node 22 o superior.")
    return cand


def find_iscc() -> str | None:
    for p in (os.environ.get("ISCC"), shutil.which("iscc"),
              r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe")):
        if p and os.path.isfile(p):
            return p
    return None


def main():
    args = set(sys.argv[1:])
    ffmpeg_dir = Path(os.environ.get("RENOVA_FFMPEG_DIR", r"C:\ffmpeg\bin"))
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        if not (ffmpeg_dir / name).is_file():
            raise SystemExit(f"Falta {ffmpeg_dir / name}. Definí RENOVA_FFMPEG_DIR.")
    node = find_node()

    step("Ícono")
    subprocess.check_call([sys.executable, str(ROOT / "tools" / "make_icon.py")])

    step("PyInstaller")
    shutil.rmtree(APP_DIR, ignore_errors=True)
    subprocess.check_call([sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "ReNova.spec"),
                           "--noconfirm", "--distpath", str(DIST), "--workpath", str(ROOT / "build" / "renova")])

    step("Copiando ffmpeg y Node.js")
    BIN.mkdir(parents=True, exist_ok=True)
    for f in ["ffmpeg.exe", "ffprobe.exe", *[Path(p).name for p in glob.glob(str(ffmpeg_dir / "*.dll"))]]:
        shutil.copy2(ffmpeg_dir / f, BIN / f)
    shutil.copy2(node, BIN / "node.exe")
    for lic in ("LICENSE", "LICENSE.txt", "COPYING.txt"):
        src = ffmpeg_dir.parent / lic
        if src.is_file():
            shutil.copy2(src, BIN / f"ffmpeg-{lic}")
    size = sum(f.stat().st_size for f in APP_DIR.rglob("*") if f.is_file())
    print(f"Tamaño de la app: {size / 1024 / 1024:.0f} MB")

    if "--skip-selftest" not in args:
        step("Autodiagnóstico del ejecutable")
        r = subprocess.run([str(APP_DIR / "ReNova.exe"), "--selftest"], timeout=300)
        report_path = Path(os.environ["LOCALAPPDATA"]) / "ReNova" / "selftest.txt"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if r.returncode != 0 or not report.get("ok"):
            raise SystemExit("El autodiagnóstico falló: revisá el informe de arriba.")

    step("Versión portable (.zip)")
    portable = DIST / f"ReNova-{APP_VERSION}-portable.zip"
    portable.unlink(missing_ok=True)
    with zipfile.ZipFile(portable, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in APP_DIR.rglob("*"):
            if f.is_file():
                z.write(f, Path("ReNova") / f.relative_to(APP_DIR))
    print(f"{portable} ({portable.stat().st_size / 1024 / 1024:.0f} MB)")

    if "--skip-installer" not in args:
        step("Instalador (Inno Setup)")
        iscc = find_iscc()
        if not iscc:
            print("Inno Setup 6 no está instalado: se omitió el instalador. "
                  "Instalalo con:  winget install JRSoftware.InnoSetup")
        else:
            subprocess.check_call([iscc, f"/DAppVersion={APP_VERSION}", f"/DSourceDir={APP_DIR}",
                                   f"/DOutDir={DIST}", str(ROOT / "packaging" / "installer.iss")])
            for f in DIST.glob("ReNova-Setup-*.exe"):
                print(f"{f} ({f.stat().st_size / 1024 / 1024:.0f} MB)")
            step("Manifiesto de actualización (latest.json)")
            write_manifest(DIST / f"ReNova-Setup-{APP_VERSION}.exe")
    print("\nListo.")


def release_notes(version: str) -> list[str]:
    """Viñetas de la sección `## <versión>` de CHANGELOG.md."""
    notes, inside = [], False
    for line in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            inside = line[3:].strip() == version
            continue
        if inside:
            if line.startswith("- "):
                notes.append(line[2:].strip())
            elif line.startswith("  ") and notes:
                notes[-1] += " " + line.strip()
    return notes


def write_manifest(setup: Path) -> Path:
    import datetime
    import hashlib
    from renova import updater
    h = hashlib.sha256()
    with open(setup, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    notes = release_notes(APP_VERSION)
    if not notes:
        print(f"AVISO: CHANGELOG.md no tiene viñetas para {APP_VERSION}; el aviso saldrá sin novedades.")
    manifest = {"version": APP_VERSION, **updater.release_urls(APP_VERSION), "sha256": h.hexdigest(),
                "size": setup.stat().st_size, "notes": notes, "date": datetime.date.today().isoformat()}
    out = DIST / "latest.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return out


if __name__ == "__main__":
    main()
