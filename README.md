# Mappa dei Centri Antiviolenza – 1522

Mappa georiferita dei Centri Antiviolenza e degli sportelli presenti in Italia, costruita a partire
dall'**elenco ufficiale** pubblicato dal 1522 (Dipartimento per le Pari Opportunità – Presidenza del Consiglio dei Ministri):
<https://www.1522.eu/centri-antiviolenza/mappa.html>

> **In caso di pericolo immediato chiama il 112.**
> Il **1522** è il numero gratuito antiviolenza e stalking, attivo 24 ore su 24, anche via chat su [1522.eu](https://www.1522.eu/).

## Come funziona

- `scripts/aggiorna.py` legge la pagina ufficiale e controlla la data **«Aggiornamento della Mappatura»**: se non è cambiata rispetto all'ultima esecuzione si ferma subito (con `--forza` rielabora comunque).
- Se la data è cambiata estrae tutte le schede (regione, provincia, comune, centro, indirizzo, contatti, orari, sportelli secondari, note) e le georeferenzia (`scripts/geocoder_anncsu.py`):
  1. **ANNCSU** – numeri civici georiferiti dell'Agenzia delle Entrate/ISTAT, letti dall'[API REST di Cruscotto Italia (AgID)](https://cruscotto-italia.dati.gov.it/api.html): civico esatto oppure via (civico più vicino);
  2. **OpenStreetMap Nominatim** – solo per i comuni che in ANNCSU non hanno ancora i civici georiferiti;
  3. coordinate di riferimento del **comune** (o del capoluogo di provincia) come ultima risorsa, segnalate in mappa come *posizione approssimata*.
- I nomi delle vie vengono normalizzati prima del confronto (numeri romani e in lettere, abbreviazioni come `Str.ne`, `Gen.`, `C.so`), così «Via XX Settembre» trova «VIA VENTI SETTEMBRE» e «Piazza Vittorio Emanuele II» non viene confusa con «…TERZO».
- `data/geocache.json` conserva il risultato per ogni centro: agli aggiornamenti successivi vengono georeferenziati solo i centri nuovi o modificati.
- La GitHub Action `.github/workflows/aggiorna.yml` gira ogni giorno e committa solo quando cambia la mappatura ufficiale.
- `index.html` è una pagina statica (Leaflet + MarkerCluster) servita da GitHub Pages.

## Dati prodotti

| File | Contenuto |
|---|---|
| `data/centri.geojson` | tutti i centri con geometria e tutti i campi della fonte |
| `data/centri.csv` | stessi dati in formato tabellare |
| `data/meta.json` | data della mappatura ufficiale, conteggi, qualità della localizzazione |
| `data/geocache.json` | cache della georeferenziazione per centro |

## Funzioni della mappa

- clic su un centro: popup + barra laterale con tutte le informazioni (telefoni cliccabili, orari con evidenza del giorno corrente, sportelli, note, indicazioni stradali);
- ricerca per comune/provincia/nome, filtro per regione e per contatto h24, "vicino a me";
- link diretto a ogni centro (`…/#<id>`);
- pulsante **Esci subito** (anche tasto `Esc`) che abbandona la pagina sostituendo la cronologia.

## Esecuzione locale

```bash
pip install -r requirements.txt
python scripts/aggiorna.py
python -m http.server 8000   # poi apri http://localhost:8000
```

## Note

- Progetto **non ufficiale**, nato per dare visibilità ai centri: la fonte autorevole resta [1522.eu](https://www.1522.eu/centri-antiviolenza/mappa.html). Prima di recarsi presso un centro è bene telefonare.
- Il logo 1522 in `assets/` è quello messo a disposizione dal portale nella pagina [Download](https://www.1522.eu/i-media/download.html), utilizzato secondo le relative [linee guida](https://www.1522.eu/images/download/1522_manuale_logo_linee_guida.pdf).
- Civici: ANNCSU (Agenzia delle Entrate – ISTAT) tramite Cruscotto Italia (AgID), licenza CC BY 4.0.
- Mappe e geocodifica di ripiego © contributori [OpenStreetMap](https://www.openstreetmap.org/copyright) (ODbL).
