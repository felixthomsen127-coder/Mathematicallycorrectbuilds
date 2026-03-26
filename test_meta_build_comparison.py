import pytest
from pathlib import Path

import meta_build_comparison
from meta_build_comparison import BlitzMetaClient, MetaBuildSample, OpggMetaClient, UggMetaClient, compare_optimizer_build_to_ugg


_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (_FIXTURES_DIR / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_meta_cache():
    meta_build_comparison._META_BUILD_CACHE.clear()


def test_compare_modes_include_all_and_context(monkeypatch):
    samples = [
        MetaBuildSample(source="u.gg", label="meta-a", item_names=["Sword", "Shield", "Boots"], win_rate=0.52),
        MetaBuildSample(source="u.gg", label="meta-b", item_names=["Rod", "Hat", "Boots"], win_rate=0.55),
    ]

    monkeypatch.setattr(UggMetaClient, "fetch_top_builds", lambda self, champion, role, tier, region, patch, **kw: samples)

    def _eval(names):
        if "Sword" in names:
            return {
                "weighted_score": 100.0,
                "metrics": {"damage": 200.0, "healing": 10.0, "tankiness": 30.0, "lifesteal": 0.1},
            }
        return {
            "weighted_score": 80.0,
            "metrics": {"damage": 150.0, "healing": 20.0, "tankiness": 40.0, "lifesteal": 0.05},
        }

    result = compare_optimizer_build_to_ugg(
        champion="Aatrox",
        optimizer_item_names=["Sword", "Shield", "Boots"],
        role="jungle",
        comparison_mode="all",
        tier="emerald_plus",
        region="global",
        patch="live",
        optimizer_weighted_score=120.0,
        optimizer_metrics={"damage": 220.0, "healing": 10.0, "tankiness": 30.0, "lifesteal": 0.1},
        evaluate_meta_build_fn=_eval,
        item_id_to_name={"1": "Sword", "2": "Shield", "3": "Boots"},
    )

    assert result["available"] is True
    assert result["comparison_mode"] == "all"
    assert result["comparison_context"]["tier"] == "emerald_plus"
    assert "item_overlap" in result["modes"]
    assert "power_delta" in result["modes"]
    assert "component_balance" in result["modes"]


def test_rune_parser_extracts_structured_pages_from_json_payload():
    html = """
    <html><body>
      <script>
        {"props":{"pageProps":{"runes":[{"primaryTreeId":8000,"secondaryTreeId":8400,"perks":[8010,9104,8299,8473]}]}}}
      </script>
    </body></html>
    """
    client = UggMetaClient()
    pages = client._parse_runes_from_html(html)

    assert pages
    first = pages[0]
    assert first.primary_tree == "Precision"
    assert first.secondary_tree == "Resolve"
    assert "Conqueror" in first.rune_names


def test_rune_parser_does_not_fabricate_pages_from_plain_text():
    html = """
    <html><body>
      <script>
        const payload = "Conqueror Last Stand Bone Plating Electrocute Treasure Hunter";
      </script>
    </body></html>
    """
    client = UggMetaClient()
    pages = client._parse_runes_from_html(html)

    assert pages == []


def test_compare_works_without_item_id_map_when_named_samples_exist(monkeypatch):
    samples = [
        MetaBuildSample(source="u.gg", label="named", item_names=["Void Staff", "Rabadon's Deathcap", "Sorcerer's Shoes"]),
    ]

    monkeypatch.setattr(UggMetaClient, "fetch_top_builds", lambda self, champion, role, tier, region, patch, **kw: samples)

    result = compare_optimizer_build_to_ugg(
        champion="Lux",
        optimizer_item_names=["Void Staff", "Rabadon's Deathcap", "Sorcerer's Shoes"],
        comparison_mode="all",
        item_id_to_name=None,
    )

    assert result["available"] is True
    assert result["source"] == "u.gg"


def test_compare_surfaces_fetch_failure_details(monkeypatch):
    def _fake_fetch(self, champion, role, tier, region, patch, item_id_to_name=None):
        self.last_error = "https://u.gg/lol/champions/lux/build timed out"
        return []

    monkeypatch.setattr(UggMetaClient, "fetch_top_builds", _fake_fetch)

    result = compare_optimizer_build_to_ugg(
        champion="Lux",
        optimizer_item_names=["Void Staff", "Liandry's Torment", "Sorcerer's Shoes"],
        item_id_to_name={"3135": "Void Staff", "6653": "Liandry's Torment", "3020": "Sorcerer's Shoes"},
    )

    assert result["available"] is False
    assert "live providers" in result["reason"]
    assert any("timed out" in x for x in result.get("warnings", []))


def test_component_alignment_uses_symmetric_denominator(monkeypatch):
    samples = [
        MetaBuildSample(source="u.gg", label="meta-a", item_names=["Rod", "Hat", "Boots"], win_rate=0.55),
    ]

    monkeypatch.setattr(UggMetaClient, "fetch_top_builds", lambda self, champion, role, tier, region, patch, **kw: samples)

    def _eval(_names):
        return {
            "weighted_score": 80.0,
            "metrics": {"damage": 100.0, "healing": 0.0, "tankiness": 0.0, "lifesteal": 0.0},
        }

    result = compare_optimizer_build_to_ugg(
        champion="Aatrox",
        optimizer_item_names=["Sword", "Shield", "Boots"],
        optimizer_weighted_score=120.0,
        optimizer_metrics={"damage": 0.0, "healing": 0.0, "tankiness": 0.0, "lifesteal": 0.0},
        evaluate_meta_build_fn=_eval,
        item_id_to_name={"1": "Sword", "2": "Shield", "3": "Boots"},
    )

    first = result["modes"]["component_balance"][0]
    assert 0.0 <= float(first["component_alignment"]) <= 1.0
    assert float(first["component_alignment"]) > 0.70


def test_curated_fallback_is_used_when_live_has_no_samples(monkeypatch):
    def _ugg_empty(self, champion, role, tier, region, patch, item_id_to_name=None):
        self.last_error = "u.gg parse produced no rows"
        return []

    monkeypatch.setattr(UggMetaClient, "fetch_top_builds", _ugg_empty)

    result = compare_optimizer_build_to_ugg(
        champion="Briar",
        optimizer_item_names=["The Collector", "Profane Hydra", "Mercury's Treads"],
        role="jungle",
        patch="16.6",
        item_id_to_name={"1": "The Collector", "2": "Profane Hydra", "3": "Mercury's Treads"},
    )

    assert result["available"] is True
    assert result["source"] == "u.gg"
    assert result.get("fallback_used") is False
    assert any("curated" in str(x).lower() for x in result.get("warnings", []))


def test_live_provider_failure_uses_combined_reason(monkeypatch):
    def _ugg_empty(self, champion, role, tier, region, patch, item_id_to_name=None):
        self.last_error = "u.gg timeout"
        return []

    monkeypatch.setattr(UggMetaClient, "fetch_top_builds", _ugg_empty)

    result = compare_optimizer_build_to_ugg(
        champion="Lux",
        optimizer_item_names=["Void Staff", "Rabadon's Deathcap", "Sorcerer's Shoes"],
        item_id_to_name={"3135": "Void Staff", "3089": "Rabadon's Deathcap", "3020": "Sorcerer's Shoes"},
    )

    assert result["available"] is False
    assert "live providers" in result["reason"]


def test_no_fallback_flag_when_live_source_available(monkeypatch):
    live_samples = [
        MetaBuildSample(source="lolalytics", label="live-a", item_names=["Sword", "Shield", "Boots"]),
    ]

    monkeypatch.setattr(UggMetaClient, "fetch_top_builds", lambda self, champion, role, tier, region, patch, **kw: live_samples)

    result = compare_optimizer_build_to_ugg(
        champion="Aatrox",
        optimizer_item_names=["Sword", "Shield", "Boots"],
        role="jungle",
        item_id_to_name={"1": "Sword", "2": "Shield", "3": "Boots"},
    )

    assert result["available"] is True
    assert result["source"] == "lolalytics"
    assert result.get("fallback_used") is False


def test_parse_builds_returns_empty_when_no_structured_payloads(monkeypatch):
    monkeypatch.setattr(meta_build_comparison, "_extract_json_script_payloads", lambda _html: [])

    client = UggMetaClient()
    rows = client._parse_builds_from_html(
        "<html><body><div>No structured payloads</div></body></html>",
        item_id_to_name={"1": "Sword", "2": "Shield", "3": "Boots", "4": "Dagger"},
    )

    assert rows == []


def test_parse_builds_can_extract_named_arrays_without_id_map(monkeypatch):
    payload = {
        "build": {
            "items": ["Void Staff", "Rabadon's Deathcap", "Sorcerer's Shoes", "Shadowflame"],
        }
    }
    monkeypatch.setattr(meta_build_comparison, "_extract_json_script_payloads", lambda _html: [payload])

    client = UggMetaClient()
    rows = client._parse_builds_from_html("<html></html>", item_id_to_name=None)

    assert rows
    assert rows[0].label == "structured-json"
    assert "Void Staff" in rows[0].item_names


def test_cache_fallback_is_used_when_all_providers_fail(monkeypatch):
    meta_build_comparison._META_BUILD_CACHE.clear()

    primed = [
        MetaBuildSample(source="u.gg", label="prime", item_names=["Sword", "Shield", "Boots"]),
    ]

    monkeypatch.setattr(UggMetaClient, "fetch_top_builds", lambda self, champion, role, tier, region, patch, **kw: primed)
    first = compare_optimizer_build_to_ugg(
        champion="Aatrox",
        optimizer_item_names=["Sword", "Shield", "Boots"],
        role="jungle",
        item_id_to_name={"1": "Sword", "2": "Shield", "3": "Boots"},
    )
    assert first["available"] is True
    assert first.get("cache_used") is False

    def _fail_ugg(self, champion, role, tier, region, patch, item_id_to_name=None):
        self.last_error = "u.gg timeout"
        return []

    monkeypatch.setattr(UggMetaClient, "fetch_top_builds", _fail_ugg)

    cached = compare_optimizer_build_to_ugg(
        champion="Aatrox",
        optimizer_item_names=["Sword", "Shield", "Boots"],
        role="jungle",
        item_id_to_name={"1": "Sword", "2": "Shield", "3": "Boots"},
    )

    assert cached["available"] is True
    assert cached.get("cache_used") is True
    assert cached.get("live_fetch_failed") is True
    assert any("cached" in str(x).lower() for x in cached.get("warnings", []))


def test_curated_briar_baseline_used_when_live_fetch_fails(monkeypatch):
    def _ugg_empty(self, champion, role, tier, region, patch, item_id_to_name=None):
        self.last_error = "u.gg timeout"
        return []

    monkeypatch.setattr(UggMetaClient, "fetch_top_builds", _ugg_empty)

    result = compare_optimizer_build_to_ugg(
        champion="Briar",
        optimizer_item_names=["The Collector", "Profane Hydra", "Mercury's Treads"],
        role="jungle",
        patch="16.6",
        item_id_to_name={"1": "The Collector", "2": "Profane Hydra", "3": "Mercury's Treads"},
    )

    assert result["available"] is True
    assert result["source"] == "u.gg"
    assert result.get("fallback_used") is False
    assert result["meta_builds"]
    first_items = result["meta_builds"][0]["items"]
    assert "The Collector" in first_items


def test_blitz_parser_extracts_window_nuxt_keyed_build_fixture():
    html = _read_fixture("blitz_window_nuxt.html")
    client = BlitzMetaClient()
    rows = client._parse_builds_from_html(html, item_id_to_name=None)

    assert rows
    assert rows[0].source == "blitz.gg"
    assert rows[0].label in {"blitz-keyed-json", "structured-json"}
    assert "Kraken Slayer" in rows[0].item_names


def test_opgg_parser_extracts_keyed_build_fixture():
    html = _read_fixture("opgg_keyed_builds.html")
    client = OpggMetaClient()
    rows = client._parse_builds_from_html(html, item_id_to_name=None)

    assert rows
    assert rows[0].source == "op.gg"
    assert rows[0].label in {"opgg-keyed-json", "structured-json"}
    assert "Luden's Companion" in rows[0].item_names
