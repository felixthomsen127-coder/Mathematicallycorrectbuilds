from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
import requests_cache
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from bs4 import BeautifulSoup

from optimizer import ChampionProfile, ItemStats


class DataSourceError(RuntimeError):
    pass


_HTTP_CACHE_PATH = Path(os.environ.get("LOCALAPPDATA", str(Path(__file__).resolve().parent))) / "mathematically_correct_builds" / "http_cache"
_HTTP_CACHE_PATH.mkdir(parents=True, exist_ok=True)
_HTTP_SESSION = requests_cache.CachedSession(
    cache_name=str(_HTTP_CACHE_PATH / "requests_cache"),
    backend="sqlite",
    expire_after=60 * 60,
    allowable_methods=("GET",),
)


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.6, min=0.6, max=6.0),
    retry=retry_if_exception_type(requests.RequestException),
)
def _http_get(url: str, **kwargs: Any) -> requests.Response:
    return _HTTP_SESSION.get(url, **kwargs)


class LocalJsonCache:
    def __init__(self, namespace: str = "mathematically_correct_builds"):
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            base = Path(local_appdata) / namespace / "cache"
        else:
            base = Path(__file__).resolve().parent / ".cache"
        self.base_dir = base
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str, max_age_seconds: Optional[float] = None) -> Optional[Dict]:
        path = self._path_for_key(key)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            timestamp = float(payload.get("timestamp", 0))
            if max_age_seconds is not None and (time.time() - timestamp) > max_age_seconds:
                return None
            return payload.get("value")
        except Exception:
            return None

    def set(self, key: str, value: Dict) -> None:
        path = self._path_for_key(key)
        payload = {"timestamp": time.time(), "value": value}
        with path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    def clear(self, prefix: str = "") -> int:
        deleted = 0
        for path in self.base_dir.glob("*.json"):
            if prefix and not path.name.startswith(self._normalize_key(prefix)):
                continue
            try:
                path.unlink()
                deleted += 1
            except Exception:
                continue
        return deleted

    def _path_for_key(self, key: str) -> Path:
        normalized = self._normalize_key(key)
        return self.base_dir / f"{normalized}.json"

    @staticmethod
    def _normalize_key(key: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_.-]", "_", key)


@dataclass(frozen=True)
class ChampionScaling:
    source: str
    ability_breakdown: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    placeholder_used: bool = False
    fallback_reasons: List[str] = field(default_factory=list)


class LeagueWikiClient:
    BASE = "https://wiki.leagueoflegends.com/en-us/api.php"

    def __init__(self, timeout_seconds: float = 12.0):
        self.timeout_seconds = timeout_seconds
        self.cache = LocalJsonCache()

    def get_latest_patch(self, force_refresh: bool = False) -> str:
        cache_key = "wiki_versions_latest"
        if not force_refresh:
            cached = self.cache.get(cache_key, max_age_seconds=60 * 60 * 12)
            if cached and cached.get("patch"):
                return str(cached["patch"])

        patch = "wiki-live"
        try:
            revs = [
                self._fetch_page_revision_timestamp("Module:ChampionData/data"),
                self._fetch_page_revision_timestamp("Module:ItemData/data"),
            ]
            parts = [x for x in revs if x]
            if parts:
                digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
                patch = f"wiki-rev-{digest}"
        except Exception:
            patch = "wiki-live"

        self.cache.set(cache_key, {"patch": patch})
        return patch

    def _fetch_page_revision_timestamp(self, title: str) -> str:
        params = {
            "action": "query",
            "format": "json",
            "prop": "revisions",
            "rvprop": "timestamp",
            "rvlimit": 1,
            "titles": title,
        }
        res = _http_get(self.BASE, params=params, timeout=self.timeout_seconds)
        res.raise_for_status()
        pages = res.json().get("query", {}).get("pages", {})
        for page in pages.values():
            revs = page.get("revisions", [])
            if revs:
                return str(revs[0].get("timestamp", "") or "")
        return ""

    def get_items(self, patch: str, force_refresh: bool = False) -> List[ItemStats]:
        cache_key = f"wiki_items_v2_{patch}"
        if not force_refresh:
            cached = self.cache.get(cache_key)
            if cached and isinstance(cached.get("items"), list):
                return [ItemStats(**x) for x in cached["items"]]

        module_text = self._fetch_wiki_module("Module:ItemData/data")
        entries = self._iter_named_lua_tables(module_text)

        results: List[ItemStats] = []
        for item_name, block in entries:
            item_id = int(self._extract_first_number_field(block, "id", default=0))
            if item_id <= 0:
                continue

            # Keep SR-usable items for the optimizer baseline.
            if not any(self._extract_mode_flag(block, key) for key in ("classic sr 5v5", "CLASSIC", "SR", "sr")):
                continue

            gold = self._extract_first_number_field(block, "buy", default=0.0)
            if gold < 1000:
                continue

            stats = self._extract_stats_from_block(block)
            tags = tuple(self._extract_string_list_field(block, "type"))
            effects_text = self._flatten_effect_descriptions(block)
            unique_passives = self._extract_unique_passive_names(effects_text)

            inferred = self._infer_passive_coefficients(
                item_name,
                effects_text,
                effects_text,
                list(tags),
            )

            results.append(
                ItemStats(
                    item_id=str(item_id),
                    name=item_name,
                    total_gold=gold,
                    ad=float(stats.get("ad", 0.0)),
                    ap=float(stats.get("ap", 0.0)),
                    attack_speed=float(stats.get("as", 0.0)),
                    hp=float(stats.get("hp", 0.0)),
                    armor=float(stats.get("armor", 0.0)),
                    mr=float(stats.get("mr", 0.0)),
                    ability_haste=float(stats.get("ah", 0.0)),
                    lifesteal=float(stats.get("lifesteal", 0.0)),
                    omnivamp=float(stats.get("omnivamp", 0.0)),
                    tags=tags,
                    unique_group=self._infer_unique_group(item_name),
                    unique_passives=unique_passives,
                    damage_amp=inferred.get("damage_amp", 0.0),
                    bonus_true_damage=inferred.get("bonus_true_damage", 0.0),
                    heal_amp=inferred.get("heal_amp", 0.0),
                    shield_amp=inferred.get("shield_amp", 0.0),
                    armor_pen=float(stats.get("armpen", 0.0)) + inferred.get("armor_pen", 0.0),
                    magic_pen=float(stats.get("mpen", 0.0)) + inferred.get("magic_pen", 0.0),
                    flat_armor_pen=float(stats.get("lethality", 0.0)) + inferred.get("flat_armor_pen", 0.0),
                    flat_magic_pen=float(stats.get("mpenflat", 0.0)) + inferred.get("flat_magic_pen", 0.0),
                    max_hp_damage=inferred.get("max_hp_damage", 0.0),
                )
            )

        self.cache.set(cache_key, {"items": [asdict(x) for x in results]})
        return results

    def get_all_champions(self, patch: str, force_refresh: bool = False) -> List[Dict[str, str]]:
        """Return sorted list of {name, slug, icon_url} for every champion."""
        cache_key = f"wiki_champion_list_{patch}"
        if not force_refresh:
            cached = self.cache.get(cache_key, max_age_seconds=60 * 60 * 12)
            if cached and isinstance(cached.get("champions"), list):
                cached_champions = cached["champions"]
                # Guard against test-polluted cache payloads (for example a single
                # "Unit Champ" fixture) or partial writes.
                cached_names = {str(x.get("name", "")) for x in cached_champions if isinstance(x, dict)}
                has_fixture_name = any(name.lower().startswith("unit") for name in cached_names)
                if len(cached_champions) >= 150 and not has_fixture_name:
                    return cached_champions

        champions_data = self._load_wiki_champion_data(force_refresh=force_refresh)
        champions = sorted(
            [
                {
                    "name": entry["name"],
                    "slug": entry["slug"],
                    "icon_url": entry["icon_url"],
                }
                for entry in champions_data.values()
            ],
            key=lambda x: x["name"],
        )
        self.cache.set(cache_key, {"champions": champions})
        return champions

    def _slug_for_champion(self, patch: str, champion: str) -> str:
        """Resolve any champion display name to wiki champion slug."""
        name_lower = champion.strip().lower()
        try:
            all_champs = self.get_all_champions(patch)
            for entry in all_champs:
                if entry["name"].lower() == name_lower:
                    return entry["slug"]
            # Fallback: strip apostrophes/spaces and match slug case-insensitively
            stripped = re.sub(r"['\s]", "", name_lower)
            for entry in all_champs:
                if entry["slug"].lower() == stripped:
                    return entry["slug"]
        except Exception:
            pass
        # Last resort: legacy normalization (handles offline/cache-miss edge cases)
        return re.sub(r"['\s]", "", champion.strip())

    def get_champion_profile(self, patch: str, champion: str, force_refresh: bool = False) -> ChampionProfile:
        slug = self._slug_for_champion(patch, champion)
        cache_key = f"wiki_champion_{patch}_{slug}"
        if not force_refresh:
            cached = self.cache.get(cache_key)
            if cached:
                return ChampionProfile(**{k: v for k, v in cached.items() if k in ChampionProfile.__dataclass_fields__})

        def _resolve_champion_data(champions_data: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            slug_key = slug.lower()
            if slug_key in champions_data:
                return champions_data[slug_key]

            # Normalize by stripping punctuation/spaces so names like
            # "Bel'Veth" and "Aurelion Sol" still match if cache keys drift.
            normalized_slug = re.sub(r"[^a-z0-9]", "", slug_key)
            normalized_name = re.sub(r"[^a-z0-9]", "", champion.strip().lower())
            for key, value in champions_data.items():
                key_norm = re.sub(r"[^a-z0-9]", "", key.lower())
                if key_norm == normalized_slug or key_norm == normalized_name:
                    return value
            return None

        champions_data = self._load_wiki_champion_data(force_refresh=force_refresh)
        champ_data = _resolve_champion_data(champions_data)

        # Guard against stale local champion-data cache (for example after a
        # newly released champion or partial old cache write).
        if not champ_data and not force_refresh:
            champions_data = self._load_wiki_champion_data(force_refresh=True)
            champ_data = _resolve_champion_data(champions_data)

        if not champ_data:
            raise DataSourceError(f"Champion not found in wiki dataset: {champion} (slug: {slug})")

        tags = tuple(str(x) for x in champ_data.get("tags", []))
        profile = ChampionProfile(
            champion_name=champion,
            base_hp=float(champ_data.get("base_hp", 0) or 0),
            base_armor=float(champ_data.get("base_armor", 0) or 0),
            base_mr=float(champ_data.get("base_mr", 0) or 0),
            champion_tags=tags,
        )
        self.cache.set(cache_key, asdict(profile))
        return profile

    def get_champion_ability_descriptions(self, patch: str, champion: str, force_refresh: bool = False) -> Dict[str, str]:
        # Ability text extraction is performed by WikiScalingParser directly from
        # rendered wiki pages. Keep a compatibility stub.
        return {}

    def get_champion_ability_payload(self, patch: str, champion: str, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        # Structured ability payload now comes from wiki-rendered parsing inside
        # WikiScalingParser. Keep API surface for compatibility.
        return {}

    def _fetch_wiki_module(self, page: str) -> str:
        params = {
            "action": "parse",
            "page": page,
            "prop": "wikitext",
            "format": "json",
        }
        headers = {
            "User-Agent": "mathematically-correct-builds/1.0",
        }
        attempts = 4
        last_error: Optional[Exception] = None

        for attempt in range(attempts):
            try:
                res = _http_get(self.BASE, params=params, headers=headers, timeout=self.timeout_seconds)

                if res.status_code == 429:
                    retry_after = self._parse_retry_after_seconds(res.headers.get("Retry-After"))
                    if attempt == attempts - 1:
                        res.raise_for_status()
                    # Respect server hint when present, otherwise back off with jitter.
                    wait_s = retry_after if retry_after is not None else min(1.0 * (2 ** attempt), 8.0) + random.random() * 0.25
                    time.sleep(wait_s)
                    continue

                res.raise_for_status()
                payload = res.json()
                text = payload.get("parse", {}).get("wikitext", {}).get("*", "")
                if not text:
                    raise DataSourceError(f"Wiki module returned empty payload: {page}")
                return text
            except requests.RequestException as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
                time.sleep(min(0.5 * (2 ** attempt), 4.0) + random.random() * 0.25)

        raise DataSourceError(f"Failed to fetch wiki module after retries ({page}): {last_error}")

    @staticmethod
    def _parse_retry_after_seconds(value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        try:
            seconds = float(value.strip())
        except (TypeError, ValueError):
            return None
        return max(0.0, seconds)

    def _iter_named_lua_tables(self, source: str) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        i = 0
        marker = '["'
        while True:
            start = source.find(marker, i)
            if start == -1:
                break
            end_name = source.find('"]', start + 2)
            if end_name == -1:
                break
            name = source[start + 2 : end_name]
            eq = source.find("=", end_name)
            if eq == -1:
                break
            brace = source.find("{", eq)
            if brace == -1:
                i = end_name + 2
                continue
            block, end_idx = self._consume_balanced_braces(source, brace)
            if block:
                out.append((name, block))
                i = end_idx
            else:
                i = brace + 1
        return out

    def _consume_balanced_braces(self, text: str, start: int) -> Tuple[str, int]:
        depth = 0
        i = start
        in_string = False
        escaped = False
        while i < len(text):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start : i + 1], i + 1
            i += 1
        return "", len(text)

    @staticmethod
    def _extract_first_number_field(block: str, key: str, default: float = 0.0) -> float:
        pattern = re.compile(rf'\["{re.escape(key)}"\]\s*=\s*([-+]?\d+(?:\.\d+)?)')
        match = pattern.search(block)
        if not match:
            return float(default)
        try:
            return float(match.group(1))
        except ValueError:
            return float(default)

    @staticmethod
    def _extract_mode_flag(block: str, mode_key: str) -> bool:
        mode_pattern = re.compile(r'\["modes"\]\s*=\s*\{', re.IGNORECASE)
        mode_match = mode_pattern.search(block)
        if not mode_match:
            return False
        sub = block[mode_match.start() :]
        field = re.search(rf'\["{re.escape(mode_key)}"\]\s*=\s*(true|false)', sub, re.IGNORECASE)
        return bool(field and field.group(1).lower() == "true")

    @staticmethod
    def _extract_string_list_field(block: str, key: str) -> List[str]:
        pat = re.compile(rf'\["{re.escape(key)}"\]\s*=\s*\{{(.*?)\}}', re.DOTALL)
        m = pat.search(block)
        if not m:
            return []
        return [x.strip() for x in re.findall(r'"([^\"]+)"', m.group(1)) if x.strip()]

    def _extract_stats_from_block(self, block: str) -> Dict[str, float]:
        _STAT_ALIASES: Dict[str, str] = {
            "armorpen": "armpen",
            "armor_pen": "armpen",
            "magicpen": "mpen",
            "magic_pen": "mpen",
        }
        out: Dict[str, float] = {}
        pat = re.compile(r'\["stats"\]\s*=\s*\{(.*?)\}', re.DOTALL)
        m = pat.search(block)
        if not m:
            return out
        for key, val in re.findall(r'\["([^\"]+)"\]\s*=\s*([-+]?\d+(?:\.\d+)?)', m.group(1)):
            try:
                k = key.strip().lower()
                normalized_key: str = _STAT_ALIASES[k] if k in _STAT_ALIASES else k
                out[normalized_key] = float(val)
            except ValueError:
                continue
        return out

    def _flatten_effect_descriptions(self, block: str) -> str:
        effects_pat = re.compile(r'\["effects"\]\s*=\s*\{(.*?)\}\s*,\s*\["recipe"\]|\["effects"\]\s*=\s*\{(.*?)\}\s*,\s*\["buy"\]', re.DOTALL)
        m = effects_pat.search(block)
        if not m:
            return ""
        text = m.group(1) or m.group(2) or ""
        return " ".join(re.findall(r'"([^\"]+)"', text))

    def _load_wiki_champion_data(self, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        cache_key = "wiki_champion_data_compact"
        if not force_refresh:
            cached = self.cache.get(cache_key, max_age_seconds=60 * 60 * 12)
            if cached and isinstance(cached.get("champions"), dict):
                cached_champions = cached["champions"]
                # Ignore tiny/test fixture payloads and force live refresh.
                if len(cached_champions) >= 150 and "unitchamp" not in cached_champions:
                    return cached_champions

        module_text = self._fetch_wiki_module("Module:ChampionData/data")
        entries = self._iter_named_lua_tables(module_text)
        champions: Dict[str, Dict[str, Any]] = {}
        for display_name, block in entries:
            apiname_match = re.search(r'\["apiname"\]\s*=\s*"([^\"]+)"', block)
            slug = apiname_match.group(1) if apiname_match else re.sub(r"[^A-Za-z0-9]", "", display_name)
            role_list = self._extract_string_list_field(block, "role")
            if not role_list:
                role_list = self._extract_string_list_field(block, "position")

            stats_pat = re.compile(r'\["stats"\]\s*=\s*\{(.*?)\}', re.DOTALL)
            stats_match = stats_pat.search(block)
            stats_src = stats_match.group(1) if stats_match else ""

            hp_base = self._extract_first_number_field(stats_src, "hp_base", default=0.0)
            arm_base = self._extract_first_number_field(stats_src, "arm_base", default=0.0)
            mr_base = self._extract_first_number_field(stats_src, "mr_base", default=0.0)

            icon_slug = quote(slug)
            champions[slug.lower()] = {
                "name": display_name,
                "slug": slug,
                "icon_url": f"https://wiki.leagueoflegends.com/en-us/Special:FilePath/{icon_slug}Square.png",
                "base_hp": hp_base,
                "base_armor": arm_base,
                "base_mr": mr_base,
                "tags": tuple(role_list),
            }

        self.cache.set(cache_key, {"champions": champions})
        return champions

    @staticmethod
    def _extract_spell_payload(champ_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        payload: Dict[str, Dict[str, Any]] = {}

        passive = champ_data.get("passive", {})
        if passive:
            payload["passive"] = {
                "name": passive.get("name", "Passive"),
                "description": LeagueWikiClient._strip_html(passive.get("description", "")),
                "tooltip": LeagueWikiClient._strip_html(passive.get("description", "")),
                "cooldown": [],
                "cooldownBurn": "",
                "cost": [],
                "costBurn": "",
                "resource": "",
                "leveltip": {"label": [], "effect": []},
                "vars": [],
                "effect": [],
                "effectBurn": [],
                "datavalues": {},
            }

        spell_keys = ["q", "w", "e", "r"]
        for idx, spell in enumerate(champ_data.get("spells", [])[:4]):
            key = spell_keys[idx]
            payload[key] = {
                "name": spell.get("name", key.upper()),
                "description": LeagueWikiClient._strip_html(spell.get("description", "")),
                "tooltip": LeagueWikiClient._strip_html(spell.get("tooltip", "")),
                "cooldown": list(spell.get("cooldown", []) or []),
                "cooldownBurn": str(spell.get("cooldownBurn", "") or ""),
                "cost": list(spell.get("cost", []) or []),
                "costBurn": str(spell.get("costBurn", "") or ""),
                "resource": LeagueWikiClient._strip_html(spell.get("resource", "")),
                "leveltip": spell.get("leveltip", {"label": [], "effect": []}) or {"label": [], "effect": []},
                "vars": list(spell.get("vars", []) or []),
                "effect": list(spell.get("effect", []) or []),
                "effectBurn": list(spell.get("effectBurn", []) or []),
                "datavalues": dict(spell.get("datavalues", {}) or {}),
            }
        return payload

    @staticmethod
    def _extract_spell_texts(ability_payload: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
        texts: Dict[str, str] = {}
        for key, block in ability_payload.items():
            desc = str(block.get("description", "") or "").strip()
            tooltip = str(block.get("tooltip", "") or "").strip()
            if desc and tooltip:
                texts[key] = f"{desc} {tooltip}"
            else:
                texts[key] = desc or tooltip
        return texts

    def clear_cache(self) -> int:
        return self.cache.clear(prefix="wiki_")

    @staticmethod
    def _extract_unique_passive_names(effects_text: str) -> Tuple[str, ...]:
        """Extract 'UNIQUE – Name:' passive names from item wiki effects text.

        League of Legends items encode unique passives as
        ``UNIQUE – Name: description`` in the effects field.  Two items that
        share a unique passive name cannot appear in the same build.
        """
        pattern = re.compile(
            r"\bUNIQUE\b\s*[\u2013\u2014\-]\s*([A-Za-z][A-Za-z ]{1,30}?)\s*:",
            re.IGNORECASE,
        )
        _GENERIC = {"passive", "active", "aura", "unique", "bonus", "effect", "mythic"}
        names: List[str] = []
        for m in pattern.finditer(effects_text):
            name = m.group(1).strip().lower()
            if name not in _GENERIC:
                names.append(name)
        return tuple(dict.fromkeys(names))  # deduplicate, preserve order

    @staticmethod
    def _infer_unique_group(item_name: str) -> str:
        """Hardcoded fallback unique group used when wiki extraction is unavailable."""
        lowered = item_name.lower()
        _GROUPS = {
            "hydra": "hydra",
            "immolate": "immolate",
            "trinity force": "spellblade",
            "divine sunderer": "spellblade",
            "lich bane": "spellblade",
            "sheen": "spellblade",
            "spellblade": "spellblade",
            "maw of malmortius": "lifeline",
            "sterak": "lifeline",
            "immortal shieldbow": "lifeline",
            "guardian angel": "lifeline",
            "lifeline": "lifeline",
            "serylda": "last_whisper",
            "lord dominik": "last_whisper",
            "mortal reminder": "last_whisper",
            "last whisper": "last_whisper",
            "void staff": "void_staff",
            "blighting jewel": "void_staff",
            "cryptbloom": "void_staff",
            "quicksilver": "quicksilver",
            "silvermere": "quicksilver",
        }
        for keyword, group in _GROUPS.items():
            if keyword in lowered:
                return group
        return ""

    @staticmethod
    def _infer_passive_coefficients(
        item_name: str,
        description: str,
        plaintext: str,
        tags: List[str],
    ) -> Dict[str, float]:
        out = {
            "damage_amp": 0.0,
            "bonus_true_damage": 0.0,
            "heal_amp": 0.0,
            "shield_amp": 0.0,
            "armor_pen": 0.0,
            "magic_pen": 0.0,
            "flat_armor_pen": 0.0,
            "flat_magic_pen": 0.0,
            "max_hp_damage": 0.0,
        }
        name = item_name.lower()
        text = LeagueWikiClient._strip_html(f"{description} {plaintext}").lower()

        if "rabadon" in name:
            out["damage_amp"] += 0.08
        if "liandry" in name:
            out["max_hp_damage"] += 0.02
        if "blade of the ruined king" in name:
            out["max_hp_damage"] += 0.06
        if "riftmaker" in name:
            out["damage_amp"] += 0.06
        if "guinsoo" in name or "nashor" in name:
            out["bonus_true_damage"] += 12.0
        if "spirit visage" in name:
            out["heal_amp"] += 0.25
            out["shield_amp"] += 0.25
        if "void staff" in name:
            out["magic_pen"] += 0.40
        if "cryptbloom" in name:
            out["magic_pen"] += 0.30
        if "lord dominik" in name:
            out["armor_pen"] += 0.35
        if "serylda" in name:
            out["armor_pen"] += 0.30
        if "malignance" in name:
            out["damage_amp"] += 0.08
        if "blackfire torch" in name:
            out["max_hp_damage"] += 0.02
        if "stormsurge" in name:
            out["bonus_true_damage"] += 15.0

        armor_pen_percent = LeagueWikiClient._extract_percentage_before_keyword(text, "armor penetration")
        magic_pen_percent = LeagueWikiClient._extract_percentage_before_keyword(text, "magic penetration")
        max_hp_damage_percent = LeagueWikiClient._extract_percentage_before_keyword(text, "maximum health")
        increased_damage_percent = LeagueWikiClient._extract_percentage_before_keyword(text, "increased damage")
        increased_heal_percent = LeagueWikiClient._extract_percentage_before_keyword(text, "increased healing")
        increased_shield_percent = LeagueWikiClient._extract_percentage_before_keyword(text, "increased shielding")

        if armor_pen_percent > 0:
            out["armor_pen"] = max(out["armor_pen"], armor_pen_percent / 100.0)
        if magic_pen_percent > 0:
            out["magic_pen"] = max(out["magic_pen"], magic_pen_percent / 100.0)
        if max_hp_damage_percent > 0:
            out["max_hp_damage"] = max(out["max_hp_damage"], max_hp_damage_percent / 100.0)
        if increased_damage_percent > 0:
            out["damage_amp"] += increased_damage_percent / 100.0
        if increased_heal_percent > 0:
            out["heal_amp"] += increased_heal_percent / 100.0
        if increased_shield_percent > 0:
            out["shield_amp"] += increased_shield_percent / 100.0

        out["flat_magic_pen"] = LeagueWikiClient._extract_flat_pen(text, "magic penetration")
        out["flat_armor_pen"] = LeagueWikiClient._extract_flat_pen(text, "lethality")
        if "omnivamp" in text and "life steal" in text:
            out["damage_amp"] += 0.02
        if "on-hit" in text:
            out["bonus_true_damage"] += 8.0
        if "health" in text and "damage" in text and "max" in text:
            out["max_hp_damage"] = max(out["max_hp_damage"], 0.02)

        if any(str(x).lower() == "spelldamage" for x in tags):
            out["damage_amp"] += 0.01
        return out

    @staticmethod
    def _strip_html(text: str) -> str:
        return re.sub(r"<[^>]+>", " ", text or "")

    @staticmethod
    def _extract_percentage_before_keyword(text: str, keyword: str) -> float:
        pattern = re.compile(rf"(\d+(?:\.\d+)?)\s*%[^.\n]*{re.escape(keyword)}", re.IGNORECASE)
        match = pattern.search(text)
        if not match:
            return 0.0
        try:
            return float(match.group(1))
        except ValueError:
            return 0.0

    @staticmethod
    def _extract_flat_pen(text: str, keyword: str) -> float:
        pattern = re.compile(rf"(\d+(?:\.\d+)?)\s+{re.escape(keyword)}", re.IGNORECASE)
        match = pattern.search(text)
        if not match:
            return 0.0
        try:
            return float(match.group(1))
        except ValueError:
            return 0.0


class WikiScalingParser:
    """
    Pull rough scaling hints from wiki source text.

    This parser intentionally starts conservative: it only extracts broad AP/AD/
    AS/healing coefficient proxies and is designed to be improved iteratively.
    """

    BASE_API = "https://wiki.leagueoflegends.com/en-us/api.php"

    AP_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:AP|ability\s*power)", re.IGNORECASE)
    AD_PATTERN = re.compile(
        r"(\d+(?:\.\d+)?)\s*%\s*(?:(?:bonus|total)\s+)?(?:AD|bAD|tAD|attack\s*damage)",
        re.IGNORECASE,
    )
    AS_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*attack\s*speed", re.IGNORECASE)
    MS_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:bonus\s+)?(?:movement\s*speed|move\s*speed)", re.IGNORECASE)
    HEAL_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:missing\s+health|max\s+health|health)", re.IGNORECASE)
    TOKEN_PATTERN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
    PCT_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")
    KEY_PATTERNS = {
        "q": re.compile(r"\b-\s*Q\b|\bQ\s+Active:|\bQ\b\s+[-\u2013]|\(Q\)|\bQ:\s", re.IGNORECASE),
        "w": re.compile(r"\b-\s*W\b|\bW\s+Active:|\bW\b\s+[-\u2013]|\(W\)|\bW:\s", re.IGNORECASE),
        "e": re.compile(r"\b-\s*E\b|\bE\s+Active:|\bE\b\s+[-\u2013]|\(E\)|\bE:\s", re.IGNORECASE),
        "r": re.compile(r"\b-\s*R\b|\bR\s+Active:|\bR\b\s+[-\u2013]|\(R\)|\bR:\s", re.IGNORECASE),
    }
    # Patterns that indicate the ability section headings in wiki rendered text
    PASSIVE_PATTERNS = [
        re.compile(r"\bInnate\s*:", re.IGNORECASE),
        re.compile(r"\bPassive\s*[-\u2013:]", re.IGNORECASE),
    ]
    GLOBAL_CUTOFF_MARKERS = [
        "patch history",
        "past versions",
        "version history",
        "gallery",
        "collection",
        "audio",
        "references",
        "external links",
    ]
    SECTION_CUTOFF_MARKERS = [
        "map-specific differences",
        "arena differences",
        "patch history",
        "past versions",
        "version history",
        "notes",
        "bugs",
        "trivia",
        "strategy",
    ]

    def __init__(self, timeout_seconds: float = 15.0):
        self.timeout_seconds = timeout_seconds
        self.cache = LocalJsonCache()
        self.parser_revision = "v12_deterministic_dualpass"

    def get_scaling(
        self,
        champion: str,
        force_refresh: bool = False,
        use_ai_fallback: bool = False,
    ) -> ChampionScaling:
        normalized = champion.strip().lower().replace(" ", "_")
        patch_fingerprint = self._get_patch_fingerprint(force_refresh=force_refresh)
        cache_key = f"wiki_scaling_{self.parser_revision}_{patch_fingerprint}_{normalized}"
        if not force_refresh:
            cached = self.cache.get(cache_key, max_age_seconds=60 * 60 * 24 * 7)
            if cached:
                cached_scaling = ChampionScaling(**cached)
                if not self._missing_signal_keys(cached_scaling.ability_breakdown):
                    return cached_scaling

        fallback_reasons: List[str] = []
        breakdown: Dict[str, Dict[str, Any]] = {}
        source = "wiki-structured-strict+rendered-merge"
        rendered_sections: Optional[Dict[str, str]] = None

        try:
            breakdown = self._extract_from_wiki_templates(champion)
            self._validate_strict_breakdown(champion, breakdown)
        except Exception as exc:
            fallback_reasons.append(f"wiki_template_unusable: {exc}")

        # Deterministic two-pass extraction: always parse rendered wiki text and merge.
        rendered_text = self._get_rendered_text(champion)
        rendered_sections = self._extract_sections(rendered_text)
        rendered_breakdown = self._extract_from_rendered_sections(rendered_sections)
        if breakdown:
            breakdown = self._merge_breakdowns(breakdown, rendered_breakdown)
        else:
            breakdown = rendered_breakdown
            fallback_reasons.append("wiki_rendered_primary")

        if use_ai_fallback:
            still_missing = self._missing_signal_keys(breakdown)
            if still_missing:
                if rendered_sections is None:
                    rendered_text = self._get_rendered_text(champion)
                    rendered_sections = self._extract_sections(rendered_text)
                ai_breakdown = self._extract_with_ai_fallback(champion, rendered_sections, still_missing)
                if ai_breakdown:
                    breakdown = self._merge_breakdowns(breakdown, ai_breakdown)
                    source = "wiki-structured-strict+ai-fallback"
                    fallback_reasons.append(f"ai_fill_for_missing: {','.join(still_missing)}")

        still_missing_after_merge = self._missing_signal_keys(breakdown)
        has_incomplete = bool(still_missing_after_merge)
        if has_incomplete:
            fallback_reasons.append(
                f"no_signals_for: {','.join(still_missing_after_merge)}"
            )

        self._validate_strict_breakdown(champion, breakdown)

        scaling = ChampionScaling(
            source=source,
            ability_breakdown=breakdown,
            placeholder_used=has_incomplete,
            fallback_reasons=fallback_reasons,
        )
        self.cache.set(cache_key, asdict(scaling))
        return scaling

    def _extract_from_wiki_templates(self, champion: str) -> Dict[str, Dict[str, Any]]:
        template_titles = self._extract_data_template_titles(champion)
        if not template_titles:
            raise DataSourceError(f"No wiki data templates found for {champion}")

        breakdown: Dict[str, Dict[str, Any]] = {}
        for title in template_titles:
            raw_text = self._fetch_page_wikitext(title)
            text = self._materialize_wiki_template_text(raw_text)
            skill_match = re.search(r"\|\s*skill\s*=\s*([QWER])\b", text, flags=re.IGNORECASE)
            if not skill_match:
                continue
            key = skill_match.group(1).lower()

            ratios = self._extract_ratio_values(text)
            leveling_blocks = re.findall(
                r"\|\s*leveling\d*\s*=\s*(.*?)(?=\n\s*\|\s*[A-Za-z]|$)",
                text,
                flags=re.IGNORECASE | re.DOTALL,
            )
            leveling_text = " ".join(leveling_blocks)
            base_damage = self._extract_base_series_from_leveling(leveling_text)

            cd_match = re.search(
                r"\|\s*cooldown\s*=\s*(.*?)(?=\n\s*\|\s*[A-Za-z]|$)",
                text,
                flags=re.IGNORECASE | re.DOTALL,
            )
            cooldown = self._parse_numeric_series(cd_match.group(1)) if cd_match else []

            candidate = {
                "name": key.upper(),
                "ad_ratio": float(ratios.get("ad_ratio", 0.0)),
                "ap_ratio": float(ratios.get("ap_ratio", 0.0)),
                "attack_speed_ratio": float(ratios.get("attack_speed_ratio", 0.0)),
                "ms_ratio": float(ratios.get("ms_ratio", 0.0)),
                "heal_ratio": float(ratios.get("heal_ratio", 0.0)),
                "hp_ratio": 0.0,
                "bonus_hp_ratio": 0.0,
                "armor_ratio": 0.0,
                "mr_ratio": 0.0,
                "scaling_components": self._extract_scaling_components(text),
                "base_damage": base_damage,
                "cooldown": cooldown if cooldown else [1.0],
                "cost": [],
                "resource": "",
                "raw_text": text,
                "source": "wiki-template",
                "damage_type": self._extract_damage_type(text),
                "targeting": self._extract_targeting(text),
                "on_hit": self._extract_on_hit(text),
                "is_channeled": self._extract_channeled(text),
                "is_conditional": self._is_conditional(text),
                "is_stack_scaling": self._is_stack_scaling(text),
                "range_units": self._extract_range(text),
            }
            candidate["scaling_by_application"] = self._group_components_by_application(candidate.get("scaling_components", []))

            if key not in breakdown:
                breakdown[key] = candidate
            else:
                current = breakdown[key]
                for metric in ("ad_ratio", "ap_ratio", "attack_speed_ratio", "ms_ratio", "heal_ratio"):
                    current[metric] = max(float(current.get(metric, 0.0) or 0.0), float(candidate.get(metric, 0.0) or 0.0))
                cur_base = self._coerce_float_list(current.get("base_damage", []))
                cand_base = self._coerce_float_list(candidate.get("base_damage", []))
                if cand_base and (not cur_base or max(cand_base) > max(cur_base)):
                    current["base_damage"] = cand_base
                cur_cd = self._coerce_float_list(current.get("cooldown", []))
                cand_cd = self._coerce_float_list(candidate.get("cooldown", []))
                if cand_cd and (not cur_cd or cur_cd == [1.0] or max(cand_cd) > max(cur_cd)):
                    current["cooldown"] = cand_cd
                current_components = current.get("scaling_components", [])
                current["scaling_components"] = self._merge_scaling_components(current_components, candidate.get("scaling_components", []))
                current["scaling_by_application"] = self._group_components_by_application(current.get("scaling_components", []))

        for key in ("q", "w", "e", "r"):
            breakdown.setdefault(
                key,
                {
                    "name": key.upper(),
                    "ad_ratio": 0.0,
                    "ap_ratio": 0.0,
                    "attack_speed_ratio": 0.0,
                    "ms_ratio": 0.0,
                    "heal_ratio": 0.0,
                    "hp_ratio": 0.0,
                    "bonus_hp_ratio": 0.0,
                    "armor_ratio": 0.0,
                    "mr_ratio": 0.0,
                    "scaling_components": [],
                    "scaling_by_application": self._group_components_by_application([]),
                    "base_damage": [],
                    "cooldown": [1.0],
                    "cost": [],
                    "resource": "",
                    "raw_text": "",
                    "source": "wiki-template",
                    "damage_type": "unknown",
                    "targeting": "unknown",
                    "on_hit": False,
                    "is_channeled": False,
                    "is_conditional": False,
                    "is_stack_scaling": False,
                    "range_units": 0.0,
                },
            )

        return breakdown

    def _fetch_page_wikitext(self, title: str) -> str:
        params = {
            "action": "query",
            "format": "json",
            "redirects": 1,
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "titles": title,
        }
        res = _http_get(self.BASE_API, params=params, timeout=self.timeout_seconds)
        res.raise_for_status()
        pages = res.json().get("query", {}).get("pages", {})
        for page in pages.values():
            revs = page.get("revisions", [])
            if not revs:
                continue
            slot = revs[0].get("slots", {}).get("main", {})
            text = str(slot.get("*", "") or slot.get("content", "") or "")
            if text:
                return text
        return ""

    def _extract_data_template_titles(self, champion: str) -> List[str]:
        params = {
            "action": "parse",
            "format": "json",
            "page": champion,
            "prop": "templates",
        }
        res = _http_get(self.BASE_API, params=params, timeout=self.timeout_seconds)
        res.raise_for_status()
        templates = res.json().get("parse", {}).get("templates", [])

        out: List[str] = []
        champ_norm = re.sub(r"[^a-z0-9]", "", champion.lower())
        for template in templates:
            title = str(template.get("*", "") or "")
            if not title.lower().startswith("template:data "):
                continue
            raw_title = title[len("Template:Data "):]
            if "/" not in raw_title:
                continue
            champ_part = raw_title.split("/", 1)[0]
            if re.sub(r"[^a-z0-9]", "", champ_part.lower()) != champ_norm:
                continue
            out.append(title)
        return self._dedupe_preserve_order(out)

    def _materialize_wiki_template_text(self, text: str) -> str:
        if not text:
            return ""

        out = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        vars_map: Dict[str, str] = {}

        # Converge variable materialization because templates often chain
        # #vardefine / #var / #expr multiple levels deep.
        for _ in range(12):
            previous_out = out

            kept_lines: List[str] = []
            for line in out.splitlines():
                match = re.match(r"\s*\{\{#vardefine:\s*([^|}]+)\|(.*)\}\}\s*$", line, flags=re.IGNORECASE)
                if match:
                    key = match.group(1).strip().lower()
                    raw_value = match.group(2).strip()
                    resolved_value = self._resolve_wiki_vars(raw_value, vars_map)
                    vars_map[key] = resolved_value
                    continue
                kept_lines.append(line)
            out = "\n".join(kept_lines)

            replaced_any = False
            for key, value in vars_map.items():
                pattern = re.compile(r"\{\{#var:\s*" + re.escape(key) + r"\s*\}\}", flags=re.IGNORECASE)
                out, count = pattern.subn(value, out)
                if count:
                    replaced_any = True

            def repl_expr(match: re.Match[str]) -> str:
                expr = match.group(1).strip()
                value = self._safe_eval_numeric_expr(expr)
                return str(value) if value is not None else expr

            out = re.sub(r"\{\{#expr:\s*([^}]*)\}\}", repl_expr, out, flags=re.IGNORECASE)
            if not replaced_any and out == previous_out:
                break

        out = re.sub(r"\{\{#var:\s*[^}]+\}\}", " ", out, flags=re.IGNORECASE)
        return out

    def _resolve_wiki_vars(self, text: str, vars_map: Dict[str, str]) -> str:
        resolved = str(text or "")
        for _ in range(8):
            changed = False
            for key, value in vars_map.items():
                pattern = re.compile(r"\{\{#var:\s*" + re.escape(key) + r"\s*\}\}", flags=re.IGNORECASE)
                resolved, count = pattern.subn(value, resolved)
                if count:
                    changed = True
            if not changed:
                break
        return resolved

    def _safe_eval_numeric_expr(self, expr: str) -> Optional[float]:
        if not expr:
            return None
        candidate = expr.strip()
        if not re.fullmatch(r"[0-9\.\+\-\*/\(\)\s]+", candidate):
            return None
        try:
            value = eval(candidate, {"__builtins__": {}}, {})
        except Exception:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _normalize_wiki_markup_text(self, text: str) -> str:
        cleaned = str(text or "")
        cleaned = cleaned.replace("'''", " ").replace("''", " ")

        # Flatten common template wrappers and keep the semantic payload.
        for _ in range(10):
            prev = cleaned
            cleaned = re.sub(r"\{\{\s*[^{}|]+\s*\|\s*([^{}|]+?)\s*\}\}", r"\1", cleaned)
            cleaned = re.sub(r"\{\{\s*[^{}|]+\s*\|\s*[^{}|]+\s*\|\s*([^{}|]+?)\s*\}\}", r"\1", cleaned)
            if cleaned == prev:
                break

        cleaned = cleaned.replace("{{", " ").replace("}}", " ").replace("|", " ")
        return " ".join(cleaned.split())

    def _extract_base_series_from_leveling(self, leveling_text: str) -> List[float]:
        if not leveling_text:
            return []
        candidates: List[List[float]] = []
        for ap_match in re.finditer(r"\{\{\s*ap\s*\|([^{}]+)\}\}", leveling_text, flags=re.IGNORECASE):
            ap_block = ap_match.group(1)
            # Ignore percentage-only AP blocks used for ratios (e.g. 80% AD).
            if "%" in ap_block:
                continue
            series = self._parse_range_or_list_values(ap_block)
            if len(series) >= 2 and max(series) > 0:
                candidates.append(series)
        if not candidates:
            return []
        candidates.sort(key=lambda vals: (max(vals), len(vals), min(vals)), reverse=True)
        return candidates[0]

    def _parse_range_or_list_values(self, text: str) -> List[float]:
        raw = str(text or "").strip()
        if not raw:
            return []

        if " to " in raw:
            parts = [part.strip() for part in raw.split(" to ") if part.strip()]
            if len(parts) >= 2:
                left = self._safe_eval_numeric_expr(parts[0])
                right = self._safe_eval_numeric_expr(parts[1])
                if left is not None and right is not None:
                    return [float(left), float(right)]

        if "/" in raw and re.fullmatch(r"[0-9\.\+\-\*/\(\)\s/]+", raw):
            values: List[float] = []
            for part in [part.strip() for part in raw.split("/") if part.strip()]:
                value = self._safe_eval_numeric_expr(part)
                if value is not None:
                    values.append(float(value))
            if len(values) >= 2:
                return values

        return self._parse_numeric_series(raw)

    def _ability_signal_score(self, block: Dict[str, Any]) -> float:
        ratios = [
            float(block.get("ad_ratio", 0.0) or 0.0),
            float(block.get("ap_ratio", 0.0) or 0.0),
            float(block.get("attack_speed_ratio", 0.0) or 0.0),
            float(block.get("ms_ratio", 0.0) or 0.0),
            float(block.get("heal_ratio", 0.0) or 0.0),
        ]
        base = self._coerce_float_list(block.get("base_damage", []))
        score = sum(ratios) * 100.0
        if base:
            score += max(base) + len(base)
        return score

    def _validate_strict_breakdown(self, champion: str, breakdown: Dict[str, Dict[str, Any]]) -> None:
        if not breakdown:
            raise DataSourceError(f"No ability scaling data extracted for {champion}")

        for key in ("q", "w", "e", "r"):
            block = breakdown.get(key, {})
            if not isinstance(block, dict):
                block = {}
                breakdown[key] = block

            if "cooldown" not in block:
                block["cooldown"] = [1.0]

            has_signal = any(
                float(block.get(metric, 0.0) or 0.0) > 0
                for metric in (
                    "ad_ratio",
                    "ap_ratio",
                    "attack_speed_ratio",
                    "heal_ratio",
                    "hp_ratio",
                    "bonus_hp_ratio",
                    "armor_ratio",
                    "mr_ratio",
                )
            )
            base_damage = self._coerce_float_list(block.get("base_damage", []))
            if not has_signal and (not base_damage or max(base_damage) <= 0):
                block.setdefault("source", "wiki-rendered")

        passive = breakdown.get("passive")
        if isinstance(passive, dict) and passive:
            self._validate_passive_breakdown(champion, passive)

    def _extract_from_rendered_sections(self, sections: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
        breakdown: Dict[str, Dict[str, Any]] = {}

        if "all" in sections and len(sections) == 1:
            all_ratios = self._extract_ratio_values(sections["all"])
            for key in ("q", "w", "e", "r"):
                components = self._extract_scaling_components(sections["all"])
                breakdown[key] = {
                    "name": key.upper(),
                    "ad_ratio": float(all_ratios.get("ad_ratio", 0.0)),
                    "ap_ratio": float(all_ratios.get("ap_ratio", 0.0)),
                    "attack_speed_ratio": float(all_ratios.get("attack_speed_ratio", 0.0)),
                    "heal_ratio": float(all_ratios.get("heal_ratio", 0.0)),
                    "hp_ratio": 0.0,
                    "bonus_hp_ratio": 0.0,
                    "armor_ratio": 0.0,
                    "mr_ratio": 0.0,
                    "scaling_components": components,
                    "scaling_by_application": self._group_components_by_application(components),
                    "base_damage": [],
                    "cooldown": [1.0],
                    "cost": [],
                    "resource": "",
                    "raw_text": sections["all"],
                    "source": "wiki-rendered",
                    "damage_type": self._extract_damage_type(sections["all"]),
                    "targeting": self._extract_targeting(sections["all"]),
                    "on_hit": self._extract_on_hit(sections["all"]),
                    "is_channeled": self._extract_channeled(sections["all"]),
                    "is_conditional": self._is_conditional(sections["all"]),
                    "is_stack_scaling": self._is_stack_scaling(sections["all"]),
                    "range_units": self._extract_range(sections["all"]),
                }
            return breakdown

        for key, text in sections.items():
            ratios = self._extract_ratio_values(text)
            components = self._extract_scaling_components(text)
            breakdown[key] = {
                "name": key.upper(),
                "ad_ratio": float(ratios.get("ad_ratio", 0.0)),
                "ap_ratio": float(ratios.get("ap_ratio", 0.0)),
                "attack_speed_ratio": float(ratios.get("attack_speed_ratio", 0.0)),
                "heal_ratio": float(ratios.get("heal_ratio", 0.0)),
                "hp_ratio": 0.0,
                "bonus_hp_ratio": 0.0,
                "armor_ratio": 0.0,
                "mr_ratio": 0.0,
                "scaling_components": components,
                "scaling_by_application": self._group_components_by_application(components),
                "base_damage": [],
                "cooldown": [1.0],
                "cost": [],
                "resource": "",
                "raw_text": text,
                "source": "wiki-rendered",
                "damage_type": self._extract_damage_type(text),
                "targeting": self._extract_targeting(text),
                "on_hit": self._extract_on_hit(text),
                "is_channeled": self._extract_channeled(text),
                "is_conditional": self._is_conditional(text),
                "is_stack_scaling": self._is_stack_scaling(text),
                "range_units": self._extract_range(text),
            }

        for key in ("q", "w", "e", "r"):
            breakdown.setdefault(
                key,
                {
                    "name": key.upper(),
                    "ad_ratio": 0.0,
                    "ap_ratio": 0.0,
                    "attack_speed_ratio": 0.0,
                    "heal_ratio": 0.0,
                    "hp_ratio": 0.0,
                    "bonus_hp_ratio": 0.0,
                    "armor_ratio": 0.0,
                    "mr_ratio": 0.0,
                    "scaling_components": [],
                    "scaling_by_application": self._group_components_by_application([]),
                    "base_damage": [],
                    "cooldown": [1.0],
                    "cost": [],
                    "resource": "",
                    "raw_text": "",
                    "source": "wiki-rendered",
                    "damage_type": "unknown",
                    "targeting": "unknown",
                    "on_hit": False,
                    "is_channeled": False,
                    "is_conditional": False,
                    "is_stack_scaling": False,
                    "range_units": 0.0,
                },
            )
        return breakdown


    def _validate_passive_breakdown(self, champion: str, passive: Dict[str, Any]) -> None:
        # Passive is optional and does not require cooldown/cost, but if present with damage/heal text,
        # it should expose either a ratio signal or base values from Riot payload fields.
        text = " ".join(
            [
                str(passive.get("name", "") or ""),
                str(passive.get("resource", "") or ""),
                str(passive.get("raw_text", "") or ""),
                str(passive.get("source", "") or ""),
            ]
        ).lower()
        has_damage_semantics = any(tok in text for tok in ("damage", "heal", "shield", "scal"))
        has_signal = any(
            float(passive.get(metric, 0.0) or 0.0) > 0
            for metric in (
                "ad_ratio",
                "ap_ratio",
                "attack_speed_ratio",
                "heal_ratio",
                "hp_ratio",
                "bonus_hp_ratio",
                "armor_ratio",
                "mr_ratio",
            )
        )
        base_damage = self._coerce_float_list(passive.get("base_damage", []))
        if has_damage_semantics and not has_signal and (not base_damage or max(base_damage) <= 0):
            details = self._ability_diagnostics(passive)
            raise DataSourceError(
                f"Passive has scaling/damage semantics but no usable Riot scaling values for {champion} ({details})"
            )

    def _ability_diagnostics(self, block: Dict[str, Any]) -> str:
        ratios = {
            metric: float(block.get(metric, 0.0) or 0.0)
            for metric in (
                "ad_ratio",
                "ap_ratio",
                "attack_speed_ratio",
                "ms_ratio",
                "ms_ratio",
                "heal_ratio",
                "hp_ratio",
                "bonus_hp_ratio",
                "armor_ratio",
                "mr_ratio",
            )
        }
        base_damage = self._coerce_float_list(block.get("base_damage", []))
        cooldown = self._coerce_float_list(block.get("cooldown", []))
        nonzero_ratios = {k: v for k, v in ratios.items() if v > 0}
        return (
            f"cooldown_count={len(cooldown)}, cooldown_max={(max(cooldown) if cooldown else 0):.3g}, "
            f"base_damage_count={len(base_damage)}, base_damage_max={(max(base_damage) if base_damage else 0):.3g}, "
            f"nonzero_ratios={nonzero_ratios}"
        )

    def _merge_breakdowns(self, primary: Dict[str, Dict[str, Any]], secondary: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {k: dict(v) for k, v in primary.items()}
        for key, sec_values in secondary.items():
            if key not in merged:
                merged[key] = dict(sec_values)
                continue
            for metric in (
                "ad_ratio",
                "ap_ratio",
                "attack_speed_ratio",
                "ms_ratio",
                "heal_ratio",
                "hp_ratio",
                "bonus_hp_ratio",
                "armor_ratio",
                "mr_ratio",
            ):
                cur_val = float(merged[key].get(metric, 0.0) or 0.0)
                sec_val = float(sec_values.get(metric, 0.0) or 0.0)
                if sec_val > cur_val:
                    merged[key][metric] = sec_val
            if not merged[key].get("base_damage") and sec_values.get("base_damage"):
                merged[key]["base_damage"] = list(sec_values.get("base_damage", []))
            cur_cd = self._coerce_float_list(merged[key].get("cooldown", []))
            sec_cd = self._coerce_float_list(sec_values.get("cooldown", []))
            if (not cur_cd or cur_cd == [1.0]) and sec_cd:
                merged[key]["cooldown"] = list(sec_values.get("cooldown", []))
            if not merged[key].get("cost") and sec_values.get("cost"):
                merged[key]["cost"] = list(sec_values.get("cost", []))
            # damage_type: prefer specific over weak/absent values
            _WEAK = {"", "unknown", "none"}
            cur_dmg = merged[key].get("damage_type") or ""
            sec_dmg = sec_values.get("damage_type") or ""
            if cur_dmg in _WEAK and sec_dmg not in _WEAK:
                merged[key]["damage_type"] = sec_dmg
            # targeting: prefer specific over "unknown"
            if (merged[key].get("targeting") or "unknown") == "unknown":
                sec_targeting = sec_values.get("targeting") or "unknown"
                if sec_targeting != "unknown":
                    merged[key]["targeting"] = sec_targeting
            # boolean enrichment fields: OR-merge
            for _bool_field in ("on_hit", "is_channeled", "is_conditional", "is_stack_scaling"):
                if sec_values.get(_bool_field):
                    merged[key][_bool_field] = True
            # range_units: take maximum
            _sec_range = float(sec_values.get("range_units") or 0.0)
            if _sec_range > float(merged[key].get("range_units") or 0.0):
                merged[key]["range_units"] = _sec_range
            merged[key]["scaling_components"] = self._merge_scaling_components(
                merged[key].get("scaling_components", []),
                sec_values.get("scaling_components", []),
            )
            merged[key]["scaling_by_application"] = self._group_components_by_application(
                merged[key].get("scaling_components", [])
            )
            merged[key].setdefault("source", "wiki-merged")
        return merged

    def _missing_signal_keys(self, breakdown: Dict[str, Dict[str, Any]]) -> List[str]:
        missing: List[str] = []
        for key in ("q", "w", "e", "r"):
            if not self._has_usable_signal(breakdown.get(key, {})):
                missing.append(key)
        return missing

    def _has_usable_signal(self, block: Dict[str, Any]) -> bool:
        if not isinstance(block, dict):
            return False
        has_ratio = any(
            float(block.get(metric, 0.0) or 0.0) > 0
            for metric in (
                "ad_ratio",
                "ap_ratio",
                "attack_speed_ratio",
                "heal_ratio",
                "hp_ratio",
                "bonus_hp_ratio",
                "armor_ratio",
                "mr_ratio",
            )
        )
        has_components = any(
            isinstance(x, dict) and float(x.get("ratio", 0.0) or 0.0) > 0
            for x in (block.get("scaling_components", []) or [])
        )
        base_damage = self._coerce_float_list(block.get("base_damage", []))
        has_base_damage = bool(base_damage) and max(base_damage) > 0
        return has_ratio or has_base_damage or has_components

    def _extract_with_ai_fallback(
        self,
        champion: str,
        sections: Dict[str, str],
        missing_keys: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        if not sections:
            return {}

        prompt_sections: Dict[str, str] = {}
        for key in missing_keys:
            text = str(sections.get(key, "") or "").strip()
            if text:
                prompt_sections[key] = text[:4000]
        if not prompt_sections:
            for key in ("q", "w", "e", "r", "passive"):
                text = str(sections.get(key, "") or "").strip()
                if text:
                    prompt_sections[key] = text[:4000]
        if not prompt_sections:
            return {}

        instruction = {
            "champion": champion,
            "task": "Extract ONLY numeric ability scaling values from wiki text."
                    " Use 0 when unknown. Do not invent values.",
            "required_keys": ["ad_ratio", "ap_ratio", "attack_speed_ratio", "heal_ratio", "hp_ratio", "bonus_hp_ratio", "armor_ratio", "mr_ratio", "base_damage", "cooldown"],
            "abilities": prompt_sections,
            "output_schema": {
                "q": {"ad_ratio": 0.0, "ap_ratio": 0.0, "attack_speed_ratio": 0.0, "heal_ratio": 0.0, "hp_ratio": 0.0, "bonus_hp_ratio": 0.0, "armor_ratio": 0.0, "mr_ratio": 0.0, "base_damage": [], "cooldown": []},
                "w": {"ad_ratio": 0.0, "ap_ratio": 0.0, "attack_speed_ratio": 0.0, "heal_ratio": 0.0, "hp_ratio": 0.0, "bonus_hp_ratio": 0.0, "armor_ratio": 0.0, "mr_ratio": 0.0, "base_damage": [], "cooldown": []},
                "e": {"ad_ratio": 0.0, "ap_ratio": 0.0, "attack_speed_ratio": 0.0, "heal_ratio": 0.0, "hp_ratio": 0.0, "bonus_hp_ratio": 0.0, "armor_ratio": 0.0, "mr_ratio": 0.0, "base_damage": [], "cooldown": []},
                "r": {"ad_ratio": 0.0, "ap_ratio": 0.0, "attack_speed_ratio": 0.0, "heal_ratio": 0.0, "hp_ratio": 0.0, "bonus_hp_ratio": 0.0, "armor_ratio": 0.0, "mr_ratio": 0.0, "base_damage": [], "cooldown": []},
                "passive": {"ad_ratio": 0.0, "ap_ratio": 0.0, "attack_speed_ratio": 0.0, "heal_ratio": 0.0, "hp_ratio": 0.0, "bonus_hp_ratio": 0.0, "armor_ratio": 0.0, "mr_ratio": 0.0, "base_damage": [], "cooldown": []},
            },
        }

        payload = {
            "model": "mistral",
            "prompt": json.dumps(instruction, ensure_ascii=True),
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_predict": 900},
        }

        try:
            res = requests.post("http://127.0.0.1:11434/api/generate", json=payload, timeout=min(self.timeout_seconds, 8.0))
            res.raise_for_status()
            raw = str(res.json().get("response", "") or "")
            if not raw:
                return {}
            parsed: Any
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
                if not match:
                    return {}
                parsed = json.loads(match.group(0))
            if not isinstance(parsed, dict):
                return {}
            return self._sanitize_ai_breakdown(parsed, sections)
        except Exception:
            return {}

    def _sanitize_ai_breakdown(self, payload: Dict[str, Any], sections: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
        raw_map = payload.get("ability_breakdown", payload)
        if not isinstance(raw_map, dict):
            return {}

        out: Dict[str, Dict[str, Any]] = {}
        for key, values in raw_map.items():
            norm_key = str(key or "").strip().lower()
            if norm_key not in {"q", "w", "e", "r", "passive"}:
                continue
            if not isinstance(values, dict):
                continue

            def _ratio(metric: str) -> float:
                return self._normalize_ratio_value(values.get(metric, 0.0))

            base_damage_raw = values.get("base_damage", [])
            cooldown_raw = values.get("cooldown", [])
            if isinstance(base_damage_raw, str):
                base_damage = self._parse_numeric_series(base_damage_raw)
            else:
                base_damage = self._coerce_float_list(base_damage_raw)
            if isinstance(cooldown_raw, str):
                cooldown = self._parse_numeric_series(cooldown_raw)
            else:
                cooldown = self._coerce_float_list(cooldown_raw)

            _sec_text = str(sections.get(norm_key, "") or "")
            out[norm_key] = {
                "name": "Passive" if norm_key == "passive" else norm_key.upper(),
                "ad_ratio": _ratio("ad_ratio"),
                "ap_ratio": _ratio("ap_ratio"),
                "attack_speed_ratio": _ratio("attack_speed_ratio"),
                "ms_ratio": _ratio("ms_ratio"),
                "heal_ratio": _ratio("heal_ratio"),
                "hp_ratio": _ratio("hp_ratio"),
                "bonus_hp_ratio": _ratio("bonus_hp_ratio"),
                "armor_ratio": _ratio("armor_ratio"),
                "mr_ratio": _ratio("mr_ratio"),
                "scaling_components": self._extract_scaling_components(_sec_text),
                "base_damage": base_damage,
                "cooldown": cooldown,
                "cost": [],
                "resource": "",
                "raw_text": _sec_text,
                "source": "wiki-ai-fallback",
                "damage_type": self._extract_damage_type(_sec_text),
                "targeting": self._extract_targeting(_sec_text),
                "on_hit": self._extract_on_hit(_sec_text),
                "is_channeled": self._extract_channeled(_sec_text),
                "is_conditional": self._is_conditional(_sec_text),
                "is_stack_scaling": self._is_stack_scaling(_sec_text),
                "range_units": self._extract_range(_sec_text),
            }
            out[norm_key]["scaling_by_application"] = self._group_components_by_application(out[norm_key].get("scaling_components", []))
        return out

    @staticmethod
    def _normalize_ratio_value(value: Any) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.0
        if math.isnan(parsed) or math.isinf(parsed):
            return 0.0
        if parsed < 0:
            return 0.0
        # LLM output often uses percentages (e.g. 80 instead of 0.8).
        if 1.5 < parsed <= 300.0:
            return parsed / 100.0
        return parsed

    def get_saved_overrides(self, champion: str) -> Dict[str, Dict[str, float]]:
        normalized = champion.strip().lower().replace(" ", "_")
        cache_key = f"manual_scaling_override_{normalized}"
        cached = self.cache.get(cache_key)
        if not cached or not isinstance(cached, dict):
            return {}
        return cached

    def save_overrides(self, champion: str, overrides: Dict[str, Dict[str, float]]) -> None:
        normalized = champion.strip().lower().replace(" ", "_")
        cache_key = f"manual_scaling_override_{normalized}"
        self.cache.set(cache_key, overrides)

    def clear_cache(self) -> int:
        return self.cache.clear(prefix="wiki_") + self.cache.clear(prefix="manual_scaling_override_")

    def _get_patch_fingerprint(self, force_refresh: bool = False) -> str:
        cache_key = "wiki_scaling_patch_fingerprint"
        if not force_refresh:
            cached = self.cache.get(cache_key, max_age_seconds=60 * 60 * 6)
            if cached and cached.get("fingerprint"):
                return str(cached.get("fingerprint"))

        fingerprint = "wiki-live"
        try:
            revs = [
                self._fetch_page_revision_timestamp("Module:ChampionData/data"),
                self._fetch_page_revision_timestamp("Module:ItemData/data"),
            ]
            parts = [x for x in revs if x]
            if parts:
                digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
                fingerprint = f"wiki-rev-{digest}"
        except Exception:
            fingerprint = "wiki-live"

        self.cache.set(cache_key, {"fingerprint": fingerprint})
        return fingerprint

    def _fetch_page_revision_timestamp(self, title: str) -> str:
        params = {
            "action": "query",
            "format": "json",
            "prop": "revisions",
            "rvprop": "timestamp",
            "rvlimit": 1,
            "titles": title,
        }
        res = _http_get(self.BASE_API, params=params, timeout=self.timeout_seconds)
        res.raise_for_status()
        pages = res.json().get("query", {}).get("pages", {})
        for page in pages.values():
            revs = page.get("revisions", [])
            if revs:
                return str(revs[0].get("timestamp", "") or "")
        return ""

    def _get_rendered_text(self, champion: str) -> str:
        # First try the Ability_Details subpage — it has much cleaner ability section structure
        for page in (f"{champion}/Ability_Details", champion):
            params = {
                "action": "parse",
                "format": "json",
                "page": page,
                "prop": "text",
            }
            try:
                res = _http_get(self.BASE_API, params=params, timeout=self.timeout_seconds)
                if res.status_code == 404:
                    continue
                res.raise_for_status()
                payload = res.json()
                parse = payload.get("parse", {})
                html = parse.get("text", {}).get("*", "")
                if not html:
                    continue
                text = self._html_to_text(html)
                result = self._strip_irrelevant_global_chunks(text)
                if result:
                    return result
            except Exception:
                continue
        raise DataSourceError(f"Wiki page not found for champion: {champion}")

    def _html_to_text(self, html: str) -> str:
        """Convert wiki HTML to plain text, preserving scaling data in title attributes.

        The League wiki stores ratio values like '60% bonus AD' inside
        ``<abbr title="...">`` or ``<span title="...">`` attributes on tooltip
        wrappers.  A bare ``re.sub`` tag-strip discards all of those strings,
        causing the downstream ratio regexes to miss legitimate scaling values.
        BeautifulSoup extracts both visible text and title attribute text so
        patterns such as AD_PATTERN and AP_PATTERN can reliably find them.
        """
        soup = BeautifulSoup(html, "html.parser")
        # Collect title attribute text from tooltip wrappers.
        # e.g. <abbr title="Physical damage: 80/120/160 (+ 60% bonus AD)">80/120/160</abbr>
        title_texts: List[str] = []
        for tag in soup.find_all(["abbr", "span"], title=True):
            t = str(tag.get("title", "")).strip()
            if t:
                title_texts.append(t)
        visible = soup.get_text(separator=" ")
        if title_texts:
            visible = visible + " " + " ".join(title_texts)
        return " ".join(visible.split())

    def _strip_irrelevant_global_chunks(self, text: str) -> str:
        lowered = text.lower()
        cutoffs = [lowered.find(marker) for marker in self.GLOBAL_CUTOFF_MARKERS if lowered.find(marker) != -1]
        if cutoffs:
            text = text[: min(cutoffs)]
        return text

    def _extract_sections(self, text: str) -> Dict[str, str]:
        bounds: Dict[str, int] = {}

        # Use structured passive patterns (Innate: / Passive -) before falling back to bare word
        for pat in self.PASSIVE_PATTERNS:
            m = pat.search(text)
            if m:
                bounds["passive"] = m.start()
                break
        if "passive" not in bounds:
            passive_idx = text.lower().find("passive")
            if passive_idx != -1:
                bounds["passive"] = passive_idx

        for key, pattern in self.KEY_PATTERNS.items():
            match = pattern.search(text)
            if match:
                bounds[key] = match.start()

        if not bounds:
            return {"all": text}

        ordered = sorted(bounds.items(), key=lambda x: x[1])
        out: Dict[str, str] = {}
        for idx, (name, start) in enumerate(ordered):
            end = ordered[idx + 1][1] if idx + 1 < len(ordered) else len(text)
            out[name] = self._strip_irrelevant_section_chunks(text[start:end])
        return out

    def _strip_irrelevant_section_chunks(self, text: str) -> str:
        lowered = text.lower()
        cutoffs = [lowered.find(marker) for marker in self.SECTION_CUTOFF_MARKERS if lowered.find(marker) != -1]
        if cutoffs:
            text = text[: min(cutoffs)]
        text = re.sub(r"\bedit\b", " ", text, flags=re.IGNORECASE)
        return " ".join(text.split())

    def _extract_ratio_values(self, text: str) -> Dict[str, float]:
        as_labeled = self._extract_labeled_template_series_pct(text, r"bonus\s+attack\s+speed|attack\s+speed")
        ms_labeled = self._extract_labeled_template_series_pct(text, r"bonus\s+(?:movement|move)\s+speed|(?:movement|move)\s+speed")

        cleaned = self._normalize_wiki_markup_text(text)
        ap_values = [float(v) for v in self.AP_PATTERN.findall(cleaned)]
        ad_values = [float(v) for v in self.AD_PATTERN.findall(cleaned)]
        as_values = [float(v) for v in self.AS_PATTERN.findall(cleaned)]
        ms_values = [float(v) for v in self.MS_PATTERN.findall(cleaned)]
        heal_values = [float(v) for v in self.HEAL_PATTERN.findall(cleaned)]

        # Support wiki value-box formats where stat label appears before the percentage list.
        as_values.extend(self._extract_keyword_series_pct(cleaned, r"attack\s*speed"))
        ms_values.extend(self._extract_keyword_series_pct(cleaned, r"(?:movement|move)\s*speed"))
        as_values.extend(as_labeled)
        ms_values.extend(ms_labeled)

        return {
            "ad_ratio": self._normalize_pct(ad_values, fallback=0.0),
            "ap_ratio": self._normalize_pct(ap_values, fallback=0.0),
            "attack_speed_ratio": self._normalize_pct(as_values, fallback=0.0),
            "ms_ratio": self._normalize_pct(ms_values, fallback=0.0),
            "heal_ratio": self._normalize_pct(heal_values, fallback=0.0),
        }

    def _extract_keyword_series_pct(self, text: str, keyword_pattern: str) -> List[float]:
        series_re = re.compile(
            rf"{keyword_pattern}[^\d%]{{0,48}}((?:\d+(?:\.\d+)?\s*(?:/|to)\s*)*\d+(?:\.\d+)?)\s*%",
            re.IGNORECASE,
        )
        out: List[float] = []
        for match in series_re.finditer(text):
            out.extend(self._parse_numeric_series(match.group(1)))
        return out

    def _extract_labeled_template_series_pct(self, text: str, label_pattern: str) -> List[float]:
        out: List[float] = []
        wrapped_re = re.compile(
            rf"(?:st\|)?(?:[^\n]*?)({label_pattern})(?:[^\n]{{0,80}}?)\{{\{{\s*ap\s*\|\s*([^{{}}]+?)\s*\}}\}}\s*%",
            re.IGNORECASE,
        )
        plain_re = re.compile(
            rf"({label_pattern})[^\d%]{{0,40}}((?:\d+(?:\.\d+)?\s*(?:/|to)\s*)*\d+(?:\.\d+)?)\s*%",
            re.IGNORECASE,
        )
        for m in wrapped_re.finditer(str(text or "")):
            out.extend(self._parse_numeric_series(m.group(2)))
        for m in plain_re.finditer(str(text or "")):
            out.extend(self._parse_numeric_series(m.group(2)))
        return out

    def _extract_scaling_components(self, text: str) -> List[Dict[str, Any]]:
        if not text:
            return []

        text = self._normalize_wiki_markup_text(text)

        pattern = re.compile(
            r"(\d+(?:\.\d+)?)\s*%\s*((?:bonus|total)\s+)?(AD|bAD|tAD|attack\s*damage|AP|ability\s*power|attack\s*speed|movement\s*speed|move\s*speed|armor|magic\s*resist|mr|bonus\s*health|max\s*health|health)",
            re.IGNORECASE,
        )
        lower = text.lower()
        out: List[Dict[str, Any]] = []
        for match in pattern.finditer(text):
            raw_value = float(match.group(1))
            ratio = self._normalize_ratio_value(raw_value)
            if ratio <= 0:
                continue

            modifier_token = str(match.group(2) or "").strip().lower()
            stat_token = str(match.group(3) or "").strip().lower()
            if stat_token == "bad":
                stat = "ad"
                modifier = "bonus"
            elif stat_token == "tad":
                stat = "ad"
                modifier = "total"
            elif "attack damage" in stat_token or stat_token == "ad":
                stat = "ad"
                modifier = "bonus" if modifier_token.startswith("bonus") else "total" if modifier_token.startswith("total") else "unspecified"
            elif "ability" in stat_token or stat_token == "ap":
                stat = "ap"
                modifier = "total"
            elif "attack speed" in stat_token:
                stat = "attack_speed"
                modifier = "total"
            elif "movement speed" in stat_token or "move speed" in stat_token:
                stat = "move_speed"
                modifier = "total"
            elif "bonus health" in stat_token:
                stat = "bonus_hp"
                modifier = "total"
            elif "max health" in stat_token or stat_token == "health":
                stat = "bonus_hp" if modifier_token.startswith("bonus") else "hp"
                modifier = "total"
            elif "armor" in stat_token:
                stat = "armor"
                modifier = "total"
            elif "magic resist" in stat_token or stat_token == "mr":
                stat = "mr"
                modifier = "total"
            else:
                continue

            window_start = max(0, match.start() - 72)
            window_end = min(len(lower), match.end() + 72)
            context = lower[window_start:window_end]

            near_before = lower[max(0, match.start() - 28):match.start()]
            near_after = lower[match.end():min(len(lower), match.end() + 28)]

            def _nearest_distance(terms: Tuple[str, ...]) -> Optional[int]:
                best: Optional[int] = None
                for term in terms:
                    before_idx = near_before.rfind(term)
                    if before_idx != -1:
                        dist = len(near_before) - before_idx
                        best = dist if best is None else min(best, dist)
                    after_idx = near_after.find(term)
                    if after_idx != -1:
                        dist = after_idx + 1
                        best = dist if best is None else min(best, dist)
                return best

            candidates: List[Tuple[int, str]] = []
            if re.search(r"(?:heal|heals|healing|restore|restores|restoring)\s*(?:for\s*)?$", near_before):
                application = "heal"
            elif re.search(r"(?:shield|shielding)\s*(?:for\s*)?$", near_before):
                application = "shield"
            else:
                for app, terms in (
                    ("heal", ("heal", "restor", "regen")),
                    ("shield", ("shield",)),
                    ("damage", ("damage", "deals", "deal ")),
                    ("dot", ("per second", "over ", "burn", "bleed", "poison")),
                    ("buff_debuff", ("grant", "gains", "slow", "stun", "increase", "reduce", "buff", "debuff")),
                ):
                    distance = _nearest_distance(terms)
                    if distance is not None:
                        candidates.append((distance, app))

                application = "damage"
                if candidates:
                    candidates.sort(key=lambda x: x[0])
                    application = candidates[0][1]
                elif any(tok in context for tok in ("per second", "over ", "burn", "bleed", "poison")):
                    application = "dot"

            out.append(
                {
                    "application": application,
                    "stat": stat,
                    "modifier": modifier,
                    "ratio": ratio,
                    "evidence": match.group(0),
                }
            )

        return self._merge_scaling_components([], out)

    def _merge_scaling_components(self, primary: List[Dict[str, Any]], secondary: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen = set()
        for values in (primary or []), (secondary or []):
            for comp in values:
                if not isinstance(comp, dict):
                    continue
                key = (
                    str(comp.get("application", "")),
                    str(comp.get("stat", "")),
                    str(comp.get("modifier", "")),
                    round(float(comp.get("ratio", 0.0) or 0.0), 6),
                )
                if key in seen or key[3] <= 0:
                    continue
                seen.add(key)
                merged.append(
                    {
                        "application": key[0],
                        "stat": key[1],
                        "modifier": key[2],
                        "ratio": key[3],
                        "evidence": str(comp.get("evidence", "") or ""),
                    }
                )
        return merged

    def _group_components_by_application(self, components: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {
            "damage": [],
            "heal": [],
            "shield": [],
            "dot": [],
            "buff_debuff": [],
        }
        for comp in components or []:
            if not isinstance(comp, dict):
                continue
            app = str(comp.get("application", "damage") or "damage")
            if app not in grouped:
                app = "damage"
            grouped[app].append(dict(comp))
        return grouped

    # ------------------------------------------------------------------
    # Ability context enrichment helpers (Phase 2)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_damage_type(text: str) -> str:
        """Classify the primary damage type from ability description text."""
        if not text:
            return "unknown"
        lower = text.lower()
        has_physical = bool(re.search(
            r"physical\s+damage|deal[s]?\s+(?:\S+\s+){0,4}physical", lower
        ))
        has_magic = bool(re.search(
            r"magic(?:al)?\s+damage|deal[s]?\s+(?:\S+\s+){0,4}magic(?:al)?", lower
        ))
        has_true = bool(re.search(r"true\s+damage", lower))
        n_types = int(has_physical) + int(has_magic) + int(has_true)
        if n_types == 0:
            return "none"
        if n_types > 1:
            return "mixed"
        if has_physical:
            return "physical"
        if has_magic:
            return "magic"
        return "true"

    @staticmethod
    def _extract_targeting(text: str) -> str:
        """Classify the targeting type of an ability from its description text."""
        if not text:
            return "unknown"
        lower = text.lower()
        _AOE = (
            "all enemies", "nearby enemies", "in an area", "in a cone", "cone",
            "surrounding", "nearby champions", "around him", "all nearby",
            "explosion", "in a circle",
        )
        _MULTI = (
            "up to 3 enemies", "up to 4", "up to 5",
            "multiple enemies", "chain", "bounce",
        )
        _DIRECTIONAL = (
            "in a line", "direction", "bolt", "beam", "projectile", "skillshot",
        )
        if any(k in lower for k in _AOE):
            return "aoe"
        if any(k in lower for k in _MULTI):
            return "multi_target"
        if any(k in lower for k in _DIRECTIONAL):
            return "directional"
        if "single" in lower or ("target" in lower and "enemies" not in lower and "nearest" not in lower):
            return "single_target"
        return "unknown"

    @staticmethod
    def _extract_on_hit(text: str) -> bool:
        """Return True if the ability applies on-hit effects."""
        if not text:
            return False
        return bool(re.search(r"on.hit|applies?\s+on.hit|on\s+hit", text, re.IGNORECASE))

    @staticmethod
    def _extract_channeled(text: str) -> bool:
        """Return True if the ability channels or has a significant wind-up."""
        if not text:
            return False
        return bool(re.search(
            r"\bchannels?\b|\bchanneled?\b|\bwind.?up\b|\bcasting\s+time\b",
            text, re.IGNORECASE,
        ))

    @staticmethod
    def _is_conditional(text: str) -> bool:
        """Return True if the ability scales conditionally (thresholds, stacks, states)."""
        if not text:
            return False
        return bool(re.search(
            r"\bif\b.{0,50}(?:below|above|at\s+least|less\s+than|more\s+than|missing)"
            r"|\bwhen\b.{0,30}(?:below|above|less\s+than|more\s+than|missing|near|within)"
            r"|\bbased\s+on\b|stacks?\s+up\s+to",
            text, re.IGNORECASE,
        ))

    @staticmethod
    def _is_stack_scaling(text: str) -> bool:
        """Return True if the ability scales with stacks (Nasus, Sion, Kindred, etc.)."""
        if not text:
            return False
        return bool(re.search(
            r"(?:per|each)\s+stack"
            r"|permanently\s+(?:gains?|increases?)"
            r"|(?:gain|gains)\s+\d+.*(?:per\s+kill|per\s+stack)"
            r"|stacks\s+infinitely",
            text, re.IGNORECASE,
        ))

    @staticmethod
    def _extract_range(text: str) -> float:
        """Extract the primary ability range in units from text. Returns 0.0 if unknown."""
        if not text:
            return 0.0
        m = re.search(
            r"(\d{3,4})\s*(?:/\s*\d{3,4})*\s+(?:range|unit\s*range|radius|distance)",
            text, re.IGNORECASE,
        )
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return 0.0

    def _extract_ratio_values_from_tokens(self, text: str, block: Dict[str, Any]) -> Dict[str, float]:
        out = {
            "ad_ratio": 0.0,
            "ap_ratio": 0.0,
            "attack_speed_ratio": 0.0,
            "heal_ratio": 0.0,
        }
        lower = text.lower()
        for match in self.TOKEN_PATTERN.finditer(text):
            raw = match.group(1)
            token = self._normalize_token_name(raw)
            if not token:
                continue
            values = self._resolve_token_series(token, block)
            if not values:
                continue
            pct_value = self._normalize_pct(values, fallback=0.0)
            window_start = max(0, match.start() - 32)
            window_end = min(len(lower), match.end() + 32)
            window = lower[window_start:window_end]

            if "ap" in window or "ability power" in window:
                out["ap_ratio"] = max(out["ap_ratio"], pct_value)
            if "ad" in window or "attack damage" in window:
                out["ad_ratio"] = max(out["ad_ratio"], pct_value)
            if "attack speed" in window:
                out["attack_speed_ratio"] = max(out["attack_speed_ratio"], pct_value)
            if "health" in window or "heal" in window:
                out["heal_ratio"] = max(out["heal_ratio"], pct_value)
        return out

    def _extract_ratios_from_vars(self, vars_block: Any) -> Dict[str, float]:
        out = {
            "ad_ratio": 0.0,
            "ap_ratio": 0.0,
            "attack_speed_ratio": 0.0,
            "heal_ratio": 0.0,
        }
        if not isinstance(vars_block, list):
            return out

        for entry in vars_block:
            if not isinstance(entry, dict):
                continue
            link = str(entry.get("link", "") or "").lower()
            coeff = entry.get("coeff", [])
            coeff_values = self._coerce_float_list(coeff)
            if not coeff_values:
                continue
            value = max(coeff_values)
            if "spelldamage" in link or "abilitypower" in link or link == "ap":
                out["ap_ratio"] = max(out["ap_ratio"], value)
            elif "bonusattackdamage" in link or "attackdamage" in link or link in {"ad", "bonusad"}:
                out["ad_ratio"] = max(out["ad_ratio"], value)
            elif "attackspeed" in link:
                out["attack_speed_ratio"] = max(out["attack_speed_ratio"], value)
            elif "health" in link or "missinghealth" in link:
                out["heal_ratio"] = max(out["heal_ratio"], value)
        return out

    def _extract_damage_series(self, block: Dict[str, Any], labels: List[str], effects: List[str]) -> List[float]:
        for idx, label in enumerate(labels):
            if "damage" not in label.lower() and "heal" not in label.lower():
                continue
            if idx < len(effects):
                values = self._parse_numeric_series(effects[idx])
                if values:
                    return values

        effect_values = block.get("effect", [])
        if isinstance(effect_values, list):
            for entry in effect_values:
                if isinstance(entry, list):
                    values = self._coerce_float_list(entry)
                    if values and any(v != 0 for v in values):
                        return values

        effect_burn = block.get("effectBurn", [])
        if isinstance(effect_burn, list):
            for entry in effect_burn:
                if not entry:
                    continue
                values = self._parse_numeric_series(str(entry))
                if values and any(v != 0 for v in values):
                    return values
        return []

    def _extract_damage_series_from_tokens(self, text: str, block: Dict[str, Any]) -> List[float]:
        candidates: List[Tuple[int, List[float]]] = []
        lower = text.lower()
        for match in self.TOKEN_PATTERN.finditer(text):
            raw = match.group(1)
            token = self._normalize_token_name(raw)
            if not token:
                continue
            values = self._resolve_token_series(token, block)
            if not values or not any(v > 0 for v in values):
                continue
            window_start = max(0, match.start() - 48)
            window_end = min(len(lower), match.end() + 48)
            window = lower[window_start:window_end]
            score = 0
            if "damage" in window:
                score += 5
            if "physical" in window or "magic" in window or "true" in window:
                score += 2
            if "heal" in window:
                score += 1
            if "ap" in window or "ad" in window or "attack damage" in window or "ability power" in window:
                score -= 3
            candidates.append((score, values))

        if not candidates:
            return []
        candidates.sort(key=lambda x: (x[0], len(x[1]), max(x[1]) if x[1] else 0.0), reverse=True)
        return candidates[0][1]

    def _extract_damage_series_from_datavalues(self, block: Dict[str, Any]) -> List[float]:
        datavalues = block.get("datavalues", {})
        if not isinstance(datavalues, dict):
            return []

        keyword_candidates: List[List[float]] = []
        fallback_candidates: List[List[float]] = []
        for key, raw in datavalues.items():
            values = self._coerce_float_list(raw if isinstance(raw, list) else [raw])
            if not values or not any(v > 0 for v in values):
                continue
            fallback_candidates.append(values)
            lowered = str(key).lower()
            if any(tok in lowered for tok in ("damage", "dmg", "base", "impact", "burn", "hit", "heal")):
                keyword_candidates.append(values)

        target = keyword_candidates or fallback_candidates
        if not target:
            return []
        target.sort(key=lambda vals: (len(vals), max(vals) if vals else 0.0), reverse=True)
        return target[0]

    def _resolve_token_series(self, token: str, block: Dict[str, Any]) -> List[float]:
        token = token.strip().lower()
        if not token:
            return []

        if token.startswith("e") and token[1:].isdigit():
            idx = int(token[1:])
            effect = block.get("effect", [])
            if isinstance(effect, list) and 0 <= idx < len(effect):
                values = self._coerce_float_list(effect[idx])
                if values and any(v != 0 for v in values):
                    return values
            effect_burn = block.get("effectBurn", [])
            if isinstance(effect_burn, list) and 0 <= idx < len(effect_burn):
                values = self._parse_numeric_series(str(effect_burn[idx]))
                if values and any(v != 0 for v in values):
                    return values

        datavalues = block.get("datavalues", {})
        if isinstance(datavalues, dict):
            raw = datavalues.get(token)
            if raw is not None:
                values = self._coerce_float_list(raw if isinstance(raw, list) else [raw])
                if values and any(v != 0 for v in values):
                    return values

        return []

    @staticmethod
    def _normalize_token_name(token: str) -> str:
        cleaned = token.strip().lower()
        cleaned = re.sub(r"\s+", "", cleaned)
        cleaned = cleaned.split("|", 1)[0]
        cleaned = cleaned.split("*", 1)[0]
        cleaned = cleaned.split("/", 1)[0]
        cleaned = cleaned.split("+", 1)[0]
        cleaned = cleaned.split("-", 1)[0]
        cleaned = cleaned.strip("{}")
        return cleaned

    def _parse_numeric_series(self, value: str) -> List[float]:
        if not value:
            return []

        cleaned = str(value)
        cleaned = cleaned.replace("→", "->")
        cleaned = cleaned.replace("&nbsp;", " ")
        cleaned = re.sub(r"\s+", " ", cleaned)

        # Evaluate simple arithmetic segments first (e.g. 300/12, 10*1.7).
        expr_values: List[float] = []
        for match in re.finditer(r"\d+(?:\.\d+)?(?:\s*[\*\/]\s*\d+(?:\.\d+)?)+", cleaned):
            expr = match.group(0)
            if "/" in expr and "*" not in expr:
                # Treat slash-only sequences as level lists (e.g. 80/120/160), not division.
                continue
            evaluated = self._safe_eval_numeric_expr(expr)
            if evaluated is not None:
                expr_values.append(float(evaluated))

        temp = re.sub(r"\d+(?:\.\d+)?(?:\s*[\*\/]\s*\d+(?:\.\d+)?)+", " ", cleaned)
        nums = [float(x) for x in self.PCT_PATTERN.findall(temp)]
        nums.extend(expr_values)

        out: List[float] = []
        seen = set()
        for num in nums:
            rounded = round(float(num), 6)
            if rounded in seen:
                continue
            seen.add(rounded)
            out.append(float(num))
        return out

    def _coerce_float_list(self, values: Any) -> List[float]:
        if not isinstance(values, list):
            return []
        out: List[float] = []
        for value in values:
            try:
                out.append(float(value))
            except (TypeError, ValueError):
                continue
        return out


    @staticmethod
    def _normalize_pct(values: List[float], fallback: float) -> float:
        if not values:
            return fallback / 100.0 if fallback > 1 else fallback
        bounded = [v for v in values if 0 <= v <= 400]
        if not bounded:
            return fallback / 100.0 if fallback > 1 else fallback
        # Use max-rank / strongest listed value instead of averaging mixed rank rows.
        return max(bounded) / 100.0

    @staticmethod
    def _dedupe_preserve_order(values: List[str]) -> List[str]:
        out: List[str] = []
        seen = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            out.append(value)
        return out


# ---------------------------------------------------------------------------
# Ollama AI client
# ---------------------------------------------------------------------------

class OllamaClient:
    """
    Thin client for a locally-running Ollama instance (http://localhost:11434).

    Generates builds via LLM and supports an in-context feedback loop:
    good-rated builds (4-5 ★) teach the model what patterns to favour;
    bad-rated builds (1-2 ★) teach it what to avoid.
    """

    BASE = "http://localhost:11434"
    MAX_FEEDBACK_PER_CHAMP = 50

    def __init__(self, timeout_seconds: float = 60.0):
        self.timeout_seconds = timeout_seconds
        self.cache = LocalJsonCache()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_models(self) -> List[str]:
        try:
            res = _http_get(f"{self.BASE}/api/tags", timeout=5.0)
            res.raise_for_status()
            return [m["name"] for m in res.json().get("models", [])]
        except Exception:
            return []

    def is_available(self) -> bool:
        try:
            _http_get(f"{self.BASE}/api/tags", timeout=3.0).raise_for_status()
            return True
        except Exception:
            return False

    def generate_build(
        self,
        champion: str,
        enemy_profile: Dict[str, Any],
        weights: Dict[str, float],
        top_builds_context: List[Dict[str, Any]],
        model: str = "mistral",
    ) -> Dict[str, Any]:
        feedback_history = self._load_feedback(champion)
        prompt = self._build_prompt(champion, enemy_profile, weights, top_builds_context, feedback_history)

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.4, "num_predict": 600},
        }
        res = requests.post(f"{self.BASE}/api/generate", json=payload, timeout=self.timeout_seconds)
        res.raise_for_status()
        raw = res.json().get("response", "{}")
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract first JSON object from response
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            result = json.loads(match.group(0)) if match else {}

        candidates = result.get("candidates")
        if not isinstance(candidates, list):
            # Backward compatibility for older prompt templates.
            single_items = result.get("items", [])
            candidates = [
                {
                    "label": "balanced",
                    "items": single_items if isinstance(single_items, list) else [],
                    "rune_hints": result.get("rune_hints", []),
                    "reasoning": result.get("reasoning", ""),
                    "playstyle_note": result.get("playstyle_note", ""),
                    "innovation_thesis": result.get("innovation_thesis", ""),
                }
            ]

        normalized: List[Dict[str, Any]] = []
        for idx, candidate in enumerate(candidates[:6], 1):
            if not isinstance(candidate, dict):
                continue
            items = candidate.get("items", [])
            if not isinstance(items, list):
                items = []
            rune_hints = candidate.get("rune_hints", [])
            if not isinstance(rune_hints, list):
                rune_hints = []
            normalized.append(
                {
                    "label": str(candidate.get("label", f"candidate_{idx}"))[:32],
                    "items": [str(x) for x in items[:6]],
                    "rune_hints": [str(x) for x in rune_hints[:4]],
                    "reasoning": str(candidate.get("reasoning", ""))[:800],
                    "playstyle_note": str(candidate.get("playstyle_note", ""))[:280],
                    "innovation_thesis": str(candidate.get("innovation_thesis", ""))[:280],
                }
            )

        return {
            "model": model,
            "candidates": normalized,
        }

    def record_feedback(self, champion: str, build_items: List[str], rating: int, ai_reasoning: str) -> None:
        """Store a 1-5 star rating against a build for in-context learning."""
        history = self._load_feedback(champion)
        entry: Dict[str, Any] = {
            "items": build_items,
            "rating": max(1, min(5, int(rating))),
            "reasoning": ai_reasoning[:500],
            "timestamp": time.time(),
        }
        history.append(entry)
        # Keep only the most recent MAX_FEEDBACK_PER_CHAMP entries
        history = history[-self.MAX_FEEDBACK_PER_CHAMP :]
        self._save_feedback(champion, history)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_feedback(self, champion: str) -> List[Dict[str, Any]]:
        key = f"ai_feedback_{champion.strip().lower().replace(' ', '_')}"
        cached = self.cache.get(key)
        if isinstance(cached, list):
            return cached
        return []

    def _save_feedback(self, champion: str, history: List[Dict[str, Any]]) -> None:
        key = f"ai_feedback_{champion.strip().lower().replace(' ', '_')}"
        # Wrap in a dict so LocalJsonCache can store it as {"value": [...]}
        self.cache.set(key, history)  # type: ignore[arg-type]

    @staticmethod
    def _build_prompt(
        champion: str,
        enemy_profile: Dict[str, Any],
        weights: Dict[str, float],
        top_builds_context: List[Dict[str, Any]],
        feedback_history: List[Dict[str, Any]],
    ) -> str:
        good = [f for f in feedback_history if f.get("rating", 0) >= 4][-5:]
        bad = [f for f in feedback_history if f.get("rating", 0) <= 2][-5:]

        good_block = ""
        if good:
            examples = "; ".join(
                f"[{', '.join(f['items'])}] rated {f['rating']}★ — reasoning: {f['reasoning']}"
                for f in good
            )
            good_block = f"\nSuccessful build patterns (emulate the reasoning, not the exact items):\n{examples}\n"

        bad_block = ""
        if bad:
            examples = "; ".join(
                f"[{', '.join(f['items'])}] rated {f['rating']}★ — reasoning: {f['reasoning']}"
                for f in bad
            )
            bad_block = f"\nBuild patterns that were rated poorly (avoid similar approaches):\n{examples}\n"

        top_ref = ""
        if top_builds_context:
            lines = []
            for i, b in enumerate(top_builds_context[:3], 1):
                names = ", ".join(x.get("name", "") for x in b.get("order", []))
                score = b.get("weighted_score", 0)
                lines.append(f"  #{i}: [{names}] (score {score})")
            top_ref = "Mathematical top builds for reference (do NOT just copy these — give a creative alternative):\n" + "\n".join(lines) + "\n"

        damage_focus = weights.get("damage", 1.0)
        heal_focus = weights.get("healing", 0.0)
        tank_focus = weights.get("tankiness", 0.0)

        return f"""You are an expert League of Legends build theorist advising on {champion}.

Objective weights: damage={damage_focus}, healing={heal_focus}, tankiness={tank_focus}
Enemy profile: HP={enemy_profile.get('target_hp', 3200)}, Armor={enemy_profile.get('target_armor', 120)}, MR={enemy_profile.get('target_mr', 90)}

{top_ref}{good_block}{bad_block}
Generate 3 different 6-item build candidates for {champion} optimised for the objective weights above.
Rules:
- Candidate 1 should be stable/meta-adjacent.
- Candidate 2 should be balanced but not a direct copy of known meta.
- Candidate 3 should be high-risk/high-reward and intentionally innovative.
- Include rune hints for each candidate.
- Avoid duplicate items in a candidate.
Respond ONLY with valid JSON in this exact format:
{{
    "candidates": [
        {{
            "label": "stable",
            "items": ["Item Name 1", "Item Name 2", "Item Name 3", "Item Name 4", "Item Name 5", "Item Name 6"],
            "rune_hints": ["Keystone", "Primary Tree", "Secondary Tree"],
            "reasoning": "2-3 sentence explanation",
            "playstyle_note": "1 sentence gameplay note",
            "innovation_thesis": "short sentence describing what makes this candidate distinct"
        }}
    ]
}}"""


def merge_profile_with_scaling(profile: ChampionProfile, scaling: Optional[ChampionScaling]) -> ChampionProfile:
    if scaling is None:
        return profile

    active_spell_keys = ["q", "w", "e", "r"]
    represented = 0
    for key in active_spell_keys:
        block = scaling.ability_breakdown.get(key, {})
        has_ratios = any(
            (block.get(metric, 0.0) or 0.0) > 0
            for metric in (
                "ad_ratio",
                "ap_ratio",
                "attack_speed_ratio",
                "heal_ratio",
                "hp_ratio",
                "bonus_hp_ratio",
                "armor_ratio",
                "mr_ratio",
            )
        )
        has_base_damage = bool(block.get("base_damage"))
        if has_ratios or has_base_damage:
            represented += 1
    abilities_per_rotation = max(2.0, float(represented)) if represented else profile.abilities_per_rotation

    return ChampionProfile(
        champion_name=profile.champion_name,
        base_hp=profile.base_hp,
        base_armor=profile.base_armor,
        base_mr=profile.base_mr,
        abilities_per_rotation=abilities_per_rotation,
        average_combat_seconds=profile.average_combat_seconds,
        ability_breakdown=scaling.ability_breakdown,
        champion_tags=profile.champion_tags,
    )


def override_champion_scaling(scaling: ChampionScaling, overrides: Optional[Dict[str, Dict[str, float]]]) -> ChampionScaling:
    if not overrides:
        return scaling

    merged: Dict[str, Dict[str, float]] = {
        key: {
            "ad_ratio": float(values.get("ad_ratio", 0.0) or 0.0),
            "ap_ratio": float(values.get("ap_ratio", 0.0) or 0.0),
            "attack_speed_ratio": float(values.get("attack_speed_ratio", 0.0) or 0.0),
            "heal_ratio": float(values.get("heal_ratio", 0.0) or 0.0),
            "hp_ratio": float(values.get("hp_ratio", 0.0) or 0.0),
            "bonus_hp_ratio": float(values.get("bonus_hp_ratio", 0.0) or 0.0),
            "armor_ratio": float(values.get("armor_ratio", 0.0) or 0.0),
            "mr_ratio": float(values.get("mr_ratio", 0.0) or 0.0),
        }
        for key, values in scaling.ability_breakdown.items()
    }

    for key, values in overrides.items():
        if key not in merged:
            merged[key] = {
                "ad_ratio": 0.0,
                "ap_ratio": 0.0,
                "attack_speed_ratio": 0.0,
                "heal_ratio": 0.0,
            }
        for metric in (
            "ad_ratio",
            "ap_ratio",
            "attack_speed_ratio",
            "heal_ratio",
            "hp_ratio",
            "bonus_hp_ratio",
            "armor_ratio",
            "mr_ratio",
        ):
            if metric in values:
                merged[key][metric] = float(values[metric])

    return ChampionScaling(
        source=f"{scaling.source}+manual-override",
        ability_breakdown=merged,
        placeholder_used=scaling.placeholder_used,
        fallback_reasons=list(scaling.fallback_reasons),
    )
