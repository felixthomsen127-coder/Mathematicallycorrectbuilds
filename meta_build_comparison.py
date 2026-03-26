from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json
import logging
import re
import time

import requests


logger = logging.getLogger(__name__)

_META_BUILD_CACHE: Dict[str, Dict[str, Any]] = {}

_LOCAL_APPDATA = os.environ.get("LOCALAPPDATA")
_META_SNAPSHOT_ROOT = (
    Path(_LOCAL_APPDATA) / "mathematically_correct_builds" / "meta_snapshots"
    if _LOCAL_APPDATA
    else Path(__file__).resolve().parent / ".meta_snapshots"
)
_META_SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
_META_SNAPSHOT_MAX_AGE_SECONDS = 60 * 60 * 8

_CURATED_META_BUILDS: Dict[Tuple[str, str, str], List[List[str]]] = {
    (
        "briar",
        "jungle",
        "166",
    ): [
        [
            "Scorchclaw Pup",
            "The Collector",
            "Profane Hydra",
            "Mercury's Treads",
        ]
    ],
}


@dataclass(frozen=True)
class MetaBuildSample:
    source: str
    label: str
    item_names: List[str]
    win_rate: float = 0.0
    pick_rate: float = 0.0
    games: int = 0


@dataclass(frozen=True)
class MetaRunePageSample:
    source: str
    label: str
    primary_tree: str
    secondary_tree: str
    rune_names: List[str]
    win_rate: float = 0.0
    pick_rate: float = 0.0
    games: int = 0


_RUNE_ID_TO_NAME: Dict[int, str] = {
    8005: "Press the Attack",
    8008: "Lethal Tempo",
    8010: "Conqueror",
    8021: "Fleet Footwork",
    8112: "Electrocute",
    8124: "Predator",
    8128: "Dark Harvest",
    9923: "Hail of Blades",
    8214: "Summon Aery",
    8229: "Arcane Comet",
    8230: "Phase Rush",
    8437: "Grasp of the Undying",
    8439: "Aftershock",
    8465: "Guardian",
    8351: "Glacial Augment",
    8360: "Unsealed Spellbook",
    8369: "First Strike",
    9101: "Absorb Life",
    9111: "Triumph",
    9104: "Legend: Alacrity",
    9105: "Legend: Haste",
    9103: "Legend: Bloodline",
    8014: "Coup de Grace",
    8017: "Cut Down",
    8299: "Last Stand",
    8139: "Taste of Blood",
    8143: "Sudden Impact",
    8140: "Grisly Mementos",
    8126: "Cheap Shot",
    8135: "Treasure Hunter",
    8105: "Relentless Hunter",
    8106: "Ultimate Hunter",
    8134: "Ingenious Hunter",
    8136: "Zombie Ward",
    8120: "Ghost Poro",
    8138: "Eyeball Collection",
    8210: "Transcendence",
    8226: "Manaflow Band",
    8234: "Celerity",
    8233: "Absolute Focus",
    8236: "Gathering Storm",
    8237: "Scorch",
    8242: "Unflinching",
    8473: "Bone Plating",
    8446: "Demolish",
    8429: "Conditioning",
    8451: "Overgrowth",
    8444: "Second Wind",
    8345: "Biscuit Delivery",
    8347: "Cosmic Insight",
    8306: "Hextech Flashtraption",
    8313: "Triple Tonic",
    8316: "Jack of All Trades",
    8321: "Cash Back",
    8364: "Magical Footwear",
    8318: "Time Warp Tonic",
}

_TREE_ID_TO_NAME: Dict[int, str] = {
    8000: "Precision",
    8100: "Domination",
    8200: "Sorcery",
    8300: "Inspiration",
    8400: "Resolve",
}


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _jaccard(a: List[str], b: List[str]) -> float:
    if not a or not b:
        return 0.0
    sa = {_normalize_name(x) for x in a if x}
    sb = {_normalize_name(x) for x in b if x}
    if not sa or not sb:
        return 0.0
    inter = len(sa.intersection(sb))
    union = len(sa.union(sb))
    return inter / union if union else 0.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _tree_from_rune_id(rune_id: int) -> str:
    prefix = (rune_id // 100) * 100
    if prefix == 9900:
        return "Domination"
    return _TREE_ID_TO_NAME.get(prefix, "Unknown")


def _rune_name_from_id(rune_id: int) -> str:
    return _RUNE_ID_TO_NAME.get(rune_id, f"Rune {rune_id}")


def _dedupe_preserve_order(values: List[int]) -> List[int]:
    out: List[int] = []
    seen = set()
    for val in values:
        if val in seen:
            continue
        seen.add(val)
        out.append(val)
    return out


def _extract_structured_rune_pages_from_payload(payload: Any) -> List[Tuple[List[int], Optional[int], Optional[int]]]:
    pages: List[Tuple[List[int], Optional[int], Optional[int]]] = []

    def _visit(node: Any) -> None:
        if isinstance(node, dict):
            primary = _safe_int(node.get("primaryTreeId") or node.get("primaryTree") or node.get("primary_tree_id"))
            secondary = _safe_int(node.get("secondaryTreeId") or node.get("secondaryTree") or node.get("secondary_tree_id"))
            perks: List[int] = []

            raw_perks = node.get("perks") or node.get("perkIds") or node.get("runeIds") or node.get("runes")
            if isinstance(raw_perks, list):
                perks = [_safe_int(x) for x in raw_perks if _safe_int(x) > 0]
            elif isinstance(raw_perks, dict):
                for v in raw_perks.values():
                    if isinstance(v, list):
                        perks.extend(_safe_int(x) for x in v if _safe_int(x) > 0)

            if not perks:
                for key in ("keystone", "keystoneId", "primaryRuneId"):
                    rv = _safe_int(node.get(key))
                    if rv > 0:
                        perks.append(rv)
                for key in ("primaryRunes", "secondaryRunes", "subRunes", "minorRunes"):
                    arr = node.get(key)
                    if isinstance(arr, list):
                        perks.extend(_safe_int(x) for x in arr if _safe_int(x) > 0)

            perks = _dedupe_preserve_order([x for x in perks if 8000 <= x <= 9999])
            if len(perks) >= 4:
                pages.append((perks[:6], primary or None, secondary or None))

            for child in node.values():
                _visit(child)
        elif isinstance(node, list):
            for child in node:
                _visit(child)

    _visit(payload)
    return pages


def _extract_json_script_payloads(html: str) -> List[Any]:
    payloads: List[Any] = []

    def _extract_balanced_object(text: str, brace_start: int) -> Optional[str]:
        depth = 0
        in_string = False
        string_quote = ""
        escape = False
        out_chars: List[str] = []
        for idx in range(brace_start, len(text)):
            ch = text[idx]
            out_chars.append(ch)
            if in_string:
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == string_quote:
                    in_string = False
                continue

            if ch in ('\"', "'"):
                in_string = True
                string_quote = ch
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return "".join(out_chars)
        return None

    def _extract_assigned_json_objects(script_text: str) -> List[Any]:
        found: List[Any] = []
        markers = ["window.__NUXT__", "__NUXT__", "window.__NEXT_DATA__", "__NEXT_DATA__"]
        for marker in markers:
            start = 0
            while start < len(script_text):
                idx = script_text.find(marker, start)
                if idx < 0:
                    break
                eq_idx = script_text.find("=", idx + len(marker))
                if eq_idx < 0:
                    break
                brace_start = script_text.find("{", eq_idx + 1)
                if brace_start < 0:
                    break
                blob = _extract_balanced_object(script_text, brace_start)
                if blob:
                    try:
                        found.append(json.loads(blob))
                    except Exception:
                        pass
                    start = brace_start + max(1, len(blob))
                else:
                    start = brace_start + 1
        return found

    # Priority 1: Next.js __NEXT_DATA__ — the canonical source for champion-specific
    # build data. This script tag has type="application/json" and is always present.
    next_data_match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html, flags=re.DOTALL | re.IGNORECASE,
    )
    if not next_data_match:
        # Also try without id attribute (some versions use data-id)
        next_data_match = re.search(
            r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>',
            html, flags=re.DOTALL | re.IGNORECASE,
        )
    if next_data_match:
        try:
            payloads.append(json.loads(next_data_match.group(1).strip()))
        except Exception:
            pass

    # Priority 2: Other inline JSON/script blocks
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.DOTALL | re.IGNORECASE)
    for script in scripts:
        text = script.strip()
        if not text:
            continue

        if text.startswith("{") or text.startswith("["):
            try:
                payloads.append(json.loads(text))
            except Exception:
                pass

        for quoted in re.findall(r"JSON\.parse\(\s*(['\"])(.*?)\1\s*\)", text, flags=re.DOTALL):
            raw = quoted[1]
            try:
                payloads.append(json.loads(bytes(raw, "utf-8").decode("unicode_escape")))
            except Exception:
                continue

        for blob in re.findall(r"\{\s*\"props\"\s*:\s*\{.*?\}\s*\}", text, flags=re.DOTALL):
            try:
                payloads.append(json.loads(blob))
            except Exception:
                continue

        payloads.extend(_extract_assigned_json_objects(text))
    return payloads





def _normalize_lane(role: str) -> str:
    """Map champion role names to lolalytics lane slug."""
    mapping = {
        "jungle": "jungle", "jng": "jungle",
        "top": "top",
        "mid": "mid", "middle": "mid",
        "adc": "adc", "bot": "adc", "bottom": "adc",
        "support": "support", "sup": "support",
    }
    return mapping.get(_normalize_name(role), _normalize_name(role))




def _dedupe_samples(samples: List[MetaBuildSample]) -> List[MetaBuildSample]:
    out: List[MetaBuildSample] = []
    seen = set()
    for sample in samples:
        key = tuple(_normalize_name(x) for x in sample.item_names if x)
        if len(key) < 3:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(sample)
    return out


def _build_cache_key(champion: str, role: str, tier: str, region: str, patch: str) -> str:
    return "|".join((_normalize_name(champion), _normalize_name(role), _normalize_name(tier), _normalize_name(region), _normalize_name(patch)))


def _snapshot_file_path(cache_key: str) -> Path:
    token = hashlib.sha1(str(cache_key).encode("utf-8")).hexdigest()
    return _META_SNAPSHOT_ROOT / f"{token}.json"


def _serialize_meta_sample(sample: MetaBuildSample) -> Dict[str, Any]:
    return {
        "source": str(sample.source),
        "label": str(sample.label),
        "item_names": [str(x) for x in sample.item_names if str(x).strip()],
        "win_rate": float(sample.win_rate),
        "pick_rate": float(sample.pick_rate),
        "games": int(sample.games),
    }


def _deserialize_meta_sample(value: Any) -> Optional[MetaBuildSample]:
    if not isinstance(value, dict):
        return None
    names = [str(x).strip() for x in value.get("item_names", []) if str(x).strip()]
    if len(names) < 3:
        return None
    return MetaBuildSample(
        source=str(value.get("source", "u.gg") or "u.gg"),
        label=str(value.get("label", "snapshot") or "snapshot"),
        item_names=names[:6],
        win_rate=_safe_float(value.get("win_rate", 0.0), 0.0),
        pick_rate=_safe_float(value.get("pick_rate", 0.0), 0.0),
        games=_safe_int(value.get("games", 0)),
    )


def _write_meta_snapshot(
    cache_key: str,
    source: str,
    samples: List[MetaBuildSample],
    context: Optional[Dict[str, Any]] = None,
) -> None:
    rows = [_serialize_meta_sample(x) for x in samples[:6]]
    if not rows:
        return
    payload = {
        "saved_at": time.time(),
        "source": str(source or "u.gg"),
        "context": dict(context or {}),
        "samples": rows,
    }
    path = _snapshot_file_path(cache_key)
    try:
        path.write_text(json.dumps(payload, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
    except Exception as exc:
        logger.debug("Failed to write meta snapshot %s: %s", cache_key, exc)


def _read_meta_snapshot(
    cache_key: str,
    max_age_seconds: int = _META_SNAPSHOT_MAX_AGE_SECONDS,
) -> Optional[Dict[str, Any]]:
    path = _snapshot_file_path(cache_key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    saved_at = _safe_float(payload.get("saved_at", 0.0), 0.0)
    if saved_at <= 0:
        return None
    age_seconds = max(0, int(time.time() - saved_at))
    if max_age_seconds > 0 and age_seconds > int(max_age_seconds):
        return None

    rows = payload.get("samples", [])
    out: List[MetaBuildSample] = []
    if isinstance(rows, list):
        for row in rows:
            sample = _deserialize_meta_sample(row)
            if sample is not None:
                out.append(sample)
    if not out:
        return None

    return {
        "samples": out,
        "source": str(payload.get("source", "snapshot") or "snapshot"),
        "saved_at": saved_at,
        "age_seconds": age_seconds,
        "context": payload.get("context", {}),
    }


def prewarm_meta_snapshot(
    champion: str,
    role: str = "jungle",
    tier: str = "emerald_plus",
    region: str = "global",
    patch: str = "live",
    item_id_to_name: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    cache_key = _build_cache_key(champion, role, tier, region, patch)
    curated = _curated_samples(champion, role, patch)
    client = UggMetaClient()
    live_samples = client.fetch_top_builds(
        champion,
        role=role,
        tier=tier,
        region=region,
        patch=patch,
        item_id_to_name=item_id_to_name,
    )
    samples = _dedupe_samples(curated + live_samples) if curated else _dedupe_samples(list(live_samples))
    if not samples:
        return {
            "ok": False,
            "cache_key": cache_key,
            "source": "none",
            "sample_count": 0,
            "reason": str(client.last_error or "No live/cached rows available"),
        }

    source_label = str(live_samples[0].source) if live_samples else "u.gg"
    _META_BUILD_CACHE[cache_key] = {
        "samples": list(samples[:6]),
        "source": source_label,
        "saved_at": time.time(),
    }
    _write_meta_snapshot(
        cache_key=cache_key,
        source=source_label,
        samples=samples,
        context={"champion": champion, "role": role, "tier": tier, "region": region, "patch": patch},
    )
    return {
        "ok": True,
        "cache_key": cache_key,
        "source": source_label,
        "sample_count": len(samples),
        "used_curated": bool(curated and not live_samples),
    }


def _normalize_patch_key(value: str) -> str:
    return re.sub(r"[^0-9]", "", str(value or "").lower())


_FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures" / "meta_builds.json"


def _load_fixture_builds(champion: str, role: str, source: str = "u.gg") -> List[MetaBuildSample]:
    """Load curated builds from fixtures/meta_builds.json."""
    try:
        if not _FIXTURES_PATH.exists():
            return []
        with _FIXTURES_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        builds = data.get("builds", {})
        slug = f"{_normalize_name(champion)}/{_normalize_name(role)}"
        entry = builds.get(slug) or builds.get(_normalize_name(champion))
        if not entry:
            return []
        win_rate = float(entry.get("win_rate", 0.0) or 0.0)
        pick_rate = float(entry.get("pick_rate", 0.0) or 0.0)
        samples: List[MetaBuildSample] = []
        for idx, item_list in enumerate(entry.get("items", []), start=1):
            names = [str(x).strip() for x in item_list if str(x).strip()]
            if names:
                samples.append(MetaBuildSample(
                    source=source,
                    label=f"curated-{idx}",
                    item_names=names,
                    win_rate=win_rate if idx == 1 else 0.0,
                    pick_rate=pick_rate if idx == 1 else 0.0,
                ))
        return _dedupe_samples(samples)
    except Exception:
        return []


def _curated_samples(champion: str, role: str, patch: str, source: str = "u.gg") -> List[MetaBuildSample]:
    # Primary: fixture file (manually maintained, always reliable)
    fixture = _load_fixture_builds(champion, role, source=source)
    if fixture:
        return fixture
    # Fallback: hardcoded dict
    key = (_normalize_name(champion), _normalize_name(role), _normalize_patch_key(patch))
    rows = _CURATED_META_BUILDS.get(key, [])
    out: List[MetaBuildSample] = []
    for idx, item_names in enumerate(rows, start=1):
        out.append(MetaBuildSample(
            source=source,
            label=f"curated-{idx}",
            item_names=[str(x).strip() for x in item_names if str(x).strip()],
        ))
    return _dedupe_samples(out)


def _pick_best_meta_build(scored: List[Dict[str, Any]], mode: str) -> Optional[Dict[str, Any]]:
    if not scored:
        return None
    if mode == "power_delta":
        return max(scored, key=lambda x: _safe_float(x.get("score_delta_percent", 0.0), 0.0))
    if mode == "component_balance":
        return max(scored, key=lambda x: _safe_float(x.get("component_alignment", 0.0), 0.0))
    return max(scored, key=lambda x: _safe_float(x.get("similarity", 0.0), 0.0))


def _find_item_id_arrays(
    payload: Any,
    item_id_to_name: Dict[str, str],
) -> List[List[str]]:
    """Walk any parsed JSON value and return resolved item-name lists.

    Looks for lists of 3-8 integers (or integer-valued strings) all in the LoL
    item-ID range (1001-8999), or objects with ``itemId``/``item_id`` keys.
    Uses item_id_to_name to resolve IDs to English names.  Emits a build only
    if at least 3 IDs resolve to real names.  Bounded by a node-visit cap so
    large Next.js payloads don't stall.
    """
    results: List[List[str]] = []
    seen_keys: set = set()
    visited = [0]

    def _to_item_id(val: Any) -> Optional[int]:
        """Try to parse val as a LoL item ID integer."""
        try:
            i = int(val)
            if 1001 <= i <= 8999:
                return i
        except (TypeError, ValueError):
            pass
        return None

    def _try_add_names(int_vals: List[int]) -> None:
        names = [item_id_to_name[str(v)] for v in int_vals if str(v) in item_id_to_name]
        if len(names) >= 3:
            key = tuple(sorted(names))
            if key not in seen_keys:
                seen_keys.add(key)
                results.append(names[:6])

    def _visit(node: Any, depth: int) -> None:
        if visited[0] > 80_000 or len(results) >= 50:
            return
        visited[0] += 1
        if isinstance(node, list):
            # Case 1: list of integers or integer-strings (e.g. [6632, 3036, ...])
            if 3 <= len(node) <= 8:
                int_vals = []
                all_valid = True
                for x in node:
                    item_id = _to_item_id(x)
                    if item_id is not None:
                        int_vals.append(item_id)
                    else:
                        all_valid = False
                        break
                if all_valid and int_vals:
                    _try_add_names(int_vals)
            # Case 2: list of objects with itemId fields (e.g. [{"itemId": 6632}, ...])
            if 3 <= len(node) <= 8:
                obj_ids = []
                for x in node:
                    if isinstance(x, dict):
                        for field in ("itemId", "item_id", "id", "ItemId", "itemID"):
                            item_id = _to_item_id(x.get(field))
                            if item_id is not None:
                                obj_ids.append(item_id)
                                break
                if obj_ids:
                    _try_add_names(obj_ids)
            for child in node:
                _visit(child, depth + 1)
        elif isinstance(node, dict):
            # Case 3: dict with "items" or "itemIds" key containing an ID list
            for build_key in ("items", "item_ids", "itemIds", "item_list", "build",
                              "recommendedItems", "coreItems", "fullBuild", "finalItems"):
                arr = node.get(build_key)
                if isinstance(arr, list) and 3 <= len(arr) <= 8:
                    int_vals = []
                    all_valid = True
                    for x in arr:
                        item_id = _to_item_id(x)
                        if item_id is not None:
                            int_vals.append(item_id)
                        else:
                            all_valid = False
                            break
                    if all_valid and int_vals:
                        _try_add_names(int_vals)
            for child in node.values():
                _visit(child, depth + 1)

    _visit(payload, 0)
    return results


class DataDragonClient:
    """Fetches item and champion metadata from Riot's Data Dragon CDN.

    Always publicly available at no cost and without authentication.
    Returns safe defaults on any network failure to avoid crashing the optimizer.
    """

    VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
    CDN_BASE = "https://ddragon.leagueoflegends.com/cdn"

    def __init__(self, timeout_seconds: float = 6.0):
        self.timeout_seconds = timeout_seconds

    def get_latest_version(self) -> str:
        try:
            res = requests.get(self.VERSIONS_URL, timeout=self.timeout_seconds,
                               headers={"User-Agent": "Mozilla/5.0"})
            res.raise_for_status()
            versions = res.json()
            return str(versions[0]) if isinstance(versions, list) and versions else "14.1.1"
        except Exception:
            return "14.1.1"

    def get_item_id_to_name(self, version: Optional[str] = None) -> Dict[str, str]:
        try:
            v = version or self.get_latest_version()
            url = f"{self.CDN_BASE}/{v}/data/en_US/item.json"
            res = requests.get(url, timeout=self.timeout_seconds,
                               headers={"User-Agent": "Mozilla/5.0"})
            res.raise_for_status()
            data = res.json()
            return {str(k): str(v.get("name", "")) for k, v in data.get("data", {}).items() if v.get("name")}
        except Exception:
            return {}

    def get_champion_int_id(self, champion: str, version: Optional[str] = None) -> Optional[str]:
        try:
            v = version or self.get_latest_version()
            url = f"{self.CDN_BASE}/{v}/data/en_US/champion.json"
            res = requests.get(url, timeout=self.timeout_seconds,
                               headers={"User-Agent": "Mozilla/5.0"})
            res.raise_for_status()
            data = res.json()
            slug = _normalize_name(champion)
            for champ_data in data.get("data", {}).values():
                if (_normalize_name(champ_data.get("name", "")) == slug
                        or _normalize_name(champ_data.get("id", "")) == slug):
                    return str(champ_data.get("key", ""))
            return None
        except Exception:
            return None


class LolalyticsClient:
    """Best-effort lolalytics JSON API client.

    Uses Data Dragon to resolve champion integer IDs and item names, then
    queries the lolalytics champion endpoint. Silently returns an empty list
    on any failure — the caller is responsible for falling back to curated data.
    """

    BASE = "https://lolalytics.com/api"

    def __init__(self, timeout_seconds: float = 8.0):
        self.timeout_seconds = timeout_seconds
        self.last_error: str = ""

    def fetch_top_builds(
        self,
        champion: str,
        role: str = "jungle",
        tier: str = "emerald_plus",
    ) -> List[MetaBuildSample]:
        self.last_error = ""
        try:
            ddc = DataDragonClient(timeout_seconds=min(4.0, self.timeout_seconds))
            version = ddc.get_latest_version()
            champ_id = ddc.get_champion_int_id(champion, version)
            if not champ_id:
                self.last_error = f"Champion not found in Data Dragon: {champion}"
                return []
            item_id_to_name = ddc.get_item_id_to_name(version)
            lane = _normalize_lane(role)
            tier_param = tier.replace("+", "_plus").replace(" ", "_").lower()
            
            # Lolalytics API endpoint - as of 2026, the main getchampion endpoint
            endpoint_url = f"{self.BASE}/getchampion/"
            params = {
                "c": champ_id,
                "tier": tier_param,
                "patch": "latest",
                "region": "world",
                "lane": lane,
            }
            
            try:
                res = requests.get(
                    endpoint_url,
                    params=params,
                    timeout=self.timeout_seconds,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                )
                if res.status_code == 200:
                    data = res.json()
                    if isinstance(data, dict):
                        result = self._parse_builds_from_json(data, item_id_to_name)
                        if result:
                            return result
                    # Status 200 but no valid data
                    self.last_error = f"Lolalytics returned empty response for {champion}"
                    return []
                else:
                    self.last_error = f"Lolalytics API returned HTTP {res.status_code}"
                    return []
            except requests.Timeout:
                self.last_error = f"Lolalytics request timed out for {champion}"
                return []
            except requests.ConnectionError as e:
                self.last_error = f"Cannot reach Lolalytics (connection error)"
                return []
            except Exception as e:
                self.last_error = f"Lolalytics error: {str(e)[:100]}"
                return []
                
        except Exception as exc:
            self.last_error = str(exc)[:200]
            return []

    def _parse_builds_from_json(
        self,
        data: Dict[str, Any],
        item_id_to_name: Dict[str, str],
    ) -> List[MetaBuildSample]:
        samples: List[MetaBuildSample] = []
        for arr in _find_item_id_arrays(data, item_id_to_name)[:6]:
            if len(arr) >= 3:
                samples.append(MetaBuildSample(
                    source="lolalytics",
                    label="lolalytics-json",
                    item_names=arr,
                ))
        return _dedupe_samples(samples)


class UggMetaClient:
    """Live build fetcher. Delegates to LolalyticsClient which uses the stable JSON API.
    
    Simple, clean implementation with no HTML fallbacks or complexity.
    """

    def __init__(self, timeout_seconds: float = 8.0):
        self.timeout_seconds = timeout_seconds
        self.last_error: str = ""

    @staticmethod
    def _find_named_item_arrays(payload: Any) -> List[List[str]]:
        """Extract build-like arrays that already contain item names."""
        out: List[List[str]] = []
        seen: set = set()
        visited = [0]

        def _add(names: List[str]) -> None:
            cleaned = [str(x).strip() for x in names if str(x).strip()]
            if len(cleaned) < 3:
                return
            key = tuple(_normalize_name(x) for x in cleaned)
            if key in seen:
                return
            seen.add(key)
            out.append(cleaned[:6])

        def _visit(node: Any) -> None:
            if visited[0] > 80_000 or len(out) >= 50:
                return
            visited[0] += 1

            if isinstance(node, list):
                if 3 <= len(node) <= 8 and all(isinstance(x, str) for x in node):
                    _add(node)
                if 3 <= len(node) <= 8 and all(isinstance(x, dict) for x in node):
                    extracted: List[str] = []
                    for row in node:
                        if not isinstance(row, dict):
                            continue
                        val = None
                        for field in ("display_name", "displayName", "text", "name", "title", "label"):
                            if isinstance(row.get(field), str) and row.get(field).strip():
                                val = row.get(field)
                                break
                        if val:
                            extracted.append(str(val).strip())
                    if len(extracted) >= 3:
                        _add(extracted)
                for child in node:
                    _visit(child)
            elif isinstance(node, dict):
                for key in (
                    "items", "item_names", "build", "build_items", "core_items", "fullBuild", "finalItems",
                    "coreBuild", "coreItems", "recommendedItems",
                ):
                    arr = node.get(key)
                    if isinstance(arr, list) and 3 <= len(arr) <= 8 and all(isinstance(x, str) for x in arr):
                        _add(arr)
                    if isinstance(arr, list) and 3 <= len(arr) <= 8 and all(isinstance(x, dict) for x in arr):
                        extracted: List[str] = []
                        for row in arr:
                            if not isinstance(row, dict):
                                continue
                            val = None
                            for field in ("display_name", "displayName", "text", "name", "title", "label"):
                                if isinstance(row.get(field), str) and row.get(field).strip():
                                    val = row.get(field)
                                    break
                            if val:
                                extracted.append(str(val).strip())
                        if len(extracted) >= 3:
                            _add(extracted)
                for child in node.values():
                    _visit(child)

        _visit(payload)
        return out

    def _parse_builds_from_html(
        self,
        html: str,
        item_id_to_name: Optional[Dict[str, str]] = None,
        source: str = "u.gg",
        label: str = "structured-json",
    ) -> List[MetaBuildSample]:
        payloads = _extract_json_script_payloads(html or "")
        if not payloads:
            return []

        resolved: List[MetaBuildSample] = []
        id_map = item_id_to_name or {}
        for payload in payloads:
            named = self._find_named_item_arrays(payload)
            for names in named:
                resolved.append(MetaBuildSample(source=source, label=label, item_names=names))

            if id_map:
                from_ids = _find_item_id_arrays(payload, id_map)
                for names in from_ids:
                    resolved.append(MetaBuildSample(source=source, label=label, item_names=names))

        return _dedupe_samples(resolved)

    def _fetch_opgg_html_fallback(
        self,
        champion: str,
        role: str,
        item_id_to_name: Optional[Dict[str, str]],
    ) -> List[MetaBuildSample]:
        try:
            lane = _normalize_lane(role)
            url = f"https://www.op.gg/champions/{_normalize_name(champion)}/build/{lane}"
            res = requests.get(url, timeout=self.timeout_seconds, headers={"User-Agent": "Mozilla/5.0"})
            if int(getattr(res, "status_code", 0)) != 200:
                return []

            # First try structured script payloads.
            rows = OpggMetaClient(timeout_seconds=self.timeout_seconds)._parse_builds_from_html(
                str(getattr(res, "text", "") or ""),
                item_id_to_name=item_id_to_name,
            )
            if rows:
                self.last_error = "op.gg html fallback recovered builds"
                return rows

            # Minimal rendered-HTML fallback for tests/simpler pages.
            alts = [x.strip() for x in re.findall(r"alt=['\"]([^'\"]+)['\"]", str(getattr(res, "text", "") or "")) if x.strip()]
            if len(alts) >= 3:
                self.last_error = "op.gg html fallback recovered builds"
                return [MetaBuildSample(source="op.gg", label="op.gg-html-fallback", item_names=alts[:6])]
        except Exception:
            return []
        return []

    def fetch_top_builds(
        self,
        champion: str,
        role: str = "jungle",
        tier: str = "emerald_plus",
        region: str = "global",
        patch: str = "live",
        item_id_to_name: Optional[Dict[str, str]] = None,
    ) -> List[MetaBuildSample]:
        """Fetch top builds for champion. Returns up to 6 full builds."""
        client = LolalyticsClient(timeout_seconds=self.timeout_seconds)
        rows = client.fetch_top_builds(champion=champion, role=role, tier=tier)
        self.last_error = client.last_error
        if rows:
            return rows

        # Keep default behavior simple when no item map is available.
        if not item_id_to_name:
            return []

        fallback_rows = self._fetch_opgg_html_fallback(champion, role, item_id_to_name)
        if fallback_rows:
            return fallback_rows
        return rows





    def fetch_top_rune_pages(
        self,
        champion: str,
        role: str = "jungle",
        tier: str = "emerald_plus",
        region: str = "global",
        patch: str = "live",
    ) -> List[MetaRunePageSample]:
        """Rune pages not currently supported."""
        return []






class BlitzMetaClient(UggMetaClient):
    """Compatibility alias for legacy tests."""
    def _parse_builds_from_html(
        self,
        html: str,
        item_id_to_name: Optional[Dict[str, str]] = None,
    ) -> List[MetaBuildSample]:
        label = "blitz-keyed-json" if "__NUXT__" in (html or "") else "structured-json"
        return super()._parse_builds_from_html(
            html,
            item_id_to_name=item_id_to_name,
            source="blitz.gg",
            label=label,
        )


class OpggMetaClient(UggMetaClient):
    """Compatibility alias for legacy tests."""
    def _parse_builds_from_html(
        self,
        html: str,
        item_id_to_name: Optional[Dict[str, str]] = None,
    ) -> List[MetaBuildSample]:
        lowered = (html or "").lower()
        label = "opgg-keyed-json" if ("__next_data__" in lowered or "__nuxt__" in lowered) else "structured-json"
        return super()._parse_builds_from_html(
            html,
            item_id_to_name=item_id_to_name,
            source="op.gg",
            label=label,
        )


def extract_live_rune_pages(
    champion: str,
    role: str = "jungle",
    tier: str = "emerald_plus",
    region: str = "global",
    patch: str = "live",
) -> List[Dict[str, Any]]:
    """Rune extraction not currently supported. Returns empty list."""
    return []


def compare_optimizer_build_to_ugg(
    champion: str,
    optimizer_item_names: List[str],
    role: str = "jungle",
    comparison_mode: str = "all",
    tier: str = "emerald_plus",
    region: str = "global",
    patch: str = "live",
    optimizer_weighted_score: float = 0.0,
    optimizer_metrics: Optional[Dict[str, Any]] = None,
    evaluate_meta_build_fn: Optional[Any] = None,
    item_id_to_name: Optional[Dict[str, str]] = None,
    allow_persistent_snapshot: bool = False,
) -> Dict[str, Any]:
    curated = _curated_samples(champion, role, patch)

    client = UggMetaClient()
    live_samples = client.fetch_top_builds(
        champion,
        role=role,
        tier=tier,
        region=region,
        patch=patch,
        item_id_to_name=item_id_to_name,
    )
    samples = list(live_samples)

    warnings: List[str] = []
    source_label = str(live_samples[0].source) if live_samples else "u.gg"
    fallback_used = False
    cache_used = False
    snapshot_used = False
    live_fetch_failed = False
    cache_age_seconds: Optional[int] = None
    snapshot_age_seconds: Optional[int] = None
    cache_key = _build_cache_key(champion, role, tier, region, patch)
    if curated and not live_samples:
        warnings.append("Using curated champion baseline (Lolalytics API currently unavailable). To update: edit fixtures/meta_builds.json with latest meta from u.gg or op.gg.")
    if not samples:
        live_fetch_failed = True
        reason = "No build data could be parsed from live providers at this time."
        if client.last_error:
            warnings.append(f"live: {client.last_error}")

        cached = _META_BUILD_CACHE.get(cache_key)
        cached_dict = cached if isinstance(cached, dict) else None
        cached_rows = cached_dict.get("samples") if cached_dict else None
        if isinstance(cached_rows, list) and cached_rows:
            samples = [x for x in cached_rows if isinstance(x, MetaBuildSample)]
            if samples:
                cache_used = True
                source_label = str(cached_dict.get("source") or "cache") if cached_dict else "cache"
                saved_at = float(cached_dict.get("saved_at", 0.0) or 0.0) if cached_dict else 0.0
                if saved_at > 0:
                    cache_age_seconds = max(0, int(time.time() - saved_at))
                warnings.append("Live provider fetch failed; using cached build comparison data.")

        if (not samples) and curated:
            samples = list(curated)
            source_label = "u.gg"
            warnings.append("Using curated champion baseline because live providers returned no rows.")

        if not samples:
            if (not samples) and allow_persistent_snapshot:
                snapshot = _read_meta_snapshot(cache_key)
                if snapshot and isinstance(snapshot.get("samples"), list):
                    samples = [x for x in snapshot["samples"] if isinstance(x, MetaBuildSample)]
                    if samples:
                        snapshot_used = True
                        cache_used = True
                        source_label = str(snapshot.get("source") or "snapshot")
                        snapshot_age_seconds = int(snapshot.get("age_seconds", 0) or 0)
                        warnings.append("Live provider fetch failed; using persistent snapshot data.")
            if not samples:
                if warnings:
                    reason = f"{reason} {' | '.join(warnings)}"
                return {
                    "source": "none",
                    "available": False,
                    "reason": reason,
                    "comparison_mode": comparison_mode,
                    "comparison_context": {"tier": tier, "region": region, "role": role, "patch": patch},
                    "best_match": None,
                    "meta_builds": [],
                    "warnings": warnings,
                    "fallback_used": False,
                    "cache_used": False,
                    "snapshot_used": False,
                    "snapshot_age_seconds": None,
                    "live_fetch_failed": True,
                }

    if not samples:
        return {
            "source": "none",
            "available": False,
            "reason": "No build data could be parsed from live providers at this time.",
            "comparison_mode": comparison_mode,
            "comparison_context": {"tier": tier, "region": region, "role": role, "patch": patch},
            "best_match": None,
            "meta_builds": [],
            "warnings": warnings,
            "fallback_used": False,
            "cache_used": False,
            "snapshot_used": False,
            "snapshot_age_seconds": None,
            "live_fetch_failed": live_fetch_failed,
        }

    samples = _dedupe_samples(samples)
    if samples and not cache_used:
        _META_BUILD_CACHE[cache_key] = {
            "samples": list(samples[:6]),
            "source": source_label,
            "saved_at": time.time(),
        }
        if allow_persistent_snapshot:
            _write_meta_snapshot(
                cache_key=cache_key,
                source=source_label,
                samples=samples,
                context={"champion": champion, "role": role, "tier": tier, "region": region, "patch": patch},
            )

    scored = []
    eval_cache: Dict[Tuple[str, ...], Dict[str, Any]] = {}

    def _evaluate_build(names: List[str]) -> Dict[str, Any]:
        key = tuple(str(x) for x in (names or []))
        cached = eval_cache.get(key)
        if cached is not None:
            return cached
        if evaluate_meta_build_fn is None:
            payload = {"weighted_score": 0.0, "metrics": {}}
            eval_cache[key] = payload
            return payload
        try:
            raw = evaluate_meta_build_fn(list(names or [])) or {}
            metrics = raw.get("metrics", {}) if isinstance(raw, dict) else {}
            payload = {
                "weighted_score": _safe_float(raw.get("weighted_score", 0.0), 0.0) if isinstance(raw, dict) else 0.0,
                "metrics": dict(metrics) if isinstance(metrics, dict) else {},
            }
        except Exception as exc:
            logger.warning("Meta build evaluation failed for %s (%s): %s", champion, ",".join(names[:3]), exc)
            payload = {"weighted_score": 0.0, "metrics": {}}
        eval_cache[key] = payload
        return payload

    def _build_stage_breakdown(meta_items: List[str]) -> List[Dict[str, Any]]:
        if evaluate_meta_build_fn is None:
            return []
        stage_cap = min(6, len(optimizer_item_names), len(meta_items))
        if stage_cap <= 0:
            return []

        out: List[Dict[str, Any]] = []
        for stage in range(1, stage_cap + 1):
            optimizer_slice = list(optimizer_item_names[:stage])
            meta_slice = list(meta_items[:stage])
            opt_eval = _evaluate_build(optimizer_slice)
            meta_eval = _evaluate_build(meta_slice)

            opt_score = _safe_float(opt_eval.get("weighted_score", 0.0), 0.0)
            meta_score = _safe_float(meta_eval.get("weighted_score", 0.0), 0.0)
            score_delta = opt_score - meta_score
            score_delta_pct = (score_delta / meta_score) * 100.0 if meta_score > 0 else 0.0

            opt_metrics = opt_eval.get("metrics", {}) if isinstance(opt_eval.get("metrics", {}), dict) else {}
            meta_metrics = meta_eval.get("metrics", {}) if isinstance(meta_eval.get("metrics", {}), dict) else {}

            out.append(
                {
                    "stage": stage,
                    "optimizer": {
                        "items": optimizer_slice,
                        "weighted_score": round(opt_score, 3),
                        "metrics": {
                            "damage": round(_safe_float(opt_metrics.get("damage", 0.0), 0.0), 3),
                            "healing": round(_safe_float(opt_metrics.get("healing", 0.0), 0.0), 3),
                            "tankiness": round(_safe_float(opt_metrics.get("tankiness", 0.0), 0.0), 3),
                            "lifesteal": round(_safe_float(opt_metrics.get("lifesteal", 0.0), 0.0), 3),
                        },
                    },
                    "meta": {
                        "items": meta_slice,
                        "weighted_score": round(meta_score, 3),
                        "metrics": {
                            "damage": round(_safe_float(meta_metrics.get("damage", 0.0), 0.0), 3),
                            "healing": round(_safe_float(meta_metrics.get("healing", 0.0), 0.0), 3),
                            "tankiness": round(_safe_float(meta_metrics.get("tankiness", 0.0), 0.0), 3),
                            "lifesteal": round(_safe_float(meta_metrics.get("lifesteal", 0.0), 0.0), 3),
                        },
                    },
                    "delta": {
                        "weighted_score": round(score_delta, 3),
                        "weighted_score_percent": round(score_delta_pct, 3),
                    },
                }
            )
        return out

    optimizer_metrics = optimizer_metrics or {}
    for sample in samples:
        similarity = round(_jaccard(optimizer_item_names, sample.item_names), 4)
        meta_score = 0.0
        component_alignment = 0.0
        stage_breakdown = _build_stage_breakdown(sample.item_names)
        if evaluate_meta_build_fn is not None:
            try:
                eval_payload = _evaluate_build(sample.item_names)
                meta_score = _safe_float(eval_payload.get("weighted_score", 0.0), 0.0)
                meta_metrics = eval_payload.get("metrics", {}) if isinstance(eval_payload, dict) else {}
                if isinstance(meta_metrics, dict) and optimizer_metrics:
                    deltas = []
                    for key in ("damage", "healing", "tankiness", "lifesteal"):
                        lhs = _safe_float(optimizer_metrics.get(key, 0.0), 0.0)
                        rhs = _safe_float(meta_metrics.get(key, 0.0), 0.0)
                        denom = max(1.0, abs(lhs), abs(rhs))
                        deltas.append(abs(lhs - rhs) / denom)
                    component_alignment = round(max(0.0, 1.0 - (sum(deltas) / max(1, len(deltas)))), 4)
            except Exception as exc:
                logger.warning("Meta build evaluation failed for %s (%s): %s", champion, sample.label, exc)
                meta_score = 0.0
                component_alignment = 0.0

        score_delta = optimizer_weighted_score - meta_score
        score_delta_pct = 0.0
        if meta_score > 0:
            score_delta_pct = (score_delta / meta_score) * 100.0

        scored.append(
            {
                "label": sample.label,
                "items": sample.item_names,
                "win_rate": sample.win_rate,
                "pick_rate": sample.pick_rate,
                "games": sample.games,
                "similarity": similarity,
                "meta_weighted_score": round(meta_score, 3),
                "score_delta": round(score_delta, 3),
                "score_delta_percent": round(score_delta_pct, 3),
                "component_alignment": component_alignment,
                "stage_breakdown": stage_breakdown,
            }
        )

    overlap_sorted = sorted(scored, key=lambda x: x["similarity"], reverse=True)
    power_sorted = sorted(scored, key=lambda x: x["score_delta_percent"], reverse=True)
    component_sorted = sorted(scored, key=lambda x: x["component_alignment"], reverse=True)

    mode = str(comparison_mode or "all").strip().lower()
    if mode not in {"all", "item_overlap", "power_delta", "component_balance"}:
        mode = "all"

    best_by_mode = {
        "item_overlap": _pick_best_meta_build(overlap_sorted, "item_overlap"),
        "power_delta": _pick_best_meta_build(power_sorted, "power_delta"),
        "component_balance": _pick_best_meta_build(component_sorted, "component_balance"),
    }

    selected_best = best_by_mode.get(mode if mode != "all" else "item_overlap")
    selected_builds = (
        overlap_sorted
        if mode == "item_overlap"
        else power_sorted
        if mode == "power_delta"
        else component_sorted
        if mode == "component_balance"
        else overlap_sorted
    )

    return {
        "source": source_label,
        "available": True,
        "reason": "Using cached build comparison data because live fetch failed." if cache_used else "",
        "comparison_mode": mode,
        "comparison_context": {"tier": tier, "region": region, "role": role, "patch": patch},
        "best_match": selected_best,
        "best_by_mode": best_by_mode,
        "modes": {
            "item_overlap": overlap_sorted,
            "power_delta": power_sorted,
            "component_balance": component_sorted,
        },
        "meta_builds": selected_builds,
        "warnings": warnings,
        "fallback_used": fallback_used,
        "cache_used": cache_used,
        "snapshot_used": snapshot_used,
        "cache_age_seconds": cache_age_seconds,
        "snapshot_age_seconds": snapshot_age_seconds,
        "live_fetch_failed": live_fetch_failed,
    }
