#!/usr/bin/env python3
"""
Scarica l'elenco ufficiale dei Centri Antiviolenza da 1522.eu,
lo converte in GeoJSON e geocodifica gli indirizzi sui numeri civici ANNCSU
esposti da Cruscotto Italia (AgID). Lo script procede solo se sul portale
è cambiata la data di aggiornamento della mappatura (oppure con --forza);
una cache persistente evita di rielaborare i centri non modificati.

Uso:  python scripts/aggiorna.py
Output:
  data/centri.geojson   -> dati per la mappa
  data/centri.csv       -> stessi dati in formato tabellare
  data/meta.json        -> data aggiornamento sorgente, conteggi, ecc.
  data/geocache.json    -> cache del geocoding (da versionare)
"""
import csv
import hashlib
import html
import json
import math
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geocoder_anncsu import Geocoder  # noqa: E402

SOURCE_URL = "https://www.1522.eu/centri-antiviolenza/mappa.html"
UA = "mappa-centri-antiviolenza-1522/1.0 (+https://github.com/piersoft/centri-antiviolenza)"

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE_FILE = DATA / "geocache.json"

PLACEHOLDER_NOTES = {"note specifiche su orari, come contattare, ecc...."}


# ---------------------------------------------------------------- utilità
def clean(s: str) -> str:
    s = html.unescape(s or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def norm_key(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    d = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(d))


# ---------------------------------------------------------------- scraping
def fetch_html() -> str:
    r = requests.get(SOURCE_URL, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def parse(page: str):
    soup = BeautifulSoup(page, "html.parser")
    meta = {"fonte": SOURCE_URL}
    m = soup.find("meta", attrs={"property": "dateModified"})
    if m:
        meta["pagina_modificata"] = m.get("content")
    acc = soup.select_one(".mappa-acc")
    if acc:
        mm = re.search(r"Aggiornamento della Mappatura al\s*([0-9/.\-]+)", acc.get_text(" "))
        if mm:
            meta["aggiornamento_mappatura"] = mm.group(1)

    centri = []
    for det in soup.select("details.mappa-acc-item"):
        regione = clean(det.summary.get_text()) if det.summary else ""
        body = det.select_one(".mappa-acc-body") or det
        cur = None
        for div in body.find_all("div", recursive=False):
            strong = div.find("strong")
            if strong:
                label = clean(strong.get_text()).rstrip(":").strip()
                strong.extract()
                value = clean(div.get_text(" "))
                value = re.sub(r"\s+;", ";", value)
                tels = [clean(a.get("href", ""))[4:] for a in div.find_all("a", href=True)
                        if a["href"].lower().startswith("tel:")]
                mails = [a["href"][7:] for a in div.find_all("a", href=True)
                         if a["href"].lower().startswith("mailto:")]
                links = [a["href"] for a in div.find_all("a", href=True)
                         if a["href"].lower().startswith("http")]
                if label.lower() == "provincia":
                    cur = {"regione": regione, "campi": [], "telefoni": [], "email": [],
                           "link": [], "note": []}
                    centri.append(cur)
                if cur is None:
                    continue
                cur["campi"].append({"etichetta": label, "valore": value})
                cur["telefoni"] += tels
                cur["email"] += mails
                cur["link"] += links
            else:
                txt = clean(div.get_text(" "))
                if cur is not None and txt and txt.lower() not in PLACEHOLDER_NOTES:
                    cur["note"].append(txt)

    out = []
    for c in centri:
        def first(prefix):
            for f in c["campi"]:
                if f["etichetta"].lower().startswith(prefix):
                    return f["valore"]
            return ""
        rec = {
            "regione": c["regione"],
            "provincia": first("provincia"),
            "comune": first("comune"),
            "centro": first("centro").rstrip("."),
            "indirizzo": first("indirizzo"),
            "contatti": first("contatti"),
            "orari": first("orari") or first("orario"),
            "telefoni": list(dict.fromkeys(c["telefoni"])),
            "email": list(dict.fromkeys(c["email"])),
            "link": list(dict.fromkeys(c["link"])),
            "campi": c["campi"],
            "note": c["note"],
        }
        rec["h24"] = bool(re.search(r"h\s?24|24\s?ore", json.dumps(c, ensure_ascii=False), re.I))
        sig = "|".join([rec["regione"], rec["comune"], rec["centro"], rec["indirizzo"]])
        rec["id"] = hashlib.sha1(sig.encode()).hexdigest()[:12]
        out.append(rec)
    # id univoci anche in caso di duplicati identici
    seen = {}
    for r in out:
        if r["id"] in seen:
            seen[r["id"]] += 1
            r["id"] = f'{r["id"]}-{seen[r["id"]]}'
        else:
            seen[r["id"]] = 0
    return out, meta


# ---------------------------------------------------------------- normalizzazione
def clean_street(addr: str) -> str:
    a = addr
    a = re.split(r"\b(c/o|presso|c\\o|int\.|interno|piano|scala)\b", a, flags=re.I)[0]
    a = re.split(r"[;(]", a)[0]
    a = re.sub(r"\bs\.?n\.?c?\.?\b", "", a, flags=re.I)  # snc
    a = re.sub(r"\bn\.\s*", "", a, flags=re.I)
    return a.strip(" ,.-")

def comuni_candidati(comune: str):
    """'Avellino – Ambito A04' -> ['Avellino']; 'Carpi, Soliera e Novi' -> ['Carpi','Soliera','Novi']"""
    s = comune.replace("’", "'")
    s = re.split(r"\s*[-–]?\s*\bAmbito\b", s, flags=re.I)[0] or s
    s = re.sub(r"\s+Municipalit[aà].*$", "", s, flags=re.I)
    s = re.sub(r"\bS\.\s*Maria\b", "Santa Maria", s)
    s = re.sub(r"\s*\(([^)]*)\)", r", \1", s)  # 'Triggiano (Capurso)' -> 'Triggiano, Capurso
    parti = re.split(r"\s*[,;/]\s*|\s*[-–]\s+|\s+[-–]\s*|\s+e\s+", s)
    out = []
    for p in parti:
        p = re.sub(r"^(sede|sportello)( di)?\s+", "", p.strip(), flags=re.I)
        p = re.split(r"\s+(localit[aà]|loc\.|frazione|fraz\.)\s+", p, flags=re.I)[0]
        p = p.strip(" .-–")
        if p and len(p) > 2 and not re.search(r"servono|tutta|^ambito", p, re.I):
            out.append(p)
    return list(dict.fromkeys(out)) or [comune]

def indirizzi_candidati(addr: str):
    """Restituisce coppie (comune_indicato|None, via) dall'indirizzo grezzo."""
    a = addr.replace("’", "'")
    out = []
    # forma '(Comune) via ...; (Comune2) via ...'
    for m in re.finditer(r"\(([^)]+)\)\s*b?\s*([^;(]+)", a):
        luogo, via = m.group(1).strip(), clean_street(m.group(2))
        if via and not re.search(r"centro|sportello", luogo, re.I):
            out.append((luogo, via))
    via = clean_street(a)
    if via and not re.search(r"in attesa|comunicazione|riservat", via, re.I):
        # '... – Grosseto' o '..., Trento' in coda
        out.insert(0, (None, re.split(r"\s+[–-]\s+", via)[0]))
    return out


# ---------------------------------------------------------------- main
def main():
    DATA.mkdir(exist_ok=True)
    forza = "--forza" in sys.argv
    page = fetch_html()
    centri, meta = parse(page)

    # Si procede solo se la data di aggiornamento pubblicata sul portale è cambiata
    old_meta = json.loads((DATA / "meta.json").read_text()) if (DATA / "meta.json").exists() else {}
    stessa_data = (
        meta.get("aggiornamento_mappatura") == old_meta.get("aggiornamento_mappatura")
        and meta.get("pagina_modificata") == old_meta.get("pagina_modificata")
    )
    if stessa_data and (DATA / "centri.geojson").exists() and not forza:
        print(f"Mappatura invariata (al {meta.get('aggiornamento_mappatura')}, "
              f"pagina modificata {meta.get('pagina_modificata')}): nessun aggiornamento necessario.")
        return
    print(f"Nuova mappatura: {old_meta.get('aggiornamento_mappatura')} -> {meta.get('aggiornamento_mappatura')}")
    if len(centri) < 50:
        sys.exit(f"Solo {len(centri)} centri trovati: struttura della pagina cambiata? Interrompo.")

    g = Geocoder(CACHE_FILE, UA, ROOT / ".cache")
    feats, stats = [], {"civico": 0, "via": 0, "indirizzo_osm": 0, "comune": 0, "provincia": 0, "non_trovato": 0}
    for i, c in enumerate(centri, 1):
        r = g.locate(c, comuni_candidati(c["comune"]), indirizzi_candidati(c["indirizzo"]))
        pt, prec = r["pt"], r["precisione"]
        stats[prec] += 1
        c["precisione"] = prec
        c["istat"] = r["istat"]
        c["riscontro_anncsu"] = r["riscontro"]
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(pt[1], 6), round(pt[0], 6)]} if pt else None,
            "properties": c,
        })
        if i % 50 == 0:
            print(f"  {i}/{len(centri)} (file scaricati da Cruscotto Italia: {g.calls})", flush=True)
            g.save()
    g.save()

    # piccolo scostamento per punti coincidenti (stesso indirizzo / centroide)
    groups = {}
    for f in feats:
        if f["geometry"]:
            groups.setdefault(tuple(f["geometry"]["coordinates"]), []).append(f)
    for (lon, lat), fs in groups.items():
        if len(fs) > 1:
            for k, f in enumerate(fs):
                ang = 2 * math.pi * k / len(fs)
                r = 0.004 if fs[0]["properties"]["precisione"] == "comune" else 0.0004
                f["geometry"]["coordinates"] = [round(lon + r * math.cos(ang), 6), round(lat + r * math.sin(ang), 6)]

    meta.update({
        "totale_centri": len(centri),
        "regioni": len({c["regione"] for c in centri}),
        "precisione": stats,
    })
    # impronta del contenuto: se nulla è cambiato si conserva la data precedente
    impronta = hashlib.sha1(json.dumps([meta, feats], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if old_meta.get("impronta") == impronta:
        print("Nessuna variazione rispetto all'ultima esecuzione.")
        return
    meta["impronta"] = impronta
    meta["generato_il"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (DATA / "centri.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "metadata": meta, "features": feats}, ensure_ascii=False))
    (DATA / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    cols = ["id", "regione", "provincia", "comune", "centro", "indirizzo", "contatti", "orari",
            "telefoni", "email", "h24", "note", "lat", "lon", "precisione", "istat", "riscontro_anncsu"]
    with open(DATA / "centri.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for f in feats:
            p = f["properties"]
            co = f["geometry"]["coordinates"] if f["geometry"] else [None, None]
            w.writerow({**{k: p.get(k, "") for k in cols},
                        "telefoni": "; ".join(p["telefoni"]), "email": "; ".join(p["email"]),
                        "note": " | ".join(p["note"]), "lat": co[1], "lon": co[0]})
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
