"""Debug script to inspect u.gg page structure"""
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import time
import re

options = Options()
options.add_argument("--headless")
options.add_argument("--disable-blink-features=AutomationControlled")

service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=options)

url = "https://u.gg/lol/champions/briar/build?role=jungle"
print(f"[*] Loading {url}...")
driver.get(url)

print("[*] Waiting for JavaScript to render...")
time.sleep(5)

print("[*] Getting page source...")
source = driver.page_source

# Look for common patterns
patterns = [
    (r'window\.__DATA__', 'window.__DATA__'),
    (r'window\.__INITIAL_STATE__', 'window.__INITIAL_STATE__'),
    (r'"builds"', 'builds JSON key'),
    (r'"itemIds"', 'itemIds JSON key'),
    (r'"items"', 'items JSON key'),
    (r'<script[^>]*>(.*?"champions".*?)<\/script>', 'script with champions'),
]

print("\n[*] Searching for data patterns:")
for pattern, desc in patterns:
    if re.search(pattern, source, re.IGNORECASE | re.DOTALL):
        print(f"  ✓ Found: {desc}")
    else:
        print(f"  ✗ Not found: {desc}")

# Try to extract JSON data
json_matches = re.findall(r'\{[^{}]*"(?:builds|items)"[^{}]*\}', source)
if json_matches:
    print(f"\n[*] Found {len(json_matches)} JSON objects with builds/items")
    print(f"  First match: {json_matches[0][:200]}...")
else:
    print("\n[*] No JSON objects with builds/items found")

# Look for item-related data
item_matches = re.findall(r'(?:"itemId"|"name"|"tier")\s*:\s*([^,}]+)', source)
if item_matches:
    print(f"\n[*] Found {len(item_matches)} item-related fields")
    print(f"  Sample values: {item_matches[:5]}")

# Save a sample of the page source for inspection
with open('ugg_page_sample.html', 'w', encoding='utf-8') as f:
    # Save first 10KB and last 10KB
    f.write("<!-- FIRST 10KB -->\n")
    f.write(source[:10000])
    f.write("\n\n<!-- LAST 10KB -->\n")
    f.write(source[-10000:])

print("\n[*] Saved page sample to ugg_page_sample.html")

# Extract all script tags and check their content
scripts = re.findall(r'<script[^>]*>(.*?)<\/script>', source, re.DOTALL)
print(f"\n[*] Found {len(scripts)} script tags")

for i, script in enumerate(scripts[:5]):
    if len(script) < 500:
        print(f"  Script {i}: {script[:100]}")
    else:
        # Look for JSON-like content
        if '{' in script:
            print(f"  Script {i}: Contains objects - {script[:80]}...")
        if 'champion' in script.lower():
            print(f"  Script {i}: Contains 'champion' - {script[max(0, script.lower().find('champion')-50):script.lower().find('champion')+100]}")

driver.quit()
print("\n[*] Browser closed")
