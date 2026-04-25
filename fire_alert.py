#!/usr/bin/env python3
"""
Fire Alert - Interactive Geofence Notifier (single threshold + single coord prompt)

Put this file and municipalities.csv in the same folder (Fire_Detection/).
Run:  python fire_alert.py   (Windows)    or    python3 fire_alert.py   (macOS/Linux)
"""

from __future__ import annotations
import csv, math, os, re
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class Municipality:
    name: str
    latitude: float
    longitude: float
    phone_e164: str
    distance_km: float = 0.0


def get_csv_path() -> str:
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
    
def _to_float(val: str) -> float:
    return float((val or "").strip().replace(",", "."))

def _normalize_phone(phone: str) -> str:
    phone = (phone or "").strip()
    if phone and not phone.startswith("+") and phone[0].isdigit():
        phone = "+" + phone
    return phone

def load_municipalities(csv_path: str) -> List[Municipality]:
    results: List[Municipality] = []
    if not os.path.exists(csv_path):
        print(f"[ERROR] Can't find CSV at: {csv_path}")
        print("Make sure 'municipalities.csv' is in the same folder as fire_alert.py.")
        return results

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        sample = f.read(4096); f.seek(0)
        dialect = _sniff_dialect(sample)
        reader = csv.DictReader(f, dialect=dialect)

        field_map = {(fn or "").strip().lstrip("\ufeff").lower(): fn for fn in (reader.fieldnames or [])}
        def get(row, key): 
            orig = field_map.get(key, "")
            return (row.get(orig) if orig else "") if row else ""

        required = {"municipality","latitude","longitude","phone_e164"}
        if not required.issubset(set(field_map.keys())):
            print("[ERROR] CSV is missing required headers.")
            print("Expected: municipality, latitude, longitude, phone_e164")
            print("Detected:", reader.fieldnames)
            return results

        for i, row in enumerate(reader, start=2):
            try:
                name  = (get(row,"municipality") or "").strip()
                lat   = _to_float(get(row,"latitude"))
                lon   = _to_float(get(row,"longitude"))
                phone = _normalize_phone(get(row,"phone_e164"))
                if not name:
                    raise ValueError("empty municipality name")
                results.append(Municipality(name=name, latitude=lat, longitude=lon, phone_e164=phone))
            except Exception as e:
                print(f"[WARN] Skipping bad row {i}: {e}")
    return results
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlmb/2)**2)
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1-a))

def filter_within_threshold(munis: List[Municipality],
                            center_lat: float, center_lon: float,
                            threshold_km: float) -> List[Municipality]:
    selected: List[Municipality] = []
    for m in munis:
        d = haversine_km(center_lat, center_lon, m.latitude, m.longitude)
        m.distance_km = d
        if 0.0 <= d <= threshold_km:
            selected.append(m)
    selected.sort(key=lambda x: x.distance_km)
    return selected

def send_sms_via_api_stub(phone_e164: str, message_text: str) -> None:
    # Placeholder for your future SMS API call.
    print()

def prompt_coords(default_lat: float = 34.094, default_lon: float = 35.651) -> Tuple[float, float]:
    """
    Ask once for both numbers. Accepts formats like:
      34.094, 35.651   OR   34.094 35.651   OR   [34.094, 35.651]
    Tip: If you use decimal commas (34,094), separate the two values with a SPACE.
    """
    while True:
        raw = input(f"Fire Latitude and Longitude coordinates [{default_lat}, {default_lon}]: ").strip()
        if raw == "":
            return default_lat, default_lon

        # remove brackets and normalize separators
        raw = raw.strip("[]()")
        raw = raw.replace(";", " ").replace("|", " ")
        # try comma first; if too many commas (decimal-commas), fall back to whitespace
        parts = [p.strip() for p in raw.split(",")] if raw.count(",") in (0,1) else raw.split()
        if len(parts) == 1:
            parts = parts[0].split()  # maybe user used only space
        if len(parts) != 2:
            print("Please enter two numbers like: 34.094, 35.651  (or space separated).")
            continue
        try:
            lat = float(parts[0].replace(",", "."))
            lon = float(parts[1].replace(",", "."))
            return lat, lon
        except ValueError:
            print("Couldn't parse numbers. Try again (example: 34.094, 35.651).")

def prompt_threshold(default_km: float = 10.0) -> float:
    while True:
        raw = input(f"Threshold distance in km [<= {default_km} default {default_km}]: ").strip()
        if raw == "":
            return default_km
        raw = raw.replace(",", ".")
        try:
            v = float(raw)
            if v < 0:
                print("Threshold must be >= 0.")
                continue
            return v
        except ValueError:
            print("Please enter a valid number (e.g., 10).")


# --------------------------- Main ---------------------------
def main():
    print("=== Fire Alert (Interactive) ===")
    lat, lon = prompt_coords()
    threshold_km = prompt_threshold(10.0)  # min is implicitly 0

    csv_path = get_csv_path()
    municipalities = load_municipalities(csv_path)
    if not municipalities:
        return

    matches = filter_within_threshold(municipalities, lat, lon, threshold_km)

    print(f"\nMunicipalities within the threshold distance of 0-{threshold_km} km:")
    if not matches:
        print("None found within the specified distance.")
        return

    for m in matches:
        msg = f"FIRE ALERT: Fire near {lat:.5f},{lon:.5f}. Please check {m.name}."
        send_sms_via_api_stub(m.phone_e164, msg)
        print(f"{m.name}: At a Distance of {m.distance_km:.1f}km from the fire -> SMS sent to {m.phone_e164}")


if __name__ == "__main__":
    main()

