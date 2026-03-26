import requests
import re

# Try to find the API endpoint by checking what URLs the page references or calls
# Let's test various API endpoint formats that might be used by Lolalytics

test_endpoints = [
    'https://lolalytics.com/api/champion/aatrox/jungle',
    'https://lolalytics.com/api/champion/aatrox/jungle/emerald_plus',
    'https://lolalytics.com/api/champion/aatrox',
    'https://api.lolalytics.com/champion/aatrox',
    'https://api.lolalytics.com/champion/aatrox/builds',
    'https://lolalytics.com/api/getchampion/266',
    'https://lolalytics.com/api/builds/aatrox/jungle',
    'https://lolalytics.com/api/lol/champion/aatrox/build',
]

print("Testing API endpoints:")
for endpoint in test_endpoints:
    try:
        r = requests.get(endpoint, timeout=3, headers={'User-Agent': 'Mozilla/5.0'})
        status = r.status_code
        is_json = False
        try:
            data = r.json()
            is_json = True
            keys = list(data.keys())[:3] if isinstance(data, dict) else 'array'
            print(f"✓ {endpoint}: {status} (JSON with keys: {keys})")
        except:
            if status == 200:
                print(f"✓ {endpoint}: {status} (HTML/TEXT)")
            elif status == 404:
                print(f"✗ {endpoint}: {status} (Not found)")
            else:
                print(f"? {endpoint}: {status}")
    except Exception as e:
        print(f"✗ {endpoint}: {type(e).__name__}")
