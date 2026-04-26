#!/usr/bin/env python3
"""
FDEDC System – Minimal Flask Server
Serves static files and handles CSV-based persistence.
Run: python server.py
Then open: http://localhost:5000
"""

from flask import Flask, request, jsonify, send_from_directory
import csv
import os
from datetime import datetime

app = Flask(__name__, static_folder='.', static_url_path='')

# -------------------- HOMEPAGE

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

# -------------------- STATIC HTML PAGES

@app.route('/login.html')
def login_page():
    return send_from_directory('.', 'login.html')

@app.route('/admin.html')
def admin_page():
    return send_from_directory('.', 'admin.html')

@app.route('/dashboard.html')
def dashboard_page():
    return send_from_directory('.', 'dashboard.html')

# -------------------- STATIC DATA FILES

@app.route('/<path:filename>')
def serve_static(filename):
    """Serve any file from the project folder (CSV, GeoJSON, JS, CSS, images)."""
    if os.path.isfile(os.path.join(os.path.dirname(__file__), filename)):
        return send_from_directory('.', filename)
    return jsonify({'error': 'File not found'}), 404

# -------------------- API: SAVE STATIONS

@app.route('/api/save-stations', methods=['POST'])
def save_stations():
    """
    Receives JSON array of station objects and overwrites municipalities.csv.
    Expected format: [{"municipality": "...", "latitude": ..., "longitude": ..., "phone_e164": "...", "address": "...", "status": "Active"}, ...]
    """
    try:
        data = request.json
        if not data or not isinstance(data, list):
            return jsonify({'status': 'error', 'error': 'Expected a JSON array of stations'}), 400

        # Determine headers from the first station object
        headers = ['municipality', 'latitude', 'longitude', 'phone_e164', 'address', 'status']

        with open('municipalities.csv', 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for station in data:
                writer.writerow([
                    station.get('municipality', ''),
                    station.get('latitude', ''),
                    station.get('longitude', ''),
                    station.get('phone_e164', ''),
                    station.get('address', ''),
                    station.get('status', 'Active')
                ])

        return jsonify({'status': 'ok', 'message': f'Saved {len(data)} stations'})

    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

# -------------------- API: RESOLVE FIRE

@app.route('/api/resolve-fire', methods=['POST'])
def resolve_fire():
    """
    Marks a fire as resolved by appending to resolved_fires.csv.
    Expected format: {"lat": 33.89, "lon": 35.50, "source": "NASA FIRMS", "product_id": "...", "resolved_by": "Operator", "notes": "...", "timestamp": "..."}
    """
    try:
        fire = request.json
        if not fire:
            return jsonify({'status': 'error', 'error': 'No data received'}), 400

        required = ['lat', 'lon']
        for field in required:
            if field not in fire:
                return jsonify({'status': 'error', 'error': f'Missing field: {field}'}), 400

        file_exists = os.path.isfile('resolved_fires.csv')

        with open('resolved_fires.csv', 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['lat', 'lon', 'source', 'product_id', 'resolved_by', 'notes', 'timestamp'])
            writer.writerow([
                fire.get('lat', ''),
                fire.get('lon', ''),
                fire.get('source', ''),
                fire.get('product_id', ''),
                fire.get('resolved_by', ''),
                fire.get('notes', ''),
                fire.get('timestamp', datetime.now().isoformat())
            ])

        return jsonify({'status': 'ok', 'message': 'Fire marked as resolved'})

    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

# -------------------- API: AUDIT LOG

@app.route('/api/audit-log', methods=['POST'])
def audit_log():
    """
    Appends an entry to audit_log.csv.
    Expected format: {"timestamp": "...", "user": "...", "action": "...", "details": "..."}
    """
    try:
        entry = request.json
        if not entry:
            return jsonify({'status': 'error', 'error': 'No data received'}), 400

        file_exists = os.path.isfile('audit_log.csv')

        with open('audit_log.csv', 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['timestamp', 'user', 'action', 'details'])
            writer.writerow([
                entry.get('timestamp', datetime.now().isoformat()),
                entry.get('user', 'unknown'),
                entry.get('action', ''),
                entry.get('details', '')
            ])

        return jsonify({'status': 'ok', 'message': 'Audit log updated'})

    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

# -------------------- API: REGISTER USER

@app.route('/api/register', methods=['POST'])
def register_user():
    """
    Registers a new user by appending to users.csv.
    Expected format: {"username": "...", "password": "...", "fullName": "...", "email": "..."}
    """
    try:
        data = request.json
        if not data:
            return jsonify({'status': 'error', 'error': 'No data received'}), 400

        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        full_name = data.get('fullName', '').strip()
        email = data.get('email', '').strip()

        # Basic validation
        if not username or not password or not full_name:
            return jsonify({'status': 'error', 'error': 'Username, password, and full name are required'}), 400

        # Check for duplicate username in users.csv
        users_file = 'users.csv'
        file_exists = os.path.isfile(users_file)

        if file_exists:
            with open(users_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('username', '').strip() == username:
                        return jsonify({'status': 'error', 'error': 'Username already exists'}), 409

        # Append new user
        with open(users_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['username', 'password', 'full_name', 'email', 'role', 'created_at'])
            writer.writerow([
                username,
                password,
                full_name,
                email,
                'Operator',
                datetime.now().isoformat()
            ])

        return jsonify({'status': 'ok', 'message': 'Account created successfully'})

    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


# -------------------- START SERVER 
if __name__ == '__main__':
    print("=" * 50)
    print("🔥 FDEDC System Server")
    print("=" * 50)
    print("Open your browser to: http://localhost:5000")
    print("Press Ctrl+C to stop the server")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=True)