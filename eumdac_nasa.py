#!/usr/bin/env python3
"""
Fire Hub: EUMETSAT AFM (CAP) + NASA FIRMS → CSV + SMS (3 nearest municipalities + default contact)
-----------------------------------------------------------------------------------------------

What's new in this version
- Runs continuously by default: interval now defaults to 900s (15 minutes).
- Interval can be overridden by CLI flag --interval or env POLL_INTERVAL_SECONDS.
- Sleep logic is drift-resistant: schedules next run relative to the previous planned start time.
- Lebanon-only SMS gate using a local GeoJSON polygon (no Shapely required).

Run examples
  - Default (every 15 min):   python fire_hub.py
  - Custom interval:          python fire_hub.py --interval 600
  - One-shot (run once):      python fire_hub.py --interval 0
"""

from __future__ import annotations
import argparse
import csv
import io
import json
import math
import os
import sys
import time
import gzip
import tarfile
import zipfile
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Tuple

# ----------------- tiny logger -----------------
def log(level: str, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level.upper():5}] {msg}")

# ----------------- config: files ----------------
AFM_STATE_FILE = "afm_cap_state.json"
FIRMS_STATE_FILE = "firms_state.json"
ALERTS_STATE_FILE = "alerts_state.json"   # per-source alert de-dup (what we already texted)

AFM_CSV_FILE = "afm_cap_points.csv"
FIRMS_CSV_FILE = "firms_hotspots.csv"
ALERTS_LOG_FILE = "alerts_log.csv"

# ----------------- EUMETSAT ---------------------
COLLECTION_ID = "EO:EUM:DAT:0801"  # Active Fire Monitoring (CAP) - MTG - 0°

# EUMETSAT credentials via env (you currently hard-coded them here)
# CONSUMER_KEY = os.getenv("EUMETSAT_CONSUMER_KEY", "").strip()
# CONSUMER_SECRET = os.getenv("EUMETSAT_CONSUMER_SECRET", "").strip()
CONSUMER_KEY = "GegzspYThyMHcChZ8uxeqI0CgAUa"
CONSUMER_SECRET = "bBnOL2sjnqek31KBkslwW4wycF8a"

# BBOX for filtering EUMETSAT CAP points (broad region pre-filter)
# CURRENTLY: MIDDLE EAST (for testing so you can see hits quickly)
#   lat ∈ [20.0, 45.0], lon ∈ [15.0, 60.0]
AFM_BBOX_LAT_MIN = float(os.getenv("AFM_LAT_MIN", "20.0"))
AFM_BBOX_LAT_MAX = float(os.getenv("AFM_LAT_MAX", "45.0"))
AFM_BBOX_LON_MIN = float(os.getenv("AFM_LON_MIN", "15.0"))
AFM_BBOX_LON_MAX = float(os.getenv("AFM_LON_MAX", "60.0"))
# LEBANON bounds (box only; NOT used for precise SMS gate):
#    lat ∈ [32.05, 35.69] ; lon ∈ [34.10, 37.64]

# ----------------- NASA FIRMS -------------------
FIRMS_URLS = [
    # Lebanon box from your original code (leave as-is)
    "https://firms.modaps.eosdis.nasa.gov/api/area/csv/8b994cbf1cdd98b1cc95d19385722745/VIIRS_NOAA21_NRT/35.0,33.0,36.8,34.9/1",
    "https://firms.modaps.eosdis.nasa.gov/api/area/csv/8b994cbf1cdd98b1cc95d19385722745/VIIRS_SNPP_NRT/35.0,33.0,36.8,34.9/1",
    "https://firms.modaps.eosdis.nasa.gov/api/area/csv/8b994cbf1cdd98b1cc95d19385722745/VIIRS_NOAA20_NRT/35.0,33.0,36.8,34.9/1",
]
MIN_FRP_MW = 1.0          # keep all confidences; still apply a small FRP guard
DEDUP_DISTANCE_KM = 0.5   # same event if within 0.5 km
DEDUP_TIME_MIN = 20       # and within 20 minutes
TOP_N_MUNICIPALITIES = 3

# ----------------- SMS provider -----------------
BSB_API_URL = "https://www.bestsmsbulk.com/bestsmsbulkapi/sendSmsAPI.php"
BSB_USERNAME = os.getenv("BSB_USER", "")
BSB_PASSWORD = os.getenv("BSB_PASS", "")
BSB_SENDERID = os.getenv("BSB_SENDER", "FireAlerts")

ENABLE_SMS_API = os.getenv("ENABLE_SMS_API", "0") == "1"  # <-- toggle here (or via env)
ALWAYS_NOTIFY_PHONE = os.getenv("ALWAYS_NOTIFY_PHONE", "96171097068").strip()  # e.g. "+9617xxxxxx"

# ----------------- Precise Lebanon border (GeoJSON; no Shapely) -----------------
# Looks for a file named lebanon.geojson in the SAME folder as this script by default.
DEFAULT_GEOJSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lebanon.geojson")
LEBANON_GEOJSON = os.getenv("LEBANON_GEOJSON", DEFAULT_GEOJSON_PATH)
LEBANON_ONLY_SMS = os.getenv("LEBANON_ONLY_SMS", "1") == "1"  # SMS only if inside Lebanon polygon

def _load_geojson_polygons(path: str):
    """
    Load Lebanon border polygons from a GeoJSON file and normalize to:
      [
        [  # polygon 0
          [[lon, lat], [lon, lat], ...],        # outer ring (may be open or closed)
          [[lon, lat], ...], [[lon, lat], ...]  # holes (optional)
        ],
        [  # polygon 1
          ...
        ]
      ]
    """
    if not path or not os.path.exists(path):
        log("warn", f"LEBANON_GEOJSON not set or file missing at: {path} — precise border filter disabled.")
        return []
    with open(path, "r", encoding="utf-8") as f:
        gj = json.load(f)

    def _norm_geom(geom):
        t = (geom or {}).get("type")
        if t == "Polygon":
            return [geom["coordinates"]]            # [rings]
        if t == "MultiPolygon":
            return geom["coordinates"]              # [[rings], [rings], ...]
        raise ValueError(f"Unsupported GeoJSON geometry type: {t}")

    polys = []
    t = gj.get("type")
    if t == "FeatureCollection":
        for ft in gj.get("features", []):
            g = ft.get("geometry")
            if g:
                polys.extend(_norm_geom(g))
    elif t == "Feature":
        polys.extend(_norm_geom(gj.get("geometry")))
    else:
        polys.extend(_norm_geom(gj))

    if polys:
        log("info", f"Loaded Lebanon border with {len(polys)} polygon(s) from {path}")
    else:
        log("warn", f"No polygons found in {path}")
    return polys

# --- pure-Python point-in-polygon (ray casting) with boundary handling ---
def _point_on_segment(px: float, py: float, x1: float, y1: float, x2: float, y2: float, eps: float = 1e-12) -> bool:
    # Quick bounding box check
    if px < min(x1, x2) - eps or px > max(x1, x2) + eps or py < min(y1, y2) - eps or py > max(y1, y2) + eps:
        return False
    # Colinearity via cross product
    dx, dy = x2 - x1, y2 - y1
    cross = dx * (py - y1) - dy * (px - x1)
    if abs(cross) > eps * (abs(dx) + abs(dy) + 1.0):
        return False
    return True

def _point_in_ring_or_boundary(lat: float, lon: float, ring: List[List[float]]) -> bool:
    """
    Ray-casting in lon/lat space.
    Returns True if the point is inside OR exactly on the boundary of the ring.
    ring is [[lon, lat], ...]; open rings are treated as closed.
    """
    inside = False
    n = len(ring)
    if n == 0:
        return False
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        # Boundary check first
        if _point_on_segment(lon, lat, x1, y1, x2, y2):
            return True
        # Standard ray-cast toggle
        if ((y1 > lat) != (y2 > lat)):
            x_int = x1 + (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-16)
            if x_int >= lon:
                inside = not inside
    return inside

def _point_in_polygon_with_holes(lat: float, lon: float, polygon: List[List[List[float]]]) -> bool:
    """
    polygon: [outer_ring, hole1, hole2, ...]
    Includes outer boundary; excludes holes (including their boundaries).
    """
    if not polygon:
        return False
    outer = polygon[0]
    if not _point_in_ring_or_boundary(lat, lon, outer):
        return False
    for hole in polygon[1:]:
        if _point_in_ring_or_boundary(lat, lon, hole):
            return False
    return True

# Load polygons once at import
LEBANON_POLYS = _load_geojson_polygons(LEBANON_GEOJSON)

def point_in_lebanon(lat: float, lon: float) -> bool:
    """
    True if (lat,lon) is inside (or on the border of) Lebanon according to lebanon.geojson.
    If the GeoJSON failed to load:
      - with LEBANON_ONLY_SMS=1, treat as outside (no SMS)
      - with LEBANON_ONLY_SMS=0, allow SMS
    """
    if not LEBANON_POLYS:
        return not LEBANON_ONLY_SMS
    for poly in LEBANON_POLYS:
        if _point_in_polygon_with_holes(lat, lon, poly):
            return True
    return False

# ----------------- data models ------------------
@dataclass
class Municipality:
    name: str
    latitude: float
    longitude: float
    phone_e164: str
    distance_km: float = 0.0

@dataclass
class Hotspot:  # for FIRMS
    lat: float
    lon: float
    confidence: str  # l/n/h (lowercase)
    frp: float
    daynight: str
    when_utc_text: str
    when_dt: Optional[datetime]
    source: str       # e.g., VIIRS_NOAA21_NRT

# ----------------- CSV helpers ------------------
def ensure_csv(path: str, headers: List[str]) -> None:
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(headers)

def append_rows(path: str, rows: Iterable[Iterable]) -> int:
    cnt = 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow(r)
            cnt += 1
    return cnt

# ------------ municipalities loading ------------
def get_muni_csv_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "municipalities.csv")

def _sniff_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except Exception:
        class _D(csv.Dialect):
            delimiter = ","
            quotechar = '"'
            doublequote = True
            skipinitialspace = False
            lineterminator = "\n"
            quoting = csv.QUOTE_MINIMAL
        return _D()

def _to_float(s: str) -> float:
    return float((s or "").strip().replace(",", "."))

def _normalize_phone(phone: str) -> str:
    p = (phone or "").strip()
    if p and not p.startswith("+") and p[0].isdigit():
        p = "+" + p
    return p

def load_municipalities(csv_path: str) -> List[Municipality]:
    results: List[Municipality] = []
    if not os.path.exists(csv_path):
        log("error", f"Can't find CSV at: {csv_path} (need municipalities.csv)")
        return results

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        sample = f.read(4096); f.seek(0)
        dialect = _sniff_dialect(sample)
        reader = csv.DictReader(f, dialect=dialect)
        field_map = {(fn or "").strip().lstrip("\ufeff").lower(): fn for fn in (reader.fieldnames or [])}

        def get(row, key):
            orig = field_map.get(key, "")
            return (row.get(orig) if orig else "") if row else ""

        required = {"municipality", "latitude", "longitude", "phone_e164"}
        if not required.issubset(set(field_map.keys())):
            log("error", "municipalities.csv is missing headers; need: municipality, latitude, longitude, phone_e164")
            log("error", f"Detected: {reader.fieldnames}")
            return results

        for i, row in enumerate(reader, start=2):
            try:
                name = (get(row, "municipality") or "").strip()
                lat = _to_float(get(row, "latitude"))
                lon = _to_float(get(row, "longitude"))
                phone = _normalize_phone(get(row, "phone_e164"))
                if not name:
                    raise ValueError("empty municipality name")
                results.append(Municipality(name=name, latitude=lat, longitude=lon, phone_e164=phone))
            except Exception as e:
                log("warn", f"Skipping bad row {i}: {e}")
    log("info", f"Loaded {len(results)} municipalities")
    return results

# ---------------- distance & tools --------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlmb/2)**2)
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

def closest_municipalities(munis: List[Municipality], lat: float, lon: float, n=TOP_N_MUNICIPALITIES) -> List[Municipality]:
    for m in munis:
        m.distance_km = haversine_km(lat, lon, m.latitude, m.longitude)
    return sorted(munis, key=lambda x: x.distance_km)[:max(0, min(n, len(munis)))]

def maps_link(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat:.10f}%2C{lon:.10f}"

def in_bbox(lat: float, lon: float, lat_min: float, lat_max: float, lon_min: float, lon_max: float) -> bool:
    return (lat_min <= lat <= lat_max) and (lon_min <= lon <= lon_max)

# ---------------- state helpers -----------------
def load_state(path: str, default: dict) -> dict:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

# ---------------- alerts log --------------------
ALERTS_LOG_HEADERS = [
    "ts_utc", "source", "lat", "lon", "recipients", "message", "extra", "product_or_feed"
]

def log_alert(source: str, lat: float, lon: float, recipients: List[str], message: str,
              extra: str, product_or_feed: str) -> None:
    ensure_csv(ALERTS_LOG_FILE, ALERTS_LOG_HEADERS)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = [ts, source, f"{lat:.10f}", f"{lon:.10f}", ",".join(recipients), message, extra, product_or_feed]
    append_rows(ALERTS_LOG_FILE, [row])

# ---------------- SMS helpers -------------------
def _post_form(url: str, fields: dict, timeout: int = 30) -> str:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        try:
            return body.decode("utf-8", errors="replace")
        except Exception:
            return body.decode("latin-1", errors="replace")

def send_sms(destinations: List[str], message_text: str) -> str:
    if not destinations:
        return "NO_DEST"
    fields = {
        "username": BSB_USERNAME,
        "password": BSB_PASSWORD,
        "senderid": BSB_SENDERID,
        "destination": ",".join(d.strip().lstrip("+") if d.startswith("+") else d.strip() for d in destinations),
        "message": message_text,
    }
    if not ENABLE_SMS_API:
        log("sms  ", f"DRY RUN → would POST to {BSB_API_URL} with: {fields}")
        return "DRY_RUN"
    return _post_form(BSB_API_URL, fields)

# --------------- EUMETSAT: CAP parsing ----------
def _ns_any(tag: str) -> str:
    return f".//{{*}}{tag}"

def extract_cap_xml_from_blob(blob: bytes) -> List[bytes]:
    out: List[bytes] = []
    if b"<alert" in blob or b":alert" in blob:
        out.append(blob); return out
    # ZIP
    if len(blob) >= 4 and blob[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                for name in zf.namelist():
                    with zf.open(name) as fh:
                        data = fh.read()
                        if b"<alert" in data or b":alert" in data:
                            out.append(data)
            return out
        except Exception:
            pass
    # GZIP
    if len(blob) >= 2 and blob[:2] == b"\x1f\x8b":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(blob)) as gf:
                data = gf.read()
            out.extend(extract_cap_xml_from_blob(data))
            return out
        except Exception:
            pass
    # TAR
    if len(blob) > 265 and blob[257:262] == b"ustar":
        try:
            with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
                for m in tf.getmembers():
                    if not m.isfile(): continue
                    fh = tf.extractfile(m)
                    if not fh: continue
                    data = fh.read()
                    if b"<alert" in data or b":alert" in data:
                        out.append(data)
            return out
        except Exception:
            pass
    return out

def parse_cap_points(xml_bytes: bytes) -> Tuple[Optional[str], Optional[str], Optional[str], List[Tuple[float, float]]]:
    """
    Returns (stamp_iso, certainty, severity, [(lat, lon), ...]) from a CAP XML.
    Collects:
      - <point> "lat,lon"
      - <circle> "lat,lon radiusKM" (center only)
      - <polygon> centroid of vertices
    """
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return None, None, None, []

    sent = None
    s = root.find(_ns_any("sent"))
    if s is not None and (s.text or "").strip():
        sent = s.text.strip()

    info = root.find(_ns_any("info"))
    certainty = severity = None
    effective = None
    if info is not None:
        c = info.find(_ns_any("certainty"))
        if c is not None and (c.text or "").strip():
            certainty = c.text.strip()
        sv = info.find(_ns_any("severity"))
        if sv is not None and (sv.text or "").strip():
            severity = sv.text.strip()
        e = info.find(_ns_any("effective"))
        if e is not None and (e.text or "").strip():
            effective = e.text.strip()

    stamp = effective or sent

    pts: List[Tuple[float, float]] = []

    # <point>
    for p in root.findall(_ns_any("point")) + root.findall(_ns_any("info/area/point")):
        try:
            t = (p.text or "").strip().replace(" ", "")
            lat_s, lon_s = t.split(",")
            pts.append((float(lat_s), float(lon_s)))
        except Exception:
            pass

    # <circle> "lat,lon radiusKM"
    for c in root.findall(_ns_any("circle")) + root.findall(_ns_any("info/area/circle")):
        try:
            t = (c.text or "").strip().replace("km", "").replace("KM", "")
            parts = t.replace(",", " ").split()
            if len(parts) >= 3:
                lat, lon = float(parts[0]), float(parts[1])
                pts.append((lat, lon))
        except Exception:
            pass

    # <polygon> centroid
    for pg in root.findall(_ns_any("polygon")) + root.findall(_ns_any("info/area/polygon")):
        try:
            verts = []
            for pair in (pg.text or "").strip().split():
                a = pair.replace(",", " ").split()
                if len(a) == 2:
                    verts.append((float(a[0]), float(a[1])))
            if verts:
                clat = sum(v[0] for v in verts) / len(verts)
                clon = sum(v[1] for v in verts) / len(verts)
                pts.append((clat, clon))
        except Exception:
            pass

    return stamp, certainty, severity, pts

def ensure_eumdac() -> bool:
    try:
        import eumdac  # noqa: F401
        return True
    except Exception as e:
        log("error", "Missing 'eumdac' — pip install eumdac")
        log("error", f"Details: {e}")
        return False

def afm_get_products(start: datetime, end: datetime):
    import eumdac
    token = eumdac.AccessToken((CONSUMER_KEY, CONSUMER_SECRET))
    ds = eumdac.DataStore(token)
    try:
        col = ds.get_collection(COLLECTION_ID)
    except Exception:
        col = ds.collection(COLLECTION_ID)
    try:
        results = col.search(dtstart=start, dtend=end)
    except TypeError:
        results = col.search(start=start, end=end)
    for p in results:
        yield p

def afm_read_cap_payloads(product) -> List[bytes]:
    caps: List[bytes] = []
    # product.open()
    if hasattr(product, "open"):
        try:
            with product.open() as fh:
                blob = fh.read()
                if isinstance(blob, str):
                    blob = blob.encode("utf-8", "replace")
            found = extract_cap_xml_from_blob(blob)
            if found:
                caps.extend(found)
                return caps
        except Exception as e:
            log("debug", f"product.open() failed: {e}")

    # assets()
    assets = getattr(product, "assets", None)
    items = []
    if isinstance(assets, dict):
        items = list(assets.items())
    elif callable(assets):
        try:
            a2 = assets()
            if isinstance(a2, dict):
                items = list(a2.items())
        except Exception:
            pass
    for name, asset in items:
        if not hasattr(asset, "open"): continue
        try:
            with asset.open() as fh:
                blob = fh.read()
                if isinstance(blob, str):
                    blob = blob.encode("utf-8", "replace")
            found = extract_cap_xml_from_blob(blob)
            caps.extend(found)
        except Exception as e:
            log("debug", f"asset '{name}' open() failed: {e}")
    if caps: return caps

    # entries()
    entries = getattr(product, "entries", None)
    if callable(entries):
        try:
            entries = entries()
        except Exception:
            entries = None
    if isinstance(entries, dict):
        entries = list(entries.items())
    elif isinstance(entries, list):
        entries = [(getattr(e, "name", getattr(e, "id", "entry")), e) for e in entries]
    else:
        entries = []
    for name, ent in entries:
        if not hasattr(ent, "open"): continue
        try:
            with ent.open() as fh:
                blob = fh.read()
                if isinstance(blob, str):
                    blob = blob.encode("utf-8", "replace")
            found = extract_cap_xml_from_blob(blob)
            caps.extend(found)
        except Exception as e:
            log("debug", f"entry '{name}' open() failed: {e}")

    return caps

# ---------------- FIRMS helpers -----------------
def _parse_dt(acq_date: str, acq_time: str):
    t = (acq_time or "").strip()
    if t.isdigit():
        if len(t) == 3:
            hh, mm = t[0], t[1:]
        else:
            hh, mm = t[:-2].rjust(2, "0"), t[-2:]
        text = f"{acq_date} {hh}:{mm}Z"
        try:
            dtobj = datetime.strptime(f"{acq_date} {hh}{mm}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
        except Exception:
            dtobj = None
    else:
        text = f"{acq_date} {t}Z".strip()
        try:
            dtobj = datetime.strptime(f"{acq_date} {t}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except Exception:
            dtobj = None
    return text, dtobj

def fetch_firms_hotspots(csv_url: str, source_label: str) -> List[Hotspot]:
    with urllib.request.urlopen(csv_url, timeout=30) as resp:
        data = resp.read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    out: List[Hotspot] = []
    for row in reader:
        try:
            lat = float(row["latitude"])
            lon = float(row["longitude"])
            conf = (row.get("confidence") or "").strip().lower()
            frp = float(row.get("frp") or 0.0)
            daynight = (row.get("daynight") or "").strip()
            acq_date = (row.get("acq_date") or "").strip()
            acq_time = (row.get("acq_time") or "").strip()
            when_text, dtobj = _parse_dt(acq_date, acq_time)
            out.append(Hotspot(lat, lon, conf, frp, daynight, when_text, dtobj, source_label))
        except Exception:
            continue
    return out

def screen_hotspots(rows: List[Hotspot], min_frp=MIN_FRP_MW) -> List[Hotspot]:
    return [r for r in rows if r.frp >= min_frp]

def _conf_score(c: str) -> int:
    return {"l": 0, "n": 1, "h": 2}.get(c, -1)

def dedup_hotspots(rows: List[Hotspot], time_window_min=DEDUP_TIME_MIN, spatial_km=DEDUP_DISTANCE_KM) -> List[Hotspot]:
    rows2 = [r for r in rows if r.when_dt is not None]
    rows2.sort(key=lambda r: r.when_dt)
    kept: List[Hotspot] = []
    for r in rows2:
        duplicate_idx = None
        for i, k in enumerate(kept):
            if abs((r.when_dt - k.when_dt).total_seconds()) > time_window_min * 60:
                continue
            if haversine_km(r.lat, r.lon, k.lat, k.lon) <= spatial_km:
                duplicate_idx = i
                break
        if duplicate_idx is None:
            kept.append(r)
        else:
            def strength(h: Hotspot):
                return (_conf_score(h.confidence), h.frp)
            if strength(r) > strength(kept[duplicate_idx]):
                kept[duplicate_idx] = r
    kept.extend([r for r in rows if r.when_dt is None])
    return kept

# ------------- message builders -----------------
def conf_human(c: str) -> str:
    c = (c or "").lower()
    label = {"l": "low", "n": "nominal", "h": "high"}.get(c, "unknown")
    return f"{c.upper()} ({label})"

def build_msg_firms(lat: float, lon: float, confidence: str, frp_mw: float, min_frp_mw: float, feed: str) -> str:
    link = maps_link(lat, lon)
    return (f"[NASA FIRMS – {feed}] "
            f"{link} "
            f"Confidence: {conf_human(confidence)} | FRP: {frp_mw:.2f} MW (min filter: {min_frp_mw:.2f} MW). "
            "Caution: Fire detected near your region. Please act promptly.")

def build_msg_afm(lat: float, lon: float, severity: Optional[str], certainty: Optional[str]) -> str:
    link = maps_link(lat, lon)
    sev = severity or "Unknown"
    cer = certainty or "Unknown"
    return (f"[EUMETSAT AFM (CAP)] "
            f"{link} "
            f"Severity: {sev} | Certainty: {cer}. "
            "Caution: Fire detected near your region. Please act promptly.")

# ------------- per-source CSV headers -----------
AFM_HEADERS = ["ingested_at_utc","cap_time_utc","latitude","longitude","certainty","severity","product_id"]
FIRMS_HEADERS = ["ingested_at_utc","when_utc","latitude","longitude","confidence","frp_mw","feed"]

# ------------- alert de-dup keys ----------------
def key_afm_alert(pid: str, lat: float, lon: float) -> str:
    return f"EUMETSAT|{pid}|{round(lat, 3)}|{round(lon, 3)}"

def key_firms_alert(feed: str, when: Optional[datetime], lat: float, lon: float) -> str:
    # round to minute & ~100m
    ts = when.strftime("%Y-%m-%dT%H:%MZ") if when else "NA"
    return f"NASA|{feed}|{ts}|{round(lat, 3)}|{round(lon, 3)}"

# ---------------- main processors ---------------
def process_eumetsat(lookback_min: int, municipalities: List[Municipality],
                     afm_state: dict, alerts_state: dict) -> int:
    if not ensure_eumdac():
        return 0
    if not CONSUMER_KEY or not CONSUMER_SECRET:
        log("error", "Missing EUMETSAT creds (EUMETSAT_CONSUMER_KEY/EUMETSAT_CONSUMER_SECRET)")
        return 0

    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_min)
    log("step", f"EUMETSAT: search {COLLECTION_ID} from {start:%Y-%m-%d %H:%M:%SZ} to {end:%Y-%m-%d %H:%M:%SZ}")
    log("info", f"Filter bbox (TEST Middle East): lat[{AFM_BBOX_LAT_MIN},{AFM_BBOX_LAT_MAX}] "
                f"lon[{AFM_BBOX_LON_MIN},{AFM_BBOX_LON_MAX}] "
                f"(Lebanon would be lat[32.05,35.69] lon[34.10,37.64])")

    seen_ids = set(afm_state.get("seen_ids", []))
    ensure_csv(AFM_CSV_FILE, AFM_HEADERS)
    ensure_csv(ALERTS_LOG_FILE, ALERTS_LOG_HEADERS)

    sent_keys = set(alerts_state.get("sent_keys", []))
    total_points = 0
    total_alerts = 0

    for i, prod in enumerate(afm_get_products(start, end), 1):
        pid = str(prod)
        log("info", f"EUMETSAT product {i}: {pid}")
        if pid in seen_ids:
            log("debug", "  already processed; skipping parse")
            continue

        cap_blobs = afm_read_cap_payloads(prod)
        if not cap_blobs:
            log("debug", "  no CAP payloads found; skipping")
            seen_ids.add(pid)
            continue

        wrote_rows = 0
        for cap in cap_blobs:
            stamp, certainty, severity, points = parse_cap_points(cap)
            if not points:
                continue
            # bbox filter (TEST Middle East)
            points = [p for p in points if in_bbox(p[0], p[1], AFM_BBOX_LAT_MIN, AFM_BBOX_LAT_MAX,
                                                   AFM_BBOX_LON_MIN, AFM_BBOX_LON_MAX)]
            if not points:
                continue

            rows = []
            ing = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            for lat, lon in points:
                rows.append([
                    ing,
                    stamp or "",
                    format(lat, ".17g"),   # latitude as full-precision text
                    format(lon, ".17g"),   # longitude as full-precision text
                    certainty or "",
                    severity or "",
                    pid,
                ])

                # ---- Lebanon-only SMS gate ----
                if LEBANON_ONLY_SMS and not point_in_lebanon(lat, lon):
                    log("info", f"EUMETSAT: outside Lebanon — skipping SMS for {lat:.5f},{lon:.5f}")
                    continue

                # alert building (per point)
                key = key_afm_alert(pid, lat, lon)
                if key in sent_keys:
                    continue

                nearest = closest_municipalities(municipalities, lat, lon, TOP_N_MUNICIPALITIES)
                recipients = [m.phone_e164 for m in nearest if m.phone_e164]
                if ALWAYS_NOTIFY_PHONE and ALWAYS_NOTIFY_PHONE not in recipients:
                    recipients.append(ALWAYS_NOTIFY_PHONE)

                msg = build_msg_afm(lat, lon, severity, certainty)
                log("sms  ", f"EUMETSAT → to {len(recipients)}: {recipients} | msg: {msg}")
                resp = send_sms(recipients, msg)
                log("sms  ", f"provider response: {resp}")
                log_alert("EUMETSAT", lat, lon, recipients, msg,
                          f"severity={severity or ''}; certainty={certainty or ''}; cap_time={stamp or ''}",
                          pid)
                sent_keys.add(key)
                total_alerts += len(recipients)

            wrote_rows += append_rows(AFM_CSV_FILE, rows)
            total_points += len(rows)

        log("info", f"EUMETSAT write: {wrote_rows} rows from product")
        seen_ids.add(pid)

    afm_state["seen_ids"] = list(seen_ids)[-1000:]
    save_state(AFM_STATE_FILE, afm_state)

    alerts_state["sent_keys"] = list(sent_keys)[-5000:]
    save_state(ALERTS_STATE_FILE, alerts_state)

    log("info", f"EUMETSAT done. Points kept (in bbox): {total_points}; SMS sent: {total_alerts}")
    return total_points

def process_firms(municipalities: List[Municipality],
                  firms_state: dict, alerts_state: dict) -> int:
    ensure_csv(FIRMS_CSV_FILE, FIRMS_HEADERS)
    ensure_csv(ALERTS_LOG_FILE, ALERTS_LOG_HEADERS)

    sent_keys = set(alerts_state.get("sent_keys", []))
    all_rows: List[Hotspot] = []

    log("step", "FIRMS: fetching all feeds...")
    for url in FIRMS_URLS:
        feed = url.split("/")[7] if len(url.split("/")) > 7 else "FIRMS"
        try:
            rows = fetch_firms_hotspots(url, feed)
            log("info", f"{feed}: fetched {len(rows)} rows")
            rows = screen_hotspots(rows)
            log("info", f"{feed}: kept after FRP≥{MIN_FRP_MW}: {len(rows)}")
            all_rows.extend(rows)
        except Exception as e:
            log("warn", f"Failed to fetch {feed}: {e}")

    if not all_rows:
        log("info", "FIRMS: no rows kept.")
        return 0

    before = len(all_rows)
    merged = dedup_hotspots(all_rows, DEDUP_TIME_MIN, DEDUP_DISTANCE_KM)
    after = len(merged)
    log("info", f"FIRMS de-dup: {before} → {after} unique (≤{DEDUP_DISTANCE_KM} km & ≤{DEDUP_TIME_MIN} min)")

    # append to CSV & alert
    ing = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out_rows = []
    total_alerts = 0

    for h in merged:
        out_rows.append([ing, h.when_utc_text, f"{h.lat:.10f}", f"{h.lon:.10f}", h.confidence, f"{h.frp:.2f}", h.source])

        key = key_firms_alert(h.source, h.when_dt, h.lat, h.lon)
        if key in sent_keys:
            continue

        # ---- Lebanon-only SMS gate ----
        if LEBANON_ONLY_SMS and not point_in_lebanon(h.lat, h.lon):
            log("info", f"FIRMS: outside Lebanon — skipping SMS for {h.lat:.5f},{h.lon:.5f}")
            continue

        nearest = closest_municipalities(municipalities, h.lat, h.lon, TOP_N_MUNICIPALITIES)
        recipients = [m.phone_e164 for m in nearest if m.phone_e164]
        if ALWAYS_NOTIFY_PHONE and ALWAYS_NOTIFY_PHONE not in recipients:
            recipients.append(ALWAYS_NOTIFY_PHONE)

        msg = build_msg_firms(h.lat, h.lon, h.confidence, h.frp, MIN_FRP_MW, h.source)
        log("sms  ", f"FIRMS → to {len(recipients)}: {recipients} | msg: {msg}")
        resp = send_sms(recipients, msg)
        log("sms  ", f"provider response: {resp}")
        log_alert("NASA FIRMS", h.lat, h.lon, recipients, msg,
                  f"confidence={h.confidence}; frp={h.frp:.2f}; when={h.when_utc_text}", h.source)
        sent_keys.add(key)
        total_alerts += len(recipients)

    wrote = append_rows(FIRMS_CSV_FILE, out_rows)
    log("info", f"FIRMS write: {wrote} rows; SMS sent: {total_alerts}")

    alerts_state["sent_keys"] = list(sent_keys)[-5000:]
    save_state(ALERTS_STATE_FILE, alerts_state)
    save_state(FIRMS_STATE_FILE, firms_state)  # reserved for future

    return wrote

# ----------------- main loop --------------------
def main():
    default_interval = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))  # 15 minutes by default
    ap = argparse.ArgumentParser(description="EUMETSAT AFM (CAP) + NASA FIRMS → CSV + SMS")
    ap.add_argument("--lookback", type=int, default=20, help="Minutes lookback for EUMETSAT CAP search")
    ap.add_argument("--interval", type=int, default=default_interval,
                    help="Polling interval seconds; 0 = run once (default from POLL_INTERVAL_SECONDS or 900)")
    args = ap.parse_args()

    # ready CSVs
    ensure_csv(AFM_CSV_FILE, AFM_HEADERS)
    ensure_csv(FIRMS_CSV_FILE, FIRMS_HEADERS)
    ensure_csv(ALERTS_LOG_FILE, ALERTS_LOG_HEADERS)

    # states
    afm_state = load_state(AFM_STATE_FILE, {"seen_ids": [], "last_run_utc": None})
    firms_state = load_state(FIRMS_STATE_FILE, {})
    alerts_state = load_state(ALERTS_STATE_FILE, {"sent_keys": []})

    # municipalities
    munis = load_municipalities(get_muni_csv_path())
    if not munis:
        sys.exit(1)

    if not ALWAYS_NOTIFY_PHONE:
        log("warn", "ALWAYS_NOTIFY_PHONE is empty — default contact will NOT be included.")

    if args.interval > 0 and args.lookback < (args.interval // 60):
        log("warn", f"lookback ({args.lookback} min) < interval ({args.interval//60} min). "
                    "Consider increasing lookback to avoid gaps.")

    def one_cycle():
        start = datetime.now(timezone.utc)
        log("info", f"Cycle start: {start:%Y-%m-%d %H:%M:%SZ}")
        # EUMETSAT first (CAP → Severity/Certainty)
        try:
            process_eumetsat(args.lookback, munis, afm_state, alerts_state)
        except Exception as e:
            log("error", f"EUMETSAT processing failed: {e}")

        # NASA FIRMS next (Confidence/FRP)
        try:
            process_firms(munis, firms_state, alerts_state)
        except Exception as e:
            log("error", f"FIRMS processing failed: {e}")
        end = datetime.now(timezone.utc)
        log("info", f"Cycle end:   {end:%Y-%m-%d %H:%M:%SZ} (elapsed {(end-start).total_seconds():.1f}s)")

    interval = int(args.interval)
    if interval <= 0:
        one_cycle()
        return

    log("info", f"Starting polling loop every {interval}s (lookback={args.lookback}min) "
                f"SMS={'ON' if ENABLE_SMS_API else 'OFF'} default={ALWAYS_NOTIFY_PHONE or 'none'}")
    log("info", f"Lebanon-only SMS is {'ON' if LEBANON_ONLY_SMS else 'OFF'}; GeoJSON: {LEBANON_GEOJSON}")

    # Drift-resistant scheduler
    next_run = time.time()
    try:
        while True:
            one_cycle()
            next_run += interval
            sleep_for = max(0, next_run - time.time())
            log("info", f"Sleeping {sleep_for:.1f}s until next cycle…")
            time.sleep(sleep_for)
    except KeyboardInterrupt:
        log("info", "Stopped by user.")

if __name__ == "__main__":
    main()
