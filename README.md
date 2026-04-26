# Wildfire Detection & Emergency Response System – Lebanon

## Project Overview
This project is an AI-powered wildfire monitoring and emergency response platform designed to detect wildfire hotspots in Lebanon using NASA FIRMS / EUMETSAT satellite data, map them to the nearest municipality, identify the closest civil defense station, and automatically send SMS alerts for emergency response.

The system combines:
- Satellite fire detection
- Municipality geolocation
- Civil defense station mapping
- SMS notifications
- Web dashboard visualization
- Admin management tools

---

# Objectives
- Detect wildfire incidents automatically
- Map fires to Lebanese municipalities
- Alert the nearest response station
- Provide a public dashboard for fire tracking
- Enable admins to manage station data
- Maintain logs for transparency and auditing

---

# System Components

## 1. Frontend Dashboard (`index.html`)
Public-facing interface that:
- Displays wildfire incidents
- Shows fire location
- Filters active/resolved fires
- Links users to login/admin system

### Features:
- Fire list display
- Resolve button
- Fire status filtering
- Navigation to login

---

## 2. Login System (`login.html`)
Secure access page for authorized users.

### Features:
- Hardcoded authentication
- Username/password validation
- Redirect to admin dashboard

---

## 3. Admin Dashboard (`admin.html`)
Administrative panel for managing municipality and station data.

### Features:
- View municipality list
- Edit station details
- Update coverage information
- Audit logging of changes

---

## 4. Fire Detection Engine (`eumdac_nasa.py`)
Python pipeline that:
- Connects to NASA/EUMETSAT fire datasets
- Extracts wildfire coordinates
- Matches incidents to Lebanese municipalities
- Determines nearest station

---

## 5. SMS Alert System (`sms.py`)
Responsible for:
- Sending emergency alerts
- Notifying nearest response teams
- Logging alert history

---

# File Structure

```plaintext
project-root/
│
├── eumdac_nasa.py        # Satellite wildfire detection pipeline
├── sms.py                # SMS alert system
├── index.html            # Public dashboard
├── login.html            # User login page
├── admin.html            # Admin panel
├── municipalities.csv    # Municipality + station database
├── lebanon.geojson       # Lebanon geographic boundary data
├── alerts_log.csv        # Fire alert logs
├── audit_log.csv         # Admin action logs
├── requirements.txt      # Python dependencies
└── README.md             # Project documentation
