"""
U.GG Champion Build Scraper
Attempts to extract item builds for League of Legends champions from u.gg
Uses Selenium if available for JavaScript rendering, falls back to requests
"""

import requests
import json
import re
import logging
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
import time
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class UGGScraper:
    """Scrapes champion build data from u.gg"""
    
    BASE_URL = "https://u.gg/lol/champions"
    REQUEST_TIMEOUT = 10
    
    # Headers to mimic legitimate browser requests
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'max-age=0',
    }
    
    CHAMPIONS_TO_SCRAPE = [
        'aatrox',
        'briar',
        'elise',
        'khazix',
        'Lee Sin',
    ]
    
    ROLE = 'jungle'
    
    @staticmethod
    def try_selenium_scrape(champion: str, role: str) -> Optional[List[List[str]]]:
        """
        Try using Selenium to scrape u.gg (requires Chrome and chromedriver)
        More reliable for JavaScript-heavy pages
        Uses webdriver-manager to automatically download chromedriver
        """
        try:
            from selenium import webdriver
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager
            
            logger.info(f"  Attempting Selenium scrape for {champion}/{role}...")
            
            # Set up Chrome options
            options = Options()
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--start-maximized")
            options.add_argument("user-agent=" + UGGScraper.HEADERS['User-Agent'])
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            
            # Try to create driver with automatic chromedriver management
            try:
                service = Service(ChromeDriverManager().install())
                driver = webdriver.Chrome(service=service, options=options)
            except Exception as e:
                logger.debug(f"  Selenium driver init failed: {e}")
                return None
            
            try:
                # Navigate to champion page
                url = f"{UGGScraper.BASE_URL}/{champion.replace(' ', '-').lower()}/build?role={role}"
                logger.debug(f"  Loading URL: {url}")
                driver.get(url)
                
                # Wait for page to fully load (u.gg is JavaScript-heavy)
                wait = WebDriverWait(driver, 10)
                
                # Try to wait for content to render
                try:
                    # Look for common League of Legends item names that would indicate loaded content
                    wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "[class*='build']")))
                    time.sleep(2)  # Extra wait for data to fully render
                except:
                    logger.debug("  Timeout waiting for build data")
                
                # Get page source and extract JSON data
                page_source = driver.page_source
                
                # Try to extract JSON from page source
                # u.gg embeds data in window.__INITIAL_STATE__ or similar
                json_patterns = [
                    r'window\.__DATA__\s*=\s*({.*?"builds".*?});',
                    r'<script[^>]*>.*?({.*?"items".*?})',
                    r'"builds"\s*:\s*(\[.*?\])',
                ]
                
                for pattern in json_patterns:
                    match = re.search(pattern, page_source, re.DOTALL)
                    if match:
                        try:
                            data = json.loads(match.group(1))
                            if data:
                                logger.info(f"  ✓ Extracted data via Selenium for {champion}/{role}")
                                # Try to parse builds from extracted data
                                return UGGScraper._parse_builds_from_data(data, champion)
                        except json.JSONDecodeError:
                            continue
                
                logger.debug(f"  No JSON data found in page source for {champion}/{role}")
                return None
                
            finally:
                driver.quit()
                
        except ImportError:
            logger.debug("  Selenium not installed, skipping Selenium scraper")
            return None
        except Exception as e:
            logger.debug(f"  Selenium scrape failed: {e}")
            return None
    
    @staticmethod
    def _parse_builds_from_data(data: Any, champion: str) -> Optional[List[List[str]]]:
        """
        Parse build data from extracted JSON
        """
        try:
            # Try different data structure formats
            if isinstance(data, dict):
                if 'builds' in data:
                    builds = data['builds']
                elif 'items' in data:
                    builds = data['items']
                else:
                    return None
            elif isinstance(data, list):
                builds = data
            else:
                return None
            
            if not isinstance(builds, list):
                return None
            
            # Extract item names from builds
            item_builds = []
            for build in builds[:6]:  # Top 6 builds
                items = []
                if isinstance(build, dict):
                    if 'items' in build and isinstance(build['items'], list):
                        items = build['items']
                    elif 'itemIds' in build and isinstance(build['itemIds'], list):
                        items = build['itemIds']
                elif isinstance(build, list):
                    items = build
                
                if items:
                    item_builds.append(items)
            
            if item_builds:
                logger.debug(f"    Parsed {len(item_builds)} builds from data")
                return item_builds
            
        except Exception as e:
            logger.debug(f"  Data parsing failed: {e}")
        
        return None
    
    @staticmethod
    def try_api_scrape(champion: str, role: str) -> Optional[List[List[str]]]:
        """
        Try to find and use the u.gg API endpoints
        u.gg uses internal APIs - we'll try to discover them
        """
        try:
            # Try using the u.gg API endpoint pattern
            # Format: /api/v2/champion/[champion]/stats
            
            champion_normalized = champion.replace(' ', '-').lower()
            
            # Try multiple API endpoint patterns
            api_patterns = [
                f"https://acs.leagueoflegends.com/v1/perks",  # Rune data
                f"https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/championdata/{champion_normalized}.json",
                f"https://ddragon.leagueoflegends.com/cdn/latest/data/en_US/champion/{champion.capitalize()}.json",
            ]
            
            for api_url in api_patterns:
                try:
                    logger.debug(f"  Trying API: {api_url}")
                    response = requests.get(api_url, headers=UGGScraper.HEADERS, timeout=5)
                    if response.status_code == 200:
                        data = response.json()
                        if data:
                            logger.info(f"  ✓ Fetched data from API for {champion}/{role}")
                            return None  # API data format doesn't directly give us builds
                except:
                    continue
            
            return None
            
        except Exception as e:
            logger.debug(f"  API scrape failed: {e}")
            return None
    
    @staticmethod
    def try_direct_build_scrape(champion: str, role: str) -> Optional[List[List[str]]]:
        """
        Try direct HTTP request to u.gg champion page and extract builds
        Uses regex parsing and DOM-like querying on HTML
        """
        try:
            champion_slug = champion.replace(' ', '-').lower()
            url = f"https://u.gg/lol/champions/{champion_slug}/build?role={role}"
            
            logger.debug(f"  Attempting direct scrape: {url}")
            
            response = requests.get(
                url,
                headers=UGGScraper.HEADERS,
                timeout=UGGScraper.REQUEST_TIMEOUT,
                allow_redirects=True
            )
            
            if response.status_code != 200:
                logger.debug(f"    HTTP {response.status_code}")
                return None
            
            html = response.text
            
            # Try to extract JSON data embedded in page
            # u.gg typically embeds data in <script> tags as JSON
            json_pattern = r'<script[^>]*>.*?"builds":\s*(\[.*?\])'
            json_match = re.search(json_pattern, html, re.DOTALL)
            
            if json_match:
                try:
                    builds_json = json_match.group(1)
                    # Clean up JSON
                    builds_data = json.loads(builds_json)
                    
                    if builds_data and isinstance(builds_data, list):
                        logger.info(f"  ✓ Extracted {len(builds_data)} builds for {champion}/{role}")
                        
                        # Parse builds into item sequences
                        item_builds = []
                        for build in builds_data[:6]:  # Top 6 builds
                            if isinstance(build, dict) and 'items' in build:
                                items = build['items']
                                if items:
                                    item_builds.append(items)
                        
                        if item_builds:
                            return item_builds
                except json.JSONDecodeError:
                    pass
            
            # Alternative: look for item data in different format
            # u.gg may store it in data attributes or different script tags
            item_pattern = r'(?:data-item=|itemId["\']?\s*:\s*)["\']?(\d+)["\']?'
            item_matches = re.findall(item_pattern, html)
            
            if item_matches:
                logger.info(f"  ✓ Found item data in HTML for {champion}/{role}")
                logger.debug(f"    Found {len(item_matches)} items: {item_matches[:10]}")
                # Return what we found (item IDs, will need to be converted to names)
                return None  # Not yet matched to item names
            
            return None
            
        except Exception as e:
            logger.debug(f"  Direct scrape failed: {e}")
            return None
    
    @staticmethod
    def scrape_champion(champion: str, role: str = 'jungle') -> Optional[List[List[str]]]:
        """
        Attempt to scrape champion build data with multiple fallback methods
        Returns list of item build sequences, or None if all methods fail
        """
        logger.info(f"Scraping {champion}/{role} from u.gg...")
        
        # Method 1: Try Selenium (most reliable for JS-heavy sites)
        builds = UGGScraper.try_selenium_scrape(champion, role)
        if builds:
            return builds
        
        # Method 2: Try direct HTTP + HTML parsing
        builds = UGGScraper.try_direct_build_scrape(champion, role)
        if builds:
            return builds
        
        # Method 3: Try API endpoints
        builds = UGGScraper.try_api_scrape(champion, role)
        if builds:
            return builds
        
        logger.warning(f"  ✗ All scrape methods failed for {champion}/{role}")
        return None
    
    @staticmethod
    def scrape_all_champions(champions: Optional[List[str]] = None, role: str = 'jungle') -> Dict[str, Optional[List[List[str]]]]:
        """
        Scrape builds for multiple champions
        Returns dict mapping champion/role to builds
        """
        if champions is None:
            champions = UGGScraper.CHAMPIONS_TO_SCRAPE
        
        results = {}
        
        for champion in champions:
            builds = UGGScraper.scrape_champion(champion, role)
            key = f"{champion.lower()}/{role}"
            results[key] = builds
            
            # Be polite to the server
            time.sleep(2)
        
        return results


def test_ugg_scraper():
    """Test the scraper with a single champion"""
    logger.info("Testing U.GG scraper...")
    
    result = UGGScraper.scrape_champion('briar', 'jungle')
    
    if result:
        logger.info(f"✓ Successfully scraped builds: {result}")
    else:
        logger.warning("✗ Scraper returned no data")
    
    return result


if __name__ == "__main__":
    test_ugg_scraper()
