"""Quick test of individual data sources"""
import requests
import sys

sources = {
    'CDragon': "https://raw.communitydragon.org/latest/game/data/characters/champions.json",
    'DDragon': "https://ddragon.leagueoflegends.com/cdn/latest/data/en_US/item.json",
    'MetaSrc': "https://www.metasrc.com/api/v1/champs",
    'OP.GG': "https://api.opgg.com/v2.0/meta",
}

print("[*] Testing which data sources are accessible...")
print()

for name, url in sources.items():
    try:
        print(f"[{name}] {url}")
        response = requests.get(url, timeout=5)
        print(f"  Status: {response.status_code}")
        
        if response.status_code == 200:
            print(f"  ✓ ACCESSIBLE - {len(response.text)} bytes")
        else:
            print(f"  ✗ ERROR - {response.status_code}")
    except requests.exceptions.Timeout:
        print(f"  ✗ TIMEOUT")
    except Exception as e:
        print(f"  ✗ FAILED - {str(e)[:50]}")
    print()
