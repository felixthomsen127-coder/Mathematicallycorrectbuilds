"""
Automated meta build fetcher - scrapes U.GG to update meta_builds.json
"""

import requests
import json
import re
from typing import Dict, List, Any
import time
from pathlib import Path

class MetaBuildAutoUpdater:
    """Fetches live meta builds from U.GG and updates meta_builds.json"""
    
    BASE_URL = "https://u.gg/lol/tier-lists"
    FIXTURES_PATH = Path(__file__).parent / "fixtures" / "meta_builds.json"
    
    # Champion name mappings (U.GG -> standardized names)
    CHAMPION_ROLE_PAIRS = [
        ("aatrox", "jungle"),
        ("briar", "jungle"),
        ("elise", "jungle"),
        ("khazix", "jungle"),
        ("leesin", "jungle"),
    ]
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def fetch_champion_builds(self, champion: str, role: str) -> List[List[str]]:
        """Fetch builds for a champion/role from U.GG"""
        url = f"{self.BASE_URL}/{role}"
        
        try:
            print(f"Fetching {champion}/{role} from U.GG...")
            r = self.session.get(url, timeout=10)
            
            # Extract JSON data from Next.js __NEXT_DATA__ 
            match = re.search(r'__NEXT_DATA__.*?"props":\s*(\{.*?\})\s*</script>', 
                            r.text, re.DOTALL | re.IGNORECASE)
            
            if not match:
                print(f"  ✗ Could not extract data from page")
                return []
            
            # Look for the champion's build data in the page
            # U.GG typically shows top item builds for each champion
            champion_pattern = champion.lower().replace("'", "").replace(" ", "")
            
            # Find all item sequences for this champion
            # Pattern: look for champion name followed by item names
            builds = []
            
            # Try to extract from the rendered HTML
            # U.GG displays items in this format: <img alt="Item Name" ...>
            item_pattern = r'alt="([^"]*(?:Trinity|Eclipse|sundered|cleaver|hydra|manamune|collector|serylda|kaenic|spirit|zhonya|void|liandry|rylai)[^"]*)(\s*[Ss]tarpack)?"'
            
            matches = re.findall(item_pattern, r.text, re.IGNORECASE)
            if matches:
                print(f"  ✓ Found {len(matches)} item references")
            
            # For now, return empty - full extraction is complex
            return []
            
        except Exception as e:
            print(f"  ✗ Error: {e}")
            return []
    
    def load_current_meta(self) -> Dict[str, Any]:
        """Load current meta_builds.json"""
        try:
            with open(self.FIXTURES_PATH, 'r') as f:
                return json.load(f)
        except:
            return {
                "version": "16.6",
                "last_updated": "2026-03-26",
                "builds": {},
                "_comment": "Automatically updated meta builds"
            }
    
    def save_meta(self, data: Dict[str, Any]):
        """Save updated meta_builds.json"""
        with open(self.FIXTURES_PATH, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"✓ Saved updated meta_builds.json")
    
    def update_all(self):
        """Fetch and update all champion builds"""
        print("Starting automated meta build update...\n")
        
        meta = self.load_current_meta()
        updated_count = 0
        
        for champion, role in self.CHAMPION_ROLE_PAIRS:
            builds = self.fetch_champion_builds(champion, role)
            if builds:
                key = f"{champion}/{role}"
                meta["builds"][key] = {
                    "items": builds,
                    "source_note": "Auto-fetched from U.GG",
                }
                updated_count += 1
        
        print(f"\nUpdated {updated_count}/{len(self.CHAMPION_ROLE_PAIRS)} champions")
        
        if updated_count > 0:
            self.save_meta(meta)


if __name__ == "__main__":
    updater = MetaBuildAutoUpdater()
    updater.update_all()
