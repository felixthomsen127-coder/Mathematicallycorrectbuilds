import requests
import re

# Try to fetch the main Lolalytics champion page to see if it has any API endpoints
url = 'https://lolalytics.com/lol/aatrox/top/jungle'
print(f'Fetching: {url}')

try:
    r = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
    print(f'Status: {r.status_code}')
    print(f'Response length: {len(r.text)} bytes')
    
    # Check if there are markers for data
    markers = ['__NEXT_DATA__', '__NUXT__', 'window.__state__', 'api.lolalytics', 'getchampion']
    for marker in markers:
        if marker in r.text:
            print(f'Found: {marker}')
        else:
            print(f'NOT found: {marker}')
    
    # Try to find any API calls or data structures
    if 'fetch(' in r.text or 'axios' in r.text or 'XMLHttpRequest' in r.text:
        print('Found API client calls in HTML')
        
except Exception as e:
    print(f'Error: {e}')
