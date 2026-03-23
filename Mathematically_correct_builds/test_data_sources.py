import json
from unittest.mock import patch

import pytest

from data_sources import DataSourceError, LeagueWikiClient, WikiScalingParser, override_champion_scaling


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _make_briar_breakdown() -> dict:
    """Realistic ability breakdown fixture for Briar used across multiple tests."""
    def _ability(name, ad=0.0, ap=0.0, attack_speed=0.0, ms=0.0, heal=0.0,
                 damage_type="physical", targeting="single_target",
                 base=None, cd=None, has_damage_reduction=False):
        return {
            "name": name,
            "ad_ratio": ad,
            "ap_ratio": ap,
            "attack_speed_ratio": attack_speed,
            "ms_ratio": ms,
            "heal_ratio": heal,
            "hp_ratio": 0.0,
            "bonus_hp_ratio": 0.0,
            "armor_ratio": 0.0,
            "mr_ratio": 0.0,
            "scaling_components": [],
            "scaling_by_application": {"damage": [], "heal": [], "shield": [], "dot": [], "buff_debuff": []},
            "base_damage": base or [80.0, 120.0, 160.0],
            "cooldown": cd or [10.0, 9.0, 8.0],
            "cost": [],
            "resource": "",
            "raw_text": "",
            "source": "wiki-structured-strict",
            "damage_type": damage_type,
            "targeting": targeting,
            "on_hit": False,
            "is_channeled": False,
            "is_conditional": False,
            "is_stack_scaling": False,
            "range_units": 550.0,
            "has_damage_reduction": has_damage_reduction,
            "damage_reduction_ratio": 0.2 if has_damage_reduction else 0.0,
        }
    return {
        "q": _ability("Q", ad=0.8, ap=0.3, damage_type="mixed", base=[60.0, 100.0, 140.0], cd=[8.0, 7.0, 6.0]),
        "w": _ability("W", attack_speed=0.15, ms=0.25, damage_type="physical", base=[20.0, 30.0, 40.0], cd=[12.0, 11.0, 10.0]),
        "e": _ability("E", ad=0.4, ap=0.4, damage_type="magic", has_damage_reduction=True, base=[80.0, 120.0, 160.0], cd=[16.0, 15.0, 14.0]),
        "r": _ability("R", ap=0.6, damage_type="magic", base=[150.0, 250.0, 350.0], cd=[120.0, 100.0, 80.0]),
    }


def test_strict_scaling_requires_structured_payload(monkeypatch):
    parser = WikiScalingParser()
    briar_breakdown = _make_briar_breakdown()
    monkeypatch.setattr(parser, "_extract_from_wiki_templates", lambda champion: briar_breakdown)
    monkeypatch.setattr(parser, "_get_rendered_text", lambda champion: "rendered wiki text for Briar")
    monkeypatch.setattr(parser, "_extract_sections", lambda text: {"q": "Q deals 80% AD physical damage.", "w": "W grants attack speed.", "e": "E channels for magic damage with 40% AD + 40% AP.", "r": "R deals 60% AP magic damage."})
    monkeypatch.setattr(parser, "_extract_from_rendered_sections", lambda sections: {})
    monkeypatch.setattr(parser.cache, "set", lambda *args, **kwargs: None)

    scaling = parser.get_scaling("Briar", force_refresh=True)
    assert scaling.source == "wiki-structured-strict+rendered-merge"
    assert "q" in scaling.ability_breakdown
    assert "e" in scaling.ability_breakdown


def test_briar_preserves_mixed_ad_ap_and_utility_scalings(monkeypatch):
    parser = WikiScalingParser()
    briar_breakdown = _make_briar_breakdown()
    monkeypatch.setattr(parser, "_extract_from_wiki_templates", lambda champion: briar_breakdown)
    monkeypatch.setattr(parser, "_get_rendered_text", lambda champion: "rendered wiki text for Briar")
    monkeypatch.setattr(parser, "_extract_sections", lambda text: {"q": "Q deals 80% AD physical damage.", "w": "W grants attack speed.", "e": "E channels for magic damage with 40% AD + 40% AP.", "r": "R deals 60% AP magic damage."})
    monkeypatch.setattr(parser, "_extract_from_rendered_sections", lambda sections: {})
    monkeypatch.setattr(parser.cache, "set", lambda *args, **kwargs: None)

    scaling = parser.get_scaling("Briar", force_refresh=True, use_ai_fallback=False)
    q = scaling.ability_breakdown.get("q", {})
    w = scaling.ability_breakdown.get("w", {})
    e = scaling.ability_breakdown.get("e", {})
    r = scaling.ability_breakdown.get("r", {})

    # Q and E should preserve AD+AP coexistence even when damage type is not pure AD.
    assert float(q.get("ad_ratio", 0.0) or 0.0) > 0.0
    assert float(q.get("ap_ratio", 0.0) or 0.0) > 0.0
    assert float(e.get("ad_ratio", 0.0) or 0.0) > 0.0
    assert float(e.get("ap_ratio", 0.0) or 0.0) > 0.0

    # Briar utility patterns include AS/MS scaling.
    assert float(w.get("attack_speed_ratio", 0.0) or 0.0) > 0.0
    assert float(w.get("ms_ratio", 0.0) or 0.0) > 0.0

    # R has AP and may include AD terms depending on section parsing.
    assert float(r.get("ap_ratio", 0.0) or 0.0) > 0.0



def test_override_champion_scaling_rewrites_per_ability_values():
    from data_sources import ChampionScaling
    base_breakdown = {
        "q": {"ad_ratio": 0.0, "ap_ratio": 0.6, "attack_speed_ratio": 0.0, "heal_ratio": 0.0,
               "base_damage": [80.0, 120.0], "cooldown": [8.0, 6.0], "cost": [], "resource": ""},
        "w": {"ad_ratio": 0.5, "ap_ratio": 0.0, "attack_speed_ratio": 0.0, "heal_ratio": 0.0,
               "base_damage": [50.0, 90.0], "cooldown": [10.0], "cost": [], "resource": ""},
        "e": {"ad_ratio": 0.4, "ap_ratio": 0.0, "attack_speed_ratio": 0.0, "heal_ratio": 0.0,
               "base_damage": [40.0, 80.0], "cooldown": [8.0], "cost": [], "resource": ""},
        "r": {"ad_ratio": 0.0, "ap_ratio": 0.8, "attack_speed_ratio": 0.0, "heal_ratio": 0.0,
               "base_damage": [150.0, 300.0], "cooldown": [100.0], "cost": [], "resource": ""},
    }
    scaling = ChampionScaling(source="wiki-structured-strict", ability_breakdown=base_breakdown)

    overridden = override_champion_scaling(scaling, {"q": {"ap_ratio": 0.9}, "passive": {"heal_ratio": 0.3}})

    assert overridden.ability_breakdown["q"]["ap_ratio"] == 0.9
    assert overridden.ability_breakdown["passive"]["heal_ratio"] == 0.3
    # Other abilities are unchanged
    assert overridden.ability_breakdown["w"]["ad_ratio"] == 0.5


def test_wiki_scaling_saved_overrides_round_trip():
    parser = WikiScalingParser()
    champion = "Unit Test Champion"
    overrides = {"q": {"ad_ratio": 0.8}, "passive": {"heal_ratio": 0.25}}

    parser.save_overrides(champion, overrides)
    loaded = parser.get_saved_overrides(champion)

    assert loaded == overrides


def test_champion_profile_includes_tags_from_wiki_payload():
    client = LeagueWikiClient()
    patch_version = "99.99.1"
    slug = "UnitChamp"

    champion_module_payload = {
        "parse": {
            "wikitext": {
                "*": (
                    'return { '
                    '["Unit Champ"] = {'
                    '["apiname"] = "UnitChamp",'
                    '["role"] = {"Mage", "Assassin"},'
                    '["stats"] = {["hp_base"] = 700, ["arm_base"] = 32, ["mr_base"] = 30}'
                    '}'
                    ' }'
                )
            }
        }
    }

    def _fake_get(url, *args, **kwargs):
        params = kwargs.get("params", {})
        if params.get("page") == "Module:ChampionData/data":
            return _FakeResponse(champion_module_payload)
        return _FakeResponse({}, status_code=404)

    with patch("data_sources.requests.get", side_effect=_fake_get), patch("data_sources._http_get", side_effect=_fake_get), patch.object(client, "_slug_for_champion", return_value=slug):
        profile = client.get_champion_profile(patch_version, "Unit Champ", force_refresh=True)

    assert "Mage" in profile.champion_tags
    assert "Assassin" in profile.champion_tags


def test_ai_fallback_fills_missing_signals_from_wiki_sections(monkeypatch):
    parser = WikiScalingParser()

    minimal_breakdown = {
        key: {
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
            "base_damage": [],
            "cooldown": [1.0],
            "cost": [],
            "resource": "",
            "raw_text": "",
            "source": "wiki-template",
        }
        for key in ("q", "w", "e", "r")
    }

    monkeypatch.setattr(parser, "_extract_from_wiki_templates", lambda champion: minimal_breakdown)
    monkeypatch.setattr(parser, "_get_rendered_text", lambda champion: "rendered wiki text")
    monkeypatch.setattr(
        parser,
        "_extract_sections",
        lambda text: {
            "q": "Q deals 80% AP damage.",
            "w": "W utility.",
            "e": "E utility.",
            "r": "R utility.",
        },
    )
    monkeypatch.setattr(parser, "_extract_from_rendered_sections", lambda sections: minimal_breakdown)

    class _FakeAiResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "response": json.dumps(
                    {
                        "q": {"ap_ratio": 80, "base_damage": [70, 110, 150], "cooldown": [8, 7, 6]},
                        "w": {"ad_ratio": 20, "cooldown": [12, 11, 10]},
                        "e": {"ad_ratio": 15, "cooldown": [16, 15, 14]},
                        "r": {"ap_ratio": 60, "cooldown": [120, 100, 80]},
                    }
                )
            }

    monkeypatch.setattr("data_sources.requests.post", lambda *args, **kwargs: _FakeAiResponse())

    scaling = parser.get_scaling("Unit AI Champ", force_refresh=True, use_ai_fallback=True)

    assert scaling.source == "wiki-structured-strict+ai-fallback"
    assert scaling.ability_breakdown["q"]["ap_ratio"] == pytest.approx(0.8)
    assert scaling.ability_breakdown["q"]["base_damage"] == [70.0, 110.0, 150.0]


def test_extract_ratio_values_supports_bad_shortform():
    parser = WikiScalingParser()

    ratios = parser._extract_ratio_values("Deals physical damage equal to 80% bAD.")

    assert ratios["ad_ratio"] == pytest.approx(0.8)


def test_extract_ratio_values_supports_movement_speed_ratio():
    parser = WikiScalingParser()

    ratios = parser._extract_ratio_values("Gain 24% bonus movement speed for 5 seconds.")

    assert ratios["ms_ratio"] == pytest.approx(0.24)


def test_materialize_wiki_template_text_resolves_nested_vars_and_expr():
    parser = WikiScalingParser()

    template_text = (
        "{{#vardefine:ratio|80}}\n"
        "{{#vardefine:bonus|{{#var:ratio}}}}\n"
        "| scaling = {{#expr: {{#var:bonus}} / 100 }} bonus AD\n"
    )

    materialized = parser._materialize_wiki_template_text(template_text)

    assert "0.8" in materialized
    assert "{{#var:" not in materialized


def test_get_scaling_rebuilds_when_cached_breakdown_has_no_signals(monkeypatch):
    parser = WikiScalingParser()

    stale_breakdown = {
        key: {
            "name": key.upper(),
            "ad_ratio": 0.0,
            "ap_ratio": 0.0,
            "attack_speed_ratio": 0.0,
            "heal_ratio": 0.0,
            "hp_ratio": 0.0,
            "bonus_hp_ratio": 0.0,
            "armor_ratio": 0.0,
            "mr_ratio": 0.0,
            "base_damage": [],
            "cooldown": [1.0],
            "cost": [],
            "resource": "",
            "raw_text": "",
            "source": "wiki-template",
        }
        for key in ("q", "w", "e", "r")
    }

    fresh_breakdown = {
         "q": {"name": "Q", "ad_ratio": 0.8, "ap_ratio": 0.0, "attack_speed_ratio": 0.0, "ms_ratio": 0.0, "heal_ratio": 0.0,
               "hp_ratio": 0.0, "bonus_hp_ratio": 0.0, "armor_ratio": 0.0, "mr_ratio": 0.0,
               "base_damage": [70.0, 100.0], "cooldown": [8.0], "cost": [], "resource": "", "raw_text": "", "source": "wiki-template"},
         "w": {"name": "W", "ad_ratio": 0.2, "ap_ratio": 0.0, "attack_speed_ratio": 0.0, "ms_ratio": 0.0, "heal_ratio": 0.0,
               "hp_ratio": 0.0, "bonus_hp_ratio": 0.0, "armor_ratio": 0.0, "mr_ratio": 0.0,
               "base_damage": [40.0, 80.0], "cooldown": [10.0], "cost": [], "resource": "", "raw_text": "", "source": "wiki-template"},
         "e": {"name": "E", "ad_ratio": 0.1, "ap_ratio": 0.0, "attack_speed_ratio": 0.0, "ms_ratio": 0.0, "heal_ratio": 0.0,
               "hp_ratio": 0.0, "bonus_hp_ratio": 0.0, "armor_ratio": 0.0, "mr_ratio": 0.0,
               "base_damage": [30.0, 60.0], "cooldown": [12.0], "cost": [], "resource": "", "raw_text": "", "source": "wiki-template"},
         "r": {"name": "R", "ad_ratio": 0.3, "ap_ratio": 0.0, "attack_speed_ratio": 0.0, "ms_ratio": 0.0, "heal_ratio": 0.0,
               "hp_ratio": 0.0, "bonus_hp_ratio": 0.0, "armor_ratio": 0.0, "mr_ratio": 0.0,
               "base_damage": [100.0, 200.0], "cooldown": [100.0], "cost": [], "resource": "", "raw_text": "", "source": "wiki-template"},
    }

    stale_payload = {
        "source": "wiki-structured-strict",
        "ability_breakdown": stale_breakdown,
        "placeholder_used": False,
        "fallback_reasons": [],
    }

    monkeypatch.setattr(parser.cache, "get", lambda *args, **kwargs: stale_payload)
    monkeypatch.setattr(parser, "_extract_from_wiki_templates", lambda champion: fresh_breakdown)
    monkeypatch.setattr(parser, "_get_rendered_text", lambda champion: "")
    monkeypatch.setattr(parser, "_extract_sections", lambda text: {"q": "", "w": "", "e": "", "r": ""})
    monkeypatch.setattr(parser, "_extract_from_rendered_sections", lambda sections: {})
    monkeypatch.setattr(parser.cache, "set", lambda *args, **kwargs: None)

    scaling = parser.get_scaling("Unit Cache Champ", force_refresh=False, use_ai_fallback=False)

    assert scaling.ability_breakdown["q"]["ad_ratio"] == pytest.approx(0.8)


def test_extract_scaling_components_keeps_multiple_applications():
    parser = WikiScalingParser()

    components = parser._extract_scaling_components(
        "Deals 80% bonus AD damage and heals for 40% AP. Grants shield scaling with 20% bonus health."
    )
    grouped = parser._group_components_by_application(components)

    damage_stats = {(x["stat"], x["modifier"], x["ratio"]) for x in grouped["damage"]}
    heal_stats = {(x["stat"], x["ratio"]) for x in grouped["heal"]}
    shield_stats = {(x["stat"], x["ratio"]) for x in grouped["shield"]}

    assert ("ad", "bonus", 0.8) in damage_stats
    assert ("ap", 0.4) in heal_stats
    assert ("bonus_hp", 0.2) in shield_stats


def test_latest_patch_uses_revision_fingerprint_when_available(monkeypatch):
    client = LeagueWikiClient()

    def _fake_get(url, *args, **kwargs):
        params = kwargs.get("params", {})
        title = params.get("titles", "")
        if title == "Module:ChampionData/data":
            return _FakeResponse({"query": {"pages": {"1": {"revisions": [{"timestamp": "2026-03-23T10:00:00Z"}]}}}})
        if title == "Module:ItemData/data":
            return _FakeResponse({"query": {"pages": {"1": {"revisions": [{"timestamp": "2026-03-23T11:00:00Z"}]}}}})
        return _FakeResponse({}, status_code=404)

    monkeypatch.setattr("data_sources._http_get", _fake_get)
    monkeypatch.setattr("data_sources.requests.get", _fake_get)
    monkeypatch.setattr(client.cache, "set", lambda *a, **kw: None)

    patch = client.get_latest_patch(force_refresh=True)

    assert patch.startswith("wiki-rev-")


# ---------------------------------------------------------------------------
# Phase 2 enrichment extractor unit tests
# ---------------------------------------------------------------------------

class TestEnrichmentExtractors:
    """Unit tests for the 7 per-ability enrichment static methods on WikiScalingParser."""

    def setup_method(self):
        self.p = WikiScalingParser()

    # _extract_damage_type
    def test_damage_type_physical(self):
        assert self.p._extract_damage_type("Deals 100 physical damage to the target.") == "physical"

    def test_damage_type_magic(self):
        assert self.p._extract_damage_type("The explosion deals 80 magic damage to all enemies.") == "magic"

    def test_damage_type_true(self):
        assert self.p._extract_damage_type("Inflicts 50 true damage.") == "true"

    def test_damage_type_mixed(self):
        assert self.p._extract_damage_type(
            "Deals 60 physical damage and 40 magic damage."
        ) == "mixed"

    def test_damage_type_none_for_utility(self):
        result = self.p._extract_damage_type("Grants a shield equal to 20% of maximum health.")
        assert result in ("none", "unknown")

    def test_damage_type_empty_string_is_unknown(self):
        assert self.p._extract_damage_type("") == "unknown"

    # _extract_targeting
    def test_targeting_aoe(self):
        assert self.p._extract_targeting("Deals damage to all nearby enemies in an area.") == "aoe"

    def test_targeting_directional(self):
        assert self.p._extract_targeting("Fires a bolt in a line.") == "directional"

    def test_targeting_single_target(self):
        assert self.p._extract_targeting("Targets a single enemy champion.") == "single_target"

    def test_targeting_unknown_when_empty(self):
        assert self.p._extract_targeting("") == "unknown"

    # _extract_on_hit
    def test_on_hit_detected(self):
        assert self.p._extract_on_hit("This attack applies on-hit effects.") is True

    def test_on_hit_absent(self):
        assert self.p._extract_on_hit("Deals magic damage in a cone.") is False

    # _extract_channeled
    def test_channeled_detected(self):
        assert self.p._extract_channeled("Channels for 2 seconds dealing damage.") is True

    def test_channeled_absent(self):
        assert self.p._extract_channeled("Instantly dashes to target.") is False

    # _is_conditional
    def test_conditional_below_threshold(self):
        assert self.p._is_conditional("Deals bonus damage if the target is below 50% health.") is True

    def test_conditional_absent(self):
        assert self.p._is_conditional("Deals 200 magic damage.") is False

    # _is_stack_scaling
    def test_stack_scaling_detected(self):
        assert self.p._is_stack_scaling("Gains 2 damage per stack, up to 400 stacks.") is True

    def test_stack_scaling_absent(self):
        assert self.p._is_stack_scaling("Deals 100 physical damage.") is False

    # _extract_range
    def test_range_extracted(self):
        assert self.p._extract_range("650 range targeted ability.") == 650.0

    def test_range_zero_when_absent(self):
        assert self.p._extract_range("Melee attack.") == 0.0

    # _extract_unique_passive_names
    def test_unique_passive_names_parses_wiki_format(self):
        effects_text = (
            "UNIQUE \u2013 Carve: Applies a stack (max 6).\n"
            "UNIQUE \u2013 Rage: Kills grant movement speed."
        )
        names = LeagueWikiClient._extract_unique_passive_names(effects_text)
        assert "carve" in names
        assert "rage" in names

    def test_unique_passive_names_filters_generic_words(self):
        effects_text = "UNIQUE - Passive: Deals bonus damage.\nUNIQUE \u2013 Active: Use to gain speed."
        names = LeagueWikiClient._extract_unique_passive_names(effects_text)
        # "passive" and "active" are generic and must be excluded
        assert "passive" not in names
        assert "active" not in names

    def test_unique_passive_names_deduplicates(self):
        effects_text = (
            "UNIQUE \u2013 Carve: text one.\nUNIQUE \u2013 Carve: text two."
        )
        names = LeagueWikiClient._extract_unique_passive_names(effects_text)
        assert names.count("carve") == 1


