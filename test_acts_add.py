import requests

url = "http://192.168.65.2:8000/api/v1"

# 1. create dummy user
res = requests.post(f"{url}/auth/register", json={
    "email": "testcors@example.com",
    "full_name": "Test",
    "password": "password"
})

# 2. login
res = requests.post(f"{url}/auth/token", data={
    "username": "testcors@example.com",
    "password": "password"
})
token = res.json().get("access_token")

# 3. hit /acts/add
headers = {
    "Origin": "http://192.168.65.2:3000",
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}
print("Requesting acts/add...")
r = requests.post(f"{url}/acts/add", json={"act_name": "constitution"}, headers=headers)
print("Status Code:", r.status_code)
print("Headers:", dict(r.headers))
print("Response:", r.text)
