from __future__ import annotations

from collections import defaultdict, deque
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
from threading import Lock, Thread
import time
from typing import Any, Deque, Dict, List, Optional
from urllib.parse import quote
from uuid import uuid4

from flask import Flask, Response, jsonify, render_template, request, send_file
from flask_caching import Cache
from flask_compress import Compress
import orjson
import requests
import requests_cache
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from data_sources import (
    ChampionScaling,
    DataSourceError,
    LeagueWikiClient,
    OllamaClient,
    WikiScalingParser,
    merge_profile_with_scaling,
    override_champion_scaling,
    _safe_json,
    RATE_LIMITER,
)
from meta_build_comparison import compare_optimizer_build_to_ugg, extract_live_rune_pages, prewarm_meta_snapshot, _safe_float as _safe_float
from optimizer import BuildConstraints, BuildOptimizer, EnemyProfile, ItemStats, ObjectiveWeights, RuneChoice, RunePage, SearchSettings
import simulation as sim_engine


app = Flask(__name__)
Compress(app)
cache = Cache(app, config={
  "CACHE_TYPE": "SimpleCache",
  "CACHE_DEFAULT_TIMEOUT": 300,
})


def _json_response(payload: Any, status: int = 200) -> Response:
  return Response(orjson.dumps(payload), status=status, mimetype="application/json")


_ICON_HTTP_SESSION = requests_cache.CachedSession(
  cache_name=str((Path(os.environ.get("LOCALAPPDATA", str(Path(__file__).resolve().parent))) / "mathematically_correct_builds" / "icon_http_cache")),
  backend="sqlite",
  expire_after=60 * 60 * 24,
  allowable_methods=("GET",),
)


@retry(
  reraise=True,
  stop=stop_after_attempt(3),
  wait=wait_exponential(multiplier=0.4, min=0.4, max=4.0),
  retry=retry_if_exception_type(requests.RequestException),
)
def _http_get_with_retry(url: str, **kwargs: Any) -> requests.Response:
  """Cached session GET with rate-limiting, default headers and 429 handling."""
  headers = kwargs.pop("headers", {}) or {}
  headers.setdefault(
    "User-Agent",
    "mathematically-correct-builds/1.0 (+https://github.com/felixthomsen127-coder/Mathematicallycorrectbuilds)",
  )
  timeout = kwargs.pop("timeout", 10.0)

  # Apply shared rate limiting
  try:
    RATE_LIMITER.wait()
  except Exception:
    pass

  try:
    res = _ICON_HTTP_SESSION.get(url, headers=headers, timeout=timeout, **kwargs)
  except requests.RequestException:
    raise

  if getattr(res, "status_code", 0) == 429:
    retry_after = None
    try:
      retry_after = LeagueWikiClient._parse_retry_after_seconds(res.headers.get("Retry-After"))
    except Exception:
      retry_after = None
    if retry_after:
      time.sleep(retry_after)
    else:
      time.sleep(min(1.0, timeout) + random.random() * 0.25)
    raise requests.RequestException(f"429 Too Many Requests: {url}")

  return res

riot = LeagueWikiClient()
wiki = WikiScalingParser()
ollama = OllamaClient()

_LOCAL_APPDATA = os.environ.get("LOCALAPPDATA")
_ICON_CACHE_ROOT = (
  Path(_LOCAL_APPDATA) / "mathematically_correct_builds" / "icon_cache"
  if _LOCAL_APPDATA
  else Path(__file__).resolve().parent / ".icon_cache"
)
_ICON_CACHE_ROOT.mkdir(parents=True, exist_ok=True)

_RUNE_CATALOG_LOCK = Lock()
_RUNE_CATALOG: Dict[str, Any] = {
  "fetched_at": 0.0,
  "version": "",
  "by_id": {},
  "name_to_id": {},
  "styles": [],
  "styles_by_name": {},
}

_CHAMPION_SLUG_INDEX_LOCK = Lock()
_CHAMPION_SLUG_INDEX: Dict[str, Any] = {
  "fetched_at": 0.0,
  "patch": "",
  "by_norm": {},
}

_DEFAULT_SHARD_OPTIONS: List[List[Dict[str, str]]] = [
  [
    {"id": "5008", "name": "Adaptive Force", "icon_url": "/api/icon/shard/5008"},
    {"id": "5005", "name": "Attack Speed", "icon_url": "/api/icon/shard/5005"},
    {"id": "5007", "name": "Ability Haste", "icon_url": "/api/icon/shard/5007"},
  ],
  [
    {"id": "5008b", "name": "Adaptive Force", "icon_url": "/api/icon/shard/5008"},
    {"id": "5002", "name": "Armor", "icon_url": "/api/icon/shard/5002"},
    {"id": "5003", "name": "Magic Resist", "icon_url": "/api/icon/shard/5003"},
  ],
  [
    {"id": "5011", "name": "Health", "icon_url": "/api/icon/shard/5011"},
    {"id": "5013", "name": "Tenacity and Slow Resist", "icon_url": "/api/icon/shard/5013"},
    {"id": "5001", "name": "Health Scaling", "icon_url": "/api/icon/shard/5001"},
  ],
]

_jobs_lock = Lock()
_optimize_jobs: Dict[str, Dict[str, Any]] = {}
_duration_history: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=10))
_sweep_lock = Lock()
_sweep_state: Dict[str, Any] = {
  "running": False,
  "last_patch_fingerprint": "",
  "last_run_at": 0.0,
  "last_report": None,
  "last_error": "",
}
_meta_snapshot_lock = Lock()
_meta_snapshot_running: Dict[str, float] = {}
_meta_snapshot_recent: Dict[str, Dict[str, Any]] = {}
_background_cache = LeagueWikiClient().cache
_executor_lock = Lock()
_optimize_executor: Optional[ProcessPoolExecutor] = None
_prefetch_lock = Lock()
_prefetch_queue: Deque[Dict[str, Any]] = deque()
_prefetch_pending_keys: set[str] = set()
_prefetch_completed_keys: set[str] = set()
_prefetch_thread: Optional[Thread] = None
_prefetch_state: Dict[str, Any] = {
  "running": False,
  "patch": "",
  "started_at": 0.0,
  "updated_at": 0.0,
  "completed_at": 0.0,
  "total": 0,
  "completed": 0,
  "failed": 0,
  "current": None,
  "current_label": "",
  "queue_size": 0,
  "errors": [],
  "last_error": "",
  "ready": False,
  "priority_champion": "",
  "totals_by_kind": {},
  "completed_by_kind": {},
}

_PREFETCH_ROLE_OPTIONS = ("jungle", "top", "middle", "adc", "support")
_PREFETCH_TIER_OPTIONS = ("emerald_plus", "diamond_plus", "master_plus", "platinum_plus")
_PREFETCH_REGION_OPTIONS = ("global", "na", "euw", "kr")


def _meta_snapshot_key(champion: str, role: str, tier: str, region: str, patch: str) -> str:
  return "|".join([
    str(champion or "").strip().lower(),
    str(role or "").strip().lower(),
    str(tier or "").strip().lower(),
    str(region or "").strip().lower(),
    str(patch or "").strip().lower(),
  ])


def _prefetch_marker_key(patch: str) -> str:
  token = str(patch or "live").strip().lower() or "live"
  return f"startup_prefetch_{token}"


def _load_prefetch_marker(patch: str) -> Dict[str, Any]:
  cached = _background_cache.get(_prefetch_marker_key(patch))
  return dict(cached) if isinstance(cached, dict) else {}


def _save_prefetch_marker(patch: str, payload: Dict[str, Any]) -> None:
  try:
    _background_cache.set(_prefetch_marker_key(patch), dict(payload))
  except Exception:
    pass


def _balanced_worker_count() -> int:
  cpu_count = os.cpu_count() or 2
  physical = 0
  try:
    import psutil  # type: ignore[import-not-found]

    physical = int(psutil.cpu_count(logical=False) or 0)
  except Exception:
    physical = 0
  baseline = physical or cpu_count
  return max(1, min(max(1, cpu_count - 1), baseline, 4))


def _get_optimize_executor() -> ProcessPoolExecutor:
  global _optimize_executor
  with _executor_lock:
    if _optimize_executor is None:
      _optimize_executor = ProcessPoolExecutor(max_workers=_balanced_worker_count())
    return _optimize_executor


def _prefetch_task_key(task: Dict[str, Any]) -> str:
  kind = str(task.get("kind", "") or "")
  if kind == "items":
    return f"items|{task.get('patch', '')}"
  if kind == "champions":
    return f"champions|{task.get('patch', '')}"
  if kind == "scaling":
    return f"scaling|{task.get('patch', '')}|{task.get('champion', '')}"
  if kind == "meta":
    return "meta|{patch}|{champion}|{role}|{tier}|{region}".format(
      patch=task.get("patch", ""),
      champion=task.get("champion", ""),
      role=task.get("role", ""),
      tier=task.get("tier", ""),
      region=task.get("region", ""),
    )
  return json.dumps(task, sort_keys=True)


def _prefetch_task_label(task: Dict[str, Any]) -> str:
  kind = str(task.get("kind", "") or "")
  if kind == "items":
    return "Fetching item catalog"
  if kind == "champions":
    return "Fetching champion catalog"
  if kind == "scaling":
    return f"Scaling: {task.get('champion', 'unknown')}"
  if kind == "meta":
    return (
      f"Meta: {task.get('champion', 'unknown')} | {task.get('role', 'role')} | "
      f"{task.get('tier', 'tier')} | {task.get('region', 'region')}"
    )
  return "Background fetch"


def _prefetch_progress_payload() -> Dict[str, Any]:
  with _prefetch_lock:
    total = int(_prefetch_state.get("total", 0) or 0)
    completed = int(_prefetch_state.get("completed", 0) or 0)
    failed = int(_prefetch_state.get("failed", 0) or 0)
    running = bool(_prefetch_state.get("running"))
    pct = 100.0 if total and completed >= total else ((completed / total) * 100.0 if total else (100.0 if _prefetch_state.get("ready") else 0.0))
    totals_by_kind = dict(_prefetch_state.get("totals_by_kind", {}) or {})
    completed_by_kind = dict(_prefetch_state.get("completed_by_kind", {}) or {})
    critical_total = sum(int(totals_by_kind.get(kind, 0) or 0) for kind in ("items", "champions", "scaling"))
    critical_completed = sum(int(completed_by_kind.get(kind, 0) or 0) for kind in ("items", "champions", "scaling"))
    critical_pct = 100.0 if critical_total and critical_completed >= critical_total else ((critical_completed / critical_total) * 100.0 if critical_total else (100.0 if _prefetch_state.get("ready") else 0.0))
    return {
      **_prefetch_state,
      "progress_percent": round(max(0.0, min(100.0, pct)), 1),
      "critical_progress_percent": round(max(0.0, min(100.0, critical_pct)), 1),
      "critical_total": critical_total,
      "critical_completed": critical_completed,
      "queue_size": len(_prefetch_queue),
      "completed": completed,
      "failed": failed,
      "total": total,
      "running": running,
    }


def _enqueue_prefetch_task(task: Dict[str, Any], *, front: bool = False) -> bool:
  key = _prefetch_task_key(task)
  with _prefetch_lock:
    if key in _prefetch_completed_keys or key in _prefetch_pending_keys:
      return False
    if front:
      _prefetch_queue.appendleft(task)
    else:
      _prefetch_queue.append(task)
    _prefetch_pending_keys.add(key)
    _prefetch_state["queue_size"] = len(_prefetch_queue)
    return True


def _build_prefetch_tasks(patch: str) -> List[Dict[str, Any]]:
  champions = riot.get_all_champions(patch, force_refresh=False)
  tasks: List[Dict[str, Any]] = [
    {"kind": "items", "patch": patch},
    {"kind": "champions", "patch": patch},
  ]
  names = [str(row.get("name", "") or "").strip() for row in champions if str(row.get("name", "") or "").strip()]
  for champion in names:
    tasks.append({"kind": "scaling", "patch": patch, "champion": champion})
  for champion in names:
    for role in _PREFETCH_ROLE_OPTIONS:
      for tier in _PREFETCH_TIER_OPTIONS:
        for region in _PREFETCH_REGION_OPTIONS:
          tasks.append(
            {
              "kind": "meta",
              "patch": patch,
              "champion": champion,
              "role": role,
              "tier": tier,
              "region": region,
            }
          )
  return tasks


def _execute_prefetch_task(task: Dict[str, Any]) -> None:
  kind = str(task.get("kind", "") or "")
  patch = str(task.get("patch", "") or "")
  force_refresh = bool(task.get("force_refresh", False))
  if kind == "items":
    riot.get_items(patch, force_refresh=force_refresh)
    return
  if kind == "champions":
    riot.get_all_champions(patch, force_refresh=force_refresh)
    return
  if kind == "scaling":
    champion = str(task.get("champion", "") or "").strip()
    if champion:
      wiki.get_scaling(champion, force_refresh=force_refresh, use_ai_fallback=False)
    return
  if kind == "meta":
    champion = str(task.get("champion", "") or "").strip()
    if champion:
      prewarm_meta_snapshot(
        champion=champion,
        role=str(task.get("role", "jungle") or "jungle"),
        tier=str(task.get("tier", "emerald_plus") or "emerald_plus"),
        region=str(task.get("region", "global") or "global"),
        patch=str(task.get("meta_patch", "live") or task.get("patch", "live") or "live"),
      )


def _run_prefetch_cycle(patch: str, force_refresh: bool = False) -> None:
  marker = _load_prefetch_marker(patch)
  if marker.get("complete") and not force_refresh:
    with _prefetch_lock:
      totals_by_kind = dict(marker.get("totals_by_kind", {}) or {})
      _prefetch_state.update(
        {
          "running": False,
          "patch": patch,
          "started_at": float(marker.get("started_at", 0.0) or 0.0),
          "updated_at": float(marker.get("completed_at", 0.0) or time.time()),
          "completed_at": float(marker.get("completed_at", 0.0) or time.time()),
          "total": int(marker.get("total", 0) or 0),
          "completed": int(marker.get("total", 0) or 0),
          "failed": int(marker.get("failed", 0) or 0),
          "current": None,
          "current_label": "Patch cache ready",
          "queue_size": 0,
          "last_error": "",
          "ready": True,
          "totals_by_kind": totals_by_kind,
          "completed_by_kind": totals_by_kind,
        }
      )
    return

  tasks = _build_prefetch_tasks(patch)
  totals_by_kind: Dict[str, int] = defaultdict(int)
  for task in tasks:
    totals_by_kind[str(task.get("kind", "other") or "other")] += 1
  with _prefetch_lock:
    _prefetch_queue.clear()
    _prefetch_pending_keys.clear()
    _prefetch_completed_keys.clear()
    _prefetch_state.update(
      {
        "running": True,
        "patch": patch,
        "started_at": time.time(),
        "updated_at": time.time(),
        "completed_at": 0.0,
        "total": len(tasks),
        "completed": 0,
        "failed": 0,
        "current": None,
        "current_label": "Preparing background fetch",
        "queue_size": 0,
        "errors": [],
        "last_error": "",
        "ready": False,
        "totals_by_kind": dict(totals_by_kind),
        "completed_by_kind": {},
      }
    )
  for task in tasks:
    _enqueue_prefetch_task({**task, "force_refresh": force_refresh})

  while True:
    with _prefetch_lock:
      if not _prefetch_queue:
        _prefetch_state["running"] = False
        _prefetch_state["updated_at"] = time.time()
        _prefetch_state["completed_at"] = time.time()
        _prefetch_state["current"] = None
        _prefetch_state["current_label"] = "Patch cache ready"
        _prefetch_state["queue_size"] = 0
        _prefetch_state["ready"] = True
        _save_prefetch_marker(
          patch,
          {
            "complete": True,
            "started_at": _prefetch_state.get("started_at", 0.0),
            "completed_at": _prefetch_state.get("completed_at", 0.0),
            "total": _prefetch_state.get("total", 0),
            "failed": _prefetch_state.get("failed", 0),
            "totals_by_kind": _prefetch_state.get("totals_by_kind", {}),
          },
        )
        return
      task = _prefetch_queue.popleft()
      key = _prefetch_task_key(task)
      _prefetch_state["current"] = dict(task)
      _prefetch_state["current_label"] = _prefetch_task_label(task)
      _prefetch_state["queue_size"] = len(_prefetch_queue)
      _prefetch_state["updated_at"] = time.time()
    try:
      _execute_prefetch_task(task)
    except Exception as exc:
      with _prefetch_lock:
        _prefetch_state["failed"] = int(_prefetch_state.get("failed", 0) or 0) + 1
        _prefetch_state["last_error"] = str(exc)
        errors = list(_prefetch_state.get("errors", []))[-14:]
        errors.append({"task": _prefetch_task_label(task), "error": str(exc)})
        _prefetch_state["errors"] = errors
    finally:
      with _prefetch_lock:
        kind = str(task.get("kind", "other") or "other")
        _prefetch_pending_keys.discard(key)
        _prefetch_completed_keys.add(key)
        _prefetch_state["completed"] = int(_prefetch_state.get("completed", 0) or 0) + 1
        completed_by_kind = dict(_prefetch_state.get("completed_by_kind", {}) or {})
        completed_by_kind[kind] = int(completed_by_kind.get(kind, 0) or 0) + 1
        _prefetch_state["completed_by_kind"] = completed_by_kind
        _prefetch_state["updated_at"] = time.time()


def _ensure_prefetch_running(*, force_refresh: bool = False) -> None:
  global _prefetch_thread
  patch = riot.get_latest_patch(force_refresh=force_refresh)
  marker = _load_prefetch_marker(patch)
  with _prefetch_lock:
    if marker.get("complete") and not force_refresh and not _prefetch_state.get("running"):
      _prefetch_state.update(
        {
          "running": False,
          "patch": patch,
          "started_at": float(marker.get("started_at", 0.0) or 0.0),
          "updated_at": float(marker.get("completed_at", 0.0) or time.time()),
          "completed_at": float(marker.get("completed_at", 0.0) or time.time()),
          "total": int(marker.get("total", 0) or 0),
          "completed": int(marker.get("total", 0) or 0),
          "failed": int(marker.get("failed", 0) or 0),
          "current": None,
          "current_label": "Patch cache ready",
          "queue_size": 0,
          "ready": True,
          "totals_by_kind": dict(marker.get("totals_by_kind", {}) or {}),
          "completed_by_kind": dict(marker.get("totals_by_kind", {}) or {}),
        }
      )
      return
    if _prefetch_state.get("running") and _prefetch_state.get("patch") == patch:
      return
    if _prefetch_thread is not None and _prefetch_thread.is_alive():
      return
    _prefetch_thread = Thread(target=_run_prefetch_cycle, kwargs={"patch": patch, "force_refresh": force_refresh}, daemon=True)
    _prefetch_thread.start()


def _prioritize_prefetch_for_champion(
  champion: str,
  role: str = "jungle",
  tier: str = "emerald_plus",
  region: str = "global",
  patch: Optional[str] = None,
) -> None:
  champion_name = str(champion or "").strip()
  if not champion_name:
    return
  resolved_patch = str(patch or riot.get_latest_patch(force_refresh=False) or "live")
  _ensure_prefetch_running(force_refresh=False)
  with _prefetch_lock:
    _prefetch_state["priority_champion"] = champion_name
  _enqueue_prefetch_task(
    {"kind": "meta", "patch": resolved_patch, "champion": champion_name, "role": role, "tier": tier, "region": region},
    front=True,
  )
  _enqueue_prefetch_task(
    {"kind": "scaling", "patch": resolved_patch, "champion": champion_name},
    front=True,
  )


def _schedule_meta_snapshot_prewarm(champion: str, role: str, tier: str, region: str, patch: str) -> None:
  key = _meta_snapshot_key(champion, role, tier, region, patch)
  now = time.time()
  with _meta_snapshot_lock:
    if key in _meta_snapshot_running:
      return
    _meta_snapshot_running[key] = now

  def _worker() -> None:
    started_at = time.time()
    try:
      result = prewarm_meta_snapshot(
        champion=champion,
        role=role,
        tier=tier,
        region=region,
        patch=patch,
      )
      with _meta_snapshot_lock:
        _meta_snapshot_recent[key] = {
          "key": key,
          "started_at": started_at,
          "finished_at": time.time(),
          "ok": bool(result.get("ok")),
          "result": result,
        }
    except Exception as exc:
      with _meta_snapshot_lock:
        _meta_snapshot_recent[key] = {
          "key": key,
          "started_at": started_at,
          "finished_at": time.time(),
          "ok": False,
          "error": str(exc),
        }
    finally:
      with _meta_snapshot_lock:
        _meta_snapshot_running.pop(key, None)

  Thread(target=_worker, daemon=True).start()


def _rune_choice_from_name(name: str, tree: str, slot: str) -> RuneChoice:
  key = str(name or "").strip().lower()
  # Coarse rune effect mapping used for scoring during live rune-page selection.
  if "conqueror" in key:
    return RuneChoice("conq", "Conqueror", tree, slot, ad=18.0, damage_amp=0.04)
  if "electrocute" in key:
    return RuneChoice("electrocute", "Electrocute", tree, slot, damage_amp=0.06, bonus_true_damage=25.0)
  if "first strike" in key:
    return RuneChoice("firststrike", "First Strike", tree, slot, damage_amp=0.08)
  if "arcane comet" in key:
    return RuneChoice("comet", "Arcane Comet", tree, slot, ap=18.0, damage_amp=0.05)
  if "grasp" in key:
    return RuneChoice("grasp", "Grasp of the Undying", tree, slot, hp=220.0, heal_amp=0.08)
  if "alacrity" in key:
    return RuneChoice("alacrity", "Legend: Alacrity", tree, slot, attack_speed=0.12)
  if "bloodline" in key:
    return RuneChoice("bloodline", "Legend: Bloodline", tree, slot, lifesteal=0.05)
  if "last stand" in key:
    return RuneChoice("laststand", "Last Stand", tree, slot, damage_amp=0.03)
  if "transcendence" in key:
    return RuneChoice("transcendence", "Transcendence", tree, slot, ability_haste=10.0)
  if "gathering" in key:
    return RuneChoice("gathering", "Gathering Storm", tree, slot, ap=10.0)
  if "conditioning" in key:
    return RuneChoice("conditioning", "Conditioning", tree, slot, armor=12.0, mr=12.0)
  if "overgrowth" in key:
    return RuneChoice("overgrowth", "Overgrowth", tree, slot, hp=180.0)
  if "bone plating" in key:
    return RuneChoice("boneplating", "Bone Plating", tree, slot, hp=90.0)
  if "magical footwear" in key:
    return RuneChoice("boots", "Magical Footwear", tree, slot, attack_speed=0.04)
  if "cosmic insight" in key:
    return RuneChoice("cosmic", "Cosmic Insight", tree, slot, ability_haste=8.0)
  if "scorch" in key:
    return RuneChoice("scorch", "Scorch", tree, slot, bonus_true_damage=18.0)
  if "eyeball" in key:
    return RuneChoice("eyeball", "Eyeball Collection", tree, slot, ad=10.0, ap=18.0)
  if "treasure hunter" in key:
    return RuneChoice("treasure", "Treasure Hunter", tree, slot, damage_amp=0.015)
  return RuneChoice(
    rune_id=_safe_token(key.replace(" ", "_")) or "rune",
    name=str(name or "Unknown Rune"),
    tree=tree,
    slot=slot,
  )


def _live_rune_pages(
  champion: str,
  role: str,
  tier: str,
  region: str,
  patch: str,
) -> List[RunePage]:
  pages_raw = extract_live_rune_pages(
    champion=champion,
    role=role,
    tier=tier,
    region=region,
    patch=patch,
  )
  pages: List[RunePage] = []
  for idx, row in enumerate(pages_raw):
    rune_names = [str(x) for x in row.get("rune_names", []) if str(x).strip()]
    primary_tree = str(row.get("primary_tree", "Precision") or "Precision")
    secondary_tree = str(row.get("secondary_tree", "Resolve") or "Resolve")
    runes = tuple(
      _rune_choice_from_name(name, primary_tree if i < 2 else secondary_tree, "keystone" if i == 0 else "minor")
      for i, name in enumerate(rune_names[:6])
    )
    if not runes:
      continue
    page_name = str(row.get("label", f"Live Rune Page {idx + 1}"))
    pages.append(
      RunePage(
        page_id=f"live_{idx + 1}_{_safe_token(page_name)}",
        name=page_name,
        primary_tree=primary_tree,
        secondary_tree=secondary_tree,
        shards=("Adaptive Force", "Adaptive Force", "Scaling Health"),
        runes=runes,
      )
    )
  return pages


def _safe_token(value: str) -> str:
  token = str(value or "").strip().replace("\\", "").replace("/", "")
  lower = token.lower()
  for suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
    if lower.endswith(suffix):
      token = token[: -len(suffix)]
      break
  return token


def _normalized_champion_key(value: str) -> str:
  return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _refresh_champion_slug_index(force: bool = False) -> Dict[str, str]:
  now = time.time()
  with _CHAMPION_SLUG_INDEX_LOCK:
    index = _CHAMPION_SLUG_INDEX.get("by_norm", {})
    if (not force) and isinstance(index, dict) and index and now - float(_CHAMPION_SLUG_INDEX.get("fetched_at", 0.0)) < 60 * 60 * 6:
      return dict(index)

    by_norm: Dict[str, str] = {}
    patch = ""
    try:
      patch = riot.get_latest_patch(force_refresh=False)
      champions = riot.get_all_champions(patch)
      for champ in champions:
        slug = str(champ.get("slug", "") or "")
        name = str(champ.get("name", "") or "")
        if slug:
          slug_norm = _normalized_champion_key(slug)
          if slug_norm:
            by_norm[slug_norm] = slug
        if name and slug:
          name_norm = _normalized_champion_key(name)
          if name_norm:
            by_norm[name_norm] = slug
    except Exception:
      by_norm = dict(index) if isinstance(index, dict) else {}

    _CHAMPION_SLUG_INDEX["fetched_at"] = now
    _CHAMPION_SLUG_INDEX["patch"] = patch
    _CHAMPION_SLUG_INDEX["by_norm"] = by_norm
    return dict(by_norm)


def _ensure_remote_image_payload(content: bytes, content_type: str, expected_ext: str) -> None:
  body = content or b""
  if len(body) < 12:
    raise ValueError("remote image payload too small")

  ctype = str(content_type or "").split(";", 1)[0].strip().lower()
  ext = str(expected_ext or "").strip().lower()

  is_jpeg = body.startswith(b"\xff\xd8\xff")
  is_png = body.startswith(b"\x89PNG\r\n\x1a\n")
  is_gif = body.startswith(b"GIF87a") or body.startswith(b"GIF89a")
  is_webp = body[:4] == b"RIFF" and body[8:12] == b"WEBP"
  looks_like_image = is_jpeg or is_png or is_gif or is_webp

  if not ctype.startswith("image/") and not looks_like_image:
    raise ValueError(f"expected image payload, got content-type={ctype or 'unknown'}")

  if ext in {".jpg", ".jpeg"} and not (is_jpeg or ctype in {"image/jpeg", "image/jpg"}):
    raise ValueError(f"expected JPEG payload, got content-type={ctype or 'unknown'}")
  if ext == ".png" and not (is_png or ctype == "image/png"):
    raise ValueError(f"expected PNG payload, got content-type={ctype or 'unknown'}")


def _cache_icon_from_wiki(remote_url: str, namespace: str, token: str) -> Path:
  digest = hashlib.sha1(f"{namespace}:{token}".encode("utf-8")).hexdigest()
  folder = _ICON_CACHE_ROOT / namespace
  folder.mkdir(parents=True, exist_ok=True)
  path = folder / f"{digest}.png"
  if path.exists() and path.stat().st_size > 0:
    return path

  res = _http_get_with_retry(remote_url, timeout=10.0)
  res.raise_for_status()
  _ensure_remote_image_payload(res.content, res.headers.get("content-type", ""), ".png")
  path.write_bytes(res.content)
  return path


def _cache_remote_asset(remote_url: str, namespace: str, token: str, ext: str) -> Path:
  suffix = str(ext or "").strip().lower()
  if not suffix.startswith("."):
    suffix = f".{suffix}" if suffix else ".bin"
  digest = hashlib.sha1(f"{namespace}:{token}".encode("utf-8")).hexdigest()
  folder = _ICON_CACHE_ROOT / namespace
  folder.mkdir(parents=True, exist_ok=True)
  path = folder / f"{digest}{suffix}"
  if path.exists() and path.stat().st_size > 0:
    return path

  res = _http_get_with_retry(remote_url, timeout=10.0)
  res.raise_for_status()
  _ensure_remote_image_payload(res.content, res.headers.get("content-type", ""), suffix)
  path.write_bytes(res.content)
  return path


def _local_champion_icon_url(slug: str) -> str:
  return f"/api/icon/champion/{quote(str(slug or ''), safe='')}.png"


def _local_champion_splash_url(slug: str) -> str:
  return f"/api/icon/splash/{quote(str(slug or ''), safe='')}"


def _local_item_icon_url(item_name: str) -> str:
  token = str(item_name or "").replace(" ", "_")
  return f"/api/icon/item/{quote(token, safe='')}.png"


def _champion_slug_variants(raw_slug: str) -> List[str]:
  token = _safe_token(raw_slug)
  if not token:
    return []

  variants: List[str] = []

  def _push(value: str) -> None:
    value = str(value or "").strip()
    if not value:
      return
    if value not in variants:
      variants.append(value)

  _push(token)
  compact = "".join(ch for ch in token if ch.isalnum())
  _push(compact)
  _push(compact.replace(" ", ""))
  if compact:
    _push(compact[0].upper() + compact[1:])

  token_norm = _normalized_champion_key(token)
  if token_norm:
    canonical = _refresh_champion_slug_index(force=False).get(token_norm, "")
    if canonical:
      _push(canonical)

  return variants


def _normalize_rune_name(value: str) -> str:
  return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _load_rune_catalog(force: bool = False) -> Dict[str, Any]:
  now = time.time()
  with _RUNE_CATALOG_LOCK:
    if (not force) and _RUNE_CATALOG.get("by_id") and now - float(_RUNE_CATALOG.get("fetched_at", 0.0)) < 60 * 60 * 12:
      return {
        "version": _RUNE_CATALOG.get("version", ""),
        "by_id": dict(_RUNE_CATALOG.get("by_id", {})),
        "name_to_id": dict(_RUNE_CATALOG.get("name_to_id", {})),
        "styles": list(_RUNE_CATALOG.get("styles", [])),
        "styles_by_name": dict(_RUNE_CATALOG.get("styles_by_name", {})),
      }

  version = ""
  by_id: Dict[int, Dict[str, str]] = {}
  name_to_id: Dict[str, int] = {}
  styles_out: List[Dict[str, Any]] = []
  styles_by_name: Dict[str, Dict[str, Any]] = {}

  versions_res = _http_get_with_retry("https://ddragon.leagueoflegends.com/api/versions.json", timeout=10.0)
  versions_res.raise_for_status()
  versions_payload = _safe_json(versions_res)
  if isinstance(versions_payload, list) and versions_payload:
    version = str(versions_payload[0])
  if not version:
    raise ValueError("Failed to resolve Data Dragon version")

  runes_url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/runesReforged.json"
  runes_res = _http_get_with_retry(runes_url, timeout=10.0)
  runes_res.raise_for_status()
  styles = _safe_json(runes_res)
  if not isinstance(styles, list):
    raise ValueError("Unexpected rune catalog payload")

  for style in styles:
    style_id = 0
    style_name = ""
    try:
      style_id = int(style.get("id", 0)) if isinstance(style, dict) else 0
    except Exception:
      style_id = 0
    if isinstance(style, dict):
      style_name = str(style.get("name", "") or "")
    slots = style.get("slots", []) if isinstance(style, dict) else []
    slot_rows: List[Dict[str, Any]] = []

    for slot_idx, slot in enumerate(slots):
      runes = slot.get("runes", []) if isinstance(slot, dict) else []
      slot_runes_out: List[Dict[str, Any]] = []
      for rune in runes:
        try:
          rune_id = int(rune.get("id", 0))
        except Exception:
          rune_id = 0
        if rune_id <= 0:
          continue
        name = str(rune.get("name", "") or "")
        icon_rel = str(rune.get("icon", "") or "")
        if not icon_rel:
          continue
        by_id[rune_id] = {
          "name": name,
          "icon_url": f"https://ddragon.leagueoflegends.com/cdn/img/{icon_rel}",
        }
        normalized = _normalize_rune_name(name)
        if normalized:
          name_to_id[normalized] = rune_id
        slot_runes_out.append(
          {
            "id": str(rune_id),
            "name": name,
            "slot_index": slot_idx,
            "icon_url": _local_rune_icon_url(str(rune_id), name),
          }
        )
      if slot_runes_out:
        slot_rows.append({"slot_index": slot_idx, "runes": slot_runes_out})

    if style_name and slot_rows:
      style_payload = {
        "id": str(style_id) if style_id > 0 else "",
        "name": style_name,
        "slots": slot_rows,
      }
      styles_out.append(style_payload)
      styles_by_name[_normalize_rune_name(style_name)] = style_payload

  with _RUNE_CATALOG_LOCK:
    _RUNE_CATALOG["fetched_at"] = now
    _RUNE_CATALOG["version"] = version
    _RUNE_CATALOG["by_id"] = by_id
    _RUNE_CATALOG["name_to_id"] = name_to_id
    _RUNE_CATALOG["styles"] = styles_out
    _RUNE_CATALOG["styles_by_name"] = styles_by_name

  return {
    "version": version,
    "by_id": by_id,
    "name_to_id": name_to_id,
    "styles": styles_out,
    "styles_by_name": styles_by_name,
  }


def _rune_catalog_response() -> Dict[str, Any]:
  catalog = _load_rune_catalog()
  # Annotate each shard row with its slot label so the UI can show it clearly.
  _SHARD_SLOT_LABELS = ["Offense", "Flex", "Defense"]
  shards_labeled = [
    {
      "slot": _SHARD_SLOT_LABELS[i] if i < len(_SHARD_SLOT_LABELS) else f"Slot {i+1}",
      "options": row,
    }
    for i, row in enumerate(_DEFAULT_SHARD_OPTIONS)
  ]
  return {
    "version": str(catalog.get("version", "") or ""),
    "styles": list(catalog.get("styles", [])),
    "shards": list(_DEFAULT_SHARD_OPTIONS),
    "shards_labeled": shards_labeled,
  }


def _ocr_runtime_status() -> Dict[str, Any]:
  status: Dict[str, Any] = {
    "available": False,
    "python_packages": False,
    "tesseract_binary": False,
    "binary_path": "",
    "reason": "OCR dependencies are unavailable.",
  }

  try:
    import PIL  # type: ignore
    import pytesseract  # type: ignore

    status["python_packages"] = True
    binary_path = shutil.which("tesseract") or ""
    if not binary_path and os.name == "nt":
      common_path = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
      if common_path.exists():
        binary_path = str(common_path)
        pytesseract.pytesseract.tesseract_cmd = binary_path

    if binary_path:
      status["binary_path"] = binary_path
      status["tesseract_binary"] = True

    try:
      version = str(pytesseract.get_tesseract_version())
      status["available"] = True
      status["reason"] = f"OCR fallback ready ({version})."
      status["pil_version"] = getattr(PIL, "__version__", "")
      return status
    except Exception as exc:
      if status["python_packages"] and not status["tesseract_binary"]:
        status["reason"] = "Install the Tesseract binary to enable OCR fallback."
      else:
        status["reason"] = f"OCR fallback unavailable: {exc}"
      status["pil_version"] = getattr(PIL, "__version__", "")
      return status
  except Exception:
    status["reason"] = "Install Pillow and pytesseract to enable OCR fallback."
    return status


def _local_rune_icon_url(rune_id: str, rune_name: str) -> str:
  token = str(rune_id or "").strip() or str(rune_name or "").strip()
  return f"/api/icon/rune/{quote(token, safe='')}.png" if token else ""


def _resolve_rune_icon_remote_url(rune_token: str) -> Optional[str]:
  token = str(rune_token or "").strip()
  if not token:
    return None

  catalog = _load_rune_catalog()
  by_id = catalog.get("by_id", {})
  name_to_id = catalog.get("name_to_id", {})

  rune_id = 0
  if token.isdigit():
    rune_id = int(token)
  else:
    rune_id = int(name_to_id.get(_normalize_rune_name(token), 0) or 0)

  if rune_id <= 0 or rune_id not in by_id:
    return None
  return str(by_id[rune_id].get("icon_url", "") or "") or None


def _float(data: Dict[str, Any], key: str, default: float) -> float:
    raw = data.get(key, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _required_float(data: Dict[str, Any], key: str) -> float:
    if key not in data:
        raise ValueError(f"Missing required field: {key}")
    raw = data.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ValueError(f"Missing required field: {key}")
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric value for {key}") from exc


def _bool(data: Dict[str, Any], key: str, default: bool = False) -> bool:
    raw = data.get(key, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw)


def _parse_csv_tokens(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        source = ",".join(str(x) for x in value)
    else:
        source = str(value)
    return [token.strip() for token in source.split(",") if token.strip()]


def _parse_override_json(value: Any) -> Dict[str, Dict[str, float]]:
    if value is None:
        return {}
    text = str(value).strip()
    if not text:
        return {}

    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Ability override JSON must be an object")

    normalized: Dict[str, Dict[str, float]] = {}
    for key, sub in parsed.items():
        if not isinstance(sub, dict):
            continue
        normalized[str(key).lower()] = {
            metric: float(sub[metric])
            for metric in ("ad_ratio", "ap_ratio", "attack_speed_ratio", "heal_ratio")
            if metric in sub
        }
    return normalized


def _resolve_item_names_to_stats(names: List[str], item_pool: List[ItemStats]) -> List[ItemStats]:
    by_lower = {x.name.lower(): x for x in item_pool}
    out: List[ItemStats] = []
    seen = set()
    for name in names:
      key = str(name or "").strip().lower()
      if not key:
        continue
      pick = by_lower.get(key)
      if pick is None:
        # Soft match fallback for minor naming variations.
        for candidate in item_pool:
          ckey = candidate.name.lower()
          if key in ckey or ckey in key:
            pick = candidate
            break
      if pick is None or pick.item_id in seen:
        continue
      seen.add(pick.item_id)
      out.append(pick)
      if len(out) >= 6:
        break
    return out


def _innovation_score(candidate_names: List[str], ranked_context: List[Dict[str, Any]]) -> float:
    cand = {str(x or "").strip().lower() for x in candidate_names if str(x or "").strip()}
    if not cand:
      return 0.0
    max_overlap = 0.0
    for ctx in ranked_context[:8]:
      order = ctx.get("order", [])
      names = {str(x.get("name", "")).strip().lower() for x in order if isinstance(x, dict)}
      if not names:
        continue
      overlap = len(cand.intersection(names)) / max(1, len(cand.union(names)))
      if overlap > max_overlap:
        max_overlap = overlap
    return round(max(0.0, 1.0 - max_overlap), 4)


def _mode_history_key(payload: Dict[str, Any]) -> str:
  mode = str(payload.get("mode", "near_exhaustive")).strip().lower() or "near_exhaustive"
  build_size = int(_float(payload, "build_size", 6))
  candidate_pool = int(_float(payload, "candidate_pool_size", 24))
  deep_search = _bool(payload, "deep_search", False)
  backend = str(payload.get("compute_backend", "auto") or "auto").strip().lower()
  return f"{mode}:{build_size}:{candidate_pool}:{deep_search}:{backend}"


def _estimate_runtime_seconds(payload: Dict[str, Any]) -> float:
    key = _mode_history_key(payload)
    with _jobs_lock:
      samples = list(_duration_history.get(key, []))

    if samples:
      baseline = sum(samples) / len(samples)
      return max(0.7, min(600.0, baseline * 1.08))

    mode = str(payload.get("mode", "near_exhaustive")).strip().lower() or "near_exhaustive"
    build_size = int(_float(payload, "build_size", 6))
    candidate_pool = int(_float(payload, "candidate_pool_size", 24))
    deep_search = _bool(payload, "deep_search", False)
    extra_restarts = int(_float(payload, "extra_restarts", 0))
    search_mult = 1.0 + (0.55 if deep_search else 0.0) + max(0, extra_restarts) * 0.09
    if mode == "heuristic":
      return max(0.8, (0.55 + build_size * 0.2 + candidate_pool * 0.03) * search_mult)
    if mode == "exhaustive":
      cap = _float(payload, "exhaustive_runtime_cap_seconds", 120.0)
      return max(2.0, min(600.0, cap * (0.8 + (0.12 if deep_search else 0.0)) + 2.0))
    return max(1.0, (1.25 + build_size * 0.33 + candidate_pool * 0.07) * search_mult)


def _run_optimization(
    payload: Dict[str, Any],
    progress_cb: Any = None,
  ) -> Dict[str, Any]:
    champion = str(payload.get("champion", "Aatrox")).strip()
    if not champion:
      raise ValueError("Champion is required")
    role = str(payload.get("role", "jungle") or "jungle")
    comparison_mode = str(payload.get("comparison_mode", "all") or "all")
    meta_tier = str(payload.get("meta_tier", "emerald_plus") or "emerald_plus")
    meta_region = str(payload.get("meta_region", "global") or "global")
    meta_patch = str(payload.get("meta_patch", "live") or "live")

    def _emit(label: str, progress: float) -> None:
      if progress_cb:
        progress_cb(label, max(0.0, min(1.0, progress)))

    force_refresh = _bool(payload, "force_refresh", False)
    t0 = time.perf_counter()
    steps: List[Dict[str, Any]] = []

    def _step(label: str, progress: float) -> None:
      steps.append({"label": label, "ms": round((time.perf_counter() - t0) * 1000)})
      _emit(label, progress)

    _step("Starting optimization", 0.03)
    patch = riot.get_latest_patch(force_refresh=force_refresh)
    profile = riot.get_champion_profile(patch, champion, force_refresh=force_refresh)
    items = riot.get_items(patch, force_refresh=force_refresh)
    _step(f"Fetched patch {patch} - {len(items)} items available", 0.20)
    _step(f"Champion profile: {champion} [{', '.join(profile.champion_tags)}]", 0.26)
    saved_overrides: Dict[str, Dict[str, float]] = {}
    _scaling_warn = ""
    try:
      wiki_scaling = wiki.get_scaling(
        champion,
        force_refresh=force_refresh,
        use_ai_fallback=False,
      )
      saved_overrides = wiki.get_saved_overrides(champion)
      overrides = _parse_override_json(payload.get("ability_overrides"))
      if not overrides:
        overrides = saved_overrides
      if overrides:
        wiki_scaling = override_champion_scaling(wiki_scaling, overrides)
        wiki.save_overrides(champion, overrides)
        saved_overrides = overrides
      profile = merge_profile_with_scaling(profile, wiki_scaling)
    except json.JSONDecodeError as exc:
      raise ValueError(f"Invalid ability override JSON: {exc}") from exc
    except ValueError:
      raise
    except Exception as exc:
      _scaling_warn = f"Ability scaling unavailable ({exc}) â€” build uses champion base stats only."
      wiki_scaling = ChampionScaling(
        source="fallback-zero",
        placeholder_used=True,
        fallback_reasons=[str(exc)],
      )

    scaling_source = wiki_scaling.source
    _step(f"Ability scaling: {scaling_source}", 0.40)

    must_include_tokens = _parse_csv_tokens(payload.get("must_include"))
    exclude_tokens = _parse_csv_tokens(payload.get("exclude"))
    id_by_lower_name = {x.name.lower(): x.item_id for x in items}
    all_ids = {x.item_id for x in items}

    def resolve_tokens(tokens: list[str]) -> list[str]:
      resolved: list[str] = []
      for token in tokens:
        if token in all_ids:
          resolved.append(token)
          continue
        lowered = token.lower()
        if lowered in id_by_lower_name:
          resolved.append(id_by_lower_name[lowered])
      return resolved

    constraints = BuildConstraints(
      must_include_ids=tuple(resolve_tokens(must_include_tokens)),
      excluded_ids=tuple(resolve_tokens(exclude_tokens)),
      require_boots=_bool(payload, "require_boots", False),
      max_total_gold=_float(payload, "max_total_gold", 0.0) or None,
    )

    enemy = EnemyProfile(
      target_hp=_required_float(payload, "enemy_hp"),
      target_armor=_required_float(payload, "enemy_armor"),
      target_mr=_required_float(payload, "enemy_mr"),
      physical_share=_required_float(payload, "enemy_physical_share"),
    )

    live_rune_pages = _live_rune_pages(
      champion=champion,
      role=role,
      tier=meta_tier,
      region=meta_region,
      patch=meta_patch,
    )
    optimizer_obj = BuildOptimizer(profile, items, rune_pages=live_rune_pages or None)
    weights = ObjectiveWeights(
      damage=_float(payload, "damage", 1.0),
      healing=_float(payload, "healing", 0.0),
      tankiness=_float(payload, "tankiness", 0.0),
      lifesteal=_float(payload, "lifesteal", 0.0),
      utility=_float(payload, "utility", 0.25),
      consistency=_float(payload, "consistency", 0.1),
    )
    settings = SearchSettings(
      mode=str(payload.get("mode", "near_exhaustive")),
      build_size=int(_float(payload, "build_size", 6)),
      candidate_pool_size=int(_float(payload, "candidate_pool_size", 24)),
      beam_width=int(_float(payload, "beam_width", 65)),
      exhaustive_runtime_cap_seconds=_float(payload, "exhaustive_runtime_cap_seconds", 120.0),
      order_permutation_cap=int(_float(payload, "order_permutation_cap", 150)),
      sa_iterations=int(_float(payload, "sa_iterations", 100)),
      deep_search=_bool(payload, "deep_search", False),
      extra_restarts=int(_float(payload, "extra_restarts", 0)),
      compute_backend=str(payload.get("compute_backend", "auto") or "auto"),
    )

    candidate_preview = optimizer_obj._candidate_pool(settings.candidate_pool_size)
    spike_ad, spike_ap = optimizer_obj._effective_spike_ratios()
    if spike_ap > spike_ad:
      pool_bias = "AP-biased"
    elif spike_ad > spike_ap:
      pool_bias = "AD-biased"
    else:
      pool_bias = "balanced"
    _step(
      f"{len(candidate_preview)} candidates selected ({pool_bias}, AD spike {spike_ad:.2f} / AP spike {spike_ap:.2f})",
      0.56,
    )

    t_search = time.perf_counter()
    _emit(f"Running {settings.mode} search", 0.60)
    ranked, pareto, checkpoints = optimizer_obj.optimize(weights, settings, constraints=constraints, enemy=enemy)
    search_ms = round((time.perf_counter() - t_search) * 1000)
    _step(f"{settings.mode} search complete — {len(ranked)} builds scored ({search_ms} ms)", 0.91)

    if ranked:
      _step(f"Top build score: {ranked[0].weighted_score:.1f} — comparing to meta builds", 0.94)

    meta_compare = {
      "source": "u.gg",
      "available": False,
      "reason": "No ranked builds available for comparison.",
      "best_match": None,
      "meta_builds": [],
    }
    if ranked:
      try:
        def _evaluate_meta_build(item_names: List[str]) -> Dict[str, Any]:
          resolved = _resolve_item_names_to_stats(item_names, items)
          if len(resolved) < 3:
            return {"weighted_score": 0.0, "metrics": {}}
          evaluated = optimizer_obj._evaluate_best_order(
            resolved,
            weights,
            order_permutation_cap=max(60, settings.order_permutation_cap),
            enemy=enemy,
          )
          return {
            "weighted_score": float(getattr(evaluated, "weighted_score", 0.0) or 0.0),
            "metrics": dict(getattr(evaluated, "metrics", {}) or {}),
          }

        best_order_names = [x.name for x in ranked[0].order]
        top = ranked[0]
        _item_id_map = {item.item_id: item.name for item in items}
        meta_compare = compare_optimizer_build_to_ugg(
          champion,
          best_order_names,
          role=role,
          comparison_mode=comparison_mode,
          tier=meta_tier,
          region=meta_region,
          patch=meta_patch,
          optimizer_weighted_score=float(top.weighted_score),
          optimizer_metrics=dict(top.metrics or {}),
          evaluate_meta_build_fn=_evaluate_meta_build,
          item_id_to_name=_item_id_map,
          allow_persistent_snapshot=True,
        )
        _step("Meta comparison complete", 0.97)
      except Exception as exc:
        meta_compare = {
          "source": "none",
          "available": False,
          "comparison_mode": comparison_mode,
          "comparison_context": {"tier": meta_tier, "region": meta_region, "role": role, "patch": meta_patch},
          "reason": f"Meta comparison failed (U.GG / Blitz.gg / OP.GG): {exc}",
          "best_match": None,
          "meta_builds": [],
        }

    _step("Serializing results", 0.97)
    response = {
      "patch": patch,
      "champion": asdict(profile),
      "wiki_scaling": asdict(wiki_scaling) if wiki_scaling else None,
      "items_considered": len(items),
      "cache_path": str(riot.cache.base_dir),
      "saved_ability_overrides_json": json.dumps(saved_overrides, indent=2) if saved_overrides else "",
      "ranked": [serialize_build(x, patch) for x in ranked],
      "pareto": [serialize_build(x, patch) for x in pareto],
      "checkpoints": {k: serialize_build(v, patch) for k, v in checkpoints.items()},
      "meta_comparison": meta_compare,
      "meta_context": {"tier": meta_tier, "region": meta_region, "role": role, "patch": meta_patch},
      "rune_pages_considered": len(live_rune_pages),
      "compute_backend": optimizer_obj.get_compute_backend(),
      "steps": steps,
      "build_warning": _scaling_warn,
    }
    elapsed_seconds = time.perf_counter() - t0
    response["total_seconds"] = round(elapsed_seconds, 3)
    _step(f"Done - total {round(elapsed_seconds * 1000)} ms", 1.0)
    response["steps"] = steps
    return response


@app.get("/health")
def health() -> Any:
  """Health check endpoint for Container Apps."""
  return _json_response({"status": "ok", "service": "mathematically-correct-builds"}), 200


@app.get("/")
def index() -> str:
  return render_template("index.html")


@app.post("/optimize")
def optimize() -> Any:
  payload = request.get_json(silent=True) or {}
  try:
    _prioritize_prefetch_for_champion(
      champion=str(payload.get("champion", "") or "").strip(),
      role=str(payload.get("role", "jungle") or "jungle"),
      tier=str(payload.get("meta_tier", "emerald_plus") or "emerald_plus"),
      region=str(payload.get("meta_region", "global") or "global"),
      patch=str(payload.get("meta_patch", "live") or "live"),
    )
    future = _get_optimize_executor().submit(_run_optimization, payload, None)
    result = future.result()
    with _jobs_lock:
      _duration_history[_mode_history_key(payload)].append(float(result.get("total_seconds", 0.0)))
    return _json_response(result)
  except ValueError as exc:
    return jsonify({"error": str(exc)}), 400
  except DataSourceError as exc:
    return jsonify({"error": str(exc)}), 502
  except Exception as exc:
    return jsonify({"error": f"Unexpected error: {exc}"}), 500


@app.post("/optimize/start")
def optimize_start() -> Any:
  payload = request.get_json(silent=True) or {}
  _prioritize_prefetch_for_champion(
    champion=str(payload.get("champion", "") or "").strip(),
    role=str(payload.get("role", "jungle") or "jungle"),
    tier=str(payload.get("meta_tier", "emerald_plus") or "emerald_plus"),
    region=str(payload.get("meta_region", "global") or "global"),
    patch=str(payload.get("meta_patch", "live") or "live"),
  )
  estimated_seconds = _estimate_runtime_seconds(payload)
  job_id = uuid4().hex
  now = time.time()

  with _jobs_lock:
    _optimize_jobs[job_id] = {
      "status": "running",
      "progress": 0.01,
      "phase": "Queued optimization",
      "created_at": now,
      "updated_at": now,
      "estimated_seconds": estimated_seconds,
    }

  def _worker() -> None:
    def _progress(label: str, progress: float) -> None:
      with _jobs_lock:
        job = _optimize_jobs.get(job_id)
        if not job:
          return
        # Progress is milestone-based from real completed steps.
        if progress > float(job.get("progress", 0.0)):
          job["progress"] = progress
        job["phase"] = label
        job["updated_at"] = time.time()

    future: Optional[Future] = None
    try:
      with _jobs_lock:
        job = _optimize_jobs.get(job_id)
        if job:
          job["phase"] = "Running optimization in worker process"
          job["updated_at"] = time.time()
      future = _get_optimize_executor().submit(_run_optimization, payload, None)
      while future is not None and not future.done():
        with _jobs_lock:
          job = _optimize_jobs.get(job_id)
          if job and job.get("status") == "running":
            elapsed = max(0.0, time.time() - float(job.get("created_at", time.time())))
            est = max(0.5, float(job.get("estimated_seconds", 1.0) or 1.0))
            projected = min(0.92, max(float(job.get("progress", 0.01) or 0.01), elapsed / est * 0.9))
            job["progress"] = projected
            job["phase"] = "Running optimization in worker process"
            job["updated_at"] = time.time()
        time.sleep(0.25)

      result = future.result() if future is not None else _run_optimization(payload, progress_cb=_progress)
      elapsed = float(result.get("total_seconds", 0.0))
      with _jobs_lock:
        _duration_history[_mode_history_key(payload)].append(elapsed)
        job = _optimize_jobs.get(job_id)
        if job:
          job.update(
            {
              "status": "complete",
              "progress": 1.0,
              "phase": "Optimization complete",
              "result": result,
              "updated_at": time.time(),
            }
          )
    except ValueError as exc:
      with _jobs_lock:
        job = _optimize_jobs.get(job_id)
        if job:
          job.update({"status": "error", "error": str(exc), "error_code": 400, "updated_at": time.time()})
    except DataSourceError as exc:
      with _jobs_lock:
        job = _optimize_jobs.get(job_id)
        if job:
          job.update({"status": "error", "error": str(exc), "error_code": 502, "updated_at": time.time()})
    except Exception as exc:
      with _jobs_lock:
        job = _optimize_jobs.get(job_id)
        if job:
          job.update(
            {
              "status": "error",
              "error": f"Unexpected error: {exc}",
              "error_code": 500,
              "updated_at": time.time(),
            }
          )

  Thread(target=_worker, daemon=True).start()
  return jsonify({"job_id": job_id, "estimated_seconds": round(estimated_seconds, 2)})


@app.get("/optimize/status/<job_id>")
def optimize_status(job_id: str) -> Any:
  with _jobs_lock:
    job = dict(_optimize_jobs.get(job_id, {}))
  if not job:
    return jsonify({"error": "Optimization job not found"}), 404

  now = time.time()
  elapsed = max(0.0, now - float(job.get("created_at", now)))
  progress = max(0.0, min(1.0, float(job.get("progress", 0.0))))
  estimated_seconds = max(0.5, float(job.get("estimated_seconds", 1.0)))

  if job.get("status") == "running":
    projected_total = max(estimated_seconds, elapsed / max(progress, 0.05))
    eta_seconds = max(0.0, projected_total - elapsed)
  else:
    eta_seconds = 0.0

  response: Dict[str, Any] = {
    "job_id": job_id,
    "status": job.get("status", "running"),
    "phase": job.get("phase", "Running"),
    "progress": round(progress, 4),
    "progress_percent": int(round(progress * 100)),
    "elapsed_seconds": round(elapsed, 2),
    "eta_seconds": round(eta_seconds, 2),
    "estimated_seconds": round(estimated_seconds, 2),
  }
  if job.get("status") == "complete":
    response["result"] = job.get("result", {})
  if job.get("status") == "error":
    response["error"] = job.get("error", "Optimization failed")

  return _json_response(response)


@app.post("/refresh-data")
def refresh_data() -> Any:
    payload = request.get_json(silent=True) or {}
    champion = str(payload.get("champion", "Aatrox")).strip() or "Aatrox"

    try:
        riot_deleted = riot.clear_cache()
        wiki_deleted = wiki.clear_cache()

        cache.clear()
        patch = riot.get_latest_patch(force_refresh=True)
        profile = riot.get_champion_profile(patch, champion, force_refresh=True)
        items = riot.get_items(patch, force_refresh=True)

        scaling = wiki.get_scaling(
            champion,
            force_refresh=True,
        )

        return jsonify(
            {
                "ok": True,
                "patch": patch,
                "champion": champion,
                "profile_loaded": asdict(profile),
                "wiki_scaling": asdict(scaling) if scaling else None,
                "items_count": len(items),
                "cache_path": str(riot.cache.base_dir),
                "entries_deleted": {"riot": riot_deleted, "wiki": wiki_deleted},
            }
        )
    except Exception as exc:
        return jsonify({"error": f"Failed to refresh cache: {exc}"}), 500


@app.get("/api/champion-scaling")
@cache.cached(timeout=900, query_string=True)
def api_champion_scaling() -> Any:
    champion = str(request.args.get("champion", "")).strip()
    if not champion:
        return jsonify({"error": "champion is required"}), 400
    force_refresh = str(request.args.get("force_refresh", "false")).lower() in {"1", "true", "yes", "on"}
    _prioritize_prefetch_for_champion(champion=champion, patch=riot.get_latest_patch(force_refresh=False))

    try:
        scaling = wiki.get_scaling(champion, force_refresh=force_refresh, use_ai_fallback=False)
        return jsonify({"champion": champion, "wiki_scaling": asdict(scaling)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/champion-dps-simulation")
@cache.cached(timeout=600, query_string=True)
def api_champion_dps_simulation() -> Any:
    """
    Compute burst and/or DPS simulation for a champion + item build.

    Query parameters:
      champion        â€” champion name (required)
      items           â€” comma-separated item IDs (optional; uses top-6 optimal build if omitted)
      simulation_type â€” "burst" | "dps" | "both"  (default: "both")
      level           â€” champion level 1-18 (default: 18)
      target_armor    â€” target dummy armor (default: 100)
      target_mr       â€” target dummy magic resist (default: 50)
      duration        â€” DPS window in seconds (default: 10)
    """
    champion = str(request.args.get("champion", "")).strip()
    if not champion:
        return jsonify({"error": "champion is required"}), 400

    sim_type = str(request.args.get("simulation_type", "both")).lower()
    try:
      level = max(1, min(18, int(request.args.get("level", 18))))
      target_armor = max(0.0, float(request.args.get("target_armor", 100.0)))
      target_mr = max(0.0, float(request.args.get("target_mr", 50.0)))
      target_hp = max(1.0, float(request.args.get("target_hp", 2500.0)))
      duration = max(1.0, min(60.0, float(request.args.get("duration", 10.0))))
    except (TypeError, ValueError) as exc:
        return jsonify({"error": f"Invalid numeric parameter: {exc}"}), 400

    try:
        # Get champion scaling breakdown
        scaling = wiki.get_scaling(champion, use_ai_fallback=False)
        breakdown = scaling.ability_breakdown

        # Resolve items
        items_param = str(request.args.get("items", "")).strip()
        items: List[Any] = []
        if items_param:
            patch = riot.get_latest_patch()
            all_items = {str(x.item_id): x for x in riot.get_items(patch)}
            requested_ids = [x.strip() for x in items_param.split(",") if x.strip()]
            items = [all_items[iid] for iid in requested_ids if iid in all_items]
        else:
            # If no explicit item IDs are passed, compute a default top build.
            patch = riot.get_latest_patch()
            profile = merge_profile_with_scaling(
              riot.get_champion_profile(patch, champion),
              scaling,
            )
            all_items_list = riot.get_items(patch)
            optimizer = BuildOptimizer(profile, all_items_list)
            default_weights = ObjectiveWeights(damage=1.0, healing=0.0, tankiness=0.0, lifesteal=0.0)
            default_settings = SearchSettings(mode="near_exhaustive", build_size=6)
            default_enemy = EnemyProfile(
              target_hp=3200.0,
              target_armor=120.0,
              target_mr=90.0,
              physical_share=0.5,
            )
            ranked, _pareto, _checkpoints = optimizer.optimize(
              weights=default_weights,
              settings=default_settings,
              constraints=BuildConstraints(),
              enemy=default_enemy,
            )
            items = list(ranked[0].order) if ranked else []

        # Base stats from profile
        try:
          patch = riot.get_latest_patch()
          profile_for_sim = riot.get_champion_profile(patch, champion)
          champion_base = {
                "base_hp": float(getattr(profile_for_sim, "base_hp", 600.0) or 600.0),
                "base_armor": float(getattr(profile_for_sim, "base_armor", 35.0) or 35.0),
                "base_mr": float(getattr(profile_for_sim, "base_mr", 32.0) or 32.0),
            }
        except Exception:
            champion_base = {}

        result_payload: Dict[str, Any] = {"champion": champion, "level": level}

        if sim_type in ("burst", "both"):
            result_payload["burst"] = sim_engine.burst_damage(
                breakdown, champion_base, items,
                target_armor=target_armor, target_mr=target_mr, level=level,
            )

        if sim_type in ("dps", "both"):
          result_payload["dps"] = sim_engine.dps_simulation(
            breakdown, champion_base, items,
            duration=duration,
            target_armor=target_armor, target_mr=target_mr,
            target_hp=target_hp, level=level,
          )

        return jsonify(result_payload)

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _compute_champion_scaling_sweep(force_refresh: bool = False, limit: int = 0) -> Dict[str, Any]:
    patch = riot.get_latest_patch(force_refresh=force_refresh)
    champions = riot.get_all_champions(patch, force_refresh=force_refresh)
    if limit > 0:
        champions = champions[:limit]

    started = time.time()
    failures: List[Dict[str, str]] = []
    source_counts: Dict[str, int] = defaultdict(int)
    missing_by_ability: Dict[str, int] = defaultdict(int)
    component_count_total = 0

    for idx, champ in enumerate(champions):
        champion_name = str(champ.get("name", "") or "").strip()
        if not champion_name:
            continue
        try:
            scaling = wiki.get_scaling(champion_name, force_refresh=force_refresh, use_ai_fallback=False)
            source_counts[str(scaling.source)] += 1
            for ability_key in ("q", "w", "e", "r", "passive"):
                block = scaling.ability_breakdown.get(ability_key, {})
                if not isinstance(block, dict):
                    missing_by_ability[ability_key] += 1
                    continue
                components = block.get("scaling_components", [])
                if isinstance(components, list):
                    component_count_total += len(components)
                has_signal = any(float(block.get(metric, 0.0) or 0.0) > 0 for metric in (
                    "ad_ratio",
                    "ap_ratio",
                    "attack_speed_ratio",
                    "heal_ratio",
                    "hp_ratio",
                    "bonus_hp_ratio",
                    "armor_ratio",
                    "mr_ratio",
                ))
                has_component_signal = any(
                    isinstance(x, dict) and float(x.get("ratio", 0.0) or 0.0) > 0
                    for x in (components or [])
                )
                base_damage = block.get("base_damage", [])
                has_base = isinstance(base_damage, list) and any(float(v) > 0 for v in base_damage if isinstance(v, (int, float)))
                if not has_signal and not has_component_signal and not has_base:
                    missing_by_ability[ability_key] += 1
        except Exception as exc:
            failures.append({"champion": champion_name, "error": str(exc)})

        if idx and idx % 20 == 0:
            time.sleep(0.05)

    elapsed = time.time() - started
    analyzed = len(champions)
    return {
        "ok": True,
        "patch": patch,
        "champions_analyzed": analyzed,
        "failures": failures,
        "failure_count": len(failures),
        "source_counts": dict(source_counts),
        "missing_by_ability": dict(missing_by_ability),
        "avg_components_per_champion": (component_count_total / analyzed) if analyzed else 0.0,
        "elapsed_seconds": round(elapsed, 3),
        "force_refresh": force_refresh,
    }


def _background_patch_sweep_loop(poll_seconds: int = 900) -> None:
  while True:
    try:
      fingerprint = wiki._get_patch_fingerprint(force_refresh=True)
      should_run = False
      patch = riot.get_latest_patch(force_refresh=False)
      marker = _load_prefetch_marker(patch)

      with _sweep_lock:
        if not _sweep_state.get("last_patch_fingerprint"):
          _sweep_state["last_patch_fingerprint"] = fingerprint
          should_run = not bool(marker.get("complete"))
        elif fingerprint != _sweep_state.get("last_patch_fingerprint") and not _sweep_state.get("running"):
          _sweep_state["running"] = True
          _sweep_state["last_patch_fingerprint"] = fingerprint
          _sweep_state["last_error"] = ""
          should_run = True

      if should_run:
        report: Optional[Dict[str, Any]] = None
        error_text = ""
        try:
          cache.clear()
          report = _compute_champion_scaling_sweep(force_refresh=True, limit=0)
          _ensure_prefetch_running(force_refresh=True)
        except Exception as exc:
          error_text = str(exc)
        with _sweep_lock:
          _sweep_state["running"] = False
          _sweep_state["last_run_at"] = time.time()
          _sweep_state["last_report"] = report
          _sweep_state["last_error"] = error_text
    except Exception as exc:
      with _sweep_lock:
        _sweep_state["last_error"] = str(exc)
        _sweep_state["running"] = False

    try:
      _ensure_prefetch_running(force_refresh=False)
    except Exception as exc:
      with _prefetch_lock:
        _prefetch_state["last_error"] = str(exc)

    time.sleep(max(60, int(poll_seconds)))


@app.get("/api/champion-scaling-sweep")
@cache.cached(timeout=600, query_string=True)
def api_champion_scaling_sweep() -> Any:
    force_refresh = str(request.args.get("force_refresh", "false")).lower() in {"1", "true", "yes", "on"}
    limit_raw = str(request.args.get("limit", "0")).strip()
    limit = 0
    try:
        limit = max(0, int(limit_raw or "0"))
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400

    try:
        report = _compute_champion_scaling_sweep(force_refresh=force_refresh, limit=limit)
        with _sweep_lock:
            _sweep_state["last_run_at"] = time.time()
            _sweep_state["last_report"] = report
        return _json_response(report)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/champion-scaling-sweep-status")
@cache.cached(timeout=30)
def api_champion_scaling_sweep_status() -> Any:
    with _sweep_lock:
        return _json_response(
            {
                "running": bool(_sweep_state.get("running")),
                "last_patch_fingerprint": str(_sweep_state.get("last_patch_fingerprint", "") or ""),
                "last_run_at": float(_sweep_state.get("last_run_at", 0.0) or 0.0),
                "last_error": str(_sweep_state.get("last_error", "") or ""),
                "last_report": _sweep_state.get("last_report"),
            }
        )


@app.get("/api/meta-snapshot-status")
def api_meta_snapshot_status() -> Any:
  with _meta_snapshot_lock:
    running = [{"key": key, "started_at": started} for key, started in _meta_snapshot_running.items()]
    recent = sorted(
      list(_meta_snapshot_recent.values()),
      key=lambda x: float(x.get("finished_at", x.get("started_at", 0.0)) or 0.0),
      reverse=True,
    )[:30]
  return _json_response(
    {
      "running": running,
      "running_count": len(running),
      "recent": recent,
      "recent_count": len(recent),
    }
  )


@app.get("/api/prefetch-status")
def api_prefetch_status() -> Any:
  _ensure_prefetch_running(force_refresh=False)
  return _json_response(_prefetch_progress_payload())


@app.post("/api/prefetch-priority")
def api_prefetch_priority() -> Any:
  payload = request.get_json(silent=True) or {}
  champion = str(payload.get("champion", "") or "").strip()
  if not champion:
    return jsonify({"error": "champion is required"}), 400
  role = str(payload.get("role", "jungle") or "jungle")
  tier = str(payload.get("tier", "emerald_plus") or "emerald_plus")
  region = str(payload.get("region", "global") or "global")
  patch = str(payload.get("patch", "live") or "live")
  _prioritize_prefetch_for_champion(champion=champion, role=role, tier=tier, region=region, patch=patch)
  return _json_response({"ok": True, "state": _prefetch_progress_payload()})


@app.get("/api/unknown-op-sweep")
@cache.cached(timeout=600, query_string=True)
def api_unknown_op_sweep() -> Any:
    threshold_percent = _float(request.args, "threshold_percent", 5.0)
    threshold_percent = max(0.1, min(100.0, threshold_percent))
    role = str(request.args.get("role", "jungle") or "jungle")
    meta_tier = str(request.args.get("meta_tier", "emerald_plus") or "emerald_plus")
    meta_region = str(request.args.get("meta_region", "global") or "global")
    meta_patch = str(request.args.get("meta_patch", "live") or "live")
    force_refresh = str(request.args.get("force_refresh", "false")).lower() in {"1", "true", "yes", "on"}

    limit_raw = str(request.args.get("limit", "0") or "0").strip()
    try:
      limit = max(0, int(limit_raw))
    except ValueError:
      return jsonify({"error": "limit must be an integer"}), 400

    try:
      patch = riot.get_latest_patch(force_refresh=force_refresh)
      champions = riot.get_all_champions(patch, force_refresh=force_refresh)
      if limit > 0:
        champions = champions[:limit]

      items = riot.get_items(patch, force_refresh=force_refresh)
      default_weights = ObjectiveWeights(damage=1.0, healing=0.1, tankiness=0.1, lifesteal=0.0)
      default_settings = SearchSettings(
        mode="near_exhaustive",
        build_size=6,
        candidate_pool_size=24,
        beam_width=65,
        order_permutation_cap=120,
        sa_iterations=100,
      )
      default_enemy = EnemyProfile(target_hp=3200.0, target_armor=120.0, target_mr=90.0, physical_share=0.5)

      unknowns: List[Dict[str, Any]] = []
      failures: List[Dict[str, str]] = []

      for champ in champions:
        champion_name = str(champ.get("name", "") or "").strip()
        if not champion_name:
          continue
        try:
          scaling = wiki.get_scaling(champion_name, force_refresh=force_refresh, use_ai_fallback=False)
          profile = riot.get_champion_profile(patch, champion_name, force_refresh=force_refresh)
          profile = merge_profile_with_scaling(profile, scaling)
          rune_pages = _live_rune_pages(champion_name, role=role, tier=meta_tier, region=meta_region, patch=meta_patch)
          optimizer_obj = BuildOptimizer(profile, items, rune_pages=rune_pages or None)
          ranked, _pareto, _checkpoints = optimizer_obj.optimize(
            default_weights,
            default_settings,
            constraints=BuildConstraints(),
            enemy=default_enemy,
          )
          if not ranked:
            continue

          best = ranked[0]
          best_items = [x.name for x in best.order]

          def _evaluate_meta_build(item_names: List[str]) -> Dict[str, Any]:
            resolved = _resolve_item_names_to_stats(item_names, items)
            if len(resolved) < 3:
              return {"weighted_score": 0.0, "metrics": {}}
            evaluated = optimizer_obj._evaluate_best_order(
              resolved,
              default_weights,
              order_permutation_cap=default_settings.order_permutation_cap,
              enemy=default_enemy,
            )
            return {
              "weighted_score": float(getattr(evaluated, "weighted_score", 0.0) or 0.0),
              "metrics": dict(getattr(evaluated, "metrics", {}) or {}),
            }

          _item_id_map = {item.item_id: item.name for item in items}
          comparison = compare_optimizer_build_to_ugg(
            champion=champion_name,
            optimizer_item_names=best_items,
            role=role,
            comparison_mode="power_delta",
            tier=meta_tier,
            region=meta_region,
            patch=meta_patch,
            optimizer_weighted_score=float(best.weighted_score),
            optimizer_metrics=dict(best.metrics or {}),
            evaluate_meta_build_fn=_evaluate_meta_build,
            item_id_to_name=_item_id_map,
            allow_persistent_snapshot=True,
          )
          if not comparison.get("available"):
            continue

          meta_builds = comparison.get("modes", {}).get("power_delta", [])
          best_power = meta_builds[0] if meta_builds else None
          if not isinstance(best_power, dict):
            continue

          delta_pct = _float(best_power, "score_delta_percent", 0.0)
          similarity = _float(best_power, "similarity", 0.0)
          if delta_pct >= threshold_percent and similarity < 0.95:
            unknowns.append(
              {
                "champion": champion_name,
                "patch": patch,
                "optimizer_best": serialize_build(best, patch),
                "meta_best": best_power,
                "advantage_percent": round(delta_pct, 3),
                "similarity": round(similarity, 4),
                "meta_context": comparison.get("comparison_context", {}),
              }
            )
        except Exception as exc:
          failures.append({"champion": champion_name, "error": str(exc)})

      unknowns.sort(key=lambda x: float(x.get("advantage_percent", 0.0)), reverse=True)
      return _json_response(
        {
          "ok": True,
          "patch": patch,
          "threshold_percent": threshold_percent,
          "champions_analyzed": len(champions),
          "unknown_op_candidates": unknowns,
          "count": len(unknowns),
          "failures": failures,
          "failure_count": len(failures),
          "meta_context": {"tier": meta_tier, "region": meta_region, "role": role, "patch": meta_patch},
        }
      )
    except Exception as exc:
      return jsonify({"error": str(exc)}), 500


@app.get("/api/icon/champion/<path:slug>")
def api_icon_champion(slug: str) -> Any:
    token = _safe_token(slug)
    if not token:
        return jsonify({"error": "invalid champion slug"}), 400
    remote_url = f"https://wiki.leagueoflegends.com/en-us/Special:FilePath/{quote(token, safe='')}Square.png"
    try:
        icon_path = _cache_icon_from_wiki(remote_url, "champion", token)
        return send_file(icon_path, mimetype="image/png", max_age=60 * 60 * 24 * 30)
    except Exception:
        return jsonify({"error": "icon not found"}), 404


@app.get("/api/icon/item/<path:item_token>")
def api_icon_item(item_token: str) -> Any:
    token = _safe_token(item_token)
    if not token:
        return jsonify({"error": "invalid item token"}), 400
    remote_url = f"https://wiki.leagueoflegends.com/en-us/Special:FilePath/{quote(token, safe='')}_item.png"
    try:
        icon_path = _cache_icon_from_wiki(remote_url, "item", token)
        return send_file(icon_path, mimetype="image/png", max_age=60 * 60 * 24 * 30)
    except Exception:
        return jsonify({"error": "icon not found"}), 404


@app.get("/api/icon/rune/<path:rune_token>")
def api_icon_rune(rune_token: str) -> Any:
    token = _safe_token(rune_token)
    if not token:
        return jsonify({"error": "invalid rune token"}), 400
    try:
        remote_url = _resolve_rune_icon_remote_url(token)
        if not remote_url:
            return jsonify({"error": "icon not found"}), 404
        cache_token = _safe_token(token.lower().replace(" ", "_")) or "rune"
        icon_path = _cache_icon_from_wiki(remote_url, "rune", cache_token)
        return send_file(icon_path, mimetype="image/png", max_age=60 * 60 * 24 * 30)
    except Exception:
        return jsonify({"error": "icon not found"}), 404


# Map stat shard IDs to their DDragon StatMods image filenames.
_SHARD_ID_TO_DDRAGON_FILE: Dict[str, str] = {
    "5001": "StatModsHealthScalingIcon.png",
    "5002": "StatModsArmorIcon.png",
    "5003": "StatModsMagicResIcon.MagicResist.png",
    "5005": "StatModsAttackSpeedIcon.png",
    "5007": "StatModsCDRScalingIcon.png",
    "5008": "StatModsAdaptiveForceIcon.png",
    "5011": "StatModsHealthPlusIcon.png",
    "5013": "StatModsTenacityIcon.Tenacity.png",
}


@app.get("/api/icon/shard/<path:shard_id>")
def api_icon_shard(shard_id: str) -> Any:
    normalized = str(shard_id or "").strip().rstrip(".png").lower()
    # Strip any trailing .png extension from the route token
    clean_id = re.sub(r"\.png$", "", normalized)
    ddragon_file = _SHARD_ID_TO_DDRAGON_FILE.get(clean_id)
    if not ddragon_file:
        return jsonify({"error": "shard not found"}), 404
    try:
        remote_url = f"https://ddragon.leagueoflegends.com/cdn/img/perk-images/StatMods/{ddragon_file}"
        icon_path = _cache_remote_asset(remote_url, "shard", f"shard_{clean_id}", ".png")
        return send_file(icon_path, mimetype="image/png", max_age=60 * 60 * 24 * 30)
    except Exception:
        return jsonify({"error": "icon not found"}), 404


@app.get("/api/icon/splash/<path:slug>")
def api_icon_splash(slug: str) -> Any:
    token = _safe_token(slug)
    if not token:
        return jsonify({"error": "invalid champion slug"}), 400

    variants = _champion_slug_variants(token)
    if not variants:
        return jsonify({"error": "splash not found"}), 404

    for variant in variants:
      ddragon_url = f"https://ddragon.leagueoflegends.com/cdn/img/champion/splash/{quote(variant, safe='')}_0.jpg"
      try:
        splash_path = _cache_remote_asset(ddragon_url, "splash", f"{variant}_0_ddragon", ".jpg")
        return send_file(splash_path, mimetype="image/jpeg", max_age=60 * 60 * 24 * 30)
      except Exception:
        continue

    for variant in variants:
      for ext in ("jpg", "png"):
        wiki_url = f"https://wiki.leagueoflegends.com/en-us/Special:FilePath/{quote(variant, safe='')}_OriginalSkin.{ext}"
        try:
          splash_path = _cache_remote_asset(wiki_url, "splash", f"{variant}_0_wiki_{ext}", f".{ext}")
          splash_mime = "image/png" if ext == "png" else "image/jpeg"
          return send_file(splash_path, mimetype=splash_mime, max_age=60 * 60 * 24 * 30)
        except Exception:
          continue

    return jsonify({"error": "splash not found"}), 404


@app.get("/api/runes/catalog")
@cache.cached(timeout=3600)
def api_rune_catalog() -> Any:
    try:
        return jsonify(_rune_catalog_response())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/runtime-capabilities")
@cache.cached(timeout=120)
def api_runtime_capabilities() -> Any:
  try:
    return jsonify({
      "ocr": _ocr_runtime_status(),
    })
  except Exception as exc:
    return jsonify({"error": str(exc)}), 500


@app.get("/api/champions")
@cache.cached(timeout=3600)
def api_champions() -> Any:
    try:
        patch = riot.get_latest_patch()
        champions = riot.get_all_champions(patch)
        localized: List[Dict[str, Any]] = []
        for champ in champions:
            row = dict(champ)
            slug = str(row.get("slug", "") or "")
            if slug:
                row["icon_url"] = _local_champion_icon_url(slug)
                row["splash_url"] = _local_champion_splash_url(slug)
            localized.append(row)
        return jsonify({"patch": patch, "champions": localized})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/ollama-models")
@cache.cached(timeout=30)
def api_ollama_models() -> Any:
    try:
        models = ollama.list_models()
        return jsonify({"models": models, "available": ollama.is_available()})
    except Exception as exc:
        return jsonify({"models": [], "available": False, "error": str(exc)})


@app.post("/api/ai-suggest")
def api_ai_suggest() -> Any:
    payload = request.get_json(silent=True) or {}
    champion = str(payload.get("champion", "")).strip()
    if not champion:
        return jsonify({"error": "champion is required"}), 400
    if not ollama.is_available():
        return jsonify({"error": "Ollama is not running. Start Ollama and try again."}), 503

    model = str(payload.get("model", "mistral"))
    weights = ObjectiveWeights(
        damage=_float(payload, "damage", 1.0),
        healing=_float(payload, "healing", 0.0),
        tankiness=_float(payload, "tankiness", 0.0),
        lifesteal=_float(payload, "lifesteal", 0.0),
    )
    enemy = EnemyProfile(
        target_hp=_float(payload, "enemy_hp", 3200.0),
        target_armor=_float(payload, "enemy_armor", 120.0),
        target_mr=_float(payload, "enemy_mr", 90.0),
        physical_share=_float(payload, "enemy_physical_share", 0.5),
    )
    ranked_context = payload.get("ranked_context", [])

    try:
      ai_result = ollama.generate_build(
            champion=champion,
            enemy_profile=asdict(enemy),
            weights=asdict(weights),
            top_builds_context=ranked_context,
            model=model,
        )
      patch = riot.get_latest_patch(force_refresh=False)
      profile = riot.get_champion_profile(patch, champion, force_refresh=False)
      items = riot.get_items(patch, force_refresh=False)
      scaling = wiki.get_scaling(champion, force_refresh=False, use_ai_fallback=False)
      profile = merge_profile_with_scaling(profile, scaling)
      optimizer_obj = BuildOptimizer(profile, items)

      candidates_out: List[Dict[str, Any]] = []
      raw_candidates = ai_result.get("candidates", []) if isinstance(ai_result, dict) else []
      for candidate in raw_candidates:
        if not isinstance(candidate, dict):
          continue
        requested_names = [str(x) for x in candidate.get("items", [])]
        resolved_items = _resolve_item_names_to_stats(requested_names, items)
        if len(resolved_items) < 3:
          continue
        evaluated = optimizer_obj._evaluate_best_order(
          resolved_items,
          weights,
          order_permutation_cap=120,
          enemy=enemy,
        )
        serialized = serialize_build(evaluated, patch)
        serialized["requested_items"] = requested_names
        serialized["resolved_item_count"] = len(resolved_items)
        serialized["label"] = str(candidate.get("label", "candidate"))
        serialized["rune_hints"] = [str(x) for x in candidate.get("rune_hints", [])[:4]]
        serialized["reasoning"] = str(candidate.get("reasoning", ""))
        serialized["playstyle_note"] = str(candidate.get("playstyle_note", ""))
        serialized["innovation_thesis"] = str(candidate.get("innovation_thesis", ""))
        serialized["innovation_score"] = _innovation_score(requested_names, ranked_context)
        candidates_out.append(serialized)

      candidates_out.sort(
        key=lambda x: (
          float(x.get("weighted_score", 0.0) or 0.0),
          float(x.get("innovation_score", 0.0) or 0.0),
        ),
        reverse=True,
      )

      return jsonify(
        {
          "model": ai_result.get("model", model) if isinstance(ai_result, dict) else model,
          "candidates": candidates_out,
          "best_candidate": candidates_out[0] if candidates_out else None,
          "patch": patch,
        }
      )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/ai-rate")
def api_ai_rate() -> Any:
    payload = request.get_json(silent=True) or {}
    champion = str(payload.get("champion", "")).strip()
    if not champion:
        return jsonify({"error": "champion is required"}), 400
    rating = int(payload.get("rating", 0))
    if not 1 <= rating <= 5:
        return jsonify({"error": "rating must be 1-5"}), 400

    build_items = payload.get("build_items", [])
    ai_reasoning = str(payload.get("ai_reasoning", ""))

    try:
        ollama.record_feedback(
            champion=champion,
            build_items=build_items,
            rating=rating,
            ai_reasoning=ai_reasoning,
        )
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def serialize_build(build: Any, patch: str = "") -> Dict[str, Any]:
  def item_dict(i: Any) -> Dict[str, Any]:
    d: Dict[str, Any] = {"id": i.item_id, "name": i.name}
    d["icon_url"] = _local_item_icon_url(i.name)
    return d

  rune_page = getattr(build, "rune_page", None)
  rune_payload: Dict[str, Any] = {}
  if rune_page is not None:
    raw_shards = list(getattr(rune_page, "shards", ()) or ())
    # Shard slots are always: [0]=Offense, [1]=Flex, [2]=Defense
    _SHARD_SLOT_LABELS = ["Offense", "Flex", "Defense"]
    shards_labeled = [
      {"slot": _SHARD_SLOT_LABELS[i] if i < len(_SHARD_SLOT_LABELS) else f"Slot {i+1}", "name": str(s)}
      for i, s in enumerate(raw_shards)
    ]
    rune_payload = {
      "id": getattr(rune_page, "page_id", ""),
      "name": getattr(rune_page, "name", ""),
      "primary_tree": getattr(rune_page, "primary_tree", ""),
      "secondary_tree": getattr(rune_page, "secondary_tree", ""),
      "shards": raw_shards,
      "shards_labeled": shards_labeled,
      "runes": [
        {
          "id": getattr(x, "rune_id", ""),
          "name": getattr(x, "name", ""),
          "tree": getattr(x, "tree", ""),
          "slot": getattr(x, "slot", ""),
          "icon_url": _local_rune_icon_url(getattr(x, "rune_id", ""), getattr(x, "name", "")),
        }
        for x in (getattr(rune_page, "runes", ()) or ())
      ],
    }

  return {
    "weighted_score": build.weighted_score,
    "metrics": build.metrics,
    "contributions": build.contributions,
    "interactions": build.interactions,
    "trace": build.trace,
    "rune_page": rune_payload,
    "rune_effects": getattr(build, "rune_effects", {}),
    "items": [item_dict(i) for i in build.items],
    "order": [item_dict(i) for i in build.order],
  }


if __name__ == "__main__":
  debug_mode = os.environ.get("FLASK_ENV", "development").lower() != "production"
  port = int(os.environ.get("PORT", 5055))
  if (not debug_mode) or (os.environ.get("WERKZEUG_RUN_MAIN") == "true"):
    _ensure_prefetch_running(force_refresh=False)
    Thread(target=_background_patch_sweep_loop, daemon=True).start()
  if debug_mode:
    app.run(host="127.0.0.1", port=port, debug=True)
  else:
    from waitress import serve
    serve(app, host="0.0.0.0", port=port, threads=4)

