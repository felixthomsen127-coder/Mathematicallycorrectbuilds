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


def test_frozen_fixture_regression_trace_stability():
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

    top = ranked[0]
    assert top.metrics["stack_uptime"] >= 0.0
    assert top.metrics["proc_frequency"] >= 1.0
    assert top.trace["hit_events"] >= top.trace["spell_casts_est"]
    assert top.trace["realized_damage_amp_total"] <= top.trace["damage_amp_total"] + 0.2
    expected = set(payload["expected_top_contains"])
    assert {x.name for x in top.items}.intersection(expected)


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


def test_diminishing_returns_soft_caps_high_stats_more_than_mid_stats():
    champ = _strict_profile()
    opt = BuildOptimizer(champ, _fake_items())

    mid = opt._apply_diminishing_returns(220.0, 320.0, 3200.0, 170.0, 120.0)
    high = opt._apply_diminishing_returns(420.0, 620.0, 6200.0, 320.0, 240.0)

    # High stats should see a lower effective multiplier than mid stats.
    mid_mult = mid[0] / 220.0
    high_mult = high[0] / 420.0
    assert high_mult < mid_mult
    assert 0.6 <= high_mult <= 1.0


def test_effective_sustain_metric_respects_damage_source_mix():
    champ = _strict_profile()
    opt = BuildOptimizer(champ, _fake_items())

    auto_heavy = opt._effective_sustain_metric(
        lifesteal=0.15,
        omnivamp=0.10,
        auto_damage_after_mitigation=800.0,
        spell_damage_after_mitigation=200.0,
    )
    spell_heavy = opt._effective_sustain_metric(
        lifesteal=0.15,
        omnivamp=0.10,
        auto_damage_after_mitigation=200.0,
        spell_damage_after_mitigation=800.0,
    )

    assert auto_heavy > spell_heavy


def test_cast_time_reduces_auto_attack_window_in_combat_profile():
    items = _fake_items()[:2]
    common_breakdown = {
        "q": {"ad_ratio": 0.3, "base_damage": [80, 120, 160], "cooldown": [6, 6, 6], "cast_time": 0.0},
        "w": {"ad_ratio": 0.2, "base_damage": [70, 100, 130], "cooldown": [8, 8, 8], "cast_time": 0.0},
        "e": {"ad_ratio": 0.2, "base_damage": [60, 90, 120], "cooldown": [7, 7, 7], "cast_time": 0.0},
        "r": {"ad_ratio": 0.6, "base_damage": [200, 300, 400], "cooldown": [90, 90, 90], "cast_time": 0.0},
    }

    instant_profile = ChampionProfile(
        champion_name="InstantCaster",
        base_hp=700,
        base_armor=30,
        base_mr=30,
        abilities_per_rotation=3.0,
        average_combat_seconds=9.0,
        ability_breakdown=common_breakdown,
    )
    slow_profile = ChampionProfile(
        champion_name="SlowCaster",
        base_hp=700,
        base_armor=30,
        base_mr=30,
        abilities_per_rotation=3.0,
        average_combat_seconds=9.0,
        ability_breakdown={
            k: dict(v, cast_time=0.45 if k != "r" else 0.65)
            for k, v in common_breakdown.items()
        },
    )

    instant_opt = BuildOptimizer(instant_profile, items)
    slow_opt = BuildOptimizer(slow_profile, items)

    instant_pattern = instant_opt._combat_pattern_profile(items, attack_speed=1.0, ability_haste=0.0)
    slow_pattern = slow_opt._combat_pattern_profile(items, attack_speed=1.0, ability_haste=0.0)

    assert slow_pattern["auto_attacks"] < instant_pattern["auto_attacks"]
    assert slow_pattern["hit_events"] < instant_pattern["hit_events"]


def test_on_hit_profiles_realize_more_proc_damage_than_spell_only_profiles():
    proc_items = [
        ItemStats(item_id="1", name="Proc Blade", total_gold=3000, attack_speed=0.55, max_hp_damage=0.08),
        ItemStats(item_id="2", name="Tempo Edge", total_gold=2900, ad=45, attack_speed=0.20),
    ]
    neutral_runes = [RunePage(page_id="neutral", name="Neutral", primary_tree="Resolve", secondary_tree="Resolve", runes=())]
    weights = ObjectiveWeights(damage=1.0)
    enemy = EnemyProfile(target_hp=3600, target_armor=140, target_mr=100)

    on_hit_profile = ChampionProfile(
        champion_name="OnHitChamp",
        base_hp=700,
        base_armor=30,
        base_mr=30,
        abilities_per_rotation=3.0,
        average_combat_seconds=9.0,
        ability_breakdown={
            "q": {"ad_ratio": 0.3, "attack_speed_ratio": 0.4, "base_damage": [60, 100, 140], "cooldown": [7, 6, 5], "on_hit": True, "targeting": "single_target"},
            "w": {"ad_ratio": 0.2, "base_damage": [40, 80, 120], "cooldown": [10, 9, 8], "on_hit": True, "targeting": "single_target"},
            "e": {"ad_ratio": 0.2, "base_damage": [40, 80, 120], "cooldown": [9, 8, 7], "on_hit": False, "targeting": "line"},
            "r": {"ad_ratio": 0.5, "base_damage": [150, 250, 350], "cooldown": [100, 90, 80], "on_hit": False, "targeting": "single_target"},
        },
    )
    spell_profile = ChampionProfile(
        champion_name="SpellChamp",
        base_hp=700,
        base_armor=30,
        base_mr=30,
        abilities_per_rotation=3.0,
        average_combat_seconds=9.0,
        ability_breakdown={
            "q": {"ad_ratio": 0.5, "base_damage": [80, 130, 180], "cooldown": [7, 6, 5], "on_hit": False, "targeting": "line"},
            "w": {"ad_ratio": 0.4, "base_damage": [70, 110, 150], "cooldown": [10, 9, 8], "on_hit": False, "targeting": "aoe"},
            "e": {"ad_ratio": 0.3, "base_damage": [60, 90, 120], "cooldown": [9, 8, 7], "on_hit": False, "targeting": "aoe"},
            "r": {"ad_ratio": 0.6, "base_damage": [170, 270, 370], "cooldown": [100, 90, 80], "on_hit": False, "targeting": "aoe"},
        },
    )

    on_hit_eval = BuildOptimizer(on_hit_profile, proc_items, rune_pages=neutral_runes)._evaluate_build(proc_items, proc_items, weights, enemy, rune_page=neutral_runes[0])
    spell_eval = BuildOptimizer(spell_profile, proc_items, rune_pages=neutral_runes)._evaluate_build(proc_items, proc_items, weights, enemy, rune_page=neutral_runes[0])

    assert on_hit_eval.trace["max_hp_proc_damage_total"] > spell_eval.trace["max_hp_proc_damage_total"]
    assert on_hit_eval.metrics["proc_frequency"] >= spell_eval.metrics["proc_frequency"]
    assert on_hit_eval.metrics["damage"] > spell_eval.metrics["damage"]


def test_longer_combat_windows_increase_stack_uptime_and_realized_amp():
    ramp_items = [
        ItemStats(item_id="1", name="Rift Crown", total_gold=3100, ap=100, damage_amp=0.10),
        ItemStats(item_id="2", name="Burn Tome", total_gold=2900, ap=80, ability_haste=25),
    ]
    neutral_runes = [RunePage(page_id="neutral", name="Neutral", primary_tree="Resolve", secondary_tree="Resolve", runes=())]
    weights = ObjectiveWeights(damage=1.0)
    enemy = EnemyProfile(target_hp=3200, target_armor=110, target_mr=110)
    shared_breakdown = {
        "q": {"ap_ratio": 0.6, "base_damage": [80, 130, 180], "cooldown": [7, 6, 5], "targeting": "aoe"},
        "w": {"ap_ratio": 0.5, "base_damage": [70, 120, 170], "cooldown": [9, 8, 7], "targeting": "aoe"},
        "e": {"ap_ratio": 0.4, "base_damage": [60, 100, 140], "cooldown": [8, 7, 6], "targeting": "line"},
        "r": {"ap_ratio": 0.8, "base_damage": [180, 280, 380], "cooldown": [100, 90, 80], "targeting": "aoe"},
    }

    short_fight = ChampionProfile(
        champion_name="ShortFight",
        base_hp=700,
        base_armor=30,
        base_mr=30,
        abilities_per_rotation=2.0,
        average_combat_seconds=4.0,
        ability_breakdown=shared_breakdown,
    )
    long_fight = ChampionProfile(
        champion_name="LongFight",
        base_hp=700,
        base_armor=30,
        base_mr=30,
        abilities_per_rotation=4.0,
        average_combat_seconds=12.0,
        ability_breakdown=shared_breakdown,
    )

    short_eval = BuildOptimizer(short_fight, ramp_items, rune_pages=neutral_runes)._evaluate_build(ramp_items, ramp_items, weights, enemy, rune_page=neutral_runes[0])
    long_eval = BuildOptimizer(long_fight, ramp_items, rune_pages=neutral_runes)._evaluate_build(ramp_items, ramp_items, weights, enemy, rune_page=neutral_runes[0])

    assert long_eval.metrics["stack_uptime"] > short_eval.metrics["stack_uptime"]
    assert long_eval.trace["realized_damage_amp_total"] > short_eval.trace["realized_damage_amp_total"]
    assert long_eval.metrics["damage"] > short_eval.metrics["damage"]


def test_named_item_archetypes_raise_expected_proc_and_stack_biases():
    opt = BuildOptimizer(_strict_profile(), _fake_items())

    profile = opt._item_proc_archetypes([
        ItemStats(item_id="1", name="Nashor's Tooth", total_gold=3000),
        ItemStats(item_id="2", name="Terminus", total_gold=3000),
        ItemStats(item_id="3", name="Liandry's Torment", total_gold=3000),
    ])

    assert profile["proc_bias"] > 0.0
    assert profile["stack_bias"] > 0.0
    assert profile["amp_realization_bonus"] > 0.0
    assert profile["max_hp_proc_multiplier"] > 1.0


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


def test_briar_tags_penalize_off_class_ap_candidate_pool():
    items = [
        ItemStats(item_id="1", name="Nashor's Tooth", total_gold=3000, ap=110, attack_speed=0.5, tags=("SpellDamage", "AttackSpeed")),
        ItemStats(item_id="2", name="The Collector", total_gold=3000, ad=60, flat_armor_pen=10, tags=("Damage", "CriticalStrike", "ArmorPenetration")),
        ItemStats(item_id="3", name="Profane Hydra", total_gold=3300, ad=65, lifesteal=0.12, tags=("Damage", "LifeSteal")),
    ]
    profile = ChampionProfile(
        champion_name="Briar",
        base_hp=700,
        base_armor=32,
        base_mr=30,
        abilities_per_rotation=3.0,
        average_combat_seconds=8.0,
        ability_breakdown={
            "q": {"ap_ratio": 0.8, "base_damage": [100, 150, 200, 250, 300], "cooldown": [10, 9, 8, 7, 6]},
            "w": {"ad_ratio": 0.7, "base_damage": [80, 120, 160, 200, 240], "cooldown": [9, 8, 7, 6, 5], "damage_type": "physical"},
            "e": {"ad_ratio": 0.6, "base_damage": [70, 105, 140, 175, 210], "cooldown": [12, 11, 10, 9, 8], "damage_type": "physical"},
            "r": {"ad_ratio": 1.0, "base_damage": [150, 250, 350], "cooldown": [120, 100, 80], "damage_type": "physical"},
        },
        champion_tags=("Fighter", "Assassin"),
    )

    pool = BuildOptimizer(profile, items)._candidate_pool(2)

    assert pool
    assert all("Nashor" not in x.name for x in pool)


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

