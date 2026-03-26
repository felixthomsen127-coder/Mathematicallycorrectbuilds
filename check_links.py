import requests
import re

r = requests.get('https://lolalytics.com/', timeout=5, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})

# Get all href attributes
links = re.findall(r'href="([^"]*)"', r.text)
# Filter to only internal links and unique ones
internal_links = sorted(set([l for l in links if l.startswith('/') and len(l) < 100 and 'lol' in l.lower() or 'champion' in l.lower()]))

print("Sample internal links with 'lol' or 'champion':")
for link in internal_links[:20]:
    print(f"  {link}")

# Check for any data attributes
if 'data-' in r.text:
    data_attrs = re.findall(r'data-[a-z-]+="[^"]*"', r.text)
    print(f"\nFound {len(set(data_attrs))} unique data attributes")
    
# Check for next.js or vue data
if '__NEXT_DATA__' in r.text:
    print("Using Next.js")
if '__NUXT__' in r.text:
    print("Using Nuxt")
