import requests
import json
headers = {
    "Origin": "http://192.168.65.2:3000",
    "Content-Type": "application/json",
    # Intentionally omitted Authorization to see if 401 has CORS headers
}
print("Testing acts/add")
r = requests.post("http://localhost:8000/api/v1/acts/add", json={"act_name": "constitution"}, headers=headers)
print(r.status_code)
print(r.headers)
print(r.text)
