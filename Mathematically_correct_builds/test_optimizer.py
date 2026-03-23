import json
from pathlib import Path

import pytest

from optimizer import (
    BuildConstraints,
    BuildOptimizer,
    ChampionProfile,
    EnemyProfile,
    ItemStats,
    ObjectiveWeights,
    RuneChoice,
    RunePage,
    SearchSettings,
    StrictDataError,
)


def _fake_items():
    return [
        ItemStats(item_id="1", name="Sword", total_gold=3000, ad=80),
        ItemStats(item_id="2", name="Rod", total_gold=3000, ap=100),
        ItemStats(item_id="3", name="Armor", total_gold=2800, hp=400, armor=60),
        ItemStats(item_id="4", name="Fangs", total_gold=2900, ad=40, lifesteal=0.12, damage_amp=0.05),
        ItemStats(item_id="5", name="Speed", total_gold=2600, attack_speed=0.5),
        ItemStats(item_id="6", name="Veil", total_gold=2800, mr=60, hp=300, heal_amp=0.2),
        ItemStats(item_id="7", name="Steel Boots", total_gold=1100, armor=20),
        ItemStats(item_id="8", name="Breaker", total_gold=3200, ad=45, armor_pen=0.35),
    ]


def _strict_profile(champion_name: str = "Test") -> ChampionProfile:
    return ChampionProfile(
        champion_name=champion_name,
        base_hp=700,
        base_armor=30,
        base_mr=30,
        abilities_per_rotation=3.0,
        average_combat_seconds=9.0,
        ability_breakdown={
            "q": {"ap_ratio": 0.6, "base_damage": [80, 130, 180, 230, 280], "cooldown": [8, 7, 6, 5, 4]},
            "w": {"ad_ratio": 0.5, "base_damage": [70, 110, 150, 190, 230], "cooldown": [12, 11, 10, 9, 8]},
            "e": {"ap_ratio": 0.4, "base_damage": [60, 95, 130, 165, 200], "cooldown": [10, 9, 8, 7, 6]},
            "r": {"ap_ratio": 0.8, "base_damage": [200, 350, 500], "cooldown": [120, 100, 80]},
        },
    )


def test_search_settings_defaults_match_balanced_preset():
    settings = SearchSettings()

    assert settings.mode == "near_exhaustive"
    assert settings.build_size == 6
    assert settings.candidate_pool_size == 24
    assert settings.beam_width == 65
    assert settings.exhaustive_runtime_cap_seconds == 120.0
    assert settings.order_permutation_cap == 150
    assert settings.sa_iterations == 100
    assert settings.deep_search is False
    assert settings.extra_restarts == 0
    assert settings.compute_backend == "auto"


def test_optimizer_returns_ranked_and_pareto():
    champ = _strict_profile()
    opt = BuildOptimizer(champ, _fake_items())

    ranked, pareto, checkpoints = opt.optimize(
        ObjectiveWeights(damage=1.0, healing=0.6, tankiness=0.2, lifesteal=0.2),
        SearchSettings(mode="near_exhaustive", build_size=4, candidate_pool_size=8, beam_width=15),
        enemy=EnemyProfile(),
    )

    assert ranked
    assert pareto
    assert checkpoints
    assert ranked[0].weighted_score >= ranked[-1].weighted_score


def test_explainability_contains_contributions_and_interactions():
    champ = _strict_profile()
    opt = BuildOptimizer(champ, _fake_items())

    ranked, _, _ = opt.optimize(
        ObjectiveWeights(damage=1.0, healing=0.4, tankiness=0.1, lifesteal=0.2),
        SearchSettings(mode="near_exhaustive", build_size=4, candidate_pool_size=8, beam_width=20),
        enemy=EnemyProfile(),
    )

    assert ranked
    top = ranked[0]
    assert "damage_component" in top.contributions
    assert "utility_component" in top.contributions
    assert "consistency_component" in top.contributions
    assert "order_component" in top.contributions
    assert "utility" in top.metrics
    assert "consistency" in top.metrics
    assert isinstance(top.interactions, list)


def test_constraints_require_boots_and_exclude_item():
    champ = _strict_profile()
    opt = BuildOptimizer(champ, _fake_items())

    ranked, _, _ = opt.optimize(
        ObjectiveWeights(damage=1.0),
        SearchSettings(mode="near_exhaustive", build_size=3, candidate_pool_size=8, beam_width=20),
        constraints=BuildConstraints(require_boots=True, excluded_ids=("8",)),
        enemy=EnemyProfile(),
    )

    assert ranked
    names = [x.name.lower() for x in ranked[0].items]
    assert any("boots" in x for x in names)
    assert not any(x.item_id == "8" for x in ranked[0].items)


def test_frozen_fixture_regression_expected_core_items():
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "frozen_optimizer_fixture.json"
    with fixture_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    frozen_payload = dict(payload["champion"])
    for key in ("ad_ratio", "ap_ratio", "attack_speed_ratio", "heal_ratio"):
        frozen_payload.pop(key, None)
    frozen = ChampionProfile(**frozen_payload)
    strict_seed = _strict_profile(frozen.champion_name)
    champion = ChampionProfile(
        champion_name=frozen.champion_name,
        base_hp=frozen.base_hp,
        base_armor=frozen.base_armor,
        base_mr=frozen.base_mr,
        abilities_per_rotation=strict_seed.abilities_per_rotation,
        average_combat_seconds=strict_seed.average_combat_seconds,
        ability_breakdown=strict_seed.ability_breakdown,
        champion_tags=frozen.champion_tags,
    )
    items = [ItemStats(**x) for x in payload["items"]]
    weights = ObjectiveWeights(**payload["weights"])
    settings = SearchSettings(**payload["settings"])

    ranked, _, _ = BuildOptimizer(champion, items).optimize(weights, settings, enemy=EnemyProfile())
    assert ranked

    top_names = {x.name for x in ranked[0].items}
    expected = set(payload["expected_top_contains"])
    assert top_names.intersection(expected)


def test_enemy_profile_changes_damage_score():
    champ = _strict_profile()
    opt = BuildOptimizer(champ, _fake_items())
    settings = SearchSettings(mode="near_exhaustive", build_size=4, candidate_pool_size=8, beam_width=20)
    weights = ObjectiveWeights(damage=1.0)

    ranked_squishy, _, _ = opt.optimize(weights, settings, enemy=EnemyProfile(target_hp=2200, target_armor=60, target_mr=50))
    ranked_tanky, _, _ = opt.optimize(weights, settings, enemy=EnemyProfile(target_hp=4500, target_armor=260, target_mr=220))

    assert ranked_squishy
    assert ranked_tanky
    assert ranked_squishy[0].metrics["damage"] > ranked_tanky[0].metrics["damage"]


def test_checkpoints_include_early_and_full_stages():
    champ = _strict_profile()
    opt = BuildOptimizer(champ, _fake_items())

    _, _, checkpoints = opt.optimize(
        ObjectiveWeights(damage=1.0, tankiness=0.2),
        SearchSettings(mode="near_exhaustive", build_size=4, candidate_pool_size=8, beam_width=20),
        enemy=EnemyProfile(),
    )

    assert "1_item" in checkpoints
    assert "2_item" in checkpoints
    assert "3_item" in checkpoints
    assert "4_item" in checkpoints


def test_spell_aware_ability_breakdown_increases_spell_damage_value():
    items = [
        ItemStats(item_id="1", name="AP Tome", total_gold=3000, ap=120),
        ItemStats(item_id="2", name="AD Blade", total_gold=3000, ad=70),
        ItemStats(item_id="3", name="Steel Boots", total_gold=1100, armor=20),
    ]

    base_profile = ChampionProfile(
        champion_name="SpellChamp",
        base_hp=700,
        base_armor=30,
        base_mr=30,
        abilities_per_rotation=3.0,
        ability_breakdown={
            "q": {"base_damage": [10, 20, 30, 40, 50], "cooldown": [8, 7, 6, 5, 4]},
            "w": {"base_damage": [10, 20, 30, 40, 50], "cooldown": [8, 7, 6, 5, 4]},
            "e": {"base_damage": [10, 20, 30, 40, 50], "cooldown": [8, 7, 6, 5, 4]},
            "r": {"base_damage": [20, 40, 60], "cooldown": [120, 100, 80]},
        },
    )
    spell_profile = ChampionProfile(
        champion_name="SpellChamp",
        base_hp=700,
        base_armor=30,
        base_mr=30,
        abilities_per_rotation=3.0,
        ability_breakdown={
            "q": {"ap_ratio": 0.7, "ad_ratio": 0.0, "attack_speed_ratio": 0.0, "heal_ratio": 0.0, "base_damage": [100, 150, 200, 250, 300], "cooldown": [8, 7, 6, 5, 4]},
            "w": {"ap_ratio": 0.5, "ad_ratio": 0.0, "attack_speed_ratio": 0.0, "heal_ratio": 0.0, "base_damage": [90, 140, 190, 240, 290], "cooldown": [10, 9, 8, 7, 6]},
            "e": {"ap_ratio": 0.4, "ad_ratio": 0.0, "attack_speed_ratio": 0.0, "heal_ratio": 0.0, "base_damage": [80, 120, 160, 200, 240], "cooldown": [11, 10, 9, 8, 7]},
            "r": {"ap_ratio": 0.9, "ad_ratio": 0.0, "attack_speed_ratio": 0.0, "heal_ratio": 0.0, "base_damage": [200, 350, 500], "cooldown": [120, 100, 80]},
        },
    )

    settings = SearchSettings(mode="near_exhaustive", build_size=2, candidate_pool_size=3, beam_width=6)
    weights = ObjectiveWeights(damage=1.0)
    enemy = EnemyProfile(target_hp=3000, target_armor=100, target_mr=80)

    ranked_base, _, _ = BuildOptimizer(base_profile, items).optimize(weights, settings, enemy=enemy)
    ranked_spell, _, _ = BuildOptimizer(spell_profile, items).optimize(weights, settings, enemy=enemy)

    assert ranked_base
    assert ranked_spell
    assert ranked_spell[0].metrics["damage"] > ranked_base[0].metrics["damage"]


def test_lower_spell_cooldowns_increase_damage_projection():
    items = [
        ItemStats(item_id="1", name="Arcane Blade", total_gold=3000, ap=100, ad=30),
        ItemStats(item_id="2", name="Battle Tome", total_gold=2800, ap=90),
    ]

    slow_profile = ChampionProfile(
        champion_name="CooldownChamp",
        base_hp=700,
        base_armor=30,
        base_mr=30,
        abilities_per_rotation=3.0,
        average_combat_seconds=9.0,
        ability_breakdown={
            "q": {"ap_ratio": 0.6, "base_damage": [80, 130, 180, 230, 280], "cooldown": [12, 11, 10, 9, 8]},
            "w": {"ap_ratio": 0.5, "base_damage": [70, 110, 150, 190, 230], "cooldown": [14, 13, 12, 11, 10]},
            "e": {"ap_ratio": 0.4, "base_damage": [60, 95, 130, 165, 200], "cooldown": [13, 12, 11, 10, 9]},
            "r": {"ap_ratio": 0.8, "base_damage": [150, 275, 400], "cooldown": [120, 100, 80]},
        },
    )
    fast_profile = ChampionProfile(
        champion_name="CooldownChamp",
        base_hp=700,
        base_armor=30,
        base_mr=30,
        abilities_per_rotation=3.0,
        average_combat_seconds=9.0,
        ability_breakdown={
            "q": {"ap_ratio": 0.6, "base_damage": [80, 130, 180, 230, 280], "cooldown": [7, 6.5, 6, 5.5, 5]},
            "w": {"ap_ratio": 0.5, "base_damage": [70, 110, 150, 190, 230], "cooldown": [8, 7.5, 7, 6.5, 6]},
            "e": {"ap_ratio": 0.4, "base_damage": [60, 95, 130, 165, 200], "cooldown": [8.5, 8, 7.5, 7, 6.5]},
            "r": {"ap_ratio": 0.8, "base_damage": [150, 275, 400], "cooldown": [120, 100, 80]},
        },
    )

    settings = SearchSettings(mode="near_exhaustive", build_size=2, candidate_pool_size=2, beam_width=4)
    weights = ObjectiveWeights(damage=1.0)
    enemy = EnemyProfile(target_hp=3200, target_armor=110, target_mr=95)

    ranked_slow, _, _ = BuildOptimizer(slow_profile, items).optimize(weights, settings, enemy=enemy)
    ranked_fast, _, _ = BuildOptimizer(fast_profile, items).optimize(weights, settings, enemy=enemy)

    assert ranked_slow
    assert ranked_fast
    assert ranked_fast[0].metrics["damage"] > ranked_slow[0].metrics["damage"]


def test_class_tags_influence_spell_projection_ult_usage():
    items = [
        ItemStats(item_id="1", name="Burst Rod", total_gold=3000, ap=110),
        ItemStats(item_id="2", name="Burst Blade", total_gold=3000, ad=50),
    ]
    base_breakdown = {
        "q": {"ap_ratio": 0.6, "base_damage": [100, 150, 200, 250, 300], "cooldown": [8, 7, 6, 5, 4]},
        "w": {"ap_ratio": 0.4, "base_damage": [80, 120, 160, 200, 240], "cooldown": [9, 8, 7, 6, 5]},
        "e": {"ap_ratio": 0.5, "base_damage": [70, 110, 150, 190, 230], "cooldown": [10, 9, 8, 7, 6]},
        "r": {"ap_ratio": 1.1, "base_damage": [200, 350, 500], "cooldown": [120, 100, 80]},
    }

    mage = ChampionProfile(
        champion_name="MageChamp",
        base_hp=680,
        base_armor=28,
        base_mr=30,
        abilities_per_rotation=3.0,
        average_combat_seconds=9.0,
        ability_breakdown=base_breakdown,
        champion_tags=("Mage",),
    )
    marksman = ChampionProfile(
        champion_name="MarksChamp",
        base_hp=680,
        base_armor=28,
        base_mr=30,
        abilities_per_rotation=3.0,
        average_combat_seconds=9.0,
        ability_breakdown=base_breakdown,
        champion_tags=("Marksman",),
    )

    settings = SearchSettings(mode="near_exhaustive", build_size=2, candidate_pool_size=2, beam_width=4)
    weights = ObjectiveWeights(damage=1.0)

    ranked_mage, _, _ = BuildOptimizer(mage, items).optimize(weights, settings, enemy=EnemyProfile())
    ranked_marksman, _, _ = BuildOptimizer(marksman, items).optimize(weights, settings, enemy=EnemyProfile())

    assert ranked_mage
    assert ranked_marksman
    assert ranked_mage[0].metrics["damage"] >= ranked_marksman[0].metrics["damage"]


def test_tank_stat_spell_scaling_increases_damage_projection():
    items = [
        ItemStats(item_id="1", name="Health Armor Core", total_gold=3000, hp=500, armor=70),
        ItemStats(item_id="2", name="Pure Damage Blade", total_gold=3000, ad=80),
    ]

    plain_profile = ChampionProfile(
        champion_name="TankCaster",
        base_hp=900,
        base_armor=40,
        base_mr=32,
        abilities_per_rotation=2.5,
        ability_breakdown={
            "q": {"base_damage": [80, 120, 160, 200, 240], "cooldown": [8, 7, 6, 5, 4], "damage_type": "magic"},
            "w": {"base_damage": [60, 90, 120, 150, 180], "cooldown": [12, 11, 10, 9, 8], "damage_type": "magic"},
            "e": {"base_damage": [40, 70, 100, 130, 160], "cooldown": [10, 9, 8, 7, 6], "damage_type": "magic"},
            "r": {"base_damage": [120, 210, 300], "cooldown": [120, 100, 80], "damage_type": "magic"},
        },
    )
    tank_scaling_profile = ChampionProfile(
        champion_name="TankCaster",
        base_hp=900,
        base_armor=40,
        base_mr=32,
        abilities_per_rotation=2.5,
        ability_breakdown={
            "q": {
                "base_damage": [80, 120, 160, 200, 240],
                "cooldown": [8, 7, 6, 5, 4],
                "bonus_hp_ratio": 0.12,
                "armor_ratio": 0.4,
                "damage_type": "magic",
            },
            "w": {
                "base_damage": [60, 90, 120, 150, 180],
                "cooldown": [12, 11, 10, 9, 8],
                "hp_ratio": 0.04,
                "damage_type": "magic",
            },
            "e": {
                "base_damage": [40, 70, 100, 130, 160],
                "cooldown": [10, 9, 8, 7, 6],
                "damage_type": "magic",
            },
            "r": {
                "base_damage": [120, 210, 300],
                "cooldown": [120, 100, 80],
                "damage_type": "magic",
            },
        },
    )

    settings = SearchSettings(mode="near_exhaustive", build_size=1, candidate_pool_size=2, beam_width=2)
    weights = ObjectiveWeights(damage=1.0)

    ranked_plain, _, _ = BuildOptimizer(plain_profile, items).optimize(weights, settings, enemy=EnemyProfile())
    ranked_tank, _, _ = BuildOptimizer(tank_scaling_profile, items).optimize(weights, settings, enemy=EnemyProfile())

    assert ranked_plain
    assert ranked_tank
    assert ranked_tank[0].items[0].name == "Health Armor Core"
    assert ranked_tank[0].metrics["damage"] > ranked_plain[0].metrics["damage"]


def test_strict_optimizer_requires_enemy_profile():
    optimizer = BuildOptimizer(_strict_profile(), _fake_items())
    with pytest.raises(ValueError):
        optimizer.optimize(ObjectiveWeights(damage=1.0), SearchSettings(mode="heuristic", build_size=2, candidate_pool_size=4))


def test_strict_optimizer_rejects_missing_ability_breakdown():
    champ = ChampionProfile(champion_name="NoData", base_hp=700, base_armor=30, base_mr=30)
    optimizer = BuildOptimizer(champ, _fake_items())
    with pytest.raises(StrictDataError):
        optimizer.optimize(
            ObjectiveWeights(damage=1.0),
            SearchSettings(mode="heuristic", build_size=2, candidate_pool_size=4),
            enemy=EnemyProfile(),
        )


def test_strict_optimizer_rejects_invalid_cooldown_data():
    bad_profile = _strict_profile("BadCooldown")
    bad_breakdown = dict(bad_profile.ability_breakdown)
    bad_breakdown["q"] = {**bad_breakdown["q"], "cooldown": []}
    profile = ChampionProfile(
        champion_name=bad_profile.champion_name,
        base_hp=bad_profile.base_hp,
        base_armor=bad_profile.base_armor,
        base_mr=bad_profile.base_mr,
        abilities_per_rotation=bad_profile.abilities_per_rotation,
        average_combat_seconds=bad_profile.average_combat_seconds,
        ability_breakdown=bad_breakdown,
        champion_tags=bad_profile.champion_tags,
    )

    optimizer = BuildOptimizer(profile, _fake_items())
    with pytest.raises(StrictDataError):
        optimizer.optimize(
            ObjectiveWeights(damage=1.0),
            SearchSettings(mode="heuristic", build_size=2, candidate_pool_size=4),
            enemy=EnemyProfile(),
        )


# ---------------------------------------------------------------------------
# Unique-passive conflict tests
# ---------------------------------------------------------------------------

def test_unique_passive_conflict_blocks_build():
    """Two items sharing a unique passive name must never appear in the same build."""
    optimizer = BuildOptimizer.__new__(BuildOptimizer)
    constraints = BuildConstraints(max_total_gold=None)

    item_a = ItemStats(item_id="1", name="A", total_gold=3000, ad=40,
                       unique_passives=("carve",))
    item_b = ItemStats(item_id="2", name="B", total_gold=3000, ad=35,
                       unique_passives=("carve",))
    item_c = ItemStats(item_id="3", name="C", total_gold=3000, ap=80,
                       unique_passives=("spellblade",))

    assert not optimizer._valid_partial_build([item_a, item_b], constraints)
    assert optimizer._valid_partial_build([item_a, item_c], constraints)
    assert optimizer._valid_partial_build([item_b, item_c], constraints)


def test_non_conflicting_unique_passives_allowed():
    """Items with different unique passives may coexist in a build."""
    optimizer = BuildOptimizer.__new__(BuildOptimizer)
    constraints = BuildConstraints(max_total_gold=None)

    ta = ItemStats(item_id="1", name="A", total_gold=3000, ad=40,
                   unique_passives=("last whisper",))
    tb = ItemStats(item_id="2", name="B", total_gold=3000, ad=35,
                   unique_passives=("warlord",))
    tc = ItemStats(item_id="3", name="C", total_gold=3000, ap=80,
                   unique_passives=("annihilate",))

    assert optimizer._valid_partial_build([ta, tb, tc], constraints)


def test_empty_unique_passives_never_conflict():
    """Items with empty unique_passives should never trigger a conflict."""
    optimizer = BuildOptimizer.__new__(BuildOptimizer)
    constraints = BuildConstraints(max_total_gold=None)

    items = [
        ItemStats(item_id=str(i), name=f"Item{i}", total_gold=3000, ad=40)
        for i in range(6)
    ]
    assert optimizer._valid_partial_build(items, constraints)


def test_last_whisper_unique_group_conflict_blocks_build():
    """Serylda and Last Whisper items must not coexist via unique_group fallback."""
    optimizer = BuildOptimizer.__new__(BuildOptimizer)
    constraints = BuildConstraints(max_total_gold=None)

    serylda = ItemStats(
        item_id="1",
        name="Serylda's Grudge",
        total_gold=3200,
        ad=45,
        unique_group="last_whisper",
    )
    last_whisper = ItemStats(
        item_id="2",
        name="Last Whisper",
        total_gold=1450,
        ad=20,
        unique_group="last_whisper",
    )

    assert not optimizer._valid_partial_build([serylda, last_whisper], constraints)


def test_optimizer_selects_best_rune_page_for_build_value():
    profile = _strict_profile("RuneChamp")
    items = [
        ItemStats(item_id="1", name="AP Core", total_gold=3000, ap=100),
        ItemStats(item_id="2", name="AP Burst", total_gold=3100, ap=120),
        ItemStats(item_id="3", name="Steel Boots", total_gold=1100, armor=20),
    ]

    pages = [
        RunePage(
            page_id="ad_page",
            name="AD Page",
            primary_tree="Precision",
            secondary_tree="Resolve",
            runes=(RuneChoice("ad", "AD", "Precision", "keystone", ad=40.0),),
        ),
        RunePage(
            page_id="ap_page",
            name="AP Page",
            primary_tree="Sorcery",
            secondary_tree="Inspiration",
            runes=(RuneChoice("ap", "AP", "Sorcery", "keystone", ap=80.0),),
        ),
    ]

    optimizer = BuildOptimizer(profile, items, rune_pages=pages)
    ranked, _, _ = optimizer.optimize(
        ObjectiveWeights(damage=1.0),
        SearchSettings(mode="heuristic", build_size=2, candidate_pool_size=3, order_permutation_cap=12),
        enemy=EnemyProfile(),
    )

    assert ranked
    assert ranked[0].rune_page is not None
    assert ranked[0].rune_page.page_id == "ap_page"
    assert ranked[0].rune_effects.get("ap", 0.0) > 0.0

