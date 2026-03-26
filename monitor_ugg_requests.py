"""
Monitor network requests on u.gg to find where build data comes from.
Uses Selenium with logging to capture network activity (via browser console/DevTools).
"""
import logging
import time
import json
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Enable Chrome logging to capture network requests
caps = DesiredCapabilities.CHROME
caps['goog:loggingPrefs'] = {'driver': 'INFO', 'browser': 'ALL', 'performance': 'ALL'}

chrome_options = Options()
chrome_options.add_argument('--headless')
chrome_options.add_argument('--disable-blink-features=AutomationControlled')
chrome_options.add_argument('--disable-dev-shm-usage')
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--disable-gpu')

try:
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options, desired_capabilities=caps)
    
    logger.info("[1/4] Loading u.gg/lol/champions/aatrox/build...")
    driver.get("https://www.u.gg/lol/champions/aatrox/build")
    
    logger.info("[2/4] Waiting for page to load (10 seconds)...")
    time.sleep(10)
    
    logger.info("[3/4] Checking browser logs for API requests...")
    # Get browser console logs
    logs = driver.get_log('browser')
    
    # Extract URLs from logs
    api_urls = set()
    for log in logs:
        msg = log.get('message', '')
        # Look for API calls
        if 'api' in msg.lower() or 'fetch' in msg.lower() or 'xhr' in msg.lower():
            logger.info(f"  LOG: {msg[:200]}")
            # Try to extract URLs
            if 'http' in msg:
                parts = msg.split('http')
                for part in parts[1:]:
                    url_part = part.split()[0].split('"')[0].split("'")[0]
                    if url_part.startswith('s://'):
                        api_urls.add('http' + url_part)
    
    if api_urls:
        logger.info("\n[API URLs Found]:")
        for url in sorted(api_urls):
            logger.info(f"  - {url[:100]}")
    
    # Try to manually trigger build data fetch by looking for specific selectors
    logger.info("\n[4/4] Searching for build elements in DOM...")
    
    selectors = [
        "[class*='ItemBuild']",
        "[class*='itemBuild']", 
        "button[class*='build']",
        "[data-testid*='build']",
        "[href*='items']",
        "[class*='6ItemBuild']",
    ]
    
    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            if elements:
                logger.info(f"✓ Found {len(elements)} with '{selector}'")
                for elem in elements[:2]:
                    text = elem.text or elem.get_attribute('innerHTML')[:100]
                    logger.info(f"    Content: {text}")
        except:
            pass
    
    # Check for hardcoded data in any visible text
    logger.info("\n[Checking visible text for item names]...")
    visible_text = driver.find_element(By.TAG_NAME, "body").text
    
    item_keywords = ['trinity', 'divine', 'manamune', 'cleaver', 'kaenic', 'maw', 'serylda']
    found_items = []
    for item in item_keywords:
        if item in visible_text.lower():
            found_items.append(item)
    
    if found_items:
        logger.info(f"✓ Found item keywords in page: {', '.join(found_items)}")
    
    driver.quit()
    logger.info("\nDone!")
    
except Exception as e:
    logger.error(f"Error: {e}", exc_info=True)
