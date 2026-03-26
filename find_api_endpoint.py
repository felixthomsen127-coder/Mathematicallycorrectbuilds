import requests
import re
import json

url = 'https://lolalytics.com/lol/aatrox/build/'
r = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})

# Look for any API calls or data in the page
api_patterns = [
    r'/api/v\d+/[^"\']*',  # /api/v1/... /api/v2/... etc
    r'https://[^"\']*api[^"\']*',
    r'fetch\(["\']([^"\']*)["\']',  # fetch("...") calls
]

found_apis = set()
for pattern in api_patterns:
    matches = re.findall(pattern, r.text)
    found_apis.update(matches)

if found_apis:
    print("Found API references:")
    for api in list(found_apis)[:10]:
        print(f"  {api}")
else:
    print("No direct API references found")

# Look for __NEXT_DATA__
if '__NEXT_DATA__' in r.text:
    print("\nHas __NEXT_DATA__")
    idx = r.text.find('__NEXT_DATA__')
    start = r.text.find('{', idx)
    end = r.text.find('</script>', start)
    if start > 0 and end > start:
        try:
            json_str = r.text[start:end]
            data = json.loads(json_str)
            print(f"Data keys: {list(data.keys())}")
            if 'props' in data:
                print(f"Props keys: {list(data.get('props', {}).keys())}")
        except Exception as e:
            print(f"Could not parse: {e}")

# Look for any window.variables
if 'window.' in r.text:
    window_vars = re.findall(r'window\.([a-zA-Z_]\w*)\s*=', r.text)
    if window_vars:
        print(f"\nFound window variables: {list(set(window_vars))[:5]}")
