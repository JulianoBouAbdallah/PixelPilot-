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
        ef _normalize_phone(phone: str) -> str:
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

