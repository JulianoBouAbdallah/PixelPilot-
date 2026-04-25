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
