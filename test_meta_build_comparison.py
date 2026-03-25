import meta_build_comparison
from meta_build_comparison import BlitzMetaClient, MetaBuildSample, OpggMetaClient, UggMetaClient, compare_optimizer_build_to_ugg


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


def test_compare_requires_item_id_map():
    result = compare_optimizer_build_to_ugg(
        champion="Lux",
        optimizer_item_names=["Void Staff", "Rabadon's Deathcap", "Sorcerer's Shoes"],
        comparison_mode="all",
        item_id_to_name=None,
    )

    assert result["available"] is False
    assert "item ID mapping" in result["reason"]
    assert result.get("warnings")


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
    assert "timed out" in result["reason"]
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


def test_blitz_fallback_is_used_when_ugg_has_no_samples(monkeypatch):
    def _ugg_empty(self, champion, role, tier, region, patch, item_id_to_name=None):
        self.last_error = "u.gg parse produced no rows"
        return []

    fallback_samples = [
        MetaBuildSample(source="blitz.gg", label="blitz-a", item_names=["Sword", "Shield", "Boots"]),
    ]

    def _blitz_ok(self, champion, role, tier, region, patch, item_id_to_name=None):
        self.last_error = ""
        return fallback_samples

    monkeypatch.setattr(UggMetaClient, "fetch_top_builds", _ugg_empty)
    monkeypatch.setattr(BlitzMetaClient, "fetch_top_builds", _blitz_ok)

    result = compare_optimizer_build_to_ugg(
        champion="Aatrox",
        optimizer_item_names=["Sword", "Shield", "Boots"],
        role="jungle",
        item_id_to_name={"1": "Sword", "2": "Shield", "3": "Boots"},
    )

    assert result["available"] is True
    assert result["source"] == "blitz.gg"
    assert result.get("fallback_used") is True
    assert any("fallback" in str(x).lower() for x in result.get("warnings", []))


def test_dual_provider_failure_uses_combined_reason(monkeypatch):
    def _ugg_empty(self, champion, role, tier, region, patch, item_id_to_name=None):
        self.last_error = "u.gg timeout"
        return []

    def _blitz_empty(self, champion, role, tier, region, patch, item_id_to_name=None):
        self.last_error = "blitz timeout"
        return []

    def _opgg_empty(self, champion, role, tier, region, patch, item_id_to_name=None):
        self.last_error = "opgg timeout"
        return []

    monkeypatch.setattr(UggMetaClient, "fetch_top_builds", _ugg_empty)
    monkeypatch.setattr(BlitzMetaClient, "fetch_top_builds", _blitz_empty)
    monkeypatch.setattr(OpggMetaClient, "fetch_top_builds", _opgg_empty)

    result = compare_optimizer_build_to_ugg(
        champion="Lux",
        optimizer_item_names=["Void Staff", "Rabadon's Deathcap", "Sorcerer's Shoes"],
        item_id_to_name={"3135": "Void Staff", "3089": "Rabadon's Deathcap", "3020": "Sorcerer's Shoes"},
    )

    assert result["available"] is False
    assert "U.GG, Blitz.gg, or OP.GG" in result["reason"]
    assert "No U.GG build data could be parsed at this time." not in result["reason"]


def test_opgg_fallback_is_used_when_ugg_and_blitz_fail(monkeypatch):
    def _ugg_empty(self, champion, role, tier, region, patch, item_id_to_name=None):
        self.last_error = "u.gg parse produced no rows"
        return []

    def _blitz_empty(self, champion, role, tier, region, patch, item_id_to_name=None):
        self.last_error = "blitz parse produced no rows"
        return []

    opgg_samples = [
        MetaBuildSample(source="op.gg", label="opgg-a", item_names=["Sword", "Shield", "Boots"]),
    ]

    def _opgg_ok(self, champion, role, tier, region, patch, item_id_to_name=None):
        self.last_error = ""
        return opgg_samples

    monkeypatch.setattr(UggMetaClient, "fetch_top_builds", _ugg_empty)
    monkeypatch.setattr(BlitzMetaClient, "fetch_top_builds", _blitz_empty)
    monkeypatch.setattr(OpggMetaClient, "fetch_top_builds", _opgg_ok)

    result = compare_optimizer_build_to_ugg(
        champion="Aatrox",
        optimizer_item_names=["Sword", "Shield", "Boots"],
        role="jungle",
        item_id_to_name={"1": "Sword", "2": "Shield", "3": "Boots"},
    )

    assert result["available"] is True
    assert result["source"] == "op.gg"
    assert result.get("fallback_used") is True


def test_ocr_fallback_rows_are_used_when_other_extractors_fail(monkeypatch):
    monkeypatch.setattr(meta_build_comparison, "_extract_json_script_payloads", lambda _html: [])
    monkeypatch.setattr(meta_build_comparison, "_extract_item_names_from_visual_text", lambda _html, _map: [])
    monkeypatch.setattr(
        meta_build_comparison,
        "_extract_item_names_from_ocr",
        lambda _html, _map: [["Sword", "Shield", "Boots", "Dagger"]],
    )

    client = UggMetaClient()
    rows = client._parse_builds_from_html(
        "<html><body><div>No structured payloads</div></body></html>",
        item_id_to_name={"1": "Sword", "2": "Shield", "3": "Boots", "4": "Dagger"},
    )

    assert rows
    assert rows[0].label == "ocr-fallback"
