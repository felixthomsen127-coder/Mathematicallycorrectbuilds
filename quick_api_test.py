import requests
from urllib.parse import urljoin

print("Testing basic API availability:\n")

# Quick test - just check status codes without waiting too long
tests = [
    ("U.GG builds", "https://u.gg/lol/tier-lists/jungle"),
    ("OP.GG meta", "https://www.op.gg/ranking/tier"),
    ("MetaSrc", "https://www.metasrc.com/lol/tier-lists"),
]

for name, url in tests:
    try:
        r = requests.head(url, timeout=2, allow_redirects=True, headers={'User-Agent': 'Mozilla/5.0'})
        print(f"✓ {name}: {r.status_code} at {r.url}")
    except requests.Timeout:
        print(f"⏱ {name}: Timeout")
    except Exception as e:
        print(f"✗ {name}: {type(e).__name__}")
