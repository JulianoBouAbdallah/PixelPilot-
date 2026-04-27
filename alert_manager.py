#!/usr/bin/env python3
"""
FDEDC System – Alert Manager
Handles SMS alert creation and dispatch for detected fires.
"""

import csv
import os
from datetime import datetime, timezone
from typing import List

TOP_N_MUNICIPALITIES = 3
ALWAYS_NOTIFY_PHONE = os.getenv("ALWAYS_NOTIFY_PHONE", "96171097068").strip()
ALERTS_LOG_FILE = "alerts_log.csv"
ENABLE_SMS = os.getenv("ENABLE_SMS_API", "0") == "1"

# Import SMS sender
try:
    from communication.sms import send_sms
except ImportError:
    def send_sms(destinations: list, message: str) -> str:
        print(f"[SMS STUB] Would send to {destinations}: {message[:80]}...")
        return "STUB_SENT"


def maps_link(lat: float, lon: float) -> str:
    """Generate a Google Maps link for the fire location."""
    return f"https://www.google.com/maps/search/?api=1&query={lat:.6f}%2C{lon:.6f}"


def build_alert_message(lat: float, lon: float, source: str, details: str = "") -> str:
    """Build the SMS alert message."""
    link = maps_link(lat, lon)
    return (
        f"[{source}] FIRE ALERT\n"
        f"Location: {link}\n"
        f"{details}\n"
        "Please take action as soon as possible before the fire spreads!"
    )


def log_alert(source: str, lat: float, lon: float, recipients: List[str], message: str, details: str, product_id: str = "") -> None:
    """Log an alert to alerts_log.csv."""
    file_exists = os.path.isfile(ALERTS_LOG_FILE)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(ALERTS_LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["ts_utc", "source", "lat", "lon", "recipients", "message", "extra", "product_or_feed"])
        writer.writerow([
            ts, source, f"{lat:.10f}", f"{lon:.10f}",
            ",".join(recipients), message, details, product_id
        ])


def send_fire_alert(lat: float, lon: float, municipalities: list, source: str = "SAT", details: str = "") -> int:
    """
    Main function: send SMS alerts about a fire to the nearest municipalities.

    Returns:
        Number of recipients alerted
    """
    from ingestion import closest_municipalities, point_in_lebanon

    lebanon_only = os.getenv("LEBANON_ONLY_SMS", "1") == "1"
    if lebanon_only and not point_in_lebanon(lat, lon):
        print(f"[ALERT] Fire at ({lat:.4f}, {lon:.4f}) outside Lebanon – skipping SMS")
        return 0

    nearest = closest_municipalities(municipalities, lat, lon, TOP_N_MUNICIPALITIES)
    recipients = [m["phone_e164"] for m in nearest if m.get("phone_e164")]

    if ALWAYS_NOTIFY_PHONE and ALWAYS_NOTIFY_PHONE not in recipients:
        recipients.append(ALWAYS_NOTIFY_PHONE)

    if not recipients:
        print(f"[ALERT] No recipients found for fire at ({lat:.4f}, {lon:.4f})")
        return 0

    message = build_alert_message(lat, lon, source, details)

    print(f"[ALERT] Sending to {len(recipients)} recipients: {recipients}")
    if ENABLE_SMS:
        response = send_sms(recipients, message)
        print(f"[ALERT] SMS response: {response}")
    else:
        print(f"[ALERT] SMS DISABLED – would send: {message[:100]}...")

    log_alert(source, lat, lon, recipients, message, details)

    print(f"Fire at ({lat:.5f}, {lon:.5f}) [{source}]")
    print(f"  Nearest: {', '.join(m['name'] + f' ({m[\"distance_km\"]:.1f} km)' for m in nearest)}")
    return len(recipients)


# Quick test
if __name__ == "__main__":
    from ingestion import load_municipalities
    munis = load_municipalities("municipalities.csv")
    if munis:
        print("Test: Sending alert for Beirut coordinates...")
        count = send_fire_alert(33.8889, 35.4955, munis, source="TEST", details="Test alert – please ignore")
        print(f"Alerts sent to {count} recipients")
    else:
        print("No municipalities loaded. Check municipalities.csv exists.")
