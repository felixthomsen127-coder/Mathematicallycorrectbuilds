import requests
from bs4 import BeautifulSoup
import json

# Try to fetch a champion page and see if there's any API data embedded in it
urls_to_try = [
    'https://lolalytics.com/',
    'https://lolalytics.com/lol/tier-list',
    'https://lolalytics.com/lol/jungle/tier-list',
]

for url in urls_to_try:
    print(f"\n=== Testing {url} ===")
    try:
        r = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
        print(f"Status: {r.status_code}")
        
        # Look for __NEXT_DATA__ or similar markers
        if '__NEXT_DATA__' in r.text:
            print("Has __NEXT_DATA__")
            # Extract the JSON
            idx = r.text.find('__NEXT_DATA__')
            if idx > 0:
                # Find the JSON object
                start = r.text.find('{', idx)
                end = r.text.find('</script>', start)
                if start > 0 and end > start:
                    try:
                        data_str = r.text[start:end]
                        data = json.loads(data_str)
                        print(f"Found JSON data with keys: {list(data.keys())[:5]}")
                    except:
                        pass
        
        # Look for any API endpoints mentioned in script tags
        if 'api.lolalytics' in r.text or '/api/' in r.text:
            print("Found '/api/' reference in page")
            
    except Exception as e:
        print(f"Error: {e}")
