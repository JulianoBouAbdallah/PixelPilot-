# PixelPilot-
Data Structure
The system utilizes a combination of structured CSV files and JSON-based state files to manage fire detection data, alert distribution, and system tracking. This design ensures efficient data storage, ease of processing, and prevention of duplicate operations.
1. municipalities.csv
This file stores information about municipalities or response stations that receive fire alerts. It is a core dataset used to determine the nearest entities to a detected fire location.
Attributes:
municipality: Name of the municipality
latitude: Geographic latitude coordinate
longitude: Geographic longitude coordinate
phone_e164: Contact phone number in international (E.164) format
address: Physical or descriptive location of the station
type: Type of station (e.g., Fire Station, Civil Defense, Hospital)
coverage: Estimated response radius of the station
This dataset supports proximity calculations and can be extended to include additional operational metadata.
2. afm_cap_points.csv (EUMETSAT Data)
This file contains fire detection data obtained from EUMETSAT CAP (Common Alerting Protocol) products. Each row represents a detected fire point extracted from satellite alerts.
Attributes:
ingested_at_utc: Timestamp indicating when the data was stored in the system
cap_time_utc: Timestamp provided within the CAP alert
latitude: Latitude of the detected fire location
longitude: Longitude of the detected fire location
certainty: Confidence level of detection (e.g., low, nominal, high)
severity: Intensity classification of the fire (e.g., minor, moderate, severe, extreme)
product_id: Unique identifier of the satellite data product
3. firms_hotspots.csv (NASA FIRMS Data)
This file stores fire hotspot data retrieved from NASA FIRMS. It complements EUMETSAT data by providing additional detection sources and fire intensity metrics.
Attributes:
ingested_at_utc: Timestamp indicating when the data was recorded
when_utc: Timestamp of fire detection
latitude: Latitude of the hotspot
longitude: Longitude of the hotspot
confidence: Detection confidence level (l = low, n = nominal, h = high)
frp_mw: Fire Radiative Power, representing fire intensity in megawatts
feed: Source satellite or data feed
4. alerts_log.csv
This file maintains a complete record of all alerts sent by the system. It is primarily used for auditing, monitoring, and debugging purposes.
Attributes:
ts_utc: Timestamp when the alert was sent
source: Origin of the data (EUMETSAT or NASA FIRMS)
lat, lon: Geographic coordinates of the fire
recipients: List of recipient phone numbers
message: Content of the alert message
extra: Additional information such as severity or confidence
product_or_feed: Identifier of the data source
5. JSON State Files
JSON files are used to maintain system state and ensure efficient processing without duplication.
afm_cap_state.json
Stores identifiers of previously processed EUMETSAT products. This prevents redundant data processing during subsequent system cycles.
alerts_state.json
Maintains a list of previously sent alert keys. This ensures that duplicate SMS notifications are not issued for the same fire event.
firms_state.json
Reserved for tracking NASA FIRMS processing state. It can be extended for additional control or optimization features.