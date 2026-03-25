"""
Tests for simulation.py — burst_damage, dps_simulation, compute_total_stats.
"""
import pytest

from optimizer import ItemStats
import simulation as sim


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _physical_ability(ad_ratio: float = 1.0, base: float = 100.0, cd: float = 8.0) -> dict:
    return {
        "ad_ratio": ad_ratio,
        "ap_ratio": 0.0,
        "bonus_hp_ratio": 0.0,
        "damage_type": "physical",
        "targeting": "single_target",
        "on_hit": False,
        "is_channeled": False,
        "is_conditional": False,
        "is_stack_scaling": False,
        "range_units": 550.0,
        "base_damage": [base],
        "cooldown": [cd],
        "scaling_components": [],
    }


def _magic_ability(ap_ratio: float = 0.8, base: float = 150.0, cd: float = 10.0) -> dict:
    return {
        "ad_ratio": 0.0,
        "ap_ratio": ap_ratio,
        "bonus_hp_ratio": 0.0,
        "damage_type": "magic",
        "targeting": "aoe",
        "on_hit": False,
        "is_channeled": False,
        "is_conditional": False,
        "is_stack_scaling": False,
        "range_units": 0.0,
        "base_damage": [base],
        "cooldown": [cd],
        "scaling_components": [],
    }


def _no_items():
    return []


def _ad_item(ad: float = 60.0) -> ItemStats:
    return ItemStats(item_id="t1", name="Test Sword", total_gold=3000, ad=ad)


def _ap_item(ap: float = 120.0) -> ItemStats:
    return ItemStats(item_id="t2", name="Test Rod", total_gold=3000, ap=ap)


# ---------------------------------------------------------------------------
# compute_total_stats
# ---------------------------------------------------------------------------

class TestComputeTotalStats:

    def test_no_items_returns_base_values(self):
        stats = sim.compute_total_stats(None, [], level=18)
        assert stats["total_ad"] > 0
        assert stats["total_hp"] > 0
        assert stats["total_attack_speed"] > 0

    def test_ad_item_adds_to_total_ad(self):
        stats_base = sim.compute_total_stats(None, [], level=18)
        stats_with = sim.compute_total_stats(None, [_ad_item(60)], level=18)
        assert stats_with["total_ad"] == pytest.approx(stats_base["total_ad"] + 60, abs=0.1)

    def test_ap_item_adds_to_total_ap(self):
        stats = sim.compute_total_stats(None, [_ap_item(120)], level=18)
        assert stats["total_ap"] == pytest.approx(120.0, abs=0.1)

    def test_custom_base_stats_applied(self):
        base = {"base_ad": 70.0, "ad_growth": 4.0, "base_hp": 650.0, "hp_growth": 100.0}
        stats = sim.compute_total_stats(base, [], level=18)
        # At level 18: 70 + 4*1 = 74  (growth factor at L18 = 1.0)
        assert stats["total_ad"] == pytest.approx(74.0, abs=0.3)

    def test_attack_speed_safety_ceiling(self):
        # The safety ceiling is 5.0 (LoL has no practical item AS cap; 2.5 was incorrect)
        huge_as = ItemStats(item_id="x", name="X", total_gold=100, attack_speed=20.0)
        stats = sim.compute_total_stats(None, [huge_as], level=18)
        assert stats["total_attack_speed"] <= 5.0
        assert stats["total_attack_speed"] > 2.5  # confirms old cap is no longer enforced


# ---------------------------------------------------------------------------
# _post_mitigation_factor
# ---------------------------------------------------------------------------

class TestMitigationFactors:

    def test_zero_armor_means_full_damage(self):
        factor = sim._post_mitigation_factor(0.0, 0.0, 0.0, 0.0, 18)
        assert factor == pytest.approx(1.0)

    def test_100_armor_gives_half_damage(self):
        factor = sim._post_mitigation_factor(100.0, 0.0, 0.0, 0.0, 18)
        assert factor == pytest.approx(0.5, abs=0.01)

    def test_armor_pen_reduces_effective_armor(self):
        no_pen = sim._post_mitigation_factor(100.0, 0.0, 0.0, 0.0, 18)
        with_pen = sim._post_mitigation_factor(100.0, 0.30, 0.0, 0.0, 18)
        assert with_pen > no_pen

    def test_flat_armor_pen_reduces_effective_armor(self):
        no_flat = sim._post_mitigation_factor(60.0, 0.0, 0.0, 0.0, 18)
        with_flat = sim._post_mitigation_factor(60.0, 0.0, 20.0, 0.0, 18)
        assert with_flat > no_flat

    def test_magic_pen_reduces_mr(self):
        no_pen = sim._post_mitigation_magic_factor(50.0, 0.0, 0.0)
        with_pen = sim._post_mitigation_magic_factor(50.0, 0.35, 0.0)
        assert with_pen > no_pen


# ---------------------------------------------------------------------------
# burst_damage
# ---------------------------------------------------------------------------

class TestBurstDamage:

    def _breakdown(self):
        return {
            "q": _physical_ability(1.0, 100.0),
            "w": _physical_ability(0.8, 80.0),
            "e": _physical_ability(0.6, 60.0),
            "r": _physical_ability(1.2, 200.0),
        }

    def test_burst_returns_positive_total(self):
        result = sim.burst_damage(self._breakdown(), {}, _no_items())
        assert result["total"] > 0

    def test_burst_breakdown_has_all_ability_keys(self):
        result = sim.burst_damage(self._breakdown(), {}, _no_items())
        assert set(result["per_ability"].keys()) >= {"q", "w", "e", "r"}

    def test_burst_increases_with_more_ad(self):
        base = sim.burst_damage(self._breakdown(), {}, [])
        boosted = sim.burst_damage(self._breakdown(), {}, [_ad_item(100)])
        assert boosted["total"] > base["total"]

    def test_burst_magic_ability_uses_ap(self):
        breakdown = {"q": _magic_ability(0.8, 100.0)}
        with_ap = sim.burst_damage(breakdown, {}, [_ap_item(120)])
        without_ap = sim.burst_damage(breakdown, {}, [])
        assert with_ap["total"] > without_ap["total"]

    def test_burst_higher_armor_reduces_physical_damage(self):
        breakdown = {"q": _physical_ability(1.0, 0.0)}
        low_armor = sim.burst_damage(breakdown, {}, [_ad_item(100)], target_armor=0)
        high_armor = sim.burst_damage(breakdown, {}, [_ad_item(100)], target_armor=200)
        assert low_armor["total"] > high_armor["total"]

    def test_burst_detail_has_enrichment_fields(self):
        result = sim.burst_damage(self._breakdown(), {}, [])
        detail = result["breakdown_detail"]["q"]
        assert "damage_type" in detail
        assert "targeting" in detail
        assert "on_hit" in detail

    def test_true_damage_ignores_armor(self):
        true_ability = {
            "ad_ratio": 1.0, "ap_ratio": 0.0, "bonus_hp_ratio": 0.0,
            "damage_type": "true", "base_damage": [0.0], "cooldown": [8.0],
            "scaling_components": [], "on_hit": False, "is_channeled": False,
            "is_conditional": False, "is_stack_scaling": False, "range_units": 0.0,
        }
        low = sim.burst_damage({"q": true_ability}, {}, [_ad_item(100)], target_armor=0)
        high = sim.burst_damage({"q": true_ability}, {}, [_ad_item(100)], target_armor=300)
        assert low["total"] == pytest.approx(high["total"], rel=0.01)


# ---------------------------------------------------------------------------
# dps_simulation
# ---------------------------------------------------------------------------

class TestDpsSimulation:

    def _breakdown(self):
        return {
            "q": _physical_ability(1.0, 100.0, cd=6.0),
            "w": _physical_ability(0.8, 80.0, cd=10.0),
            "e": _physical_ability(0.6, 60.0, cd=8.0),
            "r": _physical_ability(1.2, 200.0, cd=90.0),
        }

    def test_dps_positive_for_basic_build(self):
        result = sim.dps_simulation(self._breakdown(), {}, [_ad_item(60)])
        assert result["dps"] > 0
        assert result["total_damage"] > 0

    def test_dps_includes_auto_attacks(self):
        result = sim.dps_simulation(self._breakdown(), {}, [], duration=10.0)
        assert result["auto_attacks"] > 0

    def test_dps_scales_with_duration(self):
        short = sim.dps_simulation(self._breakdown(), {}, [], duration=5.0)
        long_ = sim.dps_simulation(self._breakdown(), {}, [], duration=30.0)
        # More total damage over longer window
        assert long_["total_damage"] > short["total_damage"]

    def test_dps_increases_with_more_ad(self):
        base = sim.dps_simulation(self._breakdown(), {}, [])
        boosted = sim.dps_simulation(self._breakdown(), {}, [_ad_item(100)])
        assert boosted["dps"] > base["dps"]

    def test_dps_cast_counts_non_negative(self):
        result = sim.dps_simulation(self._breakdown(), {}, [], duration=20.0)
        for count in result["cast_counts"].values():
            assert count >= 0

    def test_dps_zero_duration_returns_zeros(self):
        result = sim.dps_simulation(self._breakdown(), {}, [], duration=0.0)
        assert result["dps"] == 0.0
        assert result["total_damage"] == 0.0
