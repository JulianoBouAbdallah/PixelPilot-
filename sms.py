
import requests

url = "https://www.bestsmsbulk.com/bestsmsbulkapi/sendSmsAPI.php"
data = {
    "username": "joeyangelil",
    "password": "Joey-angelil_357",
    "senderid": "FireAlerts",
    "destination": "96171097068",
    "message": "Hello, There is a Fire!"
}

response = requests.post(url, data=data)

if response.status_code == 200:
    print("Response:", response.text)
else:
    print("Error:", response.status_code, response.text)
    
