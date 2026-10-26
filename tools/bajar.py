#!/usr/bin/env python3
"""Baja vídeos del catálogo, los sube a GitHub Releases y actualiza catalogo.json.
  python tools/bajar.py --id ab12cd34ef56
  python tools/bajar.py --url "https://..." | titulo | etiquetas
  python tools/bajar.py --pendientes 3
"""
import argparse, hashlib, json, os, re, subprocess, sys, unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP, THU, CAT = ROOT / ".tmp", ROOT / "thumbs", ROOT / "catalogo.json"
TAG = "videos"
REPO = os.environ.get("GITHUB_REPOSITORY", "")
ALTURA = int(os.environ.get("ALTURA", "480"))
LIMITE_MB = int(os.environ.get("LIMITE_MB", "95"))
MAX_MIN = int(os.environ.get("MAX_MIN", "60"))


def slug(t):
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return (re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower() or "video")[:60]


def correr(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def descargar(url, base):
    fmt = (f"bv*[height<={ALTURA}][ext=mp4]+ba[ext=m4a]"
           f"/b[height<={ALTURA}][ext=mp4]/b[height<={ALTURA}]/b")
    r = correr(["yt-dlp", "--no-playlist", "--no-warnings", "--retries", "3",
                "--concurrent-fragments", "4", "--max-filesize", f"{LIMITE_MB}M",
                "--match-filter", f"duration < {MAX_MIN*60}",
                "-f", fmt, "--merge-output-format", "mp4", "--remux-video", "mp4",
                "-o", str(base) + ".%(ext)s", "--print", "after_move:filepath", url])
    if r.returncode != 0 or not r.stdout.strip():
        print("  fallo:", (r.stderr.strip().splitlines() or ["?"])[-1][:180])
        return None
    p = Path(r.stdout.strip().splitlines()[-1])
    return p if p.exists() else None


def subir(archivo):
    correr(["gh", "release", "view", TAG]) if True else None
    if correr(["gh", "release", "view", TAG]).returncode != 0:
        correr(["gh", "release", "create", TAG, "--title", "videos",
                "--notes", "Vídeos de la videoteca"])
    r = correr(["gh", "release", "upload", TAG, str(archivo), "--clobber"])
    if r.returncode != 0:
        print("  error subiendo:", r.stderr.strip()[:200])
        return None
    return f"https://github.com/{REPO}/releases/download/{TAG}/{archivo.name}"


def datos(archivo):
    r = correr(["ffprobe", "-v", "error", "-show_entries", "format=duration,size",
                "-of", "json", str(archivo)])
    try:
        d = json.loads(r.stdout)["format"]
        return float(d.get("duration", 0)), int(d.get("size", 0))
    except Exception:
        return 0.0, archivo.stat().st_size


def miniatura(archivo, salida):
    for t in ("3", "1", "0"):
        correr(["ffmpeg", "-y", "-loglevel", "error", "-ss", t, "-i", str(archivo),
                "-frames:v", "1", "-vf", "scale=320:-2", str(salida)])
        if salida.exists() and salida.stat().st_size > 800:
            return True
    return False


def procesar(entrada, catalogo, por_url):
    vid = entrada["id"]
    base = TMP / f"{slug(entrada.get('titulo', 'video'))}-{vid}"
    print("bajando:", entrada.get("titulo"), vid)
    archivo = descargar(entrada["url"], base)
    if not archivo:
        return False
    url_asset = subir(archivo)
    if not url_asset:
        return False
    dur, peso = datos(archivo)
    entrada["estado"] = "listo"
    entrada["archivo"] = url_asset
    entrada["dur"] = round(dur, 1)
    entrada["mb"] = round(peso / 1048576, 1)
    entrada["subido"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not entrada.get("thumb"):
        destino = THU / (vid + ".webp")
        if miniatura(archivo, destino):
            entrada["thumb"] = f"thumbs/{vid}.webp"
    archivo.unlink(missing_ok=True)
    por_url[vid] = entrada
    print("  ok:", peso // 1048576, "MB")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id")
    ap.add_argument("--url")
    ap.add_argument("--pendientes", type=int, default=0)
    a = ap.parse_args()

    TMP.mkdir(exist_ok=True)
    THU.mkdir(exist_ok=True)
    catalogo = json.loads(CAT.read_text(encoding="utf-8")) if CAT.exists() else []
    por_url = {e["id"]: e for e in catalogo}

    objetivos = []
    if a.url:
        partes = [p.strip() for p in a.url.split("|")]
        url = partes[0]
        vid = hashlib.md5(url.encode()).hexdigest()[:12]
        e = por_url.get(vid) or {"id": vid, "url": url, "estado": "pendiente", "thumb": None,
                                 "tags": [t.strip() for t in partes[2].split(",")] if len(partes) > 2 else [],
                                 "titulo": partes[1] if len(partes) > 1 and partes[1] else "Vídeo", "dur": 0}
        por_url[vid] = e
        objetivos = [e]
    elif a.id:
        e = por_url.get(a.id)
        if not e:
            print("id desconocido:", a.id); return 1
        objetivos = [e]
    elif a.pendientes:
        objetivos = [e for e in catalogo if e.get("estado") == "pendiente"][:a.pendientes]
    else:
        print("nada que hacer"); return 0

    ok = sum(1 for e in objetivos if procesar(e, catalogo, por_url))

    lista = sorted(por_url.values(), key=lambda e: e.get("titulo", "").lower())
    CAT.write_text(json.dumps(lista, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"subidos: {ok}/{len(objetivos)}")


if __name__ == "__main__":
    sys.exit(main())
