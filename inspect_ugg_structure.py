"""
Deep inspection of u.gg page structure to identify build element selectors.
"""
import json
import time
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def inspect_ugg_page(champion="aatrox", role="jungle"):
    """
    Load u.gg champion page and extract detailed DOM structure info.
    """
    driver = None
    try:
        # Setup Selenium
        chrome_options = Options()
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument('--start-maximized')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        logger.info(f"Loading u.gg for {champion}/{role}...")
        url = f"https://www.u.gg/lol/champions/{champion}/build"
        driver.get(url)
        
        # Wait for page to load
        logger.info("Waiting for page to fully load...")
        time.sleep(3)  # Initial wait for JavaScript to render
        
        # Try to wait for specific elements
        try:
            # Common patterns for build display
            WebDriverWait(driver, 10).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "[class*='build'], [class*='Build'], [class*='item'], [data-testid*='build']"))
            )
        except:
            logger.warning("Timeout waiting for specific build elements, continuing with what's loaded...")
        
        # Extract page info
        page_title = driver.title
        logger.info(f"Page title: {page_title}")
        
        # Get all scripts to check for data
        logger.info("\n=== Checking for embedded data in scripts ===")
        scripts = driver.find_elements(By.TAG_NAME, "script")
        logger.info(f"Found {len(scripts)} script tags")
        
        # Look for data in window object and common patterns
        logger.info("\n=== Checking for window object data ===")
        try:
            window_data = driver.execute_script("return Object.keys(window).filter(k => k.toLowerCase().includes('data')).slice(0, 20);")
            logger.info(f"Window data keys: {window_data}")
        except Exception as e:
            logger.warning(f"Could not extract window keys: {e}")
        
        # Check for common element patterns
        logger.info("\n=== Searching for build-related elements ===")
        selectors_to_try = [
            "[class*='build']",
            "[class*='Build']", 
            "[class*='item']",
            "[class*='Item']",
            "[data-testid*='build']",
            "[data-testid*='item']",
            ".build-path",
            "[class*='summoner']",
            "[class*='Summoner']",
            "[class*='item-card']",
            "[class*='ItemCard']",
            "div[class*='match']",
            "[role='presentation']",
            "article",
            "section",
        ]
        
        for selector in selectors_to_try:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    logger.info(f"✓ Found {len(elements)} elements with selector: {selector}")
                    # Log first element's HTML snippet
                    if elements:
                        first_html = elements[0].get_attribute('outerHTML')[:300]
                        logger.info(f"  First element HTML preview: {first_html}...")
            except:
                pass
        
        # Save full page HTML for inspection
        logger.info("\n=== Saving full page HTML ===")
        with open('ugg_full_page.html', 'w', encoding='utf-8') as f:
            f.write(driver.page_source)
        logger.info("Saved to ugg_full_page.html")
        
        # Try to extract visible text content
        logger.info("\n=== Looking for champion info in page text ===")
        body_text = driver.find_element(By.TAG_NAME, "body").text
        lines = body_text.split('\n')[:100]  # First 100 lines
        logger.info("First 100 lines of page content:")
        for i, line in enumerate(lines, 1):
            if line.strip():
                logger.info(f"  {i}: {line[:100]}")
        
        # Check meta tags for data
        logger.info("\n=== Checking meta tags ===")
        metas = driver.find_elements(By.TAG_NAME, "meta")
        for meta in metas[:10]:
            name = meta.get_attribute("name") or meta.get_attribute("property")
            content = meta.get_attribute("content")
            if name and content:
                logger.info(f"  {name}: {content[:100]}")
        
        # Try to find where item names are mentioned
        logger.info("\n=== Searching for item names ===")
        common_items = ["trinity", "divine", "manamune", "serylda", "black cleaver", "kaenic", "maw"]
        for item in common_items:
            try:
                elements = driver.find_elements(By.XPATH, f"//*[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{item}')]")
                if elements:
                    logger.info(f"✓ Found '{item}' in {len(elements)} elements")
                    parent = elements[0].find_element(By.XPATH, "ancestor::div[contains(@class, 'row') or contains(@class, 'item') or contains(@class, 'build')]")
                    logger.info(f"  Parent element: {parent.get_attribute('class')}")
            except:
                pass
        
        logger.info("\nInspection complete!")
        return driver.page_source
        
    except Exception as e:
        logger.error(f"Error inspecting page: {e}", exc_info=True)
        return None
    finally:
        if driver:
            driver.quit()

if __name__ == "__main__":
    inspect_ugg_page("aatrox", "jungle")
