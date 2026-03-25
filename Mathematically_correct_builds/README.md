# Mathematically Correct Builds

Mathematically optimized League of Legends item builds through a local Flask web app.

## What is implemented

- Wiki-only ingestion from `wiki.leagueoflegends.com` for champion, item, patch, and icon data.
- Strict ability-scaling extraction that prefers structured wiki data, merges rendered wiki sections, and supports saved per-champion override JSON.
- Item legality enforcement for both wiki-extracted unique passives and hardcoded unique groups such as the Last Whisper family.
- Build optimizer with 3 selectable modes:
  - `heuristic` - fast greedy build assembly
  - `near_exhaustive` - multi-pass random-restart beam search with optional deep-search widening and extra restarts
  - `exhaustive` - full combination sweep with a runtime cap and simulated annealing refinement
- Multi-objective scoring with weighted priorities for damage, healing, tankiness, lifesteal, utility pressure, and consistency.
- Optional GPU-assisted candidate prescoring (Numba CUDA) with automatic CPU fallback.
- Constraint handling for required items, excluded items, mandatory boots, and max total gold.
- Matchup-aware evaluation requiring enemy HP, armor, MR, and physical damage share.
- Explainability data for every build: weighted contributions, interaction bonuses, traces, and ranked checkpoints.
- Pareto frontier extraction over the objective metrics.
- Rune-aware scoring using live U.GG-derived rune pages when available, with fallback rune pages built into the app.
- U.GG comparison enrichment with 3 comparison modes: `item_overlap`, `power_delta`, and `component_balance`.
- Unknown-OP sweep that scans champions and flags optimizer builds that outperform parsed U.GG builds by a configurable threshold.
- Burst and sustained-DPS simulation against a configurable target dummy, including penetration, haste, auto attacks, and on-hit handling.
- Background patch sweep support that can rerun champion scaling diagnostics when the upstream wiki fingerprint changes.
- Optional Ollama-backed AI build suggester with local feedback storage and reranking through the same in-app scoring logic.

## Web UI

The UI is no longer embedded inline in `main.py`.

- HTML lives in `templates/index.html`.
- Styling lives in `static/css/main.css`.
- Frontend behavior lives in `static/js/app.js`.

Current UI features include:

- Searchable champion picker with locally proxied icon URLs.
- Champion splash-art cinematic background with dynamic palette matching on selection.
- Robust all-champion splash resolution for special-name champions (for example Bel'Veth, Cho'Gath, K'Sante, Dr. Mundo, Nunu & Willump, and Wukong aliases).
- Champion-reactive animated frame accents on result cards and comparison chart panels.
- Async optimize flow with progress bar, phase labels, and ETA tracking.
- Compute backend controls (`auto`, `gpu`, `cpu`) and deep-search controls (toggle + extra restarts).
- Ranked build cards with icon tooltips and charted top-score comparison.
- Parsed ability-scaling panel.
- U.GG meta-comparison panel.
- Unknown-OP sweep panel.
- Burst/DPS simulation controls and output cards.
- Optional AI Build Suggester panel.

## Install

```powershell
cd "c:\Users\Felix\.vscode\Python projects\Mathematically_correct_builds"
& ".\\.venv\\Scripts\\python.exe" -m pip install -r requirements.txt
```

## Run

### Development

```powershell
cd "c:\Users\Felix\.vscode\Python projects\Mathematically_correct_builds"
& ".\\.venv\\Scripts\\python.exe" main.py
```

Development mode uses the Flask dev server and binds to `http://127.0.0.1:5055`.

### Production-style local run

```powershell
cd "c:\Users\Felix\.vscode\Python projects\Mathematically_correct_builds"
$env:FLASK_ENV = 'production'
& ".\\.venv\\Scripts\\python.exe" main.py
```

Production mode uses Waitress and binds to `0.0.0.0:5055`.

### Production-style Docker run (no cloud subscription required)

```powershell
cd "c:\Users\Felix\.vscode\Python projects\Mathematically_correct_builds"
docker compose up --build -d
```

Then open:

- `http://127.0.0.1:5055`
- `http://127.0.0.1:5055/health`

Stop the service:

```powershell
docker compose down
```

Or use helper scripts:

```powershell
.\scripts\start.ps1
.\scripts\stop.ps1
```

## Optional AI builds

1. Install [Ollama](https://ollama.com).
2. Pull a model, for example `ollama pull mistral`.
3. Start the local server with `ollama serve`.
4. Open the app and use the AI Build Suggester card.
5. Rate results from 1 to 5 stars to store local good/bad examples for future prompts.

## Optional OCR fallback

The meta-comparison scraper can fall back to OCR when normal extraction fails.

- Python packages are included in `requirements.txt`, so reinstalling dependencies in the project venv enables the Python side.
- Windows also needs the Tesseract binary installed, typically at `C:\Program Files\Tesseract-OCR\tesseract.exe`, or available on `PATH`.
- The UI now shows a runtime status pill near the header so you can immediately see whether OCR fallback is available.

## Runtime dependencies added beyond the original baseline

- `requests-cache` for upstream HTTP caching.
- `tenacity` for retry/backoff around network calls.
- `flask-compress` for compressed responses.
- `flask-caching` for short-lived endpoint caching.
- `orjson` for faster JSON serialization.
- `waitress` for production serving.
- `numba` for optional CUDA prescoring; currently pinned with `python_version < 3.14` because upstream wheels are not yet published for 3.14.

## Cache behavior

The app now uses several caches with different scopes.

- Local JSON cache under `%LOCALAPPDATA%/mathematically_correct_builds/cache` for fetched wiki data and saved overrides.
- Shared HTTP cache for wiki requests via `requests-cache`.
- Separate icon HTTP cache plus on-disk icon files for champion and item icons.
- In-process Flask endpoint cache for frequently repeated API reads.

Important details:

- Cache timeouts are not a single global TTL anymore. Different paths use different expirations.
- `POST /refresh-data` clears the JSON caches and Flask endpoint cache, then repopulates champion/item/scaling data.
- Icon caches are repopulated lazily as icons are requested again.
- AI feedback and saved ability overrides are stored locally per champion.

## Optimize flows

There are now two optimization entry points.

- `POST /optimize` runs the full optimization synchronously and returns the final payload directly.
- `POST /optimize/start` starts a background job and returns a `job_id` plus an estimated runtime.
- `GET /optimize/status/<job_id>` polls the background job, including progress, phase, elapsed time, ETA, and the final result when complete.

The web UI uses the async job flow so long searches do not block the page.

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Render the web UI |
| `POST` | `/optimize` | Run optimization synchronously |
| `POST` | `/optimize/start` | Start an optimization background job |
| `GET` | `/optimize/status/<job_id>` | Poll optimization job status and final result |
| `POST` | `/refresh-data` | Clear local JSON cache and refetch patch, champion, item, and scaling data |
| `GET` | `/api/champions` | Return all champions with local icon URLs and current patch fingerprint |
| `GET` | `/api/champion-scaling` | Return parsed wiki scaling for one champion |
| `GET` | `/api/champion-dps-simulation` | Run burst, DPS, or combined combat simulation |
| `GET` | `/api/champion-scaling-sweep` | Run a scaling coverage/diagnostic sweep across champions |
| `GET` | `/api/champion-scaling-sweep-status` | Return the latest sweep status and report snapshot |
| `GET` | `/api/unknown-op-sweep` | Find optimizer builds that outperform parsed U.GG meta builds |
| `GET` | `/api/icon/champion/<path:slug>` | Serve cached champion icons |
| `GET` | `/api/icon/splash/<path:slug>` | Serve cached champion splash art used by dynamic UI theming (handles slug aliases and legacy image suffixes) |
| `GET` | `/api/icon/item/<path:item_token>` | Serve cached item icons |
| `GET` | `/api/icon/rune/<path:rune_token>` | Serve cached rune icons |
| `GET` | `/api/runes/catalog` | Return full rune tree catalog used by the UI |
| `GET` | `/api/runtime-capabilities` | Report OCR runtime availability and missing prerequisites |
| `GET` | `/api/ollama-models` | Return Ollama model availability and local models |
| `POST` | `/api/ai-suggest` | Generate AI build candidates and rescore them in-app |
| `POST` | `/api/ai-rate` | Persist a 1-5 star rating for an AI result |

## Optimize response enrichments

`/optimize` and completed async optimize jobs include:

- `rune_page` and `rune_effects` for each build.
- `meta_comparison` with parsed U.GG results, selected mode, all mode rankings, and best matches.
- `meta_context` and `rune_pages_considered`.
- `checkpoints` for best 1-item, 2-item, 3-item, and full-build states.
- `pareto` for non-dominated builds.
- `steps`, `total_seconds`, and optional `build_warning`.
- `saved_ability_overrides_json` when local overrides were found or stored.

`/api/ai-suggest` returns scored candidates with resolved items, weighted score, innovation score, and `best_candidate`.

## Simulation endpoint notes

`GET /api/champion-dps-simulation` supports:

- `champion` - required champion name.
- `simulation_type` - `burst`, `dps`, or `both`.
- `items` - optional comma-separated item IDs. If omitted, the app computes a default top build first.
- `level`, `target_hp`, `target_armor`, `target_mr`, and `duration`.

The response can include:

- `burst.total` and per-ability burst damage.
- `dps.dps`, `dps.total_damage`, `dps.cast_counts`, and `dps.auto_attacks`.

## Operational notes

- Enemy profile fields are required for optimize requests: `enemy_hp`, `enemy_armor`, `enemy_mr`, and `enemy_physical_share`.
- `GET /api/champions` includes a local `splash_url` field for each champion so the UI can switch cinematic backgrounds without direct third-party image calls.
- Splash asset caching now validates image payloads before writing to disk, reducing stale broken-image cache entries.
- When palette extraction fails, the UI now applies a champion-seeded fallback palette instead of a single generic fallback theme.
- Default preset is tuned for balanced quality and speed: `near_exhaustive`, build size `6`, candidate pool `24`, beam width `65`, order permutation cap `150`, SA iterations `100`, exhaustive cap `120s`.
- Simulated annealing runs only if time remains after the exhaustive combination sweep, so the runtime cap is a ceiling, not a guaranteed full pass.
- Saved ability overrides are kept in the local cache and automatically reused on later optimize runs for the same champion.
- AI feedback is stored locally per champion and injected back into future prompts as examples.
- The background patch sweep tracks the current wiki fingerprint and can refresh sweep diagnostics automatically when the upstream data changes.
