import requests
import re

print("Testing Lolalytics API availability...")

# Test if Lolalytics is even operational
r = requests.get('https://lolalytics.com/', timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
print(f"Homepage status: {r.status_code}")

# Look for any links in the homepage that suggest the structure
links = re.findall(r'href="([^"]*)"', r.text)
sample_links = [l for l in links if '/lol/' in l or '/champion' in l][:5]
if sample_links:
    print(f"Sample champion links found:")
    for link in sample_links:
        print(f"  {link}")
else:
    print("No champion links found in homepage")
    # Check the actual text for keywords
    if 'aatrox' in r.text.lower():
        print("Aatrox mentioned in page")
    if 'api' in r.text.lower():
        print("API mentioned in page")