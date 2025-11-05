import os, requests
from dotenv import load_dotenv

load_dotenv()

key = os.getenv("b840c0bae3332a6e3618be3bf427f27db2b5bad65b0e82732bd11d908d248980")
print("Loaded key starts with:", key[:6])

url = "https://api.elevenlabs.io/v1/voices"
headers = {"xi-api-key": key}
r = requests.get(url, headers=headers)
print("Status:", r.status_code)
print("Voices:", r.text[:300])
