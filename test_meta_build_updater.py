from __future__ import annotations

import os

from meta_build_updater import MetaBuildUpdater


def test_extract_ugg_builds_from_ssr_prefers_recommended_and_resolves_names() -> None:
    payload = {
        "overview_emerald_plus_world_recommended::https://stats2.u.gg/lol/1.5/overview/16_6/ranked_solo_5x5/266/1.5.0.json": {
            "data": {
                "world_emerald_plus_jungle": {
                    "matches": 200,
                    "rec_core_items": {"ids": [6697, 6610, 3047], "matches": 120},
                    "item_options_1": [
                        {"id": 6333, "matches": 100, "wins": 55, "win_rate": 55.0},
                    ],
                    "item_options_2": [
                        {"id": 6694, "matches": 80, "wins": 48, "win_rate": 60.0},
                    ],
                    "item_options_3": [
                        {"id": 3156, "matches": 60, "wins": 34, "win_rate": 56.6},
                    ],
                }
            }
        },
        "overview_emerald_plus_world_ad::https://stats2.u.gg/lol/1.5/ad-overview/16_6/ranked_solo_5x5/266/1.5.0.json": {
            "data": {
                "world_emerald_plus_jungle": {
                    "matches": 150,
                    "rec_core_items": {"ids": [6697, 3047, 6699], "matches": 80},
                    "item_options_1": [
                        {"id": 6333, "matches": 60, "wins": 31, "win_rate": 51.6},
                    ],
                    "item_options_2": [
                        {"id": 6694, "matches": 40, "wins": 24, "win_rate": 60.0},
                    ],
                }
            }
        },
    }

    item_id_to_name = {
        "6697": "Hubris",
        "6610": "Sundered Sky",
        "3047": "Plated Steelcaps",
        "6333": "Death's Dance",
        "6694": "Serylda's Grudge",
        "3156": "Maw of Malmortius",
        "6699": "Edge of Night",
    }

    allowed_item_ids = set(item_id_to_name.keys())

    builds = MetaBuildUpdater._extract_ugg_builds_from_ssr(payload, "jungle", item_id_to_name, allowed_item_ids)

    assert builds
    assert builds[0][:3] == ["Hubris", "Sundered Sky", "Plated Steelcaps"]
    assert "Death's Dance" in builds[0]


def test_extract_ugg_builds_from_ssr_filters_non_sr_items() -> None:
    payload = {
        "overview_emerald_plus_world_recommended::https://stats2.u.gg/lol/1.5/overview/16_6/ranked_solo_5x5/266/1.5.0.json": {
            "data": {
                "world_emerald_plus_jungle": {
                    "matches": 200,
                    "rec_core_items": {"ids": [6697, 6610, 3047], "matches": 120},
                    "item_options_1": [
                        {"id": 100001, "matches": 110, "wins": 60, "win_rate": 54.5},
                        {"id": 6333, "matches": 100, "wins": 55, "win_rate": 55.0},
                    ],
                    "item_options_2": [
                        {"id": 6694, "matches": 80, "wins": 48, "win_rate": 60.0},
                    ],
                }
            }
        }
    }

    item_id_to_name = {
        "6697": "Hubris",
        "6610": "Sundered Sky",
        "3047": "Plated Steelcaps",
        "6333": "Death's Dance",
        "6694": "Serylda's Grudge",
        "100001": "Non-SR Item",
    }
    allowed_item_ids = {"6697", "6610", "3047", "6333", "6694"}

    builds = MetaBuildUpdater._extract_ugg_builds_from_ssr(payload, "jungle", item_id_to_name, allowed_item_ids)

    assert builds
    assert "Non-SR Item" not in builds[0]
    assert "Serylda's Grudge" in builds[0]


def test_get_targets_supports_env_override() -> None:
    os.environ["META_BUILD_TARGETS"] = "aatrox:jungle,elise:jungle,leesin:top"
    try:
        targets = MetaBuildUpdater.get_targets()
    finally:
        del os.environ["META_BUILD_TARGETS"]

    assert targets == [
        ("aatrox", "jungle"),
        ("elise", "jungle"),
        ("leesin", "top"),
    ]


def test_apply_item_name_rules_to_builds_supports_allow_and_deny() -> None:
    builds = [
        ["Hubris", "Death's Dance", "Serylda's Grudge", "Black Cleaver"],
        ["Hubris", "Unknown Item", "Serylda's Grudge"],
    ]
    allow = {
        MetaBuildUpdater._normalize_item_name("Hubris"),
        MetaBuildUpdater._normalize_item_name("Death's Dance"),
        MetaBuildUpdater._normalize_item_name("Serylda's Grudge"),
        MetaBuildUpdater._normalize_item_name("Black Cleaver"),
    }
    deny = {MetaBuildUpdater._normalize_item_name("Death's Dance")}

    filtered, pruned, removed = MetaBuildUpdater.apply_item_name_rules_to_builds(builds, allow, deny)

    assert filtered == [["Hubris", "Serylda's Grudge", "Black Cleaver"]]
    assert pruned == 1
    assert removed == 2


def test_get_item_name_rules_reads_env() -> None:
    os.environ["META_BUILD_ITEM_ALLOWLIST"] = "Hubris, Serylda's Grudge"
    os.environ["META_BUILD_ITEM_DENYLIST"] = "Death's Dance"
    try:
        allow, deny = MetaBuildUpdater.get_item_name_rules()
    finally:
        del os.environ["META_BUILD_ITEM_ALLOWLIST"]
        del os.environ["META_BUILD_ITEM_DENYLIST"]

    assert MetaBuildUpdater._normalize_item_name("Hubris") in allow
    assert MetaBuildUpdater._normalize_item_name("Serylda's Grudge") in allow
    assert MetaBuildUpdater._normalize_item_name("Death's Dance") in deny
