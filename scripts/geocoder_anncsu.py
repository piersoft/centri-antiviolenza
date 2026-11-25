"""
Geocoding dei centri sui numeri civici georiferiti ANNCSU
(Agenzia delle Entrate / ISTAT) esposti dall'API REST di Cruscotto Italia (AgID):
  https://cruscotto-italia.dati.gov.it/api.html

- lookup/comuni-index.json   nome comune -> codice ISTAT (con sigla provincia e regione)
- istat-coords.json          coordinate del comune (ripiego)
- anncsu_full/<istat>.json   civici del comune: {"punti":[{lat,lon,odo,civ,esp,...}]}

Precisione restituita:
  civico  -> trovato il civico esatto
  via     -> trovata la via; punto sul civico più vicino o al centro della via
  indirizzo_osm -> comune senza civici ANNCSU georiferiti: indirizzo trovato su OpenStreetMap
  comune  -> solo il comune (coordinate di riferimento del comune)
  non_trovato
"""
import difflib
import json
import math
import re
import statistics
import time
import unicodedata
from pathlib import Path

import requests

BASE = "https://cruscotto-italia.dati.gov.it/data"
CACHE_VERSION = 5

TIPI = {
    "via", "viale", "vle", "piazza", "pza", "pzza", "piazzale", "corso", "cso", "largo", "vicolo",
    "vico", "contrada", "cda", "strada", "stradale", "stradella", "localita", "loc", "traversa",
    "riviera", "salita", "lungomare", "borgo", "calle", "galleria", "rione", "frazione", "fraz",
    "piazzetta", "viottolo", "circonvallazione", "lungotevere", "lungarno", "sestiere", "rampa",
    "discesa", "scalinata", "passeggiata", "parco", "villaggio", "quartiere", "campo", "fondamenta",
}
STOP = {"di", "de", "del", "della", "delle", "dei", "degli", "dello", "d", "e", "la", "le", "lo",
        "il", "gli", "i", "a", "al", "alla", "n", "snc", "sn", "bis", "ter", "km"}
ALIAS = {"reggio emilia": "Reggio nell'Emilia", "mestre": "Venezia", "lido di venezia": "Venezia",
         "gallico": "Reggio di Calabria", "carbonia iglesias": "Carbonia", "castelfranco veneto": "Castelfranco Veneto"}
ABBR = {"s": "san", "ss": "santissimo", "sta": "santa", "sto": "santo", "g": "", "f": "", "a": "",
        "p": "", "m": "", "v": "", "c": "", "l": "", "b": "", "r": "", "e": "", "u": ""}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower().replace("’", "'"))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# numeri: 'XX', '20', 'venti', 'ventesimo' -> '#20'
_UNITA = ["", "uno", "due", "tre", "quattro", "cinque", "sei", "sette", "otto", "nove"]
_SPEC = {10: "dieci", 11: "undici", 12: "dodici", 13: "tredici", 14: "quattordici", 15: "quindici",
         16: "sedici", 17: "diciassette", 18: "diciotto", 19: "diciannove"}
_DECINE = {20: "venti", 30: "trenta", 40: "quaranta", 50: "cinquanta"}
_ORD = {1: "primo", 2: "secondo", 3: "terzo", 4: "quarto", 5: "quinto", 6: "sesto", 7: "settimo",
        8: "ottavo", 9: "nono", 10: "decimo"}
NUMERI = {}
for n in range(1, 60):
    if n < 10:
        card = _UNITA[n]
    elif n < 20:
        card = _SPEC[n]
    else:
        d, u = divmod(n, 10)
        base = _DECINE[d * 10]
        card = base if u == 0 else (base[:-1] if u in (1, 8) else base) + _UNITA[u]
    NUMERI[card] = n
    if n in _ORD:
        NUMERI[_ORD[n]] = n
        NUMERI[_ORD[n][:-1] + "a"] = n
    else:
        o = (card[:-1] if card[-1] in "aeio" and not card.endswith("tre") and not card.endswith("sei") else card) + "esimo"
        NUMERI[o] = n
        NUMERI[o[:-1] + "a"] = n
NUMERI["ventitreesimo"] = 23
NUMERI["ventitreesima"] = 23
_ROM = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}


def _roman(t):
    if not re.fullmatch(r"[ivxlc]{2,7}|[vx]", t):
        return None
    tot, prev = 0, 0
    for ch in reversed(t):
        v = _ROM[ch]
        tot = tot - v if v < prev else tot + v
        prev = max(prev, v)
    return tot if 0 < tot < 100 else None


ESPANSIONI = [
    (r"\bstr\.?\s*ne\b", "stradone"), (r"\bcirc\.?\s*ne\b", "circonvallazione"),
    (r"\bc\.\s*so\b", "corso"), (r"\bp\.?\s*zz?a\b", "piazza"), (r"\bv\.\s*le\b", "viale"),
    (r"\bl\.\s*go\b", "largo"), (r"\bp\.\s*le\b", "piazzale"), (r"\breg\.", "regione"),
    (r"\bgen\.", "generale"), (r"\bmons\.", "monsignor"), (r"\bf\.\s*lli\b", "fratelli"),
    (r"\bstr\.", "strada"), (r"\bloc\.", "localita"), (r"\bc\.\s*da\b", "contrada"),
]


def name_tokens(s: str):
    """token significativi del nome della via, senza tipo toponimo, articoli e iniziali"""
    s = (s or "").lower()
    for pat, rep in ESPANSIONI:
        s = re.sub(pat, rep + " ", s)
    toks = norm(s).split()
    if toks and toks[0] in TIPI:
        toks = toks[1:]
    out = []
    for t in toks:
        if t.isdigit():
            out.append("#" + str(int(t)))
            continue
        if t in NUMERI:
            out.append("#" + str(NUMERI[t]))
            continue
        r = _roman(t)
        if r:
            out.append("#" + str(r))
            continue
        t = ABBR.get(t, t) if len(t) <= 3 and t in ABBR else t
        if t and t not in STOP and not (len(t) == 1 and t.isalpha()):
            out.append(t)
    return out


def tipo(s: str):
    t = norm(s).split()
    return t[0] if t and t[0] in TIPI else None


def _dist_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


class Nominatim:
    """Ripiego per i comuni senza civici georiferiti in ANNCSU (1 richiesta/s, policy OSMF)."""
    URL = "https://nominatim.openstreetmap.org/search"

    def __init__(self, ua):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = ua
        self.last = 0.0

    def cerca(self, via, citta):
        for attempt in range(3):
            wait = 1.1 - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.s.get(self.URL, params={"street": via, "city": citta, "countrycodes": "it",
                                                 "format": "jsonv2", "limit": 1}, timeout=30)
                self.last = time.time()
                if r.status_code == 200:
                    j = r.json()
                    return [float(j[0]["lat"]), float(j[0]["lon"])] if j else None
            except requests.RequestException:
                pass
            time.sleep(5 * (attempt + 1))
        return None


class Geocoder:
    def __init__(self, cache_file: Path, ua: str, disk_cache: Path | None = None, osm: bool = True):
        self.cache_file = cache_file
        raw = json.loads(cache_file.read_text()) if cache_file.exists() else {}
        self.cache = raw if raw.get("_versione") == CACHE_VERSION else {"_versione": CACHE_VERSION}
        self.s = requests.Session()
        self.s.headers["User-Agent"] = ua
        self.disk = disk_cache
        if self.disk:
            self.disk.mkdir(parents=True, exist_ok=True)
        self.calls = 0
        self.anncsu = {}
        self.osm = Nominatim(ua) if osm else None
        self.index = self._get("lookup/comuni-index.json")
        self.coords = self._get("istat-coords.json")
        self.by_name = {}
        for c in self.index:
            self.by_name.setdefault(norm(c["n"]), []).append(c)

    # ------------------------------------------------------------ rete
    def _get(self, path):
        if self.disk:
            f = self.disk / path.replace("/", "_")
            if f.exists() and time.time() - f.stat().st_mtime < 7 * 86400:
                return json.loads(f.read_text())
        for attempt in range(5):
            try:
                r = self.s.get(f"{BASE}/{path}", timeout=120)
                self.calls += 1
                time.sleep(0.25)  # ben sotto i 10 req/s consentiti sugli archivi _full
                if r.status_code == 200:
                    if self.disk:
                        (self.disk / path.replace("/", "_")).write_bytes(r.content)
                    return r.json()
                if r.status_code == 404:
                    return None
                print(f"    Cruscotto HTTP {r.status_code} su {path}", flush=True)
            except requests.RequestException as e:
                print(f"    Cruscotto errore {e.__class__.__name__} su {path}", flush=True)
            time.sleep(10 * 2 ** attempt)  # 429/5xx: attesa crescente
        raise RuntimeError(f"Impossibile scaricare {path}")

    def save(self):
        self.cache_file.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1, sort_keys=True))

    # ------------------------------------------------------------ comuni
    def _sigla_provincia(self, provincia, regione):
        """'Avellino' -> 'AV' cercando il capoluogo omonimo nella stessa regione"""
        for c in self.by_name.get(norm(provincia), []):
            if self._stessa_regione(c["r"], regione):
                return c["p"]
        return None

    @staticmethod
    def _stessa_regione(a, b):
        a, b = norm(a).split(), norm(b).split()
        return bool(a and b and a[0] == b[0])

    def istat(self, nome, provincia, regione):
        """nome comune (anche impreciso) -> codice ISTAT, preferendo provincia e regione indicate"""
        key = norm(nome)
        if not key:
            return None
        key = norm(ALIAS.get(key, nome))
        sigla = self._sigla_provincia(provincia, regione)
        pool = [c for c in self.index if self._stessa_regione(c["r"], regione)] or self.index

        def scegli(cands):
            if not cands:
                return None
            if sigla:
                same = [c for c in cands if c["p"] == sigla]
                if same:
                    return same[0]["i"]
            return cands[0]["i"] if len(cands) == 1 else None

        exact = [c for c in pool if norm(c["n"]) == key]
        r = scegli(exact)
        if r:
            return r
        # 'Rivarolo' -> 'Rivarolo Canavese' (solo nella provincia)
        pref = [c for c in pool if norm(c["n"]).startswith(key + " ") and (not sigla or c["p"] == sigla)]
        if len(pref) == 1:
            return pref[0]["i"]
        # refusi: 'Santomobono Terme', 'Sant'Agata dei Goti'
        nomi = {norm(c["n"]): c for c in pool if not sigla or c["p"] == sigla} or {norm(c["n"]): c for c in pool}
        m = difflib.get_close_matches(key, list(nomi), n=1, cutoff=0.85)
        if m:
            return nomi[m[0]]["i"]
        # 'Fiumicino Maccarese' -> 'Fiumicino', 'Città del Vasto' -> 'Vasto'
        parole = key.split()
        for k in range(len(parole) - 1, 0, -1):
            for sub in (" ".join(parole[:k]), " ".join(parole[-k:])):
                hit = [c for c in pool if norm(c["n"]) == sub and (not sigla or c["p"] == sigla)]
                if len(hit) == 1:
                    return hit[0]["i"]
        return None

    def capoluogo(self, provincia, regione):
        sigla = self._sigla_provincia(provincia, regione)
        for c in self.by_name.get(norm(provincia), []):
            if c["p"] == sigla:
                return c["i"]
        return None

    # ------------------------------------------------------------ vie e civici
    def _strade(self, istat):
        if istat not in self.anncsu:
            d = self._get(f"anncsu_full/{istat}.json") or {}
            strade = {}
            for p in d.get("punti", []):
                if p.get("lat") is None or p.get("lon") is None:
                    continue
                strade.setdefault(p.get("odo", ""), []).append(p)
            self.anncsu[istat] = strade
        return self.anncsu[istat]

    @staticmethod
    def split_civico(via):
        """'Via Spaventa, 47' -> ('Via Spaventa', 47); 'Via Traversa 113 bis' -> ('Via Traversa', 113)"""
        v = re.sub(r"\bkm\.?\s*[\d,.]+", "", via, flags=re.I)
        m = re.search(r"[\s,]+(?:n\.?\s*)?(\d{1,4})(?=\D|$)", v)
        if m and m.start() > 3:
            return v[:m.start()].strip(" ,"), int(m.group(1))
        return v.strip(" ,"), None

    @staticmethod
    def varianti(via):
        """'Palazzo del Principe, Via Costa Calda 17' -> [..., 'Via Costa Calda 17'];
        'Via Aldo Moro angolo via Einaudi' -> ['Via Aldo Moro', 'via Einaudi']"""
        out = [via]
        for seg in re.split(r",|\bangolo\b|/", via, flags=re.I):
            seg = seg.strip()
            if seg and seg != via and norm(seg).split()[:1] and norm(seg).split()[0] in TIPI:
                out.append(seg)
        return list(dict.fromkeys(out))

    def match_via(self, istat, via):
        for v in self.varianti(via):
            m = self._match_via(istat, v)
            if m:
                return m
        return None

    def _match_via(self, istat, via):
        strade = self._strade(istat)
        if not strade:
            return None
        nome, civ = self.split_civico(via)
        toks = name_tokens(nome)
        if not toks:
            return None
        if not tipo(nome) and len(toks) < 2:
            return None  # es. 'Mulazzo c/o Casa della Salute': non è un indirizzo
        num_src = {t for t in toks if t.startswith("#")}
        t_src = tipo(nome)
        best, best_score = None, 0
        for odo in strade:
            ot = name_tokens(odo)
            if not ot:
                continue
            inter = len(set(toks) & set(ot))
            if inter == len(set(toks)):
                score = 2 + inter / len(set(ot))           # tutti i token presenti
            else:
                num_odo = {t for t in ot if t.startswith("#")}
                if num_src != num_odo:
                    continue                               # 'Emanuele II' non è 'Emanuele III'
                ratio = difflib.SequenceMatcher(None, " ".join(toks), " ".join(ot)).ratio()
                score = ratio if ratio >= 0.88 else 0      # refusi
            if score and t_src and tipo(odo) and (tipo(odo) == t_src or {tipo(odo), t_src} <= {"via", "viale", "vle"}):
                score += 0.3 if tipo(odo) == t_src else 0.1
            if score > best_score:
                best, best_score = odo, score
        if not best:
            return None
        punti = strade[best]
        if civ is not None:
            numerici = [p for p in punti if re.match(r"^\d+", str(p.get("civ", "")))]
            esatti = [p for p in numerici if int(re.match(r"^\d+", str(p["civ"])).group()) == civ]
            if esatti:
                p = next((x for x in esatti if not x.get("esp")), esatti[0])
                return [p["lat"], p["lon"]], "civico", f'{best} {p["civ"]}{p.get("esp") or ""}'
            if numerici:
                p = min(numerici, key=lambda x: abs(int(re.match(r"^\d+", str(x["civ"])).group()) - civ))
                return [p["lat"], p["lon"]], "via", f'{best} (civico più vicino: {p["civ"]})'
        lat = statistics.median(p["lat"] for p in punti)
        lon = statistics.median(p["lon"] for p in punti)
        p = min(punti, key=lambda x: (x["lat"] - lat) ** 2 + (x["lon"] - lon) ** 2)
        return [p["lat"], p["lon"]], "via", best

    # ------------------------------------------------------------ record
    def locate(self, rec, comuni, indirizzi):
        key = "|".join([rec["regione"], rec["provincia"], rec["comune"], rec["indirizzo"]])
        if key in self.cache:
            return self.cache[key]
        prov, reg = rec["provincia"], rec["regione"]
        istat_comuni = [i for i in (self.istat(c, prov, reg) for c in comuni[:4]) if i]
        res = None
        for luogo, via in indirizzi[:3]:
            cands = [self.istat(luogo, prov, reg)] if luogo else istat_comuni[:2]
            for ist in [i for i in cands if i]:
                m = self.match_via(ist, via)
                if m:
                    res = {"pt": m[0], "precisione": m[1], "istat": ist, "riscontro": m[2]}
                    break
            if res:
                break
        if not res and self.osm:
            ref = self.coords.get(istat_comuni[0]) if istat_comuni else None
            nomi = {i: n["n"] for n in self.index for i in [n["i"]] if i in istat_comuni}
            for luogo, via in indirizzi[:2]:
                citta = [luogo] if luogo else [nomi.get(i) for i in istat_comuni[:2] if nomi.get(i)]
                for c in citta:
                    for v in self.varianti(via)[:2]:
                        if not tipo(v) and len(name_tokens(v)) < 2:
                            continue
                        pt = self.osm.cerca(v, c)
                        if pt and (not ref or _dist_km(pt, ref) < 25):
                            res = {"pt": pt, "precisione": "indirizzo_osm", "istat": istat_comuni[0] if istat_comuni else None,
                                   "riscontro": f"OpenStreetMap: {v}, {c}"}
                            break
                    if res:
                        break
                if res:
                    break
        if not res and istat_comuni:
            ist = istat_comuni[0]
            c = self.coords.get(ist)
            if c:
                res = {"pt": c, "precisione": "comune", "istat": ist, "riscontro": None}
        if not res:
            cap = self.capoluogo(prov, reg)
            if cap and self.coords.get(cap):
                res = {"pt": self.coords[cap], "precisione": "provincia", "istat": cap, "riscontro": None}
        if not res:
            res = {"pt": None, "precisione": "non_trovato", "istat": None, "riscontro": None}
        self.cache[key] = res
        return res
