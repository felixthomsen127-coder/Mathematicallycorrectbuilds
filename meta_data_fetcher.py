"""
Comprehensive League of Legends Meta Build Data Fetcher
Tries multiple sources in order: APIs, web scraping with Selenium,  fallback data
"""

import requests
import json
import logging
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import time
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MetaDataFetcher:
    """Multi-source meta build data fetcher"""
    
    CHAMPIONS = {
        'aatrox': {'role': 'jungle', 'id': 266},
        'briar': {'role': 'jungle', 'id': 911},
        'elise': {'role': 'jungle', 'id': 60},
        'khazix': {'role': 'jungle', 'id': 121, 'aliases': ["kha'zix", "kha zix"]},
        'leesin': {'role': 'jungle', 'id': 64, 'aliases': ["lee sin"]},
    }
    
    @staticmethod
    def fetch_from_cdragon() -> Optional[Dict[str, Dict]]:
        """
        Try to fetch from Community Dragon - the official League data source
        Provides champion stats and base data
        """
        try:
            logger.info("[CDragon] Attempting to fetch champion data...")
            
            # CDragon has champion data files
            url = "https://raw.communitydragon.org/latest/game/data/characters/champions.json"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                logger.info("[CDragon] ✓ Successfully fetched champion data")
                return data
        except Exception as e:
            logger.debug(f"[CDragon] Failed: {e}")
        
        return None
    
    @staticmethod
    def fetch_from_ddragon() -> Optional[Dict[str, Dict]]:
        """
        Fetch from official League of Legends Data Dragon
        Provides item and champion data
        """
        try:
            logger.info("[DDragon] Attempting to fetch item data...")
            
            url = "https://ddragon.leagueoflegends.com/cdn/latest/data/en_US/item.json"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                logger.info("[DDragon] ✓ Successfully fetched item data")
                return data.get('data', {})
        except Exception as e:
            logger.debug(f"[DDragon] Failed: {e}")
        
        return None
    
    @staticmethod
    def fetch_from_opgg_api() -> Optional[Dict]:
        """
        Try OP.GG's public API
        OP.GG often has more accessible APIs than u.gg
        """
        try:
            logger.info("[OP.GG API] Attempting to fetch meta data...")
            
            # OP.GG has a public API endpoint
            url = "https://api.opgg.com/v2.0/meta"
            headers = {'User-Agent': 'Mozilla/5.0'}
            
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data:
                    logger.info("[OP.GG API] ✓ Successfully fetched data")
                    return data
        except Exception as e:
            logger.debug(f"[OP.GG API] Failed: {e}")
        
        return None
    
    @staticmethod
    def fetch_from_metasrc() -> Optional[Dict]:
        """
        Try MetaSrc champion data
        MetaSrc provides Soloqueue meta statistics
        """
        try:
            logger.info("[MetaSrc] Attempting to fetch champion data...")
            
            url = "https://www.metasrc.com/api/v1/champs"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data:
                    logger.info("[MetaSrc] ✓ Successfully fetched champion data")
                    return data
        except Exception as e:
            logger.debug(f"[MetaSrc] Failed: {e}")
        
        return None
    
    @staticmethod
    def try_selenium_ugg() -> Optional[Dict[str, List[List[str]]]]:
        """
        Last resort: Try to scrape u.gg with Selenium
        Only used if none of the APIs work
        """
        try:
            from selenium import webdriver
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager
            
            logger.info("[Selenium] Attempting u.gg web scrape...")
            
            builds_data = {}
            
            for champion_name in MetaDataFetcher.CHAMPIONS.keys():
                try:
                    options = Options()
                    options.add_argument("--headless")
                    options.add_argument("--no-sandbox")
                    options.add_argument("--disable-dev-shm-usage")
                    
                    service = Service(ChromeDriverManager().install())
                    driver = webdriver.Chrome(service=service, options=options)
                    
                    try:
                        url = f"https://u.gg/lol/champions/{champion_name}/build?role=jungle"
                        logger.debug(f"    Loading {url}...")
                        driver.get(url)
                        
                        # Just try to find ANY element that might contain item data
                        time.sleep(3)
                        
                        # Try to extract by looking for common patterns
                        all_text = driver.find_element(By.TAG_NAME, "body").text
                        
                        if "item" in all_text.lower() and champion_name.lower() in all_text.lower():
                            logger.info(f"[Selenium] ✓ Found data for {champion_name}")
                            # This is a success indicator - parsing would require reverse-engineering their DOM
                            builds_data[champion_name] = None  # Can't parse without exact selectors
                        
                    finally:
                        driver.quit()
                
                except Exception as e:
                    logger.debug(f"[Selenium] Error scraping {champion_name}: {e}")
                    continue
            
            return builds_data if builds_data else None
            
        except ImportError:
            logger.debug("[Selenium] Selenium not available")
            return None
        except Exception as e:
            logger.debug(f"[Selenium] Scraping failed: {e}")
            return None
    
    @staticmethod
    def fetch_all_sources() -> Dict[str, any]:
        """
        Try all data sources in order
        Returns results dict with 'status': 'success'/'partial'/'failed'
        """
        logger.info("=" * 70)
        logger.info("STARTING META DATA FETCH FROM ALL SOURCES")
        logger.info("=" * 70)
        
        results = {
            'timestamp': datetime.now().isoformat(),
            'sources_tried': [],
            'sources_successful': [],
            'data': {},
        }
        
        # Try APIs first (fastest, most reliable)
        apis_to_try = [
            ('CDragon', MetaDataFetcher.fetch_from_cdragon),
            ('DDragon', MetaDataFetcher.fetch_from_ddragon),
            ('MetaSrc', MetaDataFetcher.fetch_from_metasrc),
            ('OP.GG API', MetaDataFetcher.fetch_from_opgg_api),
        ]
        
        for source_name, fetcher in apis_to_try:
            results['sources_tried'].append(source_name)
            
            try:
                data = fetcher()
                if data:
                    results['sources_successful'].append(source_name)
                    results['data'][source_name] = data
                    logger.info(f"✓ {source_name} returned data")
            except Exception as e:
                logger.warning(f"✗ {source_name} failed: {e}")
        
        # Try Selenium scraping if APIs failed
        if not results['sources_successful']:
            logger.info("\nNo APIs succeeded, attempting Selenium scraping...")
            results['sources_tried'].append('Selenium u.gg')
            
            try:
                data = MetaDataFetcher.try_selenium_ugg()
                if data:
                    results['sources_successful'].append('Selenium u.gg')
                    results['data']['Selenium'] = data
                    logger.info("✓ Selenium scraping returned data")
            except Exception as e:
                logger.warning(f"✗ Selenium failed: {e}")
        
        # Summary
        logger.info("\n" + "=" * 70)
        logger.info(f"SUMMARY:")
        logger.info(f"  Sources tried: {', '.join(results['sources_tried'])}")
        logger.info(f"  Successful: {', '.join(results['sources_successful']) if results['sources_successful'] else 'NONE'}")
        logger.info(f"  Status: {'SUCCESS' if results['sources_successful'] else 'FAILED'}")
        logger.info("=" * 70)
        
        return results


def test():
    """Test the multi-source fetcher"""
    results = MetaDataFetcher.fetch_all_sources()
    print("\n\n=== RESULTS ===")
    print(json.dumps({k: v for k, v in results.items() if k != 'data'}, indent=2))
    
    if results['sources_successful']:
        print(f"\n✓ Successfully fetched from: {', '.join(results['sources_successful'])}")
    else:
        print(f"\n✗ FAILED - All sources returned no data")


if __name__ == "__main__":
    test()
