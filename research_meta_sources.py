import requests
import json

# Test various League of Legends meta build data sources

print("=== Testing League Meta Data Sources ===\n")

# 1. Test U.GG API
print("1. Testing U.GG API endpoints:")
ugg_endpoints = [
    "https://api.u.gg/api/v1/lol/build/ranked",
    "https://api.u.gg/api/v2/lol/champion/aatrox/builds",
    "https://api.u.gg/api/v1/top",
    "https://api.u.gg/api/v1/jungle",
]

for endpoint in ugg_endpoints:
    try:
        r = requests.get(endpoint, timeout=3, headers={'User-Agent': 'Mozilla/5.0'})
        print(f"  {endpoint}: {r.status_code}")
        if r.status_code == 200:
            try:
                data = r.json()
                print(f"    Response preview: {str(data)[:100]}")
            except:
                print(f"    Response not JSON")
    except Exception as e:
        print(f"  {endpoint}: {type(e).__name__}")

# 2. Test OP.GG API
print("\n2. Testing OP.GG API endpoints:")
opgg_endpoints = [
    "https://api.op.gg/api/v1.0/lol/champion-meta",
    "https://api.op.gg/api/v1.0/meta/champion-meta",
    "https://www.op.gg/api/v1.0/lol/champion/jungle/builds/aatrox",
]

for endpoint in opgg_endpoints:
    try:
        r = requests.get(endpoint, timeout=3, headers={'User-Agent': 'Mozilla/5.0'})
        print(f"  {endpoint}: {r.status_code}")
    except Exception as e:
        print(f"  {endpoint}: {type(e).__name__}")

# 3. Test other sources
print("\n3. Testing other sources:")
other_endpoints = [
    "https://www.metasrc.com/5v5/pick-rates",
    "https://stats.leagueoflegends.com/api/champion-meta",
    "https://blitzapp.com/api/v1/lol/ranked/meta",
]

for endpoint in other_endpoints:
    try:
        r = requests.get(endpoint, timeout=3, headers={'User-Agent': 'Mozilla/5.0'})
        print(f"  {endpoint}: {r.status_code}")
    except Exception as e:
        print(f"  {endpoint}: {type(e).__name__}")
