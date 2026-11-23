"""ViceClipsSix - Radar en la nube con la API oficial de YouTube (YouTube Data API v3).

Corre en GitHub Actions (8:00, 13:00 y 18:00 de Panamá) aunque la PC de Abdiel esté apagada.
Deja reports/AAAA-MM-DD_HHMM_nube.md (+ .json); la oficina los baja al prender la PC y Lucía los revisa.

    python radar_api.py --modo noticias     # solo noticias de las últimas 72 h (cuesta ~900 unidades)
    python radar_api.py --modo completo     # noticias + formatos outlier (~2.900 unidades)
    python radar_api.py --modo completo --prueba   # 2 consultas de cada tipo, para probar la clave

La clave va en la variable de entorno YOUTUBE_API_KEY (secreto de GitHub); nunca se imprime.
Cupo gratis de la API: 10.000 unidades por día. Una búsqueda cuesta 100; leer vistas o canales, 1.
"""
import argparse
import json
import os
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

AQUI = Path(__file__).resolve().parent
REPORTS = AQUI / "reports"
API = "https://www.googleapis.com/youtube/v3/"
PANAMA = timezone(timedelta(hours=-5))
CONSULTAS = json.loads((AQUI / "consultas.json").read_text(encoding="utf-8"))
ESPANOL = re.compile(r"[áéíóúñ¿¡]|\b(cosas|noticias|curiosidades|secretos|sabías|cómo|conseguir)\b", re.I)
NEWS_WINDOW_H = 72
NEWS_MIN_VIEWS = 2000
FORMAT_MIN_VIEWS = 100_000
HOT_OUTLIER = 3.0
FORMAT_OUTLIER = 5.0
SHORT_MAX_S = 180
UNIDADES = {"usadas": 0}


class SinCupo(RuntimeError):
    pass


def api(recurso, costo=1, **params):
    clave = os.environ.get("YOUTUBE_API_KEY")
    if not clave:
        raise SystemExit("Falta YOUTUBE_API_KEY (secreto de GitHub o variable de Windows).")
    r = requests.get(API + recurso, params={**params, "key": clave}, timeout=30)
    UNIDADES["usadas"] += costo
    if r.status_code == 403 and "quota" in r.text.lower():
        raise SinCupo("se acabó el cupo diario de la API de YouTube (vuelve a las 02:00 de Panamá)")
    if r.status_code != 200:
        # el mensaje de Google no incluye la clave; igual se recorta
        raise RuntimeError(f"API {recurso} respondió {r.status_code}: {r.text[:200]}")
    return r.json()


def duracion_s(iso):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def buscar(q, por_fecha):
    es = bool(ESPANOL.search(q))
    p = {"part": "snippet", "q": q, "type": "video", "maxResults": 25,
         "order": "date" if por_fecha else "viewCount" if not es else "relevance",
         "relevanceLanguage": "es" if es else "en", "regionCode": "MX" if es else "US"}
    if por_fecha:
        p["publishedAfter"] = (datetime.now(timezone.utc) - timedelta(hours=NEWS_WINDOW_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [it["id"]["videoId"] for it in api("search", 100, **p).get("items", []) if it.get("id", {}).get("videoId")]


def videos(ids):
    out = {}
    ids = list(dict.fromkeys(ids))
    for i in range(0, len(ids), 50):
        for v in api("videos", 1, part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50])).get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            out[v["id"]] = {"id": v["id"], "title": sn.get("title"), "channel": sn.get("channelTitle"),
                            "channel_id": sn.get("channelId"), "published": sn.get("publishedAt"),
                            "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0) or 0),
                            "comments": int(st.get("commentCount", 0) or 0),
                            "duration": duracion_s(v.get("contentDetails", {}).get("duration"))}
    return out


_CANALES = {}


def canal(cid):
    """Suscriptores y mediana de vistas de sus últimos 15 videos (con ids, para separar Shorts de largos)."""
    if cid in _CANALES:
        return _CANALES[cid]
    it = (api("channels", 1, part="contentDetails,statistics", id=cid).get("items") or [{}])[0]
    subs = int(it.get("statistics", {}).get("subscriberCount", 0) or 0) or None
    up = it.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    ultimos = {}
    if up:
        ids = [x["contentDetails"]["videoId"] for x in
               api("playlistItems", 1, part="contentDetails", playlistId=up, maxResults=20).get("items", [])]
        ultimos = videos(ids)
    _CANALES[cid] = (subs, ultimos)
    return _CANALES[cid]


def mediana(cid, corto, excluir):
    subs, ult = canal(cid)
    vs = [v["views"] for v in ult.values() if v["id"] != excluir and v["views"]
          and ((v["duration"] or 0) <= SHORT_MAX_S) == corto][:15]
    if len(vs) < 3:
        vs = [v["views"] for v in ult.values() if v["id"] != excluir and v["views"]][:15]
    return (float(statistics.median(vs)) if len(vs) >= 3 else None), subs


def ficha(v):
    pub = datetime.fromisoformat(v["published"].replace("Z", "+00:00"))
    horas = max((datetime.now(timezone.utc) - pub).total_seconds() / 3600, 0.1)
    corto = (v["duration"] or 0) <= SHORT_MAX_S
    med, subs = mediana(v["channel_id"], corto, v["id"])
    return {**v, "type": "Short" if corto else "Largo", "hours": round(horas, 1), "subs": subs,
            "views_per_hour": round(v["views"] / horas, 1), "channel_median_views": med,
            "outlier_channel": round(v["views"] / med, 2) if med else None,
            "outlier_subs": round(v["views"] / subs, 2) if subs else None,
            "url": ("https://www.youtube.com/shorts/" if corto else "https://www.youtube.com/watch?v=") + v["id"]}


def noticias(consultas):
    ids = []
    for q in consultas:
        ids += buscar(q, True)
    # sin duración = transmisión en vivo: no sirve como noticia para un Short
    vs = [v for v in videos(ids).values() if v["views"] >= NEWS_MIN_VIEWS and v["duration"]]
    out = []
    for v in vs:
        r = ficha(v)
        if r["hours"] > NEWS_WINDOW_H:
            continue
        r["score"] = round(r["views_per_hour"] * (r["outlier_channel"] or 1), 1)
        r["hot"] = bool(r["outlier_channel"] and r["outlier_channel"] >= HOT_OUTLIER)
        out.append(r)
    return sorted(out, key=lambda r: r["score"], reverse=True)


def formatos(consultas):
    out, vistos = [], set()
    for q in consultas:
        ids = [i for i in buscar(q, False) if i not in vistos]
        vistos.update(ids)
        for v in videos(ids).values():
            if v["views"] < FORMAT_MIN_VIEWS or not v["duration"]:
                continue
            r = ficha(v)
            if r["outlier_channel"] and r["outlier_channel"] >= FORMAT_OUTLIER:
                r["query"] = q
                out.append(r)
    return sorted(out, key=lambda r: r["outlier_channel"] or 0, reverse=True)


def celda(t):
    return str(t or "-").replace("|", "/").replace("\n", " ")


def num(n):
    if n is None:
        return "-"
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.1f}K" if n >= 1e3 else str(n)


def informe(d, nombre):
    L = [f"# Radar ViceClipsSix (nube, API oficial) - {nombre}", "",
         f"Generado {d['generated_at']} (hora de Panamá). Modo: {d['modo']}. Unidades de API usadas: {d['unidades']}. "
         f"Caliente = outlier canal >= {HOT_OUTLIER}x. Formatos = outlier canal >= {FORMAT_OUTLIER}x.", ""]
    L += ["## 1. Noticias calientes (ultimas 72 h)", "",
          "| # | Titulo | Canal | Subs | Vistas | Horas | Vistas/h | Outlier canal | Tipo | Link |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    L += [f"| {i} | {celda(r['title'])} | {celda(r['channel'])} | {num(r['subs'])} | {num(r['views'])} | {r['hours']} | "
          f"{num(r['views_per_hour'])} | {r['outlier_channel'] or '-'}{' 🔥' if r.get('hot') else ''} | {r['type']} | [ver]({r['url']}) |"
          for i, r in enumerate(d["news"][:25], 1)]
    if d["modo"] == "completo":
        L += ["", "## 2. Formatos outlier (para adaptar a GTA 6)", "",
              "| # | Titulo | Canal | Vistas | Mediana canal | Outlier canal | Tipo | Busqueda | Link |",
              "|---|---|---|---|---|---|---|---|---|"]
        L += [f"| {i} | {celda(r['title'])} | {celda(r['channel'])} | {num(r['views'])} | {num(r['channel_median_views'])} | "
              f"{r['outlier_channel']}x | {r['type']} | {celda(r['query'])} | [ver]({r['url']}) |"
              for i, r in enumerate(d["formats"][:25], 1)]
    if d.get("aviso"):
        L += ["", f"> **Aviso:** {d['aviso']}"]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modo", choices=["noticias", "completo"], default="noticias")
    ap.add_argument("--prueba", action="store_true")
    a = ap.parse_args()
    nq, fq = CONSULTAS["noticias"], CONSULTAS["formatos"]
    if a.prueba:
        nq, fq = nq[:2], fq[:2]
    ahora = datetime.now(PANAMA)
    d = {"generated_at": ahora.isoformat(timespec="seconds"), "modo": a.modo, "news": [], "formats": [], "aviso": None}
    try:
        d["news"] = noticias(nq)
        if a.modo == "completo":
            d["formats"] = formatos(fq)
    except SinCupo as e:
        d["aviso"] = str(e)
    d["unidades"] = UNIDADES["usadas"]
    REPORTS.mkdir(exist_ok=True)
    nombre = ahora.strftime("%Y-%m-%d_%H%M") + "_nube"
    (REPORTS / f"{nombre}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    (REPORTS / f"{nombre}.md").write_text(informe(d, nombre), encoding="utf-8")
    print(f"Informe: reports/{nombre}.md · noticias {len(d['news'])} · formatos {len(d['formats'])} · unidades {d['unidades']}")
    if d["aviso"]:
        print("AVISO:", d["aviso"])


if __name__ == "__main__":
    sys.exit(main())
