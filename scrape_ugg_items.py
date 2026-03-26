"""
Robust u.gg scraper that extracts builds by finding item names in rendered content.
Uses Selenium to load and wait for React to fully render, then extracts visible text.
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

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# All League of Legends items (common ones at least)
COMMON_ITEMS = {
    'trinity': 'Trinity Force',
    'divine': 'Divine Sunderer',
    'manamune': 'Manamune',
    'muramana': 'Muramana',
    'draktharr': "Draktharr's Shadow",
    'youmuus': "Youmuu's Ghostblade",
    'serylda': "Serylda's Grudge",
    'cleaver': 'Black Cleaver',
    'kaenic': 'Kaenic Rookern',
    'wit': "Wit's End",
    'maw': "Maw of Malmortius",
    'timat': 'Tiamat',
    'titanic': 'Titanic Hydra',
    'ravenous': 'Ravenous Hydra',
    'hollow': 'Hollow Radiance',
    'adaptive': 'Adaptive Helm',
    'banshee': 'Banshee\'s Veil',
    'mantle': 'Force of Nature',
    'gargoyle': "Gargoyle's Stoneplate",
    'thornmail': 'Thornmail',
    'abyssal': 'Abyssal Mask',
    'warmog': "Warmog's Armor",
    'liandry': "Liandry's Torment",
    'rylai': "Rylai's Crystal Scepter",
    'demonic': 'Demonic Embrace',
    'morello': "Morellonomicon",
    'cosmic': 'Cosmic Drive',
    'horizon': 'Horizon Focus',
    'zhonyas': "Zhonyas Hourglass",
}

# LoL item IDs mapped to names (simplified)
ITEM_MAP = {name: name for name in COMMON_ITEMS.values()}

def scrape_champion_builds(champion="aatrox", role="jungle"):
    """
    Scrape builds for a champion from u.gg using Selenium + item name matching.
    """
    driver = None
    try:
        logger.info(f"[{champion}/{role}] Starting scrape...")
        
        # Setup Chrome
        options = Options()
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--no-sandbox')
        options.headless = True  # Run in headless mode
        
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        
        # Load the page
        url = f"https://www.u.gg/lol/champions/{champion}/build"
        logger.info(f"[{champion}/{role}] Loading {url}...")
        driver.get(url)
        
        # Wait for JavaScript to render - give React time to load
        logger.info(f"[{champion}/{role}] Waiting for React to render...")
        wait = WebDriverWait(driver, 15)
        
        # Wait for at least some main content to appear
        try:
            # Try to wait for any element that indicates content is loaded
            wait.until(lambda d: len(d.find_elements(By.TAG_NAME, "button")) > 10)
            time.sleep(2)  # Additional wait after first content appears
        except:
            logger.warning(f"[{champion}/{role}] Timeout waiting for content, continuing anyway...")
            time.sleep(5)
        
        # Get full page HTML
        html_content = driver.page_source
        text_content = driver.find_element(By.TAG_NAME, "body").text
        
        logger.info(f"[{champion}/{role}] Got {len(html_content)} bytes of HTML, {len(text_content)} bytes of text")
        
        # Save for inspection
        with open(f'ugg_{champion}_{role}_full.html', 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        # Try to extract builds by looking for patterns
        builds = extract_builds_from_text(text_content, champion)
        
        logger.info(f"[{champion}/{role}] Result: {len(builds)} builds extracted")
        return builds
        
    except Exception as e:
        logger.error(f"[{champion}/{role}] Error: {e}")
        return []
    finally:
        if driver:
            driver.quit()

def extract_builds_from_text(text, champion):
    """
    Extract item builds from the human-readable page text.
    Looks for common item names in sequence.
    """
    builds = []
    lines = text.split('\n')
    
    logger.info(f"Scanning {len(lines)} lines of text for item patterns...")
    
    # Look for lines that might contain builds (consecutive item names)
    current_build = []
    for line in lines:
        line_lower = line.lower().strip()
        
        # Check if this line contains any known item
        for item_key, item_name in COMMON_ITEMS.items():
            if item_key in line_lower:
                # Found an item!
                logger.debug(f"  Found item: {item_name} ({item_key})")
                current_build.append(item_key)
                break
        
        # If we have accumulated items and hit a non-item line, it might be a build separator
        if current_build and ('build' in line_lower or 'win' in line_lower or '% wr' in line_lower):
            if len(current_build) >= 4:  # Only count if we have at least 4 items
                builds.append(current_build)
                logger.info(f"  ✓ Extracted build: {current_build}")
            current_build = []
    
    # Check if any partial build exists at end
    if current_build and len(current_build) >= 4:
        builds.append(current_build)
        logger.info(f"  ✓ Extracted final build: {current_build}")
    
    return builds

def scrape_all_champions():
    """Scrape all 5 target champions."""
    champions = ['aatrox', 'briar', 'elise', 'khazix', 'leesin']
    all_builds = {}
    
    for champ in champions:
        builds = scrape_champion_builds(champ, 'jungle')
        all_builds[f"{champ}/jungle"] = builds
    
    # Save results
    with open('extracted_builds.json', 'w') as f:
        json.dump(all_builds, f, indent=2)
    
    logger.info(f"\nSaved {len(all_builds)} champion builds to extracted_builds.json")
    return all_builds

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("U.GG Builds Scraper - Item Name Extraction Method")
    logger.info("=" * 60)
    
    result = scrape_all_champions()
    
    logger.info("\n=== RESULTS ===")
    for champ, builds in result.items():
        logger.info(f"{champ}: {len(builds)} builds")
        for i, build in enumerate(builds, 1):
            logger.info(f"  Build {i}: {build}")
