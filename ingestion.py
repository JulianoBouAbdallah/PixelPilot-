#!/usr/bin/env python3
"""
FDEDC System – Fire Detection Ingestion Pipeline
Fetches EUMETSAT CAP and NASA FIRMS data, saves to CSV, and triggers alerts.

Usage:
    python ingestion.py --lookback 60
"""

import csv
import io
import json
import math
import os
import gzip
import tarfile
import zipfile
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

# ==================== CONFIGURATION ====================

EUMETSAT_CONSUMER_KEY = os.getenv("EUMETSAT_CONSUMER_KEY", "GegzspYThyMHcChZ8uxeqI0CgAUa")
EUMETSAT_CONSUMER_SECRET = os.getenv("EUMETSAT_CONSUMER_SECRET", "bBnOL2sjnqek31KBkslwW4wycF8a")
COLLECTION_ID = "EO:EUM:DAT:0801"

FIRMS_URLS = [
    "https://firms.modaps.eosdis.nasa.gov/api/area/csv/8b994cbf1cdd98b1cc95d19385722745/VIIRS_NOAA21_NRT/35.0,33.0,36.8,34.9/1",
    "https://firms.modaps.eosdis.nasa.gov/api/area/csv/8b994cbf1cdd98b1cc95d19385722745/VIIRS_SNPP_NRT/35.0,33.0,36.8,34.9/1",
    "https://firms.modaps.eosdis.nasa.gov/api/area/csv/8b994cbf1cdd98b1cc95d19385722745/VIIRS_NOAA20_NRT/35.0,33.0,36.8,34.9/1",
]

MIN_FRP_MW = 1.0
DEDUP_DISTANCE_KM = 0.5
DEDUP_TIME_MIN = 20
TOP_N_MUNICIPALITIES = 3

AFM_BBOX_LAT_MIN, AFM_BBOX_LAT_MAX = 20.0, 45.0
AFM_BBOX_LON_MIN, AFM_BBOX_LON_MAX = 15.0, 60.0

AFM_CSV_FILE = "afm_cap_points.csv"
FIRMS_CSV_FILE = "firms_hotspots.csv"
AFM_STATE_FILE = "afm_cap_state.json"
MUNICIPALITIES_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "municipalities.csv")
LEBANON_GEOJSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lebanon.geojson")

# ==================== LOGGING ====================

def log(level: str, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level.upper():5}] {msg}")

# ==================== DISTANCE MATH ====================

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# ==================== LEBANON BORDER CHECK ====================

def _load_geojson_polygons(path: str):
    if not path or not os.path.exists(path):
        log("warn", f"GeoJSON not found: {path} – border filter disabled.")
        return []
    with open(path, "r", encoding="utf-8") as f:
        gj = json.load(f)

    def _norm_geom(geom):
        t = (geom or {}).get("type")
        if t == "Polygon":
            return [geom["coordinates"]]
        if t == "MultiPolygon":
            return geom["coordinates"]
        return []

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
    return polys

LEBANON_POLYS = _load_geojson_polygons(LEBANON_GEOJSON)

def point_in_lebanon(lat: float, lon: float) -> bool:
    if not LEBANON_POLYS:
        return True  # If no border file, allow all
    for poly in LEBANON_POLYS:
        if not poly:
            continue
        outer = poly[0]
        inside = False
        n = len(outer)
        for i in range(n):
            x1, y1 = outer[i][0], outer[i][1]
            x2, y2 = outer[(i + 1) % n][0], outer[(i + 1) % n][1]
            if ((y1 > lat) != (y2 > lat)):
                x_int = x1 + (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-16)
                if lon <= x_int:
                    inside = not inside
        if inside:
            return True
    return False

def in_bbox(lat, lon, lat_min, lat_max, lon_min, lon_max):
    return (lat_min <= lat <= lat_max) and (lon_min <= lon <= lon_max)

# ==================== CSV HELPERS ====================

def ensure_csv(path: str, headers: List[str]) -> None:
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(headers)

def append_rows(path: str, rows: list) -> int:
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow(r)
    return len(rows)

def load_state(path: str, default: dict) -> dict:
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(path: str, state: dict) -> None:
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(path + ".tmp", path)

# ==================== MUNICIPALITIES ====================

def load_municipalities(csv_path: str) -> list:
    results = []
    if not os.path.exists(csv_path):
        log("error", f"Municipalities CSV not found: {csv_path}")
        return results
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                name = (row.get("municipality") or "").strip()
                lat = float((row.get("latitude") or "0").strip().replace(",", "."))
                lon = float((row.get("longitude") or "0").strip().replace(",", "."))
                phone = (row.get("phone_e164") or "").strip()
                if phone and not phone.startswith("+") and phone[0].isdigit():
                    phone = "+" + phone
                if name:
                    results.append({"name": name, "latitude": lat, "longitude": lon, "phone_e164": phone})
            except Exception:
                continue
    log("info", f"Loaded {len(results)} municipalities")
    return results

def closest_municipalities(munis: list, lat: float, lon: float, n: int = TOP_N_MUNICIPALITIES) -> list:
    for m in munis:
        m["distance_km"] = haversine_km(lat, lon, m["latitude"], m["longitude"])
    return sorted(munis, key=lambda x: x["distance_km"])[:max(0, min(n, len(munis)))]

# ==================== EUMETSAT: CAP PARSING ====================

def _ns_any(tag: str) -> str:
    return f".//{{*}}{tag}"

def extract_cap_xml_from_blob(blob: bytes) -> list:
    out = []
    if b"<alert" in blob or b":alert" in blob:
        out.append(blob)
        return out
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
    if len(blob) >= 2 and blob[:2] == b"\x1f\x8b":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(blob)) as gf:
                data = gf.read()
            out.extend(extract_cap_xml_from_blob(data))
            return out
        except Exception:
            pass
    if len(blob) > 265 and blob[257:262] == b"ustar":
        try:
            with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
                for m in tf.getmembers():
                    if not m.isfile():
                        continue
                    fh = tf.extractfile(m)
                    if not fh:
                        continue
                    data = fh.read()
                    if b"<alert" in data or b":alert" in data:
                        out.append(data)
            return out
        except Exception:
            pass
    return out

def parse_cap_points(xml_bytes: bytes) -> Tuple[Optional[str], Optional[str], Optional[str], list]:
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
    pts = []

    for p in root.findall(_ns_any("point")) + root.findall(_ns_any("info/area/point")):
        try:
            t = (p.text or "").strip().replace(" ", "")
            lat_s, lon_s = t.split(",")
            pts.append((float(lat_s), float(lon_s)))
        except Exception:
            pass

    for c in root.findall(_ns_any("circle")) + root.findall(_ns_any("info/area/circle")):
        try:
            t = (c.text or "").strip().replace("km", "").replace("KM", "")
            parts = t.replace(",", " ").split()
            if len(parts) >= 3:
                pts.append((float(parts[0]), float(parts[1])))
        except Exception:
            pass

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

# ==================== FIRMS HELPERS ====================

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

def fetch_firms_hotspots(csv_url: str, source_label: str) -> list:
    with urllib.request.urlopen(csv_url, timeout=30) as resp:
        data = resp.read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for row in reader:
        try:
            lat = float(row["latitude"])
            lon = float(row["longitude"])
            conf = (row.get("confidence") or "").strip().lower()
            frp = float(row.get("frp") or 0.0)
            acq_date = (row.get("acq_date") or "").strip()
            acq_time = (row.get("acq_time") or "").strip()
            when_text, dtobj = _parse_dt(acq_date, acq_time)
            out.append({
                "lat": lat, "lon": lon, "confidence": conf, "frp": frp,
                "when_text": when_text, "when_dt": dtobj, "source": source_label
            })
        except Exception:
            continue
    return out

def screen_hotspots(rows: list, min_frp: float = MIN_FRP_MW) -> list:
    return [r for r in rows if r["frp"] >= min_frp]

def dedup_hotspots(rows: list, time_window_min: int = DEDUP_TIME_MIN, spatial_km: float = DEDUP_DISTANCE_KM) -> list:
    rows2 = [r for r in rows if r["when_dt"] is not None]
    rows2.sort(key=lambda r: r["when_dt"])
    kept = []
    for r in rows2:
        dup = False
        for k in kept:
            if abs((r["when_dt"] - k["when_dt"]).total_seconds()) <= time_window_min * 60:
                if haversine_km(r["lat"], r["lon"], k["lat"], k["lon"]) <= spatial_km:
                    dup = True
                    break
        if not dup:
            kept.append(r)
    kept.extend([r for r in rows if r["when_dt"] is None])
    return kept

# ==================== MAIN INGESTION ====================

def run_ingestion(lookback_min: int = 20):
    log("info", f"Starting ingestion cycle (lookback={lookback_min} min)")

    municipalities = load_municipalities(MUNICIPALITIES_CSV)

    ensure_csv(AFM_CSV_FILE, ["ingested_at_utc", "cap_time_utc", "latitude", "longitude", "certainty", "severity", "product_id"])
    ensure_csv(FIRMS_CSV_FILE, ["ingested_at_utc", "when_utc", "latitude", "longitude", "confidence", "frp_mw", "feed"])

    total_points = 0

    # ========== EUMETSAT ==========
    try:
        import eumdac
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=lookback_min)
        log("info", f"EUMETSAT: searching {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M}")

        token = eumdac.AccessToken((EUMETSAT_CONSUMER_KEY, EUMETSAT_CONSUMER_SECRET))
        ds = eumdac.DataStore(token)
        try:
            col = ds.get_collection(COLLECTION_ID)
        except Exception:
            col = ds.collection(COLLECTION_ID)

        try:
            results = col.search(dtstart=start, dtend=end)
        except TypeError:
            results = col.search(start=start, end=end)

        afm_state = load_state(AFM_STATE_FILE, {"seen_ids": []})
        seen_ids = set(afm_state.get("seen_ids", []))
        ing = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        for prod in results:
            pid = str(prod)
            if pid in seen_ids:
                continue

            cap_blobs = []
            if hasattr(prod, "open"):
                try:
                    with prod.open() as fh:
                        blob = fh.read()
                        if isinstance(blob, str):
                            blob = blob.encode("utf-8", "replace")
                    cap_blobs = extract_cap_xml_from_blob(blob)
                except Exception:
                    pass

            for cap in cap_blobs:
                stamp, certainty, severity, points = parse_cap_points(cap)
                points = [p for p in points if in_bbox(p[0], p[1], AFM_BBOX_LAT_MIN, AFM_BBOX_LAT_MAX, AFM_BBOX_LON_MIN, AFM_BBOX_LON_MAX)]
                if points:
                    rows = [[ing, stamp or "", f"{lat:.10f}", f"{lon:.10f}", certainty or "", severity or "", pid] for lat, lon in points]
                    append_rows(AFM_CSV_FILE, rows)
                    total_points += len(rows)

                    for lat, lon in points:
                        if point_in_lebanon(lat, lon):
                            try:
                                from alert_manager import send_fire_alert
                                send_fire_alert(lat, lon, municipalities, source="EUMETSAT", details=f"severity={severity}, certainty={certainty}")
                            except ImportError:
                                pass

            seen_ids.add(pid)

        afm_state["seen_ids"] = list(seen_ids)[-1000:]
        save_state(AFM_STATE_FILE, afm_state)
        log("info", f"EUMETSAT: processed, total points so far: {total_points}")

    except ImportError:
        log("warn", "eumdac not installed – skipping EUMETSAT. Install with: pip install eumdac")
    except Exception as e:
        log("error", f"EUMETSAT failed: {e}")

    # ========== NASA FIRMS ==========
    try:
        all_hotspots = []
        for url in FIRMS_URLS:
            feed = url.split("/")[7] if len(url.split("/")) > 7 else "FIRMS"
            try:
                rows = fetch_firms_hotspots(url, feed)
                rows = screen_hotspots(rows)
                log("info", f"  {feed}: {len(rows)} hotspots kept (FRP≥{MIN_FRP_MW})")
                all_hotspots.extend(rows)
            except Exception as e:
                log("warn", f"  Failed to fetch {feed}: {e}")

        if all_hotspots:
            before = len(all_hotspots)
            merged = dedup_hotspots(all_hotspots)
            log("info", f"FIRMS dedup: {before} → {len(merged)} unique")

            ing = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            out_rows = []
            for h in merged:
                out_rows.append([ing, h["when_text"], f"{h['lat']:.10f}", f"{h['lon']:.10f}", h["confidence"], f"{h['frp']:.2f}", h["source"]])
                if point_in_lebanon(h["lat"], h["lon"]):
                    try:
                        from alert_manager import send_fire_alert
                        send_fire_alert(h["lat"], h["lon"], municipalities, source="NASA FIRMS", details=f"confidence={h['confidence']}, frp={h['frp']:.2f}")
                    except ImportError:
                        pass

            append_rows(FIRMS_CSV_FILE, out_rows)
            total_points += len(out_rows)
    except Exception as e:
        log("error", f"FIRMS failed: {e}")

    log("info", f"Ingestion complete. Total points saved: {total_points}")
    return total_points


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="FDEDC Fire Detection Ingestion Pipeline")
    ap.add_argument("--lookback", type=int, default=60, help="Minutes to look back for EUMETSAT")
    args = ap.parse_args()
    run_ingestion(lookback_min=args.lookback)
