import requests
import json
import re

url = "https://u.gg/lol/tier-lists/jungle"

r = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})

print(f"Status: {r.status_code}")
print(f"Content length: {len(r.text)}")

# Find __NEXT_DATA__
if '__NEXT_DATA__' in r.text:
    print("\nFound __NEXT_DATA__")
    # Extract it
    start = r.text.find('__NEXT_DATA__')
    # Find the JSON object
    obj_start = r.text.find('{', start)
    obj_end = r.text.find('</script>', obj_start)
    
    if obj_start > 0 and obj_end > obj_start:
        json_str = r.text[obj_start:obj_end]
        try:
            data = json.loads(json_str)
            print("JSON keys:", list(data.keys())[:10])
            
            # Look for build data
            if 'props' in data:
                props = data['props']
                print("Props keys:", list(props.keys())[:10])
                
                if 'pageProps' in props:
                    page_props = props['pageProps']
                    print("PageProps keys:", list(page_props.keys())[:10])
                    
                    # Look for tier data or champion data
                    for key in page_props.keys():
                        value = page_props[key]
                        if isinstance(value, dict):
                            print(f"  {key}: dict with keys {list(value.keys())[:5]}")
                        elif isinstance(value, list) and value:
                            print(f"  {key}: list with {len(value)} items")
                            if len(value) > 0 and isinstance(value[0], dict):
                                print(f"    First item keys: {list(value[0].keys())[:5]}")
        except Exception as e:
            print(f"Failed to parse: {e}")
            print(f"First 500 chars: {json_str[:500]}")
else:
    print("\nNo __NEXT_DATA__ found")
    
    # Look for any JSON data
    json_blocks = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', r.text, re.DOTALL)
    print(f"Found {len(json_blocks)} JSON script blocks")
