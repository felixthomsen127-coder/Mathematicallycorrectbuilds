"""
simulation.py — DPS and burst-damage simulation engine.

Given a champion's ability breakdown (enriched with v11_enriched fields),
a list of ItemStats, and target-dummy stats, compute:

  burst_damage()       — total damage of one full rotation (Q+W+E+R).
  dps_simulation()     — sustained damage per second over a duration window.

Both functions account for:
  - Post-mitigation (armor / magic resistance, penetration from items).
  - Ability damage type (physical / magic / true / mixed / none).
  - Cooldown reduction from items (ability_haste).
  - On-hit champions: on-hit proc count during auto-attack sequences.
  - Base damage and scaling (AD, AP, bonus-HP, etc.) per ability.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
AbilityBlock = Dict[str, Any]
BreakdownDict = Dict[str, AbilityBlock]


# ---------------------------------------------------------------------------
# Champion base-stat defaults (level-18 approximations).
# Keys match what the caller may pass in via *champion_base*.
# ---------------------------------------------------------------------------
_SAFE_DEFAULTS: Dict[str, float] = {
    "base_ad": 60.0,
    "ad_growth": 3.0,          # AD gained per level after L1
    "base_ap": 0.0,
    "base_hp": 600.0,
    "hp_growth": 90.0,
    "bonus_hp": 0.0,
    "base_attack_speed": 0.65,
    "attack_range": 150.0,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _coerce(value: Any, default: float = 0.0) -> float:
    """Safely coerce any value to float, returning *default* on failure."""
    try:
        v = float(value)
        if v != v:           # NaN guard
            return default
        return v
    except (TypeError, ValueError):
        return default


def _level_stat(base: float, growth: float, level: int) -> float:
    """Return a stat value at *level* using linear interpolation."""
    level = max(1, min(18, level))
    return base + growth * (level - 1) / 17.0


def _post_mitigation_factor(
    raw_armor: float,
    armor_pen_pct: float,
    flat_armor_pen: float,
    armor_pen_lethality: float,
    level: int,
) -> float:
    """
    Return the fraction of physical damage that passes through armor.

    Penetration order (per Riot's formula):
      1. Percent armor pen (e.g. Last Whisper stacks)
      2. Flat armor pen / lethality (lethality = flat pen * level scaling)
    """
    # Lethality → flat pen at the given level
    flat_from_lethality = armor_pen_lethality * (0.6 + 0.4 * (level - 1) / 17.0)
    effective_flat = flat_from_lethality + flat_armor_pen

    armor_after_pct = raw_armor * (1.0 - min(armor_pen_pct, 0.45))
    reduced_armor = max(0.0, armor_after_pct - effective_flat)
    return 100.0 / (100.0 + reduced_armor)


def _post_mitigation_magic_factor(
    raw_mr: float,
    magic_pen_pct: float,
    flat_magic_pen: float,
) -> float:
    """Return the fraction of magic damage that passes through MR."""
    mr_after_pct = raw_mr * (1.0 - min(magic_pen_pct, 0.40))
    reduced_mr = max(0.0, mr_after_pct - flat_magic_pen)
    return 100.0 / (100.0 + reduced_mr)


# ---------------------------------------------------------------------------
# Public: stat aggregation
# ---------------------------------------------------------------------------

def compute_total_stats(
    champion_base: Optional[Dict[str, float]],
    items: Sequence[Any],          # Sequence[ItemStats]
    level: int = 18,
) -> Dict[str, float]:
    """
    Aggregate champion base stats (level-scaled) with item bonuses.

    *champion_base* may be None or a partial dict — missing keys fall back to
    :data:`_SAFE_DEFAULTS`.

    Returns a flat dict of combined stats:
        total_ad, total_ap, total_hp, total_bonus_hp, total_armor,
        total_mr, total_attack_speed, ability_haste,
        armor_pen_pct, flat_armor_pen, lethality,
        magic_pen_pct, flat_magic_pen,
        crit_chance, lifesteal, omnivamp,
        max_hp_damage (% max-HP on-hit from items).
    """
    base = champion_base or {}
    level = max(1, min(18, int(level)))

    base_ad = _level_stat(
        _coerce(base.get("base_ad"), _SAFE_DEFAULTS["base_ad"]),
        _coerce(base.get("ad_growth"), _SAFE_DEFAULTS["ad_growth"]),
        level,
    )
    base_hp = _level_stat(
        _coerce(base.get("base_hp"), _SAFE_DEFAULTS["base_hp"]),
        _coerce(base.get("hp_growth"), _SAFE_DEFAULTS["hp_growth"]),
        level,
    )
    base_as = _coerce(base.get("base_attack_speed"), _SAFE_DEFAULTS["base_attack_speed"])

    bonus_ad = sum(_coerce(x.ad) for x in items)
    bonus_ap = sum(_coerce(x.ap) for x in items)
    bonus_hp = sum(_coerce(x.hp) for x in items)
    bonus_armor = sum(_coerce(x.armor) for x in items)
    bonus_mr = sum(_coerce(x.mr) for x in items)
    bonus_as_pct = sum(_coerce(x.attack_speed) for x in items)   # stored as fraction
    ability_haste = sum(_coerce(getattr(x, "ability_haste", 0.0)) for x in items)
    armor_pen_pct = min(sum(_coerce(x.armor_pen) for x in items), 0.45)
    flat_armor_pen = sum(_coerce(x.flat_armor_pen) for x in items)
    lethality = sum(_coerce(getattr(x, "lethality", 0.0)) for x in items)
    magic_pen_pct = min(sum(_coerce(x.magic_pen) for x in items), 0.40)
    flat_magic_pen = sum(_coerce(x.flat_magic_pen) for x in items)
    crit_chance = min(sum(_coerce(getattr(x, "crit_chance", 0.0)) for x in items), 1.0)
    lifesteal = sum(_coerce(getattr(x, "lifesteal", 0.0)) for x in items)
    omnivamp = sum(_coerce(getattr(x, "omnivamp", 0.0)) for x in items)
    max_hp_dmg = max(_coerce(x.max_hp_damage) for x in items) if items else 0.0

    total_ad = base_ad + bonus_ad
    total_ap = _coerce(base.get("base_ap"), 0.0) + bonus_ap
    total_hp = base_hp + bonus_hp
    # 5.0 is an unreachable safety ceiling; LoL has no practical item AS cap
    total_as = min(base_as * (1.0 + bonus_as_pct), 5.0)

    return {
        "total_ad": total_ad,
        "bonus_ad": bonus_ad,
        "total_ap": total_ap,
        "total_hp": total_hp,
        "bonus_hp": bonus_hp,
        "total_armor": _coerce(base.get("base_armor"), 35.0) + bonus_armor,
        "total_mr": _coerce(base.get("base_mr"), 32.0) + bonus_mr,
        "total_attack_speed": total_as,
        "ability_haste": ability_haste,
        "armor_pen_pct": armor_pen_pct,
        "flat_armor_pen": flat_armor_pen,
        "lethality": lethality,
        "magic_pen_pct": magic_pen_pct,
        "flat_magic_pen": flat_magic_pen,
        "crit_chance": crit_chance,
        "lifesteal": lifesteal,
        "omnivamp": omnivamp,
        "max_hp_damage": max_hp_dmg,
        "base_attack_speed": base_as,
    }


# ---------------------------------------------------------------------------
# Public: per-ability damage calculation
# ---------------------------------------------------------------------------

def _compute_ability_damage(
    block: AbilityBlock,
    stats: Dict[str, float],
    target_armor: float,
    target_mr: float,
    level: int = 18,
) -> float:
    """
    Return the expected damage of a single cast of *block*.

    The ability may have multiple scaling components.  We sum them, honour
    the ``damage_type`` field, and apply the appropriate penetration factor.
    """
    ad = stats["total_ad"]
    bonus_ad = stats["bonus_ad"]
    ap = stats["total_ap"]
    bonus_hp = stats["bonus_hp"]
    total_hp = stats["total_hp"]

    # --- gather raw (pre-mitigation) damage ---
    raw_phys = 0.0
    raw_magic = 0.0
    raw_true = 0.0

    # Ratio-based shortcut (legacy and v11_enriched both populate these).
    base_dmg_list: List[float] = []
    bd_raw = block.get("base_damage")
    if isinstance(bd_raw, list):
        try:
            base_dmg_list = [float(x) for x in bd_raw if x is not None]
        except (TypeError, ValueError):
            pass
    base_dmg = base_dmg_list[-1] if base_dmg_list else 0.0   # use max-rank value

    # Per-component scaling (richer path — v11_enriched).
    components = block.get("scaling_components") or []
    if components:
        for comp in components:
            if not isinstance(comp, dict):
                continue
            ratio = _coerce(comp.get("ratio"))
            if ratio <= 0:
                continue
            stat_type = str(comp.get("stat_type", "") or "").lower()
            app = str(comp.get("application", "damage") or "damage").lower()
            dmg_type = str(comp.get("damage_type", "") or "").lower()

            if stat_type in ("ad", "total_ad"):
                scaled = ratio * ad
            elif stat_type in ("bonus_ad", "bonus ad"):
                scaled = ratio * bonus_ad
            elif stat_type in ("ap", "total_ap"):
                scaled = ratio * ap
            elif stat_type in ("hp", "max_hp", "total_hp"):
                scaled = ratio * total_hp
            elif stat_type in ("bonus_hp", "bonus hp"):
                scaled = ratio * bonus_hp
            else:
                continue

            if app in ("heal", "shield", "buff_debuff"):
                continue  # not damage

            if dmg_type == "physical" or stat_type in ("ad", "total_ad", "bonus_ad", "bonus ad"):
                raw_phys += scaled
            elif dmg_type == "magic" or stat_type in ("ap", "total_ap"):
                raw_magic += scaled
            elif dmg_type == "true":
                raw_true += scaled
            else:
                raw_phys += scaled   # default to physical for AD-like stats

    # If no components gave damage, fall back to top-level ratios.
    if raw_phys == 0.0 and raw_magic == 0.0 and raw_true == 0.0:
        damage_type = str(block.get("damage_type", "") or "").lower()
        ad_ratio = _coerce(block.get("ad_ratio"))
        ap_ratio = _coerce(block.get("ap_ratio"))
        bonus_hp_ratio = _coerce(block.get("bonus_hp_ratio"))

        if damage_type in ("physical", ""):
            raw_phys += ad_ratio * ad + base_dmg
        elif damage_type == "magic":
            raw_magic += ap_ratio * ap + ad_ratio * ad + base_dmg
        elif damage_type == "true":
            raw_true += ap_ratio * ap + ad_ratio * ad + base_dmg
        elif damage_type == "mixed":
            half_base = base_dmg * 0.5
            raw_phys += ad_ratio * ad + half_base
            raw_magic += ap_ratio * ap + half_base
        else:
            # "none" or "unknown" — still emit base damage as physical
            raw_phys += base_dmg

        raw_phys += bonus_hp_ratio * bonus_hp  # e.g. Garen/Darius bonus-HP scalings

    # --- apply mitigation ---
    phys_factor = _post_mitigation_factor(
        target_armor,
        stats["armor_pen_pct"],
        stats["flat_armor_pen"],
        stats["lethality"],
        level,
    )
    magic_factor = _post_mitigation_magic_factor(
        target_mr,
        stats["magic_pen_pct"],
        stats["flat_magic_pen"],
    )

    return raw_phys * phys_factor + raw_magic * magic_factor + raw_true


# ---------------------------------------------------------------------------
# Public: burst and DPS
# ---------------------------------------------------------------------------

def burst_damage(
    breakdown: BreakdownDict,
    champion_stats: Dict[str, float],
    items: Sequence[Any],
    target_armor: float = 100.0,
    target_mr: float = 50.0,
    level: int = 18,
) -> Dict[str, Any]:
    """
    Compute the total damage dealt in a single full ability rotation (Q+W+E+R).

    Returns a dict:
        {
            "total":       float,
            "per_ability": {"q": float, "w": float, "e": float, "r": float},
            "breakdown_detail": {key: {raw, mitigated, damage_type, targeting, ...}},
        }
    """
    if not isinstance(champion_stats, dict):
        champion_stats = {}
    stats = compute_total_stats(champion_stats, items, level)

    per_ability: Dict[str, float] = {}
    detail: Dict[str, Dict[str, Any]] = {}

    for key in ("q", "w", "e", "r", "passive"):
        block = breakdown.get(key)
        if not isinstance(block, dict):
            continue
        dmg = _compute_ability_damage(block, stats, target_armor, target_mr, level)
        per_ability[key] = round(dmg, 1)
        detail[key] = {
            "damage": round(dmg, 1),
            "damage_type": block.get("damage_type", "unknown"),
            "targeting": block.get("targeting", "unknown"),
            "on_hit": block.get("on_hit", False),
            "is_channeled": block.get("is_channeled", False),
            "is_conditional": block.get("is_conditional", False),
            "is_stack_scaling": block.get("is_stack_scaling", False),
            "range_units": block.get("range_units", 0.0),
        }

    total = sum(per_ability.values())
    return {
        "total": round(total, 1),
        "per_ability": per_ability,
        "breakdown_detail": detail,
    }


def dps_simulation(
    breakdown: BreakdownDict,
    champion_stats: Dict[str, float],
    items: Sequence[Any],
    duration: float = 10.0,
    target_armor: float = 100.0,
    target_mr: float = 50.0,
    target_hp: float = 2500.0,
    level: int = 18,
) -> Dict[str, Any]:
    """
    Simulate sustained DPS over *duration* seconds.

    Uses a simple priority queue: abilities come off cooldown, fire
    immediately, then go back on cooldown.  Ability haste applies.  Auto
    attacks fill the gaps.

    Returns a dict:
        {
            "dps":          float,
            "total_damage": float,
            "cast_counts":  {"q": int, ...},
            "auto_attacks": int,
        }
    """
    if duration <= 0:
        return {"dps": 0.0, "total_damage": 0.0, "cast_counts": {}, "auto_attacks": 0}

    stats = compute_total_stats(champion_stats, items, level)
    ah = stats["ability_haste"]
    # Ability haste → CDR fraction:  cdr = ah / (100 + ah)
    cdr = ah / (100.0 + ah)

    # Cooldown (base = max-rank value, reduced by ability haste).
    def _ability_cd(block: AbilityBlock) -> float:
        cd_list = block.get("cooldown") or [1.0]
        try:
            valid_cds = [float(x) for x in cd_list if x is not None]
            cd_raw = valid_cds[-1] if valid_cds else 1.0
        except (TypeError, ValueError):
            cd_raw = 1.0
        return max(0.5, cd_raw * (1.0 - cdr))

    total_damage = 0.0
    cast_counts: Dict[str, int] = {}

    # Next available cast time for each ability.
    ability_keys = [k for k in ("q", "w", "e", "r") if isinstance(breakdown.get(k), dict)]
    next_cast: Dict[str, float] = {k: 0.0 for k in ability_keys}

    t = 0.0
    # Auto-attack interval (seconds between attacks).
    attack_interval = 1.0 / max(stats["total_attack_speed"], 0.05)
    next_auto = attack_interval   # first auto slightly delayed (cast animation)
    auto_count = 0

    # Simple event loop — step by the smallest upcoming event.
    _MAX_STEPS = 50_000
    step = 0
    while t < duration and step < _MAX_STEPS:
        step += 1
        # Find next ability cast time
        earliest_ability = min((v for v in next_cast.values()), default=None)
        # Next event is minimum of earliest ability cast or next auto attack.
        candidates = [c for c in [earliest_ability, next_auto] if c is not None and c <= duration]
        if not candidates:
            break
        t = min(candidates)

        if t == next_auto and t <= duration:
            # Auto attack
            aa_base_dmg = stats["total_ad"]
            # Crit: 1.75× average
            aa_base_dmg *= (1.0 + 0.75 * stats["crit_chance"])
            # Max-HP on-hit
            aa_base_dmg += stats["max_hp_damage"] * target_hp

            phys_factor = _post_mitigation_factor(
                target_armor, stats["armor_pen_pct"],
                stats["flat_armor_pen"], stats["lethality"], level,
            )
            total_damage += aa_base_dmg * phys_factor
            auto_count += 1
            next_auto = t + attack_interval

        # Cast all abilities available at time t.
        for k in ability_keys:
            if next_cast[k] <= t:
                block = breakdown[k]
                dmg = _compute_ability_damage(block, stats, target_armor, target_mr, level)
                total_damage += dmg
                cast_counts[k] = cast_counts.get(k, 0) + 1

                on_hit = block.get("on_hit", False)
                is_channeled = block.get("is_channeled", False)

                cd = _ability_cd(block)
                # Channeled abilities effectively occupy the champion for longer.
                cast_time = 0.4 if not is_channeled else max(cd * 0.5, 0.5)
                next_cast[k] = t + max(cd, cast_time)

                # On-hit abilities that emit multiple hits generate extra damage.
                if on_hit and stats["total_attack_speed"] > 1.0:
                    # Rough model: apply 1 bonus on-hit hit per 0.5 AS above baseline.
                    _base_as = stats.get("base_attack_speed", 0.65)
                    bonus_hits = int((stats["total_attack_speed"] - _base_as) / 0.5)
                    if bonus_hits > 0:
                        phys_factor = _post_mitigation_factor(
                            target_armor, stats["armor_pen_pct"],
                            stats["flat_armor_pen"], stats["lethality"], level,
                        )
                        total_damage += bonus_hits * stats["total_ad"] * phys_factor

    dps = total_damage / duration if duration > 0 else 0.0
    return {
        "dps": round(dps, 1),
        "total_damage": round(total_damage, 1),
        "cast_counts": cast_counts,
        "auto_attacks": auto_count,
    }
