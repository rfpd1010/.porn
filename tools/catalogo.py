#!/usr/bin/env python3
"""Rastrea páginas de listado y construye catalogo.json + miniaturas locales.
No descarga vídeos: solo metadatos y thumbs. Se ejecuta en GitHub Actions."""
import hashlib, html as htmlmod, io, json, os, re, subprocess, sys, time, unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
THU, SEMILLAS, CAT = ROOT / "thumbs", ROOT / "semillas.txt", ROOT / "catalogo.json"
POR_SEMILLA = int(os.environ.get("POR_SEMILLA", "400"))
MAX_TOTAL = int(os.environ.get("MAX_TOTAL", "20000"))
ANCHO = 320
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

PATRONES = [
    r'href="([^"]*view_video\.php\?viewkey=[^"]+)"',
    r'href="([^"]*/video\.[a-z0-9]+/[^"]+)"',
    r'href="([^"]*/videos/[^"]+)"',
    r'href="([^"]*/video-[^"]+)"',
    r'href="([^"]*/video/[^"]+)"',
]
RUIDO = ("?page=", "/pornstars/", "/channels/", "/categories/", "/tags/",
         "/login", "/signup", "/search", "/best/", "/new/")


def limpiar(t):
    t = re.sub(r"<[^>]+>", " ", t or "")
    return re.sub(r"\s+", " ", htmlmod.unescape(t)).strip()[:140]


def pedir(url, referer=None):
    cab = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    if referer:
        cab["Referer"] = referer
    return urllib.request.urlopen(urllib.request.Request(url, headers=cab), timeout=30).read()


def leer_semillas():
    out = []
    for linea in SEMILLAS.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        p = [x.strip() for x in linea.split("|")]
        out.append((p[0], [t.strip() for t in p[1].split(",") if t.strip()] if len(p) > 1 else []))
    return out


def por_ytdlp(url, limite):
    """Listados que yt-dlp entiende como playlist (canales, modelos, categorías)."""
    cmd = ["yt-dlp", "--flat-playlist", "--playlist-end", str(limite), "-J",
           "--no-warnings", "--socket-timeout", "30", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            return []
        data = json.loads(r.stdout)
    except Exception:
        return []
    out = []
    for e in (data.get("entries") or []):
        u = e.get("url") or e.get("webpage_url")
        if not u:
            continue
        th = e.get("thumbnails") or []
        out.append({"url": u, "titulo": e.get("title") or "Vídeo",
                    "dur": e.get("duration") or 0,
                    "thumb": th[-1].get("url") if th else None})
    return out


def por_html(url, limite):
    """Plan B universal: rastrea los enlaces de la página de listado."""
    try:
        pagina = pedir(url).decode("utf-8", "ignore")
    except Exception as e:
        print("  error al pedir:", e)
        return []
    items, vistos = [], set()
    for m in re.finditer(r'<a\s[^>]*href="([^"]+)"[^>]*>(.{0,700}?)</a>', pagina, re.S | re.I):
        href = m.group(1).replace("&amp;", "&")
        bajo = href.lower()
        if not any(re.search(p, 'href="%s"' % href, re.I) for p in PATRONES):
            continue
        if any(x in bajo for x in RUIDO):
            continue
        u = urljoin(url, href).split("#")[0]
        if u in vistos:
            continue
        vistos.add(u)
        bloque = m.group(2)
        titulo = limpiar(re.search(r'title="([^"]+)"', m.group(0)).group(1)
                         if re.search(r'title="([^"]+)"', m.group(0)) else bloque)
        img = re.search(r'<img[^>]+(?:data-src|data-original|data-thumb|src)="([^"]+)"', bloque)
        items.append({"url": u, "titulo": titulo or "Vídeo", "dur": 0,
                      "thumb": urljoin(url, img.group(1)) if img else None})
        if len(items) >= limite:
            break
    return items


def miniatura(remota, destino, referer):
    try:
        datos = pedir(remota, referer)
        im = Image.open(io.BytesIO(datos)).convert("RGB")
        w, h = im.size
        im = im.resize((ANCHO, max(1, round(h * ANCHO / w))), Image.LANCZOS)
        im.save(destino, "WEBP", quality=68, method=4)
        return True
    except Exception:
        return False


def main():
    THU.mkdir(exist_ok=True)
    catalogo = {}
    if CAT.exists():
        for e in json.loads(CAT.read_text(encoding="utf-8")):
            catalogo[e["id"]] = e

    pendientes_thumbs, nuevas = [], 0
    for semilla, tags in leer_semillas():
        print("semilla:", semilla)
        items = por_ytdlp(semilla, POR_SEMILLA) or por_html(semilla, POR_SEMILLA)
        print("  encontrados:", len(items))
        for it in items:
            vid = hashlib.md5(it["url"].encode()).hexdigest()[:12]
            e = catalogo.get(vid)
            if not e:
                if len(catalogo) >= MAX_TOTAL:
                    continue
                e = {"id": vid, "url": it["url"], "titulo": it["titulo"],
                     "dur": it.get("dur") or 0, "tags": list(dict.fromkeys(tags)),
                     "fuente": semilla, "estado": "pendiente", "thumb": None}
                catalogo[vid] = e
                nuevas += 1
            elif tags:
                e["tags"] = list(dict.fromkeys(e.get("tags", []) + tags))
            if not e.get("thumb") and it.get("thumb"):
                pendientes_thumbs.append((vid, it["thumb"], semilla))
        time.sleep(0.5)

    def bajar_thumbs(t):
        vid, remota, ref = t
        destino = THU / (vid + ".webp")
        if destino.exists() or miniatura(remota, destino, ref):
            catalogo[vid]["thumb"] = f"thumbs/{vid}.webp"

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(bajar_thumbs, pendientes_thumbs[:4000]))

    lista = sorted(catalogo.values(), key=lambda e: e.get("titulo", "").lower())
    CAT.write_text(json.dumps(lista, ensure_ascii=False, indent=1), encoding="utf-8")
    listas = sum(1 for e in lista if e["estado"] == "listo")
    print(f"nuevas: {nuevas} | catalogo: {len(lista)} | descargados: {listas}")


if __name__ == "__main__":
    sys.exit(main())
