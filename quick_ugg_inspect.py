"""Quick u.gg page inspection with timeout."""
import logging
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

chrome_options = Options()
chrome_options.add_argument('--disable-blink-features=AutomationControlled')
chrome_options.add_argument('--headless')  # Run in background
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--disable-dev-shm-usage')

try:
    logger.info("Starting WebDriver...")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    
    logger.info("Loading u.gg/lol/champions/aatrox/build...")
    driver.get("https://www.u.gg/lol/champions/aatrox/build")
    
    # Quick wait
    time.sleep(5)
    
    # Try to execute JavaScript to find data
    logger.info("Checking page for data...")
    try:
        # Check if there's a global data object
        result = driver.execute_script("""
            return {
                window_keys: Object.keys(window).filter(k => k.length < 15 && (k.includes('data') || k.includes('store') || k.includes('__'))).slice(0, 20),
                body_has_content: document.body.innerText.length > 100,
                scripts_count: document.querySelectorAll('script').length,
                external_scripts: Array.from(document.querySelectorAll('script[src]')).map(s => s.src).slice(0, 5)
            };
        """)
        logger.info(f"Page data found: {result}")
    except Exception as e:
        logger.error(f"JavaScript error: {e}")
    
    # Save a sample of the HTML
    html_content = driver.page_source
    with open('ugg_page_sample.html', 'w', encoding='utf-8') as f:
        f.write(html_content[:10000])  # First 10KB
    
    logger.info(f"Saved {len(html_content)} bytes of HTML")
    logger.info("Page title: " + driver.title)
    
    # Try to find specific text patterns
    body_text = driver.execute_script("return document.body.innerText;")
    if "aatrox" in body_text.lower():
        logger.info("✓ Found 'aatrox' in page content")
    if "item" in body_text.lower():
        logger.info("✓ Found 'item' in page content")
    
    logger.info("Inspection complete!")
    driver.quit()
    
except Exception as e:
    logger.error(f"Error: {e}")
