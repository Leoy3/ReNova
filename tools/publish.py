"""Publica la versión compilada en GitHub Releases para que ReNova la ofrezca como actualización.

Uso (después de tools/build.py):
    .venv\\Scripts\\python tools\\publish.py [--create-repo]

Sube a la release `v<versión>` del repositorio `renova.updater.REPO`:
    ReNova-Setup-<versión>.exe, ReNova-<versión>-portable.zip y latest.json
Las copias instaladas leen https://github.com/<REPO>/releases/latest/download/latest.json.
Requiere GitHub CLI (`gh`) con sesión iniciada. `--create-repo` crea el repositorio público (sólo
con un README; el código no se sube) si todavía no existe.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT))
from renova import APP_VERSION, updater  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
from build import release_notes  # noqa: E402


def gh(*args, check=True, capture=False):
    r = subprocess.run(["gh", *args], text=True, capture_output=capture)
    if check and r.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} falló:\n{(r.stderr or '') if capture else ''}")
    return r


def main():
    repo, version = updater.REPO, APP_VERSION
    tag = f"v{version}"
    setup = DIST / f"ReNova-Setup-{version}.exe"
    portable = DIST / f"ReNova-{version}-portable.zip"
    manifest = DIST / "latest.json"
    for f in (setup, manifest):
        if not f.is_file():
            raise SystemExit(f"Falta {f}. Corré primero tools/build.py.")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("version") != version:
        raise SystemExit(f"latest.json es de la versión {data.get('version')}, no de {version}. Recompilá.")
    assets = [str(setup), str(manifest)] + ([str(portable)] if portable.is_file() else [])

    if gh("repo", "view", repo, check=False, capture=True).returncode != 0:
        if "--create-repo" not in sys.argv:
            raise SystemExit(f"No existe el repositorio {repo}. Volvé a correr con --create-repo para crearlo.")
        print(f"Creando el repositorio público {repo}…")
        gh("repo", "create", repo, "--public", "--add-readme",
           "--description", "ReNova: descargador y conversor de video (H.264 compatible con Premiere)")

    notes = "\n".join(f"- {n}" for n in release_notes(version)) or "- Mejoras y correcciones."
    notes += ("\n\n**Para instalar:** descargá `ReNova-Setup-" + version + ".exe` y abrilo. "
              "Si ya tenés ReNova 2.3 o posterior, se actualiza solo desde el programa.")
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tmp:
        tmp.write(notes)

    if gh("release", "view", tag, "--repo", repo, check=False, capture=True).returncode == 0:
        print(f"La release {tag} ya existe: reemplazo los archivos.")
        gh("release", "upload", tag, *assets, "--repo", repo, "--clobber")
        gh("release", "edit", tag, "--repo", repo, "--notes-file", tmp.name, "--latest")
    else:
        print(f"Publicando {tag}…")
        gh("release", "create", tag, *assets, "--repo", repo, "--title", f"ReNova {version}",
           "--notes-file", tmp.name, "--latest")
    Path(tmp.name).unlink(missing_ok=True)

    print("Verificando lo que van a ver las copias instaladas…")
    for _ in range(10):
        try:
            req = urllib.request.Request(updater.MANIFEST_URL + f"?t={int(time.time())}",
                                         headers={"User-Agent": "ReNova-publish"})
            with urllib.request.urlopen(req, timeout=30) as r:
                live = json.loads(r.read())
            if live.get("version") == version:
                print(f"Listo: {updater.MANIFEST_URL} ofrece la {version}.")
                return
        except Exception as e:  # noqa: BLE001
            print("  todavía no:", e)
        time.sleep(3)
    raise SystemExit("La release se subió pero el manifiesto público todavía no muestra la versión nueva.")


if __name__ == "__main__":
    main()
