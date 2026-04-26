## Data Structure

The system uses a combination of structured CSV files and JSON-based state files to manage wildfire detection, alert distribution, and system state tracking. This hybrid design ensures efficient data storage, streamlined processing, and prevention of duplicate detections or repeated notifications.

---

### 1. `municipalities.csv`
This file contains information about municipalities and response stations that receive fire alerts. It serves as the primary dataset for identifying the nearest emergency response entity to a detected wildfire.

#### Attributes:
- **municipality**: Name of the municipality  
- **latitude**: Geographic latitude coordinate  
- **longitude**: Geographic longitude coordinate  
- **phone_e164**: Contact phone number in international (E.164) format  
- **address**: Physical or descriptive location of the station  
- **type**: Type of station (e.g., Fire Station, Civil Defense, Hospital)  
- **coverage**: Estimated response radius of the station  

#### Purpose:
This dataset supports:
- Geographic proximity calculations  
- Emergency station identification  
- SMS recipient targeting  
- Station metadata management  

It can also be extended to include operational hours, priority level, or specialized response capabilities.

---

### 2. `afm_cap_points.csv` (EUMETSAT Data)
This file stores wildfire detection points extracted from EUMETSAT CAP (Common Alerting Protocol) satellite products. Each record corresponds to a fire event detected via EUMETSAT systems.

#### Attributes:
- **ingested_at_utc**: Timestamp when the alert was stored in the system  
- **cap_time_utc**: Timestamp provided by the CAP product  
- **latitude**: Latitude of the detected fire  
- **longitude**: Longitude of the detected fire  
- **certainty**: Confidence level of the detection (low, nominal, high)  
- **severity**: Fire severity level (minor, moderate, severe, extreme)  
- **product_id**: Unique identifier of the EUMETSAT product  

#### Purpose:
- Stores satellite fire points  
- Tracks CAP alerts over time  
- Provides severity and certainty classification  
- Supports integration with the alert engine  

---

### 3. `firms_hotspots.csv` (NASA FIRMS Data)
This file stores wildfire hotspot data obtained from NASA FIRMS (Fire Information for Resource Management System). It acts as an additional or complementary wildfire detection source.

#### Attributes:
- **ingested_at_utc**: Timestamp when the data was recorded  
- **when_utc**: Original fire detection timestamp  
- **latitude**: Latitude of hotspot  
- **longitude**: Longitude of hotspot  
- **confidence**: Detection confidence (l = low, n = nominal, h = high)  
- **frp_mw**: Fire Radiative Power in megawatts  
- **feed**: Source satellite or detection feed  

#### Purpose:
- Provides NASA wildfire hotspot detection  
- Enhances detection reliability  
- Supplies fire intensity metrics  
- Enables multi-source validation  

---

### 4. `alerts_log.csv`
This file maintains a historical log of all alerts sent by the system. It is critical for auditing, debugging, and operational transparency.

#### Attributes:
- **ts_utc**: Timestamp when the alert was sent  
- **source**: Data source (EUMETSAT or NASA FIRMS)  
- **lat**: Fire latitude  
- **lon**: Fire longitude  
- **recipients**: List of recipient phone numbers  
- **message**: SMS alert content  
- **extra**: Additional details (severity, certainty, FRP, etc.)  
- **product_or_feed**: Data source identifier  

#### Purpose:
- Alert auditing  
- Notification verification  
- System debugging  
- Duplicate detection analysis  

---

### 5. JSON State Files
JSON files are used to preserve system state between executions and prevent duplicate processing or repeated notifications.

---

#### `afm_cap_state.json`
Stores identifiers of previously processed EUMETSAT CAP products.

#### Purpose:
- Prevents reprocessing of old CAP products  
- Improves efficiency  
- Maintains product history  

---

#### `alerts_state.json`
Stores previously generated alert keys.

#### Purpose:
- Prevents duplicate SMS notifications  
- Ensures one-time alert dispatch per event  
- Maintains alert integrity  

---

#### `firms_state.json`
Tracks NASA FIRMS processing state.

#### Purpose:
- Prevents repeated FIRMS hotspot ingestion  
- Supports future optimization  
- Enables scalable state management  

---

## Overall System Design Benefits

### Advantages:
- **Structured Storage:** CSV files are lightweight, readable, and compatible with multiple platforms  
- **State Persistence:** JSON files maintain memory between cycles  
- **Duplicate Prevention:** Prevents redundant alerts and processing  
- **Scalability:** Easy to extend with new data sources or metadata  
- **Auditability:** Full operational logs for debugging and accountability  

---

## System Workflow Integration
1. Fire detected via EUMETSAT or NASA  
2. Data stored in source CSV  
3. Municipality proximity calculated via `municipalities.csv`  
4. Alert sent to nearest station  
5. Alert recorded in `alerts_log.csv`  
6. State updated via JSON files  
7. Duplicate events ignored in future cycles  

---

## Conclusion
The data structure is designed to provide a robust, scalable, and efficient backbone for wildfire monitoring and emergency response. By combining CSV-based structured datasets with JSON state tracking, the system ensures reliable fire detection, accurate response targeting, operational transparency, and protection against duplicate processing.
