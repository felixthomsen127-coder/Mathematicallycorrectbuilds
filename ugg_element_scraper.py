"""
U.GG Champion Build Scraper - Element-based extraction version
Uses Selenium to find and extract item data via DOM elements
"""

import logging
from typing import List, Optional
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class UGGElementScraper:
    """Extract item builds from u.gg using element-based parsing"""
    
    BASE_URL = "https://u.gg/lol/champions"
    
    @staticmethod
    def scrape_champion_builds(champion: str, role: str = 'jungle') -> Optional[List[List[str]]]:
        """
        Scrape builds for a champion by finding and clicking on build items
        """
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        
        try:
            champion_slug = champion.replace(' ', '-').lower()
            url = f"{UGGScraper.BASE_URL}/{champion_slug}/build?role={role}"
            logger.info(f"Loading {url}...")
            driver.get(url)
            
            # Wait for page to load
            wait = WebDriverWait(driver, 15)
            
            # Try multiple possible selectors for builds/items
            selectors = [
                "[class*='Build'][class*='Item']",
                "[class*='build']",
                "[data-testid*='build']",
                "div[role='button'][class*='item']",
                "[class*='BuildItem']",
            ]
            
            builds = []
            
            for selector in selectors:
                try:
                    logger.info(f"  Trying selector: {selector}")
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    logger.info(f"  Found {len(elements)} elements")
                    
                    if len(elements) > 0:
                        # Extract text content
                        for elem in elements[:24]:  # Top 6 builds * 4 items
                            try:
                                text = elem.text.strip()
                                if text and len(text) < 50:  # Item names are usually short
                                    logger.debug(f"    - {text}")
                                    builds.append(text)
                            except:
                                pass
                        
                        if builds:
                            break
                except Exception as e:
                    logger.debug(f"  Selector failed: {e}")
                    continue
            
            if builds:
                # Group into build sequences (typically 4-6 items per build)
                build_sequences = []
                items_per_build = 4
                
                for i in range(0, len(builds),items_per_build):
                    sequence = builds[i:i+items_per_build]
                    if len(sequence) >= 3:  # At least 3 items
                        build_sequences.append(sequence)
                
                if build_sequences:
                    logger.info(f"✓ Extracted {len(build_sequences)} builds for {champion}/{role}")
                    return build_sequences
            
            logger.warning(f"✗ No builds found for {champion}/{role}")
            return None
            
        except Exception as e:
            logger.error(f"Scraping failed: {e}")
            return None
        finally:
            driver.quit()


def test():
    """Test the scraper"""
    logger.info("Testing U.GG element scraper...")
    result = UGGElementScraper.scrape_champion_builds('briar', 'jungle')
    if result:
        logger.info(f"Success: {result}")
    else:
        logger.warning("No data returned")


if __name__ == "__main__":
    test()
