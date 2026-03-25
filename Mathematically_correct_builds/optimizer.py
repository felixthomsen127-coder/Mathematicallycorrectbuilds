from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations, permutations
from math import exp, inf
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, cast
import random
import time

try:
    import numpy as np  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional acceleration dependency
    np = None

try:
    from numba import cuda  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional acceleration dependency
    cuda = None

_CUDA = cast(Any, cuda)
_NUMBA_CUDA_AVAILABLE = bool(_CUDA is not None and np is not None and _CUDA.is_available())
_gpu_prescore_kernel = None


if _NUMBA_CUDA_AVAILABLE:
    @_CUDA.jit
    def _gpu_prescore_kernel(
        stats,
        out,
        ad_ratio,
        ap_ratio,
        phys_frac,
        magic_frac,
        utility_factor,
        consistency_factor,
        enemy_armor,
        enemy_mr,
        enemy_hp,
        enemy_phys_share,
        w_damage,
        w_healing,
        w_tank,
        w_lifesteal,
        w_utility,
        w_consistency,
        champion_base_hp,
    ):
        i = _CUDA.grid(1)
        if i >= stats.shape[0]:
            return

        ad = stats[i, 0]
        ap = stats[i, 1]
        attack_speed = stats[i, 2]
        hp = stats[i, 3]
        armor = stats[i, 4]
        mr = stats[i, 5]
        ability_haste = stats[i, 6]
        lifesteal = stats[i, 7]
        omnivamp = stats[i, 8]
        damage_amp = stats[i, 9]
        armor_pen = stats[i, 10]
        magic_pen = stats[i, 11]
        max_hp_damage = stats[i, 12]
        bonus_true_damage = stats[i, 13]

        auto_physical = ad + attack_speed * 25.0
        spell_raw = ad * ad_ratio + ap * ap_ratio + ability_haste * 0.9 + max(0.0, hp - champion_base_hp) * 0.05

        premit_physical = auto_physical + spell_raw * phys_frac + max_hp_damage * enemy_hp * 0.5
        premit_magic = spell_raw * magic_frac + max_hp_damage * enemy_hp * 0.5
        premit_true = bonus_true_damage

        effective_armor = max(0.0, enemy_armor * (1.0 - armor_pen))
        effective_mr = max(0.0, enemy_mr * (1.0 - magic_pen))
        physical_after = premit_physical * (100.0 / (100.0 + effective_armor))
        magic_after = premit_magic * (100.0 / (100.0 + effective_mr))

        damage = (physical_after + magic_after + premit_true) * (1.0 + damage_amp)
        lifesteal_metric = lifesteal + omnivamp
        healing = damage * (lifesteal * 0.2 + omnivamp * 0.25)
        ehp_physical = hp * (1.0 + armor / 100.0)
        ehp_magic = hp * (1.0 + mr / 100.0)
        tankiness = ehp_physical * enemy_phys_share + ehp_magic * (1.0 - enemy_phys_share)
        utility = (
            ability_haste * (0.72 + utility_factor * 0.25)
            + (armor + mr) * 0.35
            + max(0.0, hp - champion_base_hp) * 0.02
        )
        consistency = (
            min(1.0, (attack_speed * 18.0 + ability_haste) / 100.0) * 40.0
            + consistency_factor * 30.0
            + (1.0 - abs(enemy_phys_share - 0.5)) * 8.0
        )

        out[i] = (
            w_damage * damage
            + w_healing * healing
            + w_tank * tankiness
            + w_lifesteal * lifesteal_metric * 100.0
            + w_utility * utility
            + w_consistency * consistency
            + damage_amp * 30.0
        )


@dataclass(frozen=True)
class ObjectiveWeights:
    damage: float = 1.0
    healing: float = 0.0
    tankiness: float = 0.0
    lifesteal: float = 0.0
    utility: float = 0.0
    consistency: float = 0.0


@dataclass(frozen=True)
class ChampionProfile:
    champion_name: str
    base_hp: float
    base_armor: float
    base_mr: float
    abilities_per_rotation: float = 4.0
    average_combat_seconds: float = 8.0
    ability_breakdown: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    champion_tags: Tuple[str, ...] = ()


@dataclass(frozen=True)
class EnemyProfile:
    target_hp: float = 3200.0
    target_armor: float = 120.0
    target_mr: float = 90.0
    physical_share: float = 0.5
    dps: float = 650.0


@dataclass(frozen=True)
class ItemStats:
    item_id: str
    name: str
    total_gold: float
    ad: float = 0.0
    ap: float = 0.0
    attack_speed: float = 0.0
    hp: float = 0.0
    armor: float = 0.0
    mr: float = 0.0
    ability_haste: float = 0.0
    lifesteal: float = 0.0
    omnivamp: float = 0.0
    tags: Tuple[str, ...] = ()
    unique_group: str = ""
    damage_amp: float = 0.0
    bonus_true_damage: float = 0.0
    heal_amp: float = 0.0
    shield_amp: float = 0.0
    armor_pen: float = 0.0
    magic_pen: float = 0.0
    flat_armor_pen: float = 0.0
    flat_magic_pen: float = 0.0
    max_hp_damage: float = 0.0
    # Wiki-extracted unique passive names (lowercase). Two items sharing any name
    # cannot appear in the same build (engine hard-blocks them).
    unique_passives: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RuneChoice:
    rune_id: str
    name: str
    tree: str
    slot: str
    ad: float = 0.0
    ap: float = 0.0
    attack_speed: float = 0.0
    hp: float = 0.0
    armor: float = 0.0
    mr: float = 0.0
    ability_haste: float = 0.0
    lifesteal: float = 0.0
    omnivamp: float = 0.0
    damage_amp: float = 0.0
    bonus_true_damage: float = 0.0
    heal_amp: float = 0.0
    armor_pen: float = 0.0
    magic_pen: float = 0.0
    flat_armor_pen: float = 0.0
    flat_magic_pen: float = 0.0
    max_hp_damage: float = 0.0


@dataclass(frozen=True)
class RunePage:
    page_id: str
    name: str
    primary_tree: str
    secondary_tree: str
    shards: Tuple[str, ...] = ()
    runes: Tuple[RuneChoice, ...] = ()


@dataclass
class BuildEvaluation:
    items: List[ItemStats]
    order: List[ItemStats]
    weighted_score: float
    metrics: Dict[str, float]
    contributions: Dict[str, float] = field(default_factory=dict)
    interactions: List[str] = field(default_factory=list)
    trace: Dict[str, float] = field(default_factory=dict)
    rune_page: Optional[RunePage] = None
    rune_effects: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchSettings:
    mode: str = "near_exhaustive"
    build_size: int = 6
    candidate_pool_size: int = 24
    beam_width: int = 65
    exhaustive_runtime_cap_seconds: float = 120.0
    order_permutation_cap: int = 150
    sa_iterations: int = 100
    deep_search: bool = False
    extra_restarts: int = 0
    compute_backend: str = "auto"


@dataclass(frozen=True)
class BuildConstraints:
    must_include_ids: Tuple[str, ...] = ()
    excluded_ids: Tuple[str, ...] = ()
    require_boots: bool = False
    max_total_gold: Optional[float] = None


class StrictDataError(ValueError):
    pass


class BuildOptimizer:
    def __init__(
        self,
        champion: ChampionProfile,
        items: Sequence[ItemStats],
        rune_pages: Optional[Sequence[RunePage]] = None,
    ):
        self.champion = champion
        self.items = list(items)
        self.rune_pages = list(rune_pages) if rune_pages else self._default_rune_pages()
        self._active_compute_backend = "cpu"

    def get_compute_backend(self) -> str:
        return self._active_compute_backend

    def optimize(
        self,
        weights: ObjectiveWeights,
        settings: SearchSettings,
        constraints: Optional[BuildConstraints] = None,
        enemy: Optional[EnemyProfile] = None,
    ) -> Tuple[List[BuildEvaluation], List[BuildEvaluation], Dict[str, BuildEvaluation]]:
        constraints = constraints or BuildConstraints()
        if enemy is None:
            raise ValueError("Enemy profile is required for strict optimization")

        self._validate_strict_champion_data()
        candidates = self._candidate_pool(settings.candidate_pool_size)
        candidates = [x for x in candidates if x.item_id not in set(constraints.excluded_ids)]
        if constraints.require_boots and not any(self._is_boots(x) for x in candidates):
            fallback_boots = [
                x for x in self.items
                if self._is_boots(x) and x.item_id not in set(constraints.excluded_ids)
            ]
            if fallback_boots:
                # Include at least one boots option so required-boots constraints remain solvable.
                candidates.append(min(fallback_boots, key=lambda item: item.total_gold if item.total_gold > 0 else inf))
        if not candidates:
            raise ValueError("No valid candidate items available after applying constraints")

        mode = settings.mode.lower().strip()
        if mode == "heuristic":
            builds = [self._heuristic_build(candidates, weights, settings.build_size, constraints, settings, enemy)]
        elif mode == "exhaustive":
            builds = self._exhaustive(candidates, weights, settings, constraints, enemy)
        else:
            builds = self._near_exhaustive(candidates, weights, settings, constraints, enemy)

        builds = [b for b in builds if self._valid_final_build(b.items, constraints)]

        ranked = sorted(builds, key=lambda b: b.weighted_score, reverse=True)
        pareto = self._pareto_frontier(ranked)
        checkpoints = self._checkpoint_best(ranked, settings, weights, enemy)
        return ranked[:20], pareto, checkpoints

    def _candidate_pool(self, pool_size: int) -> List[ItemStats]:
        # Use per-ability derived spike ratios so the pool reflects what THIS champion
        # actually benefits from, not a neutral gold-efficiency ranking that inflates AP
        # items for every champion.
        spike_ad, spike_ap = self._effective_spike_ratios()
        breakdown = self.champion.ability_breakdown or {}
        as_values = [
            self._num(b.get("attack_speed_ratio", 0.0))
            for b in breakdown.values() if isinstance(b, dict)
        ]
        spike_as = sum(as_values) / max(1.0, len(as_values)) if as_values else 0.1
        total_scale = spike_ad + spike_ap + spike_as
        if total_scale <= 0:
            total_scale = 1.0
        # Normalised weights — always keep a small floor so no item type is fully excluded
        # (important for hybrids like Kayle / Ezreal).
        ad_w = max(0.05, spike_ad / total_scale)
        ap_w = max(0.05, spike_ap / total_scale)
        as_w = max(0.05, spike_as / total_scale)

        # Damage-type profile: use explicit fields when available, else fall back to scaling ratios.
        breakdown = self.champion.ability_breakdown or {}
        phys_frac, magic_frac, on_hit_frac = self._detect_champion_damage_profile(breakdown)
        # If the breakdown has no damage_type field yet, fall back to AD/AP ratio heuristic.
        if phys_frac == 0.5 and magic_frac == 0.5:
            total_scale = spike_ad + spike_ap
            phys_frac = spike_ad / total_scale if total_scale > 0 else 0.5
            magic_frac = spike_ap / total_scale if total_scale > 0 else 0.5
        ability_signals = self._advanced_ability_signals()
        # Pen multipliers: extra boost when the champion clearly deals that damage type.
        pen_phys_mult = 1.0 + phys_frac * 0.8   # up to ×1.8 for pure physical
        pen_magic_mult = 1.0 + magic_frac * 0.8  # up to ×1.8 for pure magical
        # AS items get extra value when the champion has on-hit abilities.
        as_on_hit_mult = 1.0 + on_hit_frac * 1.0  # up to ×2.0 for pure on-hit
        utility_mult = 1.0 + ability_signals["utility_ratio"] * 0.35
        durability_mult = 1.0 + ability_signals["durability_ratio"] * 0.25
        aoe_mult = 1.0 + ability_signals["aoe_ratio"] * 0.3

        scored: List[Tuple[float, ItemStats]] = []
        for item in self.items:
            if item.total_gold <= 0:
                continue
            base_value = (
                item.ad * (ad_w / 0.333)          # scale relative to neutral weight
                + item.ap * (ap_w / 0.333)
                + 20.0 * item.attack_speed * (as_w / 0.333) * as_on_hit_mult * aoe_mult
                + (item.hp / 10.0 + item.armor + item.mr) * durability_mult
                + item.ability_haste * 1.2 * utility_mult
                + item.lifesteal * 4.0
                + item.omnivamp * 4.0
                + item.damage_amp * 120.0
                + item.bonus_true_damage * 0.3
                # Pen only valuable if the champion deals that damage type
                + item.max_hp_damage * 180.0
                + item.armor_pen * 90.0 * (ad_w / 0.333) * pen_phys_mult
                + item.flat_armor_pen * 1.2 * pen_phys_mult
                + item.magic_pen * 90.0 * (ap_w / 0.333) * pen_magic_mult
                + item.flat_magic_pen * 1.2 * pen_magic_mult
            )
            scored.append((base_value / item.total_gold, item))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[: max(2, pool_size)]]

    def _heuristic_build(
        self,
        candidates: Sequence[ItemStats],
        weights: ObjectiveWeights,
        build_size: int,
        constraints: BuildConstraints,
        settings: SearchSettings,
        enemy: EnemyProfile,
    ) -> BuildEvaluation:
        selected: List[ItemStats] = []
        remaining = list(candidates)

        for _ in range(min(build_size, len(remaining))):
            best_item: Optional[ItemStats] = None
            best_score = -inf
            for item in remaining:
                trial = selected + [item]
                if not self._valid_partial_build(trial, constraints):
                    continue
                eval_result = self._evaluate_build(trial, trial, weights, enemy)
                if eval_result.weighted_score > best_score:
                    best_score = eval_result.weighted_score
                    best_item = item
            if best_item is None:
                break
            selected.append(best_item)
            remaining.remove(best_item)

        return self._evaluate_best_order(selected, weights, settings.order_permutation_cap, enemy)

    def _near_exhaustive(
        self,
        candidates: Sequence[ItemStats],
        weights: ObjectiveWeights,
        settings: SearchSettings,
        constraints: BuildConstraints,
        enemy: EnemyProfile,
    ) -> List[BuildEvaluation]:
        target_size = min(settings.build_size, len(candidates))
        all_item_sets: List[frozenset] = []
        all_builds: List[List[ItemStats]] = []
        eval_cache: Dict[Tuple[str, ...], float] = {}

        def _cached_score(build: List[ItemStats]) -> float:
            key = tuple(x.item_id for x in build)
            cached = eval_cache.get(key)
            if cached is not None:
                return cached
            score = self._evaluate_build(build, build, weights, enemy).weighted_score
            eval_cache[key] = score
            return score

        backend = self._resolve_compute_backend(settings.compute_backend)
        # GPU does pre-scoring; CPU does final full evaluation (hybrid mode).
        # Report "gpu+cpu" so callers know both units are used.
        self._active_compute_backend = "gpu+cpu" if backend == "gpu" else "cpu"
        use_prescore = settings.deep_search or backend == "gpu"
        beam_width = max(2, settings.beam_width * (2 if settings.deep_search else 1))
        # Canonical pass + random restarts; deep search can use more restarts and wider beams.
        num_restarts = 4 + max(0, settings.extra_restarts)
        if settings.deep_search:
            num_restarts += 4
        for restart_idx in range(num_restarts):
            if restart_idx == 0:
                ordered_cands = list(candidates)
            else:
                ordered_cands = list(candidates)
                random.Random(restart_idx * 17 + 3).shuffle(ordered_cands)

            beam: List[List[ItemStats]] = [[]]
            for _ in range(target_size):
                expanded: List[List[ItemStats]] = []
                for partial in beam:
                    used_ids = {x.item_id for x in partial}
                    for item in ordered_cands:
                        if item.item_id in used_ids:
                            continue
                        trial = partial + [item]
                        if self._valid_partial_build(trial, constraints):
                            expanded.append(trial)

                if expanded and use_prescore:
                    prescores = self._batch_prescore(expanded, weights, enemy, backend)
                    scored_expanded = sorted(zip(prescores, expanded), key=lambda pair: pair[0], reverse=True)
                    shortlist_size = min(len(scored_expanded), max(beam_width, beam_width * 3))
                    shortlist = [build for _, build in scored_expanded[:shortlist_size]]
                    shortlist.sort(key=_cached_score, reverse=True)
                    beam = shortlist[:beam_width]
                elif expanded:
                    expanded.sort(key=_cached_score, reverse=True)
                    beam = expanded[:beam_width]
                else:
                    beam = []
                if not beam:
                    break

            for items in beam:
                key = frozenset(x.item_id for x in items)
                if key not in all_item_sets:
                    all_item_sets.append(key)
                    all_builds.append(items)

        return [self._evaluate_best_order(items, weights, settings.order_permutation_cap, enemy) for items in all_builds]

    def _exhaustive(
        self,
        candidates: Sequence[ItemStats],
        weights: ObjectiveWeights,
        settings: SearchSettings,
        constraints: BuildConstraints,
        enemy: EnemyProfile,
    ) -> List[BuildEvaluation]:
        start = time.time()
        target_size = min(settings.build_size, len(candidates))
        all_results: List[BuildEvaluation] = []
        seen_sets: set = set()

        for combo in combinations(candidates, target_size):
            if time.time() - start >= settings.exhaustive_runtime_cap_seconds:
                break
            combo_list = list(combo)
            if not self._valid_final_build(combo_list, constraints):
                continue
            result = self._evaluate_best_order(combo_list, weights, settings.order_permutation_cap, enemy)
            all_results.append(result)
            seen_sets.add(frozenset(x.item_id for x in combo_list))

        # Simulated annealing refinement pass if time remains
        remaining = settings.exhaustive_runtime_cap_seconds - (time.time() - start)
        if all_results and remaining > 2.0:
            sa_results = self._simulated_annealing(
                all_results,
                candidates,
                weights,
                settings,
                constraints,
                enemy,
                seen_sets,
                time_budget=remaining * 0.85,
            )
            all_results.extend(sa_results)

        return all_results

    def _simulated_annealing(
        self,
        seed_results: List[BuildEvaluation],
        candidates: Sequence[ItemStats],
        weights: ObjectiveWeights,
        settings: SearchSettings,
        constraints: BuildConstraints,
        enemy: EnemyProfile,
        seen_sets: set,
        time_budget: float,
    ) -> List[BuildEvaluation]:
        """Neighbourhood search: swap 1 item at a time from current best with cooling schedule."""
        start = time.time()
        best_seed = max(seed_results, key=lambda b: b.weighted_score)
        current_items = list(best_seed.items)
        current_score = best_seed.weighted_score
        new_results: List[BuildEvaluation] = []

        candidate_set = list(candidates)
        iterations = max(10, settings.sa_iterations)
        temp = current_score * 0.15 if current_score > 0 else 50.0
        cooling = 0.88

        rng = random.Random(42)
        for i in range(iterations):
            if time.time() - start >= time_budget:
                break
            temp *= cooling

            # Pick a random item to swap out and a random candidate to swap in
            swap_out_idx = rng.randrange(len(current_items))
            swap_in = rng.choice(candidate_set)
            if swap_in.item_id in {x.item_id for x in current_items}:
                continue

            neighbor = current_items[:swap_out_idx] + [swap_in] + current_items[swap_out_idx + 1:]
            if not self._valid_final_build(neighbor, constraints):
                continue

            key = frozenset(x.item_id for x in neighbor)
            result = self._evaluate_best_order(neighbor, weights, settings.order_permutation_cap, enemy)
            delta = result.weighted_score - current_score

            if delta > 0 or (temp > 0 and rng.random() < exp(delta / temp)):
                current_items = neighbor
                current_score = result.weighted_score
                if key not in seen_sets:
                    seen_sets.add(key)
                    new_results.append(result)

        return new_results

    def _evaluate_best_order(
        self,
        items: List[ItemStats],
        weights: ObjectiveWeights,
        order_permutation_cap: int,
        enemy: EnemyProfile,
    ) -> BuildEvaluation:
        if not items:
            return BuildEvaluation(items=[], order=[], weighted_score=0.0, metrics={})

        best: Optional[BuildEvaluation] = None
        order_limit = max(1, order_permutation_cap)
        count = 0
        for order in permutations(items):
            count += 1
            eval_result = self._evaluate_build(items, list(order), weights, enemy)
            if best is None or eval_result.weighted_score > best.weighted_score:
                best = eval_result
            if count >= order_limit:
                break

        return best if best is not None else self._evaluate_build(items, items, weights, enemy)

    def _evaluate_build(
        self,
        items: Sequence[ItemStats],
        order: Sequence[ItemStats],
        weights: ObjectiveWeights,
        enemy: EnemyProfile,
        rune_page: Optional[RunePage] = None,
    ) -> BuildEvaluation:
        if rune_page is None and self.rune_pages:
            best_eval: Optional[BuildEvaluation] = None
            for page in self.rune_pages:
                candidate = self._evaluate_build(items, order, weights, enemy, rune_page=page)
                if best_eval is None or candidate.weighted_score > best_eval.weighted_score:
                    best_eval = candidate
            if best_eval is not None:
                return best_eval

        rune_effects = self._aggregate_rune_effects(rune_page)
        ad = sum(x.ad for x in items) + rune_effects["ad"]
        ap = sum(x.ap for x in items) + rune_effects["ap"]
        attack_speed = sum(x.attack_speed for x in items) + rune_effects["attack_speed"]
        hp = self.champion.base_hp + sum(x.hp for x in items) + rune_effects["hp"]
        armor = self.champion.base_armor + sum(x.armor for x in items) + rune_effects["armor"]
        mr = self.champion.base_mr + sum(x.mr for x in items) + rune_effects["mr"]
        lifesteal = sum(x.lifesteal for x in items) + rune_effects["lifesteal"]
        omnivamp = sum(x.omnivamp for x in items) + rune_effects["omnivamp"]
        ability_haste = sum(x.ability_haste for x in items) + rune_effects["ability_haste"]
        damage_amp = sum(x.damage_amp for x in items) + rune_effects["damage_amp"]
        bonus_true_damage = sum(x.bonus_true_damage for x in items) + rune_effects["bonus_true_damage"]
        heal_amp = sum(x.heal_amp for x in items) + rune_effects["heal_amp"]
        armor_pen = sum(x.armor_pen for x in items) + rune_effects["armor_pen"]
        magic_pen = sum(x.magic_pen for x in items) + rune_effects["magic_pen"]
        flat_armor_pen = sum(x.flat_armor_pen for x in items) + rune_effects["flat_armor_pen"]
        flat_magic_pen = sum(x.flat_magic_pen for x in items) + rune_effects["flat_magic_pen"]
        max_hp_damage = sum(x.max_hp_damage for x in items) + rune_effects["max_hp_damage"]

        # Apply diminishing returns to raw stats for scoring purposes
        eff_ad, eff_ap, eff_hp, eff_armor, eff_mr = self._apply_diminishing_returns(ad, ap, hp, armor, mr)

        auto_physical = eff_ad + attack_speed * 100.0 * 0.25
        spell_phys, spell_magic, spell_rotation, kit_heal_factor = self._spell_bundle_damage(
            eff_ad,
            eff_ap,
            attack_speed,
            eff_hp,
            eff_armor,
            eff_mr,
            ability_haste,
        )
        max_hp_proc_damage = max_hp_damage * enemy.target_hp * self.champion.abilities_per_rotation

        premit_physical = auto_physical + spell_phys + max_hp_proc_damage * 0.5
        premit_magic = spell_magic + max_hp_proc_damage * 0.5
        premit_true = bonus_true_damage * self.champion.abilities_per_rotation

        effective_armor = max(0.0, enemy.target_armor * (1.0 - armor_pen) - flat_armor_pen)
        effective_mr = max(0.0, enemy.target_mr * (1.0 - magic_pen) - flat_magic_pen)
        physical_after_mitigation = premit_physical * (100.0 / (100.0 + effective_armor))
        magic_after_mitigation = premit_magic * (100.0 / (100.0 + effective_mr))

        # Pen breakpoint bonuses: reward pen items that match the enemy's resistance profile
        pen_bonus = 0.0
        if enemy.target_armor > 100 and armor_pen > 0:
            pen_bonus += armor_pen * enemy.target_armor * 0.08
        if enemy.target_armor < 80 and flat_armor_pen > 0:
            pen_bonus += flat_armor_pen * 0.06
        if enemy.target_mr > 100 and magic_pen > 0:
            pen_bonus += magic_pen * enemy.target_mr * 0.08
        if enemy.target_mr < 80 and flat_magic_pen > 0:
            pen_bonus += flat_magic_pen * 0.06

        base_damage = physical_after_mitigation + magic_after_mitigation + premit_true + pen_bonus
        damage = base_damage * (1.0 + damage_amp)

        self_heal_from_kit = kit_heal_factor
        sustain_from_damage = damage * (lifesteal * 0.22 + omnivamp * 0.28)
        healing = (self_heal_from_kit + sustain_from_damage) * (1.0 + heal_amp)

        ehp_vs_physical = eff_hp * (1.0 + eff_armor / 100.0)
        ehp_vs_magic = eff_hp * (1.0 + eff_mr / 100.0)
        # Factor in self-damage-reduction from champion abilities (e.g. Warwick E, Briar E, Garen W).
        # A champion that can reduce incoming damage by X% is effectively more tanky.
        self_dr = self._aggregate_self_damage_reduction()
        dr_factor = 1.0 + min(0.60, self_dr)  # cap contribution at +60% effective HP
        tankiness = (ehp_vs_physical * enemy.physical_share + ehp_vs_magic * (1.0 - enemy.physical_share)) * dr_factor

        lifesteal_metric = lifesteal + omnivamp
        interaction_bonus, interactions = self._interaction_bonus(items, ad, ap, lifesteal_metric, armor_pen, magic_pen)
        advanced_signals = self._advanced_ability_signals()
        gold_eff_bonus = self._gold_efficiency_bonus(items)

        burst_profile = damage * (0.92 + min(80.0, ability_haste) / 260.0 + attack_speed * 0.05)
        sustained_profile = damage * (1.0 + min(160.0, ability_haste) / 180.0 + attack_speed * 0.12)
        aoe_pressure = damage * advanced_signals["aoe_ratio"] * 0.25
        utility_profile = (
            ability_haste * 0.85
            + (eff_armor + eff_mr) * 0.32
            + max(0.0, eff_hp - self.champion.base_hp) * 0.02
            + advanced_signals["utility_ratio"] * 35.0
            + advanced_signals["range_bias"] * 22.0
        )
        consistency_metric = (
            min(1.0, burst_profile / max(1.0, sustained_profile)) * 45.0
            + (1.0 - abs(enemy.physical_share - 0.5)) * 12.0
            + advanced_signals["consistency_ratio"] * 28.0
        )

        # Early spike weighting gives item order real influence.
        order_bonus = 0.0
        running_ad = 0.0
        running_ap = 0.0
        running_hp = self.champion.base_hp
        spike_ad_ratio, spike_ap_ratio = self._effective_spike_ratios()
        for idx, item in enumerate(order, start=1):
            running_ad += item.ad
            running_ap += item.ap
            running_hp += item.hp
            spike = running_ad * spike_ad_ratio + running_ap * spike_ap_ratio + running_hp / 12.0
            order_bonus += spike / idx

        contribution_damage = weights.damage * damage
        contribution_healing = weights.healing * healing
        contribution_tank = weights.tankiness * tankiness
        contribution_ls = weights.lifesteal * lifesteal_metric * 100.0
        contribution_utility = weights.utility * utility_profile
        contribution_consistency = weights.consistency * consistency_metric
        contribution_order = 0.03 * order_bonus
        contribution_interaction = interaction_bonus
        contribution_gold_eff = gold_eff_bonus

        weighted = (
            contribution_damage
            + contribution_healing
            + contribution_tank
            + contribution_ls
            + contribution_utility
            + contribution_consistency
            + contribution_order
            + contribution_interaction
            + contribution_gold_eff
        )

        metrics = {
            "damage": round(damage, 3),
            "healing": round(healing, 3),
            "tankiness": round(tankiness, 3),
            "lifesteal": round(lifesteal_metric, 3),
            "burst_profile": round(burst_profile, 3),
            "sustained_profile": round(sustained_profile, 3),
            "aoe_pressure": round(aoe_pressure, 3),
            "utility": round(utility_profile, 3),
            "consistency": round(consistency_metric, 3),
            "order_bonus": round(order_bonus, 3),
            "interaction_bonus": round(interaction_bonus, 3),
            "gold_efficiency_bonus": round(gold_eff_bonus, 3),
            "self_damage_reduction": round(self_dr, 4),
        }

        contributions = {
            "damage_component": round(contribution_damage, 3),
            "healing_component": round(contribution_healing, 3),
            "tankiness_component": round(contribution_tank, 3),
            "lifesteal_component": round(contribution_ls, 3),
            "utility_component": round(contribution_utility, 3),
            "consistency_component": round(contribution_consistency, 3),
            "order_component": round(contribution_order, 3),
            "interaction_component": round(contribution_interaction, 3),
            "gold_efficiency_component": round(contribution_gold_eff, 3),
        }

        return BuildEvaluation(
            items=list(items),
            order=list(order),
            weighted_score=round(weighted, 3),
            metrics=metrics,
            contributions=contributions,
            interactions=interactions,
            trace={
                "ad_total": round(ad, 3),
                "ap_total": round(ap, 3),
                "attack_speed_total": round(attack_speed, 3),
                "hp_total": round(hp, 3),
                "armor_total": round(armor, 3),
                "mr_total": round(mr, 3),
                "ability_haste_total": round(ability_haste, 3),
                "damage_amp_total": round(damage_amp, 3),
                "armor_pen_total": round(armor_pen, 3),
                "magic_pen_total": round(magic_pen, 3),
                "flat_armor_pen_total": round(flat_armor_pen, 3),
                "flat_magic_pen_total": round(flat_magic_pen, 3),
                "enemy_target_hp": round(enemy.target_hp, 3),
                "enemy_target_armor": round(enemy.target_armor, 3),
                "enemy_target_mr": round(enemy.target_mr, 3),
                "spell_rotation_raw": round(spell_rotation, 3),
                "rune_damage_amp": round(rune_effects["damage_amp"], 4),
                "rune_ap": round(rune_effects["ap"], 3),
                "rune_ad": round(rune_effects["ad"], 3),
            },
            rune_page=rune_page,
            rune_effects={k: round(v, 4) for k, v in rune_effects.items() if abs(v) > 0.00001},
        )

    def _aggregate_rune_effects(self, rune_page: Optional[RunePage]) -> Dict[str, float]:
        keys = (
            "ad",
            "ap",
            "attack_speed",
            "hp",
            "armor",
            "mr",
            "ability_haste",
            "lifesteal",
            "omnivamp",
            "damage_amp",
            "bonus_true_damage",
            "heal_amp",
            "armor_pen",
            "magic_pen",
            "flat_armor_pen",
            "flat_magic_pen",
            "max_hp_damage",
        )
        out = {k: 0.0 for k in keys}
        if rune_page is None:
            return out
        for rune in rune_page.runes:
            out["ad"] += rune.ad
            out["ap"] += rune.ap
            out["attack_speed"] += rune.attack_speed
            out["hp"] += rune.hp
            out["armor"] += rune.armor
            out["mr"] += rune.mr
            out["ability_haste"] += rune.ability_haste
            out["lifesteal"] += rune.lifesteal
            out["omnivamp"] += rune.omnivamp
            out["damage_amp"] += rune.damage_amp
            out["bonus_true_damage"] += rune.bonus_true_damage
            out["heal_amp"] += rune.heal_amp
            out["armor_pen"] += rune.armor_pen
            out["magic_pen"] += rune.magic_pen
            out["flat_armor_pen"] += rune.flat_armor_pen
            out["flat_magic_pen"] += rune.flat_magic_pen
            out["max_hp_damage"] += rune.max_hp_damage
        return out

    def _default_rune_pages(self) -> List[RunePage]:
        return [
            RunePage(
                page_id="precision_conqueror",
                name="Precision: Conqueror",
                primary_tree="Precision",
                secondary_tree="Resolve",
                shards=("Adaptive Force", "Adaptive Force", "Scaling Health"),
                runes=(
                    RuneChoice("conq", "Conqueror", "Precision", "keystone", ad=18.0, damage_amp=0.04),
                    RuneChoice("alacrity", "Legend: Alacrity", "Precision", "minor", attack_speed=0.12),
                    RuneChoice("laststand", "Last Stand", "Precision", "minor", damage_amp=0.03),
                    RuneChoice("boneplating", "Bone Plating", "Resolve", "minor", hp=90.0),
                ),
            ),
            RunePage(
                page_id="domination_electrocute",
                name="Domination: Electrocute",
                primary_tree="Domination",
                secondary_tree="Sorcery",
                shards=("Adaptive Force", "Adaptive Force", "Scaling Health"),
                runes=(
                    RuneChoice("electrocute", "Electrocute", "Domination", "keystone", damage_amp=0.06, bonus_true_damage=25.0),
                    RuneChoice("eyeball", "Eyeball Collection", "Domination", "minor", ad=10.0, ap=18.0),
                    RuneChoice("treasure", "Treasure Hunter", "Domination", "minor", damage_amp=0.015),
                    RuneChoice("transcendence", "Transcendence", "Sorcery", "minor", ability_haste=10.0),
                ),
            ),
            RunePage(
                page_id="sorcery_comet",
                name="Sorcery: Arcane Comet",
                primary_tree="Sorcery",
                secondary_tree="Inspiration",
                shards=("Adaptive Force", "Adaptive Force", "Scaling Health"),
                runes=(
                    RuneChoice("comet", "Arcane Comet", "Sorcery", "keystone", ap=18.0, damage_amp=0.05),
                    RuneChoice("absolutefocus", "Absolute Focus", "Sorcery", "minor", ap=12.0, ad=6.0),
                    RuneChoice("gathering", "Gathering Storm", "Sorcery", "minor", ap=10.0),
                    RuneChoice("boots", "Magical Footwear", "Inspiration", "minor", attack_speed=0.04),
                ),
            ),
            RunePage(
                page_id="resolve_grasp",
                name="Resolve: Grasp",
                primary_tree="Resolve",
                secondary_tree="Precision",
                shards=("Attack Speed", "Adaptive Force", "Scaling Health"),
                runes=(
                    RuneChoice("grasp", "Grasp of the Undying", "Resolve", "keystone", hp=220.0, heal_amp=0.08),
                    RuneChoice("conditioning", "Conditioning", "Resolve", "minor", armor=12.0, mr=12.0),
                    RuneChoice("overgrowth", "Overgrowth", "Resolve", "minor", hp=180.0),
                    RuneChoice("bloodline", "Legend: Bloodline", "Precision", "minor", lifesteal=0.05),
                ),
            ),
            RunePage(
                page_id="inspiration_firststrike",
                name="Inspiration: First Strike",
                primary_tree="Inspiration",
                secondary_tree="Sorcery",
                shards=("Adaptive Force", "Adaptive Force", "Scaling Health"),
                runes=(
                    RuneChoice("firststrike", "First Strike", "Inspiration", "keystone", damage_amp=0.08),
                    RuneChoice("triple", "Triple Tonic", "Inspiration", "minor", ap=8.0, ad=8.0),
                    RuneChoice("cosmic", "Cosmic Insight", "Inspiration", "minor", ability_haste=8.0),
                    RuneChoice("scorch", "Scorch", "Sorcery", "minor", bonus_true_damage=18.0),
                ),
            ),
        ]

    def _spell_bundle_damage(
        self,
        ad: float,
        ap: float,
        attack_speed: float,
        hp: float,
        armor: float,
        mr: float,
        ability_haste: float,
    ) -> Tuple[float, float, float, float]:
        """Return (physical, magic, total_raw, heal_factor) spell rotation estimates."""
        breakdown = self.champion.ability_breakdown or {}
        if not breakdown:
            raise StrictDataError("Champion ability breakdown is required")

        total_physical = 0.0
        total_magic = 0.0
        total_raw = 0.0
        heal_factor = 0.0
        bonus_hp = max(0.0, hp - self.champion.base_hp)
        bonus_armor = max(0.0, armor - self.champion.base_armor)
        bonus_mr = max(0.0, mr - self.champion.base_mr)

        for key, block in breakdown.items():
            if not isinstance(block, dict):
                continue

            ad_ratio = self._num(block.get("ad_ratio", 0.0))
            ap_ratio = self._num(block.get("ap_ratio", 0.0))
            as_ratio = self._num(block.get("attack_speed_ratio", 0.0))
            heal_ratio = self._num(block.get("heal_ratio", 0.0))
            hp_ratio = self._num(block.get("hp_ratio", 0.0))
            bonus_hp_ratio = self._num(block.get("bonus_hp_ratio", 0.0))
            armor_ratio = self._num(block.get("armor_ratio", 0.0))
            mr_ratio = self._num(block.get("mr_ratio", 0.0))
            base_damage = self._base_damage_value(block.get("base_damage", []))

            ratio_damage = (
                ad * ad_ratio
                + ap * ap_ratio
                + attack_speed * 100.0 * as_ratio
                + hp * hp_ratio
                + bonus_hp * bonus_hp_ratio
                + bonus_armor * armor_ratio
                + bonus_mr * mr_ratio
            )
            spell_raw = max(0.0, base_damage + ratio_damage)
            casts = self._estimate_spell_casts(key, block, ability_haste)
            spell_raw *= casts

            damage_type = str(block.get("damage_type", "") or "").lower()
            if damage_type == "physical":
                physical_share = 1.0
            elif damage_type == "magic":
                physical_share = 0.0
            else:
                scale_total = ad_ratio + ap_ratio + as_ratio + hp_ratio + bonus_hp_ratio + armor_ratio + mr_ratio
                if scale_total > 0:
                    physical_share = (ad_ratio + 0.6 * as_ratio + 0.55 * bonus_hp_ratio + 0.85 * armor_ratio) / scale_total
                else:
                    physical_share = 0.5
            physical_share = min(0.95, max(0.05, physical_share))

            total_physical += spell_raw * physical_share
            total_magic += spell_raw * (1.0 - physical_share)
            total_raw += spell_raw
            heal_factor += spell_raw * heal_ratio * 0.16

        return total_physical, total_magic, total_raw, heal_factor

    def _estimate_spell_casts(
        self,
        key: str,
        block: Dict[str, Any],
        ability_haste: float,
    ) -> float:
        """Estimate casts over average combat duration using cooldown and haste."""
        if key == "passive":
            return 1.0

        cooldown_list = block.get("cooldown", [])
        cooldown = self._cooldown_value(cooldown_list)

        if cooldown <= 0:
            raise StrictDataError(f"Missing or invalid cooldown data for ability '{key}'")

        haste_mult = 1.0 + ability_haste / 100.0
        effective_cd = max(0.25, cooldown / max(0.2, haste_mult))
        window = max(2.5, self.champion.average_combat_seconds)

        casts = 1.0 + window / effective_cd
        if key == "r":
            # Ultimates are usually single-cast in short fights.
            return min(1.6, max(0.7, casts * 0.35))
        return min(6.0, max(0.7, casts))

    def _effective_spike_ratios(self) -> Tuple[float, float]:
        """Return (effective_ad_ratio, effective_ap_ratio) for this champion.

        When per-ability breakdown exists, each ability's own ratios are weighted
        by ability importance — this is the primary path and gives per-ability accuracy.
        When no breakdown is available, the stored ChampionProfile values are used
        (which are tag-based heuristics, not the broken 1.0/1.0 dataclass defaults).
        """
        breakdown = self.champion.ability_breakdown or {}
        if not breakdown:
            raise StrictDataError("Per-ability breakdown is required for spike ratio calculation")

        ad_total = 0.0
        ap_total = 0.0
        weight_total = 0.0
        weight_by_key = {"passive": 0.8, "q": 1.2, "w": 1.0, "e": 1.0, "r": 1.4}
        for key, block in breakdown.items():
            if not isinstance(block, dict):
                continue
            weight = weight_by_key.get(key, 1.0)
            ad_total += self._num(block.get("ad_ratio", 0.0)) * weight
            ap_total += self._num(block.get("ap_ratio", 0.0)) * weight
            weight_total += weight

        if weight_total <= 0:
            raise StrictDataError("Unable to calculate spike ratios from ability breakdown")
        return max(0.0, ad_total / weight_total), max(0.0, ap_total / weight_total)

    def _validate_strict_champion_data(self) -> None:
        breakdown = self.champion.ability_breakdown or {}
        if not breakdown:
            raise StrictDataError("Champion ability breakdown is required for strict optimization")

        has_any_combat_signal = False

        for key in ("q", "w", "e", "r"):
            block = breakdown.get(key)
            if not isinstance(block, dict):
                raise StrictDataError(f"Missing ability block for '{key}'")

            cooldown = self._cooldown_value(block.get("cooldown", []))
            if cooldown <= 0:
                raise StrictDataError(f"Missing cooldown values for ability '{key}'")

            has_signal = any(
                self._num(block.get(metric, 0.0)) > 0
                for metric in (
                    "ad_ratio",
                    "ap_ratio",
                    "attack_speed_ratio",
                    "heal_ratio",
                    "hp_ratio",
                    "bonus_hp_ratio",
                    "armor_ratio",
                    "mr_ratio",
                )
            )
            has_base_damage = self._base_damage_value(block.get("base_damage", [])) > 0
            if has_signal or has_base_damage:
                has_any_combat_signal = True

        # Some abilities are pure utility and can legitimately have no direct
        # scaling/base damage. Require at least one usable combat signal overall.
        if not has_any_combat_signal:
            raise StrictDataError("No usable scaling or base damage data found in champion abilities")

    @staticmethod
    def _cooldown_value(value: Any) -> float:
        if not isinstance(value, list):
            return 0.0
        vals: List[float] = []
        for v in value:
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if f > 0:
                vals.append(f)
        if not vals:
            return 0.0
        # Mid-rank cooldown is a decent approximation for average game state.
        return vals[len(vals) // 2]

    @staticmethod
    def _base_damage_value(value: Any) -> float:
        if not isinstance(value, list):
            return 0.0
        vals: List[float] = []
        for v in value:
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                continue
        if not vals:
            return 0.0
        return vals[-1]

    @staticmethod
    def _num(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _checkpoint_best(
        self,
        ranked: Sequence[BuildEvaluation],
        settings: SearchSettings,
        weights: ObjectiveWeights,
        enemy: EnemyProfile,
    ) -> Dict[str, BuildEvaluation]:
        if not ranked:
            return {}

        target_size = max(1, settings.build_size)
        points = [1, 2, 3, target_size]
        dedup_points: List[int] = []
        for p in points:
            if p <= target_size and p not in dedup_points:
                dedup_points.append(p)

        out: Dict[str, BuildEvaluation] = {}
        for point in dedup_points:
            best: Optional[BuildEvaluation] = None
            for build in ranked:
                if len(build.order) < point:
                    continue
                prefix = list(build.order[:point])
                eval_prefix = self._evaluate_build(prefix, prefix, weights, enemy)
                if best is None or eval_prefix.weighted_score > best.weighted_score:
                    best = eval_prefix
            if best is not None:
                out[f"{point}_item"] = best

        return out

    def _apply_diminishing_returns(
        self,
        ad: float,
        ap: float,
        hp: float,
        armor: float,
        mr: float,
    ) -> tuple[float, float, float, float, float]:
        """Apply soft diminishing returns to raw stats above natural thresholds."""
        ad_excess = max(0.0, ad - 200.0) / 200.0
        ap_excess = max(0.0, ap - 300.0) / 300.0
        hp_excess = max(0.0, hp - 3000.0) / 3000.0
        armor_excess = max(0.0, armor - 150.0) / 150.0
        mr_excess = max(0.0, mr - 100.0) / 100.0

        eff_ad = ad * (1.0 - 0.035 * ad_excess)
        eff_ap = ap * (1.0 - 0.035 * ap_excess)
        eff_hp = hp * (1.0 - 0.035 * hp_excess)
        eff_armor = armor * (1.0 - 0.035 * armor_excess)
        eff_mr = mr * (1.0 - 0.035 * mr_excess)
        return eff_ad, eff_ap, eff_hp, eff_armor, eff_mr

    def _gold_efficiency_bonus(self, items: Sequence[ItemStats]) -> float:
        """Reward items that provide above-average stat value per gold spent.
        Only applies positive bonuses; below-average items are not penalized."""
        total = 0.0
        for item in items:
            gold = max(1.0, float(getattr(item, "total_gold", 0) or 0))
            if gold < 500:
                continue  # skip components / free items
            value = (
                item.ad * 35.0
                + item.ap * 22.0
                + item.hp * 2.5
                + item.armor * 20.0
                + item.mr * 20.0
                + item.ability_haste * 25.0
            )
            efficiency = value / gold - 1.0
            # Only reward above-average efficiency; do not penalize below-average
            if efficiency > 0:
                total += efficiency * 5.0
        return min(50.0, total)

    def _interaction_bonus(
        self,
        items: Sequence[ItemStats],
        ad: float,
        ap: float,
        lifesteal_metric: float,
        armor_pen: float,
        magic_pen: float,
    ) -> Tuple[float, List[str]]:
        names = {x.name.lower() for x in items}
        bonus = 0.0
        interactions: List[str] = []

        spike_ad, spike_ap = self._effective_spike_ratios()
        if ad >= 200 and armor_pen > 0 and spike_ad > 0.1:
            pair_bonus = 35.0 * armor_pen
            bonus += pair_bonus
            interactions.append(f"AD x armor-pen spike (+{pair_bonus:.2f})")
        if ap >= 250 and magic_pen > 0 and spike_ap > 0.1:
            pair_bonus = 35.0 * magic_pen
            bonus += pair_bonus
            interactions.append(f"AP x magic-pen spike (+{pair_bonus:.2f})")
        if lifesteal_metric >= 0.18 and (ad + ap) >= 300:
            pair_bonus = 18.0
            bonus += pair_bonus
            interactions.append(f"High damage sustain loop (+{pair_bonus:.2f})")
        if any("liandry" in n for n in names) and any("rylais" in n or "rabadon" in n for n in names):
            pair_bonus = 15.0
            bonus += pair_bonus
            interactions.append(f"AP burn synergy (+{pair_bonus:.2f})")
        if any("blade of the ruined king" in n for n in names) and any("guinsoo" in n for n in names):
            pair_bonus = 14.0
            bonus += pair_bonus
            interactions.append(f"On-hit shred loop (+{pair_bonus:.2f})")

        # --- Enhanced synergies ---
        attack_speed = sum(x.attack_speed for x in items)
        if any("kraken" in n for n in names) and attack_speed > 0.5:
            pair_bonus = 18.0
            bonus += pair_bonus
            interactions.append(f"Kraken Slayer + AS synergy (+{pair_bonus:.2f})")
        if any("trinity" in n for n in names) and ad > 150:
            pair_bonus = 16.0
            bonus += pair_bonus
            interactions.append(f"Trinity Force AD spellblade (+{pair_bonus:.2f})")
        if any("rabadon" in n for n in names) and ap > 200:
            pair_bonus = 28.0 * min(1.0, (ap - 200.0) / 300.0 + 0.5)
            bonus += pair_bonus
            interactions.append(f"Rabadon's AP amplifier (+{pair_bonus:.2f})")
        if any("eclipse" in n for n in names):
            pair_bonus = 10.0
            bonus += pair_bonus
            interactions.append(f"Eclipse burst proc (+{pair_bonus:.2f})")
        if any("sundered sky" in n for n in names) and ad > 100:
            pair_bonus = 12.0
            bonus += pair_bonus
            interactions.append(f"Sundered Sky crit heal (+{pair_bonus:.2f})")

        return bonus, interactions

    def _advanced_ability_signals(self) -> Dict[str, float]:
        breakdown = self.champion.ability_breakdown or {}
        if not breakdown:
            return {
                "aoe_ratio": 0.0,
                "utility_ratio": 0.0,
                "durability_ratio": 0.0,
                "consistency_ratio": 0.0,
                "range_bias": 0.0,
            }

        total = 0
        aoe_hits = 0
        utility_hits = 0
        range_values: List[float] = []
        durability_signal = 0.0
        consistency_signal = 0.0
        for block in breakdown.values():
            if not isinstance(block, dict):
                continue
            total += 1
            targeting = str(block.get("targeting", "") or "").lower()
            key_name = str(block.get("name", "") or "").lower()
            if any(token in targeting for token in ("aoe", "cone", "line", "multi", "area", "radius")):
                aoe_hits += 1
            if bool(block.get("is_conditional")) or bool(block.get("is_channeled")) or bool(block.get("on_hit")):
                utility_hits += 1
            if any(token in key_name for token in ("slow", "stun", "root", "knock", "shield", "heal")):
                utility_hits += 1

            range_values.append(self._num(block.get("range_units", 0.0)))
            durability_signal += self._num(block.get("hp_ratio", 0.0)) + self._num(block.get("bonus_hp_ratio", 0.0))

            scale_signal = (
                self._num(block.get("ad_ratio", 0.0))
                + self._num(block.get("ap_ratio", 0.0))
                + self._num(block.get("attack_speed_ratio", 0.0))
            )
            if scale_signal > 0:
                consistency_signal += 1.0

        total = max(1, total)
        avg_range = sum(range_values) / max(1, len(range_values)) if range_values else 0.0
        return {
            "aoe_ratio": aoe_hits / total,
            "utility_ratio": min(1.0, utility_hits / total),
            "durability_ratio": min(1.0, durability_signal / max(1.0, total)),
            "consistency_ratio": min(1.0, consistency_signal / total),
            "range_bias": min(1.0, avg_range / 700.0),
        }

    def _aggregate_self_damage_reduction(self) -> float:
        """Sum ability-based self damage reduction ratios across the champion's kit.

        Abilities like Warwick E (Primal Howl), Briar E (Chilling Scream), and
        Garen W (Courage) grant the champion damage reduction while active.  The
        aggregated value is used to boost effective-HP (tankiness) in the
        objective function so that tanky-kit champions are rewarded correctly.
        The return value is a fraction between 0.0 and 0.80.
        """
        breakdown = self.champion.ability_breakdown or {}
        total_dr = 0.0
        for block in breakdown.values():
            if not isinstance(block, dict):
                continue
            if block.get("has_damage_reduction"):
                total_dr += float(block.get("damage_reduction_ratio", 0.0) or 0.0)
        return min(0.80, total_dr)

    def _resolve_compute_backend(self, backend: str) -> str:
        requested = str(backend or "auto").strip().lower()
        if requested == "cpu":
            return "cpu"
        if requested == "gpu":
            return "gpu" if _NUMBA_CUDA_AVAILABLE else "cpu"

        env_backend = str(os.environ.get("MCB_COMPUTE_BACKEND", "")).strip().lower()
        if env_backend == "cpu":
            return "cpu"
        if env_backend == "gpu":
            return "gpu" if _NUMBA_CUDA_AVAILABLE else "cpu"
        return "gpu" if _NUMBA_CUDA_AVAILABLE else "cpu"

    def _batch_prescore(
        self,
        builds: Sequence[List[ItemStats]],
        weights: ObjectiveWeights,
        enemy: EnemyProfile,
        backend: str,
    ) -> List[float]:
        if not builds:
            return []

        if np is None:
            return self._batch_prescore_python(builds, weights, enemy)

        rows: List[List[float]] = []
        for build in builds:
            rows.append([
                float(sum(x.ad for x in build)),
                float(sum(x.ap for x in build)),
                float(sum(x.attack_speed for x in build)),
                float(self.champion.base_hp + sum(x.hp for x in build)),
                float(self.champion.base_armor + sum(x.armor for x in build)),
                float(self.champion.base_mr + sum(x.mr for x in build)),
                float(sum(x.ability_haste for x in build)),
                float(sum(x.lifesteal for x in build)),
                float(sum(x.omnivamp for x in build)),
                float(sum(x.damage_amp for x in build)),
                float(sum(x.armor_pen for x in build)),
                float(sum(x.magic_pen for x in build)),
                float(sum(x.max_hp_damage for x in build)),
                float(sum(x.bonus_true_damage for x in build)),
            ])

        stats = np.asarray(rows, dtype=np.float32)
        ad_ratio, ap_ratio = self._effective_spike_ratios()
        phys_frac, magic_frac, _ = self._detect_champion_damage_profile(self.champion.ability_breakdown or {})
        advanced_signals = self._advanced_ability_signals()

        if backend == "gpu" and _NUMBA_CUDA_AVAILABLE and _gpu_prescore_kernel is not None:
            try:
                out = np.zeros((stats.shape[0],), dtype=np.float32)
                d_stats = _CUDA.to_device(stats)
                d_out = _CUDA.to_device(out)
                threads = 128
                blocks = (stats.shape[0] + threads - 1) // threads
                _gpu_prescore_kernel[blocks, threads](
                    d_stats,
                    d_out,
                    np.float32(ad_ratio),
                    np.float32(ap_ratio),
                    np.float32(phys_frac),
                    np.float32(magic_frac),
                    np.float32(advanced_signals["utility_ratio"]),
                    np.float32(advanced_signals["consistency_ratio"]),
                    np.float32(enemy.target_armor),
                    np.float32(enemy.target_mr),
                    np.float32(enemy.target_hp),
                    np.float32(enemy.physical_share),
                    np.float32(weights.damage),
                    np.float32(weights.healing),
                    np.float32(weights.tankiness),
                    np.float32(weights.lifesteal),
                    np.float32(weights.utility),
                    np.float32(weights.consistency),
                    np.float32(self.champion.base_hp),
                )
                return [float(x) for x in d_out.copy_to_host()]
            except Exception:
                self._active_compute_backend = "cpu"

        ad = stats[:, 0]
        ap = stats[:, 1]
        attack_speed = stats[:, 2]
        hp = stats[:, 3]
        armor = stats[:, 4]
        mr = stats[:, 5]
        ability_haste = stats[:, 6]
        lifesteal = stats[:, 7]
        omnivamp = stats[:, 8]
        damage_amp = stats[:, 9]
        armor_pen = stats[:, 10]
        magic_pen = stats[:, 11]
        max_hp_damage = stats[:, 12]
        bonus_true_damage = stats[:, 13]

        auto_physical = ad + attack_speed * 25.0
        spell_raw = ad * ad_ratio + ap * ap_ratio + ability_haste * 0.9 + np.maximum(0.0, hp - self.champion.base_hp) * 0.05
        premit_physical = auto_physical + spell_raw * phys_frac + max_hp_damage * enemy.target_hp * 0.5
        premit_magic = spell_raw * magic_frac + max_hp_damage * enemy.target_hp * 0.5
        effective_armor = np.maximum(0.0, enemy.target_armor * (1.0 - armor_pen))
        effective_mr = np.maximum(0.0, enemy.target_mr * (1.0 - magic_pen))
        damage = (
            premit_physical * (100.0 / (100.0 + effective_armor))
            + premit_magic * (100.0 / (100.0 + effective_mr))
            + bonus_true_damage
        ) * (1.0 + damage_amp)
        healing = damage * (lifesteal * 0.2 + omnivamp * 0.25)
        tankiness = (
            hp * (1.0 + armor / 100.0) * enemy.physical_share
            + hp * (1.0 + mr / 100.0) * (1.0 - enemy.physical_share)
        )
        lifesteal_metric = lifesteal + omnivamp
        utility = (
            ability_haste * (0.72 + advanced_signals["utility_ratio"] * 0.25)
            + (armor + mr) * 0.35
            + np.maximum(0.0, hp - self.champion.base_hp) * 0.02
        )
        consistency = (
            np.minimum(1.0, (attack_speed * 18.0 + ability_haste) / 100.0) * 40.0
            + advanced_signals["consistency_ratio"] * 30.0
            + (1.0 - abs(enemy.physical_share - 0.5)) * 8.0
        )

        score = (
            weights.damage * damage
            + weights.healing * healing
            + weights.tankiness * tankiness
            + weights.lifesteal * lifesteal_metric * 100.0
            + weights.utility * utility
            + weights.consistency * consistency
            + damage_amp * 30.0
        )
        return [float(x) for x in score.tolist()]

    def _batch_prescore_python(
        self,
        builds: Sequence[List[ItemStats]],
        weights: ObjectiveWeights,
        enemy: EnemyProfile,
    ) -> List[float]:
        ad_ratio, ap_ratio = self._effective_spike_ratios()
        phys_frac, magic_frac, _ = self._detect_champion_damage_profile(self.champion.ability_breakdown or {})
        advanced_signals = self._advanced_ability_signals()
        out: List[float] = []
        for build in builds:
            ad = sum(x.ad for x in build)
            ap = sum(x.ap for x in build)
            attack_speed = sum(x.attack_speed for x in build)
            hp = self.champion.base_hp + sum(x.hp for x in build)
            armor = self.champion.base_armor + sum(x.armor for x in build)
            mr = self.champion.base_mr + sum(x.mr for x in build)
            ability_haste = sum(x.ability_haste for x in build)
            lifesteal = sum(x.lifesteal for x in build)
            omnivamp = sum(x.omnivamp for x in build)
            damage_amp = sum(x.damage_amp for x in build)
            armor_pen = sum(x.armor_pen for x in build)
            magic_pen = sum(x.magic_pen for x in build)
            max_hp_damage = sum(x.max_hp_damage for x in build)
            bonus_true_damage = sum(x.bonus_true_damage for x in build)

            auto_physical = ad + attack_speed * 25.0
            spell_raw = ad * ad_ratio + ap * ap_ratio + ability_haste * 0.9 + max(0.0, hp - self.champion.base_hp) * 0.05
            premit_physical = auto_physical + spell_raw * phys_frac + max_hp_damage * enemy.target_hp * 0.5
            premit_magic = spell_raw * magic_frac + max_hp_damage * enemy.target_hp * 0.5
            effective_armor = max(0.0, enemy.target_armor * (1.0 - armor_pen))
            effective_mr = max(0.0, enemy.target_mr * (1.0 - magic_pen))
            damage = (
                premit_physical * (100.0 / (100.0 + effective_armor))
                + premit_magic * (100.0 / (100.0 + effective_mr))
                + bonus_true_damage
            ) * (1.0 + damage_amp)
            healing = damage * (lifesteal * 0.2 + omnivamp * 0.25)
            tankiness = (
                hp * (1.0 + armor / 100.0) * enemy.physical_share
                + hp * (1.0 + mr / 100.0) * (1.0 - enemy.physical_share)
            )
            lifesteal_metric = lifesteal + omnivamp
            utility = (
                ability_haste * (0.72 + advanced_signals["utility_ratio"] * 0.25)
                + (armor + mr) * 0.35
                + max(0.0, hp - self.champion.base_hp) * 0.02
            )
            consistency = (
                min(1.0, (attack_speed * 18.0 + ability_haste) / 100.0) * 40.0
                + advanced_signals["consistency_ratio"] * 30.0
                + (1.0 - abs(enemy.physical_share - 0.5)) * 8.0
            )

            score = (
                weights.damage * damage
                + weights.healing * healing
                + weights.tankiness * tankiness
                + weights.lifesteal * lifesteal_metric * 100.0
                + weights.utility * utility
                + weights.consistency * consistency
                + damage_amp * 30.0
            )
            out.append(float(score))
        return out

    @staticmethod
    def _is_boots(item: ItemStats) -> bool:
        lowered = item.name.lower()
        return "boots" in lowered or "greaves" in lowered or "treads" in lowered or "shoes" in lowered

    @staticmethod
    def _detect_champion_damage_profile(
        breakdown: Dict[str, Any],
    ) -> Tuple[float, float, float]:
        """Return (physical_frac, magic_frac, on_hit_frac) from ability damage_type fields."""
        counts: Dict[str, int] = {"physical": 0, "magic": 0, "true": 0, "mixed": 0}
        on_hit_count = 0
        total = 0
        for block in breakdown.values():
            if not isinstance(block, dict):
                continue
            dt = str(block.get("damage_type", "") or "").lower()
            if dt in counts:
                counts[dt] += 1
            if block.get("on_hit"):
                on_hit_count += 1
            total += 1
        if total == 0:
            return 0.5, 0.5, 0.0
        phys = (counts["physical"] + 0.5 * counts["mixed"]) / total
        magic = (counts["magic"] + 0.5 * counts["mixed"]) / total
        # If champion has all utility/unknown abilities, fall back to neutral
        if phys == 0.0 and magic == 0.0:
            return 0.5, 0.5, 0.0
        return phys, magic, on_hit_count / total

    def _valid_partial_build(self, items: Sequence[ItemStats], constraints: BuildConstraints) -> bool:
        if constraints.max_total_gold is not None:
            if sum(x.total_gold for x in items) > constraints.max_total_gold:
                return False
        # Hard-block items that share a wiki-extracted unique passive name.
        all_passives = [p for item in items for p in item.unique_passives]
        if len(all_passives) != len(set(all_passives)):
            return False
        # Fallback: also check the legacy hardcoded unique_group field.
        groups = [x.unique_group for x in items if x.unique_group]
        if len(groups) != len(set(groups)):
            return False
        return True

    def _valid_final_build(self, items: Sequence[ItemStats], constraints: BuildConstraints) -> bool:
        if not self._valid_partial_build(items, constraints):
            return False

        ids = {x.item_id for x in items}
        if constraints.must_include_ids:
            if not set(constraints.must_include_ids).issubset(ids):
                return False
        if constraints.excluded_ids:
            if any(x in ids for x in constraints.excluded_ids):
                return False
        if constraints.require_boots and not any(self._is_boots(x) for x in items):
            return False
        return True

    def _pareto_frontier(self, ranked: Iterable[BuildEvaluation]) -> List[BuildEvaluation]:
        frontier: List[BuildEvaluation] = []

        for candidate in ranked:
            dominated = False
            for other in ranked:
                if other is candidate:
                    continue
                if self._dominates(other.metrics, candidate.metrics):
                    dominated = True
                    break
            if not dominated:
                frontier.append(candidate)

        return frontier[:20]

    @staticmethod
    def _dominates(a: Dict[str, float], b: Dict[str, float]) -> bool:
        keys = ["damage", "healing", "tankiness", "lifesteal", "utility", "consistency"]
        ge_all = all(a.get(k, 0.0) >= b.get(k, 0.0) for k in keys)
        gt_any = any(a.get(k, 0.0) > b.get(k, 0.0) for k in keys)
        return ge_all and gt_any
