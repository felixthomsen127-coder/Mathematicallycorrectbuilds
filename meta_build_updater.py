"""
Automated meta build updater - fetches from multiple sources
Runs on startup to keep meta_builds.json up to date
"""

import requests
import json
import re
import os
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
import logging
from datetime import datetime, timedelta
import time
from threading import Lock

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

_UPDATER_REPORT_LOCK = Lock()
_LAST_UPDATER_REPORT: Dict[str, Any] = {
    "status": "never_run",
    "started_at": "",
    "finished_at": "",
    "duration_seconds": 0.0,
    "updated": 0,
    "failed": 0,
    "total_targets": 0,
    "failed_targets": [],
    "rules": {
        "allow": [],
        "deny": [],
    },
    "filter_stats": {
        "builds_pruned": 0,
        "items_removed": 0,
    },
    "save_ok": False,
    "error": "",
}


def _set_last_meta_build_update_report(report: Dict[str, Any]) -> None:
    with _UPDATER_REPORT_LOCK:
        _LAST_UPDATER_REPORT.clear()
        _LAST_UPDATER_REPORT.update(dict(report))


def get_last_meta_build_update_report() -> Dict[str, Any]:
    with _UPDATER_REPORT_LOCK:
        return dict(_LAST_UPDATER_REPORT)

class MetaBuildUpdater:
    """Automatically fetches and updates meta builds from available sources"""
    
    FIXTURES_PATH = Path(__file__).parent / "fixtures" / "meta_builds.json"
    REQUEST_TIMEOUT = 5  # seconds
    UGG_TIMEOUT = 15  # seconds
    USER_AGENT = "Mozilla/5.0"
    
    DEFAULT_TARGETS: Dict[str, List[str]] = {
        'aatrox': ['jungle', 'top'],
        'briar': ['jungle'],
        'elise': ['jungle'],
        'khazix': ['jungle'],
        'leesin': ['jungle'],
        'viego': ['jungle'],
        'nidalee': ['jungle'],
        'masteryi': ['jungle'],
        'diana': ['jungle'],
        'volibear': ['jungle'],
    }

    @staticmethod
    def get_targets() -> List[Tuple[str, str]]:
        """Return champion/role targets, with optional env var override.

        Override format:
            META_BUILD_TARGETS="aatrox:jungle,aatrox:top,briar:jungle"
        """
        raw = os.environ.get('META_BUILD_TARGETS', '').strip()
        if raw:
            parsed: List[Tuple[str, str]] = []
            for chunk in raw.split(','):
                part = chunk.strip()
                if not part or ':' not in part:
                    continue
                champion, role = [x.strip().lower() for x in part.split(':', 1)]
                if champion and role:
                    parsed.append((champion, role))
            if parsed:
                return parsed

        targets: List[Tuple[str, str]] = []
        for champion, roles in MetaBuildUpdater.DEFAULT_TARGETS.items():
            for role in roles:
                targets.append((champion, role))
        return targets

    @staticmethod
    def _extract_balanced_json_object(text: str, marker: str) -> Optional[Dict[str, Any]]:
        marker_idx = text.find(marker)
        if marker_idx < 0:
            return None
        brace_start = text.find("{", marker_idx)
        if brace_start < 0:
            return None

        depth = 0
        in_string = False
        escape = False
        quote = ""
        chars: List[str] = []

        for ch in text[brace_start:]:
            chars.append(ch)
            if in_string:
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == quote:
                    in_string = False
                continue

            if ch in ('\"', "'"):
                in_string = True
                quote = ch
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads("".join(chars))
                    except Exception:
                        return None
        return None

    @staticmethod
    def _extract_ugg_variant_name(payload_key: str) -> str:
        label = payload_key.split("::", 1)[0]
        if "-overview" in payload_key:
            match = re.search(r"/([a-z_]+)-overview/", payload_key)
            if match:
                return match.group(1).replace("_", "-")
        if label.endswith("_recommended") or "/overview/" in payload_key:
            return "recommended"
        return label.rsplit("_", 1)[-1].replace("_", "-")

    @staticmethod
    def _pick_unique_option(
        option_rows: Any,
        used_ids: List[int],
    ) -> Optional[int]:
        if not isinstance(option_rows, list):
            return None

        ranked = [row for row in option_rows if isinstance(row, dict) and isinstance(row.get('id'), int)]
        ranked.sort(
            key=lambda row: (
                float(row.get('matches') or 0),
                float(row.get('wins') or 0),
                float(row.get('win_rate') or 0),
            ),
            reverse=True,
        )
        for row in ranked:
            item_id = row['id']
            if item_id not in used_ids:
                return item_id
        return None

    @staticmethod
    def _resolve_item_names(item_ids: List[int], item_id_to_name: Dict[str, str]) -> List[str]:
        names: List[str] = []
        seen: set = set()
        for item_id in item_ids:
            name = item_id_to_name.get(str(item_id))
            if name and name not in seen:
                names.append(name)
                seen.add(name)
        return names

    @staticmethod
    def _normalize_item_name(name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(name or "").lower())

    @staticmethod
    def _parse_csv_env_set(env_key: str) -> set:
        raw = os.environ.get(env_key, "")
        out: set = set()
        if not raw:
            return out
        for token in raw.split(","):
            norm = MetaBuildUpdater._normalize_item_name(token)
            if norm:
                out.add(norm)
        return out

    @staticmethod
    def get_item_name_rules() -> Tuple[set, set]:
        """Read optional allow/deny name rules from environment variables.

        - META_BUILD_ITEM_ALLOWLIST: comma-separated item names.
        - META_BUILD_ITEM_DENYLIST: comma-separated item names.
        """
        allow = MetaBuildUpdater._parse_csv_env_set("META_BUILD_ITEM_ALLOWLIST")
        deny = MetaBuildUpdater._parse_csv_env_set("META_BUILD_ITEM_DENYLIST")
        return allow, deny

    @staticmethod
    def apply_item_name_rules_to_builds(
        builds: List[List[str]],
        allow: set,
        deny: set,
    ) -> Tuple[List[List[str]], int, int]:
        """Apply allow/deny rules and return filtered builds + stats.

        Returns:
            filtered_builds, builds_pruned, items_removed
        """
        filtered: List[List[str]] = []
        builds_pruned = 0
        items_removed = 0

        for build in builds or []:
            kept: List[str] = []
            for item_name in build:
                norm = MetaBuildUpdater._normalize_item_name(item_name)
                if not norm:
                    items_removed += 1
                    continue
                if allow and norm not in allow:
                    items_removed += 1
                    continue
                if deny and norm in deny:
                    items_removed += 1
                    continue
                kept.append(item_name)

            if len(kept) >= 4:
                filtered.append(kept[:6])
            else:
                builds_pruned += 1

        return filtered, builds_pruned, items_removed

    @staticmethod
    def _is_sr_item(item_data: Dict[str, Any]) -> bool:
        """Return True when the item can be purchased on Summoner's Rift."""
        if not isinstance(item_data, dict):
            return False

        maps = item_data.get('maps')
        if isinstance(maps, dict):
            sr_flag = maps.get('11')
            if sr_flag not in (True, 'true', 'True', 1, '1'):
                return False

        gold = item_data.get('gold')
        if isinstance(gold, dict):
            total = gold.get('total')
            try:
                if int(total) <= 0:
                    return False
            except Exception:
                return False

        return True

    @staticmethod
    def _extract_ugg_builds_from_ssr(
        ssr_payload: Dict[str, Any],
        role: str,
        item_id_to_name: Dict[str, str],
        allowed_item_ids: set,
    ) -> List[List[str]]:
        role_key_suffix = f"_{role.lower()}"
        candidates: List[Tuple[int, int, str, List[str]]] = []

        for payload_key, payload_value in ssr_payload.items():
            if not isinstance(payload_key, str) or not isinstance(payload_value, dict):
                continue
            if "/overview/" not in payload_key and "-overview/" not in payload_key:
                continue

            data = payload_value.get('data')
            if not isinstance(data, dict):
                continue

            role_entry = None
            for entry_key, entry_value in data.items():
                if isinstance(entry_key, str) and entry_key.endswith(role_key_suffix) and isinstance(entry_value, dict):
                    role_entry = entry_value
                    break
            if not role_entry:
                continue

            core = role_entry.get('rec_core_items') or {}
            core_ids = core.get('ids') if isinstance(core, dict) else None
            if not isinstance(core_ids, list) or not core_ids:
                continue

            build_ids: List[int] = [int(item_id) for item_id in core_ids if isinstance(item_id, int)]
            build_ids = [item_id for item_id in build_ids if str(item_id) in allowed_item_ids]
            for option_key in ('item_options_1', 'item_options_2', 'item_options_3', 'item_options_4'):
                next_item = MetaBuildUpdater._pick_unique_option(role_entry.get(option_key), build_ids)
                if next_item is not None:
                    if str(next_item) not in allowed_item_ids:
                        continue
                    build_ids.append(next_item)
                if len(build_ids) >= 6:
                    break

            names = MetaBuildUpdater._resolve_item_names(build_ids[:6], item_id_to_name)
            if len(names) < 4:
                continue

            matches = int(role_entry.get('matches') or core.get('matches') or 0)
            priority = 1 if MetaBuildUpdater._extract_ugg_variant_name(payload_key) == 'recommended' else 0
            candidates.append((priority, matches, MetaBuildUpdater._extract_ugg_variant_name(payload_key), names))

        candidates.sort(key=lambda row: (row[0], row[1]), reverse=True)

        builds: List[List[str]] = []
        seen_keys: set = set()
        for _, _, _, names in candidates:
            dedupe_key = tuple(names)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            builds.append(names)
            if len(builds) >= 6:
                break
        return builds

    @staticmethod
    def try_ugg_ssr_builds(
        champion: str,
        role: str,
        item_id_to_name: Dict[str, str],
        allowed_item_ids: set,
    ) -> Optional[List[List[str]]]:
        """Fetch real build archetypes from U.GG's embedded SSR payload."""
        try:
            url = f"https://u.gg/lol/champions/{champion.lower()}/build/{role.lower()}"
            response = requests.get(
                url,
                timeout=MetaBuildUpdater.UGG_TIMEOUT,
                headers={"User-Agent": MetaBuildUpdater.USER_AGENT},
            )
            response.raise_for_status()
            ssr_payload = MetaBuildUpdater._extract_balanced_json_object(
                response.text,
                "window.__SSR_DATA__",
            )
            if not ssr_payload:
                logger.debug(f"  U.GG SSR payload missing for {champion}/{role}")
                return None

            builds = MetaBuildUpdater._extract_ugg_builds_from_ssr(
                ssr_payload,
                role,
                item_id_to_name,
                allowed_item_ids,
            )
            if builds:
                logger.info(f"✓ Fetched {champion}/{role} from U.GG SSR ({len(builds)} builds)")
                return builds
        except Exception as e:
            logger.debug(f"  U.GG SSR fetch failed for {champion}/{role}: {e}")
        return None
    
    @staticmethod
    def load_current() -> Dict[str, Any]:
        """Load existing meta_builds.json"""
        try:
            with open(MetaBuildUpdater.FIXTURES_PATH, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load existing meta_builds.json: {e}")
            return {
                "version": "16.6",
                "last_updated": datetime.now().isoformat(),
                "_comment": "Automatically synced meta builds from live U.GG SSR data.",
                "builds": {},
            }
    
    @staticmethod
    def save(data: Dict[str, Any]) -> bool:
        """Save updated meta_builds.json"""
        try:
            data['last_updated'] = datetime.now().isoformat()
            with open(MetaBuildUpdater.FIXTURES_PATH, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"✓ Saved meta_builds.json with {len(data.get('builds', {}))} champions")
            return True
        except Exception as e:
            logger.error(f"Failed to save meta_builds.json: {e}")
            return False
    
    @staticmethod
    def try_ddragon_api() -> Optional[Dict[str, Any]]:
        """Fetch from official League of Legends Data Dragon"""
        try:
            logger.debug("  Trying Dat Dragon API...")
            versions_response = requests.get(
                "https://ddragon.leagueoflegends.com/api/versions.json",
                timeout=MetaBuildUpdater.REQUEST_TIMEOUT,
                headers={"User-Agent": MetaBuildUpdater.USER_AGENT},
            )
            versions_response.raise_for_status()
            versions = versions_response.json()
            if not isinstance(versions, list) or not versions:
                return None

            version = str(versions[0])
            url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/item.json"
            response = requests.get(
                url,
                timeout=MetaBuildUpdater.REQUEST_TIMEOUT,
                headers={"User-Agent": MetaBuildUpdater.USER_AGENT},
            )
            response.raise_for_status()
            data = response.json().get('data', {})
            if data:
                logger.info(f"✓ Connected to Data Dragon API ({len(data)} items available)")
                return data
        except Exception as e:
            logger.debug(f"  Data Dragon API failed: {e}")
        return None
    
    @staticmethod
    def try_cdragon_api() -> Optional[Dict[str, Any]]:
        """Fetch from Community Dragon"""
        try:
            logger.debug("  Trying Community Dragon API...")
            url = "https://raw.communitydragon.org/latest/game/data/characters/champions.json"
            response = requests.get(url, timeout=MetaBuildUpdater.REQUEST_TIMEOUT)
            if response.status_code == 200:
                data = response.json()
                if data:
                    logger.info(f"✓ Connected to Community Dragon API")
                    return data
        except Exception as e:
            logger.debug(f"  Community Dragon API failed: {e}")
        return None
    
    @staticmethod
    def try_opgg_api() -> Optional[Dict[str, Any]]:
        """Fetch from OP.GG API"""
        try:
            logger.debug("  Trying OP.GG API...")
            url = "https://api.opgg.com/v2.0/meta"
            response = requests.get(url, timeout=MetaBuildUpdater.REQUEST_TIMEOUT)
            if response.status_code == 200:
                data = response.json()
                if data:
                    logger.info(f"✓ Connected to OP.GG API")
                    return data
        except Exception as e:
            logger.debug(f"  OP.GG API failed: {e}")
        return None
    
    @staticmethod
    def try_lol_stats_api(champion: str, role: str) -> Optional[List[List[str]]]:
        """Try alternative League stats APIs"""
        
        # Try lolstats.net
        try:
            url = f"https://api.lolstats.net/api/champion/{champion.lower()}/{role}"
            r = requests.get(url, timeout=MetaBuildUpdater.REQUEST_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and 'items' in data:
                    logger.info(f"✓ Fetched {champion}/{role} from lolstats.net")
                    return data.get('items', [])
        except Exception as e:
            pass
        
        return None
    



def update_meta_builds_periodically() -> Dict[str, Any]:
    """
    Main function to update meta builds
    Runs on app startup in a background thread
    ONLY fetches real data - reports failures instead of using estimates
    """
    started_at = datetime.now()
    started_ts = time.time()
    failed_targets: List[str] = []
    filter_builds_pruned = 0
    filter_items_removed = 0
    allow_rules, deny_rules = MetaBuildUpdater.get_item_name_rules()

    logger.info("=" * 60)
    logger.info("Starting meta builds auto-update...")
    logger.info("=" * 60)

    try:
        meta = MetaBuildUpdater.load_current()
        meta['_comment'] = 'Automatically synced meta builds from live U.GG SSR data.'
        updated = 0
        failed = 0

        # Try global APIs first to populate cache
        logger.info("\nChecking global API sources...")
        ddragon_data = MetaBuildUpdater.try_ddragon_api()
        cdragon_data = MetaBuildUpdater.try_cdragon_api()
        opgg_data = MetaBuildUpdater.try_opgg_api()
        item_id_to_name = {
            str(item_id): item_data.get('name', '').strip()
            for item_id, item_data in (ddragon_data or {}).items()
            if isinstance(item_data, dict) and item_data.get('name')
        }
        allowed_item_ids = {
            str(item_id)
            for item_id, item_data in (ddragon_data or {}).items()
            if MetaBuildUpdater._is_sr_item(item_data)
        }

        # Now try per-champion data
        logger.info("\nFetching per-champion data...")
        targets = MetaBuildUpdater.get_targets()
        logger.info(f"Configured targets: {len(targets)} champion/role pairs")

        for champion, role in targets:
            builds = None
            source = None

            logger.info(f"Attempting to fetch {champion}/{role}...")

            builds = MetaBuildUpdater.try_ugg_ssr_builds(champion, role, item_id_to_name, allowed_item_ids)
            if builds:
                source = "u.gg"
                builds, pruned, removed = MetaBuildUpdater.apply_item_name_rules_to_builds(
                    builds,
                    allow_rules,
                    deny_rules,
                )
                filter_builds_pruned += pruned
                filter_items_removed += removed
                if builds:
                    updated += 1
                    key = f"{champion}/{role}"
                    meta['builds'][key] = {
                        'items': builds,
                        'source_note': 'Auto-fetched (u.gg SSR)',
                    }
                    logger.info(f"  ✓ Set {len(builds)} builds for {champion}/{role}")
                    continue

            # Try lolstats.net first
            builds = MetaBuildUpdater.try_lol_stats_api(champion, role)
            if builds:
                source = "lolstats.net"
                logger.info(f"  ✓ Fetched from lolstats.net")
                builds, pruned, removed = MetaBuildUpdater.apply_item_name_rules_to_builds(
                    builds,
                    allow_rules,
                    deny_rules,
                )
                filter_builds_pruned += pruned
                filter_items_removed += removed

                # Update the meta builds with real data
                if builds:
                    updated += 1
                    key = f"{champion}/{role}"
                    meta['builds'][key] = {
                        'items': builds,
                        'source_note': f'Auto-fetched ({source})',
                    }
                    logger.info(f"  ✓ Set {len(builds)} builds for {champion}/{role}")
                    continue

            # Try other data sources
            if ddragon_data:
                logger.debug(f"  DDragon data available, could use for {champion}/{role}")
            if cdragon_data:
                logger.debug(f"  CDragon data available, could use for {champion}/{role}")
            if opgg_data:
                logger.debug(f"  OP.GG data available, could use for {champion}/{role}")

            # No live data available - report failure
            logger.warning(f"  ✗ Failed to get data for {champion}/{role}")
            failed += 1
            failed_targets.append(f"{champion}/{role}")

            # Mark as failed in JSON
            key = f"{champion}/{role}"
            meta['builds'][key] = {
                'error': 'failed to get data',
                'status': 'no_api_response',
                'items': [],
                'note': 'Available APIs do not provide usable item build data - manual curation needed'
            }

        # Save the updated meta builds (even with failures)
        save_ok = MetaBuildUpdater.save(meta)
        if save_ok:
            logger.info(f"✓ Saved meta_builds.json: {updated} successful, {failed} failed")
        else:
            logger.error("✗ Failed to save meta_builds.json")

        logger.info("=" * 60)
        logger.info("Meta builds auto-update complete")
        logger.info("=" * 60)

        report = {
            "status": "ok" if save_ok else "save_failed",
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now().isoformat(),
            "duration_seconds": round(max(0.0, time.time() - started_ts), 3),
            "updated": updated,
            "failed": failed,
            "total_targets": len(targets),
            "failed_targets": failed_targets,
            "rules": {
                "allow": sorted(list(allow_rules)),
                "deny": sorted(list(deny_rules)),
            },
            "filter_stats": {
                "builds_pruned": filter_builds_pruned,
                "items_removed": filter_items_removed,
            },
            "save_ok": save_ok,
            "error": "",
        }
        _set_last_meta_build_update_report(report)
        return report
    except Exception as exc:
        report = {
            "status": "error",
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now().isoformat(),
            "duration_seconds": round(max(0.0, time.time() - started_ts), 3),
            "updated": 0,
            "failed": 0,
            "total_targets": 0,
            "failed_targets": failed_targets,
            "rules": {
                "allow": sorted(list(allow_rules)),
                "deny": sorted(list(deny_rules)),
            },
            "filter_stats": {
                "builds_pruned": filter_builds_pruned,
                "items_removed": filter_items_removed,
            },
            "save_ok": False,
            "error": str(exc),
        }
        _set_last_meta_build_update_report(report)
        raise


if __name__ == "__main__":
    update_meta_builds_periodically()

