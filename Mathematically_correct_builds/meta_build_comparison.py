from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple
import base64
import json
import logging
import re

import requests


logger = logging.getLogger(__name__)


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
    return payloads


def _extract_item_names_from_visual_text(
    html: str,
    item_id_to_name: Dict[str, str],
) -> List[List[str]]:
    """Last-resort extractor for pages that only expose visual labels/text.

    Some providers move structured data behind client-side hydration and still
    expose item names in image alts or rendered text snippets. This extractor
    scans those channels and emits a best-effort 4-6 item build.
    """
    if not item_id_to_name:
        return []

    candidates = [str(x) for x in item_id_to_name.values() if str(x).strip()]
    if not candidates:
        return []

    lowered_to_name: Dict[str, str] = {}
    for name in candidates:
        lowered_to_name.setdefault(name.lower(), name)

    alt_chunks = re.findall(r"<(?:img|div|span|a)[^>]*(?:alt|aria-label|title)=['\"]([^'\"]+)['\"][^>]*>", html, flags=re.IGNORECASE)
    plain_text = re.sub(r"<[^>]+>", " ", html)
    sources = [" ".join(alt_chunks), plain_text]

    best_matches: List[str] = []
    for src in sources:
        text = " ".join(str(src or "").split()).lower()
        if len(text) < 10:
            continue
        matched: List[str] = []
        seen = set()
        for lowered, original in sorted(lowered_to_name.items(), key=lambda kv: len(kv[0]), reverse=True):
            if len(lowered) < 4:
                continue
            pattern = r"\b" + re.escape(lowered) + r"\b"
            if re.search(pattern, text):
                key = _normalize_name(original)
                if key in seen:
                    continue
                seen.add(key)
                matched.append(original)
                if len(matched) >= 6:
                    break
        if len(matched) >= 4 and len(matched) > len(best_matches):
            best_matches = matched

    return [best_matches[:6]] if len(best_matches) >= 4 else []


def _extract_item_names_from_ocr(
    html: str,
    item_id_to_name: Dict[str, str],
) -> List[List[str]]:
    """Fallback OCR extractor for heavily script-rendered pages.

    This path is optional and only used when OCR dependencies are available.
    It scans inline base64 images and synthetic text-renders as a last resort.
    """
    if not item_id_to_name:
        return []

    try:
        from PIL import Image, ImageDraw  # type: ignore
        import pytesseract  # type: ignore
    except Exception:
        return []

    candidate_names = [str(x) for x in item_id_to_name.values() if str(x).strip()]
    if not candidate_names:
        return []

    lowered_to_name: Dict[str, str] = {}
    for name in candidate_names:
        lowered_to_name.setdefault(name.lower(), name)

    def _collect_matches(text: str) -> List[str]:
        txt = str(text or "").lower()
        found: List[str] = []
        seen = set()
        for lowered, original in sorted(lowered_to_name.items(), key=lambda kv: len(kv[0]), reverse=True):
            if len(lowered) < 4:
                continue
            if re.search(r"\b" + re.escape(lowered) + r"\b", txt):
                key = _normalize_name(original)
                if key in seen:
                    continue
                seen.add(key)
                found.append(original)
                if len(found) >= 6:
                    break
        return found

    ocr_texts: List[str] = []

    # First, OCR inline base64 images if available.
    for match in re.findall(r"data:image/[^;]+;base64,([A-Za-z0-9+/=]+)", html):
        try:
            blob = base64.b64decode(match)
            with Image.open(BytesIO(blob)) as im:
                if im.width < 24 or im.height < 24:
                    continue
                txt = pytesseract.image_to_string(im)
                if txt and txt.strip():
                    ocr_texts.append(txt)
        except Exception:
            continue

    # Then, synthesize an image from visible text and OCR that snapshot.
    if not ocr_texts:
        alt_chunks = re.findall(r"<(?:img|div|span|a)[^>]*(?:alt|aria-label|title)=['\"]([^'\"]+)['\"][^>]*>", html, flags=re.IGNORECASE)
        plain_text = re.sub(r"<[^>]+>", " ", html)
        lines: List[str] = []
        if alt_chunks:
            lines.extend([str(x) for x in alt_chunks[:64]])
        cleaned_plain = " ".join(str(plain_text or "").split())
        if cleaned_plain:
            lines.extend([cleaned_plain[i : i + 220] for i in range(0, min(len(cleaned_plain), 2800), 220)])
        if lines:
            try:
                height = max(64, min(1500, 18 * len(lines) + 24))
                canvas = Image.new("RGB", (1200, height), color=(255, 255, 255))
                drawer = ImageDraw.Draw(canvas)
                y = 8
                for line in lines:
                    drawer.text((8, y), line[:210], fill=(0, 0, 0))
                    y += 18
                    if y > height - 20:
                        break
                txt = pytesseract.image_to_string(canvas)
                if txt and txt.strip():
                    ocr_texts.append(txt)
            except Exception:
                pass

    best: List[str] = []
    for text in ocr_texts:
        matched = _collect_matches(text)
        if len(matched) >= 4 and len(matched) > len(best):
            best = matched

    return [best[:6]] if len(best) >= 4 else []


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

    Looks for lists of 4-7 integers (or integer-valued strings) all in the LoL
    item-ID range (1001-7999), or objects with ``itemId``/``item_id`` keys.
    Uses item_id_to_name to resolve IDs to English names.  Emits a build only
    if at least 4 IDs resolve to real names.  Bounded by a node-visit cap so
    large Next.js payloads don't stall.
    """
    results: List[List[str]] = []
    seen_keys: set = set()
    visited = [0]

    def _to_item_id(val: Any) -> Optional[int]:
        """Try to parse val as a LoL item ID integer."""
        try:
            i = int(val)
            if 1001 <= i <= 7999:
                return i
        except (TypeError, ValueError):
            pass
        return None

    def _visit(node: Any, depth: int) -> None:
        if visited[0] > 30_000 or len(results) >= 50:
            return
        visited[0] += 1
        if isinstance(node, list):
            # Case 1: list of integers or integer-strings (e.g. [6632, 3036, ...])
            if 4 <= len(node) <= 7:
                int_vals = []
                all_valid = True
                for x in node:
                    item_id = _to_item_id(x)
                    if item_id is not None:
                        int_vals.append(item_id)
                    else:
                        all_valid = False
                        break
                if all_valid and len(int_vals) >= 4:
                    names = [item_id_to_name[str(v)] for v in int_vals if str(v) in item_id_to_name]
                    if len(names) >= 4:
                        key = tuple(sorted(names))
                        if key not in seen_keys:
                            seen_keys.add(key)
                            results.append(names[:6])
            # Case 2: list of objects with itemId fields (e.g. [{"itemId": 6632}, ...])
            if 4 <= len(node) <= 7:
                obj_ids = []
                for x in node:
                    if isinstance(x, dict):
                        for field in ("itemId", "item_id", "id", "ItemId"):
                            item_id = _to_item_id(x.get(field))
                            if item_id is not None:
                                obj_ids.append(item_id)
                                break
                if len(obj_ids) >= 4:
                    names = [item_id_to_name[str(v)] for v in obj_ids if str(v) in item_id_to_name]
                    if len(names) >= 4:
                        key = tuple(sorted(names))
                        if key not in seen_keys:
                            seen_keys.add(key)
                            results.append(names[:6])
            for child in node:
                _visit(child, depth + 1)
        elif isinstance(node, dict):
            # Case 3: dict with "items" or "itemIds" key containing an ID list
            for build_key in ("items", "item_ids", "itemIds", "item_list", "build"):
                arr = node.get(build_key)
                if isinstance(arr, list) and 4 <= len(arr) <= 7:
                    int_vals = []
                    all_valid = True
                    for x in arr:
                        item_id = _to_item_id(x)
                        if item_id is not None:
                            int_vals.append(item_id)
                        else:
                            all_valid = False
                            break
                    if all_valid and len(int_vals) >= 4:
                        names = [item_id_to_name[str(v)] for v in int_vals if str(v) in item_id_to_name]
                        if len(names) >= 4:
                            key = tuple(sorted(names))
                            if key not in seen_keys:
                                seen_keys.add(key)
                                results.append(names[:6])
            for child in node.values():
                _visit(child, depth + 1)

    _visit(payload, 0)
    return results


class UggMetaClient:
    """Best-effort U.GG scraping helper with graceful fallback.

    U.GG markup changes regularly, so this parser is intentionally defensive.
    """

    def __init__(self, timeout_seconds: float = 8.0):
        self.timeout_seconds = timeout_seconds
        self.last_error: str = ""

    def fetch_top_builds(
        self,
        champion: str,
        role: str = "jungle",
        tier: str = "emerald_plus",
        region: str = "global",
        patch: str = "live",
        item_id_to_name: Optional[Dict[str, str]] = None,
    ) -> List[MetaBuildSample]:
        slug = _normalize_name(champion)
        if not slug:
            return []

        # The role segment is optional on some pages; try role first then generic.
        urls = [
            f"https://u.gg/lol/champions/{slug}/{role}/build?rank={tier}&region={region}&patch={patch}",
            f"https://u.gg/lol/champions/{slug}/build",
        ]

        self.last_error = ""
        html = ""
        errors: List[str] = []
        for url in urls:
            try:
                res = requests.get(
                    url,
                    timeout=self.timeout_seconds,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if res.status_code < 400 and res.text:
                    html = res.text
                    break
                errors.append(f"{url} returned HTTP {res.status_code}")
            except requests.Timeout:
                errors.append(f"{url} timed out")
            except requests.RequestException as exc:
                errors.append(f"{url} request failed: {exc}")
            except Exception:
                errors.append(f"{url} failed unexpectedly")

        if not html:
            self.last_error = "; ".join(errors[-3:])
            if self.last_error:
                logger.warning("U.GG fetch failed for %s: %s", slug, self.last_error)
            return []

        return self._parse_builds_from_html(html, item_id_to_name=item_id_to_name)

    def fetch_top_rune_pages(
        self,
        champion: str,
        role: str = "jungle",
        tier: str = "emerald_plus",
        region: str = "global",
        patch: str = "live",
    ) -> List[MetaRunePageSample]:
        slug = _normalize_name(champion)
        if not slug:
            return []

        urls = [
            f"https://u.gg/lol/champions/{slug}/{role}/build?rank={tier}&region={region}&patch={patch}",
            f"https://u.gg/lol/champions/{slug}/build",
        ]

        html = ""
        for url in urls:
            try:
                res = requests.get(
                    url,
                    timeout=self.timeout_seconds,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if res.status_code < 400 and res.text:
                    html = res.text
                    break
            except Exception:
                continue

        if not html:
            return []

        return self._parse_runes_from_html(html)

    def _parse_builds_from_html(
        self,
        html: str,
        item_id_to_name: Optional[Dict[str, str]] = None,
        source_label: str = "u.gg",
    ) -> List[MetaBuildSample]:
        out: List[MetaBuildSample] = []
        _id_map = item_id_to_name or {}

        # Primary: walk every parsed JSON payload for integer item-ID arrays.
        # This handles Next.js __NEXT_DATA__ blobs where item IDs are integers.
        if _id_map:
            for payload in _extract_json_script_payloads(html):
                for names in _find_item_id_arrays(payload, _id_map):
                    out.append(
                        MetaBuildSample(
                            source=source_label,
                            label="id-resolved",
                            item_names=names,
                        )
                    )
            if out:
                return out[:3]

        # Secondary: try reading script blocks with structured JSON name arrays.
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.DOTALL | re.IGNORECASE)
        for script in scripts:
            if "item" not in script.lower():
                continue
            for match in re.findall(r"\{\"items\":\[[^\]]+\][^{}]*\}", script):
                try:
                    blob = json.loads(match)
                except Exception:
                    continue
                items = blob.get("items", [])
                if not isinstance(items, list) or len(items) < 3:
                    continue
                names = [str(x) for x in items[:6]]
                out.append(
                    MetaBuildSample(
                        source=source_label,
                        label="parsed",
                        item_names=names,
                        win_rate=float(blob.get("winRate", 0.0) or 0.0),
                        pick_rate=float(blob.get("pickRate", 0.0) or 0.0),
                        games=int(blob.get("matches", 0) or 0),
                    )
                )

        # Fallback: scan /items/{id} URL patterns from markup.
        # Only emit builds when IDs can be resolved to real names — raw numeric
        # strings would poison Jaccard similarity scores against English item names.
        if not out:
            token_hits = re.findall(r"/items/(\d+)", html)
            if token_hits:
                uniq: List[str] = []
                seen: set = set()
                for token in token_hits:
                    if token in seen:
                        continue
                    seen.add(token)
                    uniq.append(token)
                for i in range(0, min(len(uniq), 18), 6):
                    chunk = uniq[i : i + 6]
                    if len(chunk) < 3:
                        continue
                    if _id_map:
                        resolved = [_id_map[t] for t in chunk if t in _id_map]
                        if len(resolved) >= 4:
                            out.append(
                                MetaBuildSample(
                                    source=source_label,
                                    label="id-resolved",
                                    item_names=resolved,
                                )
                            )
                    # Without an ID map, skip raw numeric strings entirely.

        # Final fallback: infer item names from alt/title/visible text when
        # structured JSON and ID-URL extraction both fail.
        if not out and _id_map:
            visual_rows = _extract_item_names_from_visual_text(html, _id_map)
            for row in visual_rows:
                out.append(
                    MetaBuildSample(
                        source=source_label,
                        label="visual-text-fallback",
                        item_names=row,
                    )
                )

        # OCR fallback: handles image/text-rendered builds when all parsers fail.
        if not out and _id_map:
            ocr_rows = _extract_item_names_from_ocr(html, _id_map)
            for row in ocr_rows:
                out.append(
                    MetaBuildSample(
                        source=source_label,
                        label="ocr-fallback",
                        item_names=row,
                    )
                )

        return out[:3]

    def _parse_runes_from_html(self, html: str) -> List[MetaRunePageSample]:
        pages: List[MetaRunePageSample] = []
        raw_payloads = _extract_json_script_payloads(html)

        for payload in raw_payloads:
            extracted = _extract_structured_rune_pages_from_payload(payload)
            for rune_ids, primary_id, secondary_id in extracted:
                rune_names = [_rune_name_from_id(x) for x in rune_ids]
                inferred_primary = _tree_from_rune_id(rune_ids[0]) if rune_ids else "Unknown"
                secondary_candidates = [x for x in rune_ids[1:] if _tree_from_rune_id(x) != inferred_primary]
                inferred_secondary = _tree_from_rune_id(secondary_candidates[0]) if secondary_candidates else inferred_primary
                primary_tree = _TREE_ID_TO_NAME.get(primary_id or 0, inferred_primary)
                secondary_tree = _TREE_ID_TO_NAME.get(secondary_id or 0, inferred_secondary)
                pages.append(
                    MetaRunePageSample(
                        source="u.gg",
                        label=f"{primary_tree} primary",
                        primary_tree=primary_tree,
                        secondary_tree=secondary_tree,
                        rune_names=rune_names,
                    )
                )

        # Strict mode: do not fabricate rune pages if structured data is missing.
        deduped: List[MetaRunePageSample] = []
        seen = set()
        for page in pages:
            key = (page.primary_tree, page.secondary_tree, tuple(_normalize_name(x) for x in page.rune_names))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(page)
        return deduped[:4]


class BlitzMetaClient(UggMetaClient):
    """Best-effort Blitz.gg scraper used as fallback when U.GG has no usable rows."""

    def fetch_top_builds(
        self,
        champion: str,
        role: str = "jungle",
        tier: str = "emerald_plus",
        region: str = "global",
        patch: str = "live",
        item_id_to_name: Optional[Dict[str, str]] = None,
    ) -> List[MetaBuildSample]:
        slug = _normalize_name(champion)
        if not slug:
            return []

        # Blitz route conventions can vary; try role route first and then generic.
        urls = [
            f"https://blitz.gg/lol/champions/{slug}/{role}/build",
            f"https://blitz.gg/lol/champions/{slug}/build",
        ]

        self.last_error = ""
        html = ""
        errors: List[str] = []
        for url in urls:
            try:
                res = requests.get(
                    url,
                    timeout=self.timeout_seconds,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if res.status_code < 400 and res.text:
                    html = res.text
                    break
                errors.append(f"{url} returned HTTP {res.status_code}")
            except requests.Timeout:
                errors.append(f"{url} timed out")
            except requests.RequestException as exc:
                errors.append(f"{url} request failed: {exc}")
            except Exception:
                errors.append(f"{url} failed unexpectedly")

        if not html:
            self.last_error = "; ".join(errors[-3:])
            if self.last_error:
                logger.warning("Blitz.gg fetch failed for %s: %s", slug, self.last_error)
            return []

        return self._parse_builds_from_html(
            html,
            item_id_to_name=item_id_to_name,
            source_label="blitz.gg",
        )


class OpggMetaClient(UggMetaClient):
    """Best-effort OP.GG scraper used after U.GG and Blitz.gg."""

    def fetch_top_builds(
        self,
        champion: str,
        role: str = "jungle",
        tier: str = "emerald_plus",
        region: str = "global",
        patch: str = "live",
        item_id_to_name: Optional[Dict[str, str]] = None,
    ) -> List[MetaBuildSample]:
        slug = _normalize_name(champion)
        if not slug:
            return []

        urls = [
            f"https://www.op.gg/champions/{slug}/{role}/build",
            f"https://www.op.gg/champions/{slug}/build",
        ]

        self.last_error = ""
        html = ""
        errors: List[str] = []
        for url in urls:
            try:
                res = requests.get(
                    url,
                    timeout=self.timeout_seconds,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if res.status_code < 400 and res.text:
                    html = res.text
                    break
                errors.append(f"{url} returned HTTP {res.status_code}")
            except requests.Timeout:
                errors.append(f"{url} timed out")
            except requests.RequestException as exc:
                errors.append(f"{url} request failed: {exc}")
            except Exception:
                errors.append(f"{url} failed unexpectedly")

        if not html:
            self.last_error = "; ".join(errors[-3:])
            if self.last_error:
                logger.warning("OP.GG fetch failed for %s: %s", slug, self.last_error)
            return []

        return self._parse_builds_from_html(
            html,
            item_id_to_name=item_id_to_name,
            source_label="op.gg",
        )


def extract_live_rune_pages(
    champion: str,
    role: str = "jungle",
    tier: str = "emerald_plus",
    region: str = "global",
    patch: str = "live",
) -> List[Dict[str, Any]]:
    client = UggMetaClient()
    samples = client.fetch_top_rune_pages(champion, role=role, tier=tier, region=region, patch=patch)
    return [
        {
            "source": x.source,
            "label": x.label,
            "primary_tree": x.primary_tree,
            "secondary_tree": x.secondary_tree,
            "rune_names": x.rune_names,
            "win_rate": x.win_rate,
            "pick_rate": x.pick_rate,
            "games": x.games,
        }
        for x in samples
    ]


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
) -> Dict[str, Any]:
    if not item_id_to_name:
        return {
            "source": "u.gg",
            "available": False,
            "reason": "U.GG comparison is unavailable because item ID mapping is missing.",
            "comparison_mode": comparison_mode,
            "comparison_context": {"tier": tier, "region": region, "role": role, "patch": patch},
            "best_match": None,
            "meta_builds": [],
            "warnings": ["Missing item ID map for U.GG parser."],
        }

    client = UggMetaClient()
    samples = client.fetch_top_builds(
        champion,
        role=role,
        tier=tier,
        region=region,
        patch=patch,
        item_id_to_name=item_id_to_name,
    )
    warnings: List[str] = []
    source_label = "u.gg"
    fallback_used = False
    if not samples:
        reason = "No build data could be parsed from U.GG, Blitz.gg, or OP.GG at this time."
        if client.last_error:
            warnings.append(f"U.GG: {client.last_error}")
        providers = [
            ("blitz.gg", BlitzMetaClient(timeout_seconds=client.timeout_seconds)),
            ("op.gg", OpggMetaClient(timeout_seconds=client.timeout_seconds)),
        ]
        for label, fallback_client in providers:
            samples = fallback_client.fetch_top_builds(
                champion,
                role=role,
                tier=tier,
                region=region,
                patch=patch,
                item_id_to_name=item_id_to_name,
            )
            if samples:
                source_label = label
                fallback_used = True
                warnings.append(f"U.GG unavailable; using {label} fallback.")
                break
            if fallback_client.last_error:
                warnings.append(f"{label}: {fallback_client.last_error}")

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
            }

    if not samples:
        return {
            "source": "none",
            "available": False,
            "reason": "No build data could be parsed from U.GG, Blitz.gg, or OP.GG at this time.",
            "comparison_mode": comparison_mode,
            "comparison_context": {"tier": tier, "region": region, "role": role, "patch": patch},
            "best_match": None,
            "meta_builds": [],
            "warnings": warnings,
            "fallback_used": False,
        }

    scored = []
    optimizer_metrics = optimizer_metrics or {}
    for sample in samples:
        similarity = round(_jaccard(optimizer_item_names, sample.item_names), 4)
        meta_score = 0.0
        component_alignment = 0.0
        if evaluate_meta_build_fn is not None:
            try:
                eval_payload = evaluate_meta_build_fn(sample.item_names)
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
    }
