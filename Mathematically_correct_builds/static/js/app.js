// State
let allChampions = [];
let selectedChampion = 'Aatrox';
let currentPatch = '';
let lastRanked = [];
let currentAiReasoning = '';
let currentAiItems = [];
let selectedRating = 0;
let highlightedChampIndex = -1;
let optimizeRunning = false;
let runeCatalog = null;
let runtimeCapabilities = null;
const iconPrefetchSeen = new Set();
let splashThemeController = null;
let splashThemeRequestId = 0;
let lastMetaComparison = null;
let lastBuildWarning = '';
const BUILD_PRESET_STORAGE_KEY = 'mcb.buildPreset';
const ENEMY_SCENARIO_STORAGE_KEY = 'mcb.enemyScenario';
const CHAMPION_STORAGE_KEY = 'mcb.selectedChampion';
const ROLE_STORAGE_KEY = 'mcb.selectedRole';

// Champion picker
async function loadChampions() {
  try {
    const res = await fetch('/api/champions');
    const data = await res.json();
    allChampions = data.champions || [];
    currentPatch = data.patch || '';
    updatePatchPill(currentPatch);
    renderChampList(allChampions);
    prefetchChampionIcons(allChampions);
    prefetchChampionSplashes(allChampions);
    const selected = allChampions.find(c => c.name === selectedChampion);
    const aatrox = allChampions.find(c => c.name === 'Aatrox') || allChampions[0];
    const initial = selected || aatrox;
    if (initial) await selectChamp(initial);
  } catch(e) {
    document.getElementById('champSelName').textContent = 'Failed to load';
  }
}

async function loadRuneCatalog() {
  try {
    const res = await fetch('/api/runes/catalog');
    const data = await res.json();
    if (!res.ok) return;
    runeCatalog = data || null;
  } catch (_) {
    runeCatalog = null;
  }
}

async function loadRuntimeCapabilities() {
  try {
    const res = await fetch('/api/runtime-capabilities');
    const data = await res.json();
    if (!res.ok) return;
    runtimeCapabilities = data || null;
    renderRuntimeNotice();
  } catch (_) {
    runtimeCapabilities = null;
    renderRuntimeNotice();
  }
}

function renderRuntimeNotice() {
  const notice = document.getElementById('runtimeNotice');
  if (!notice) return;
  const ocr = runtimeCapabilities && runtimeCapabilities.ocr ? runtimeCapabilities.ocr : null;
  if (!ocr) {
    notice.className = 'runtime-notice muted';
    notice.textContent = 'OCR fallback status unavailable.';
    return;
  }
  notice.className = `runtime-notice ${ocr.available ? 'runtime-ok' : 'runtime-warn'}`;
  notice.textContent = ocr.available
    ? `OCR fallback ready: ${String(ocr.reason || 'available')}`
    : `OCR fallback inactive: ${String(ocr.reason || 'not available')}`;
}

function prefetchIcon(url) {
  if (!url || iconPrefetchSeen.has(url)) return;
  iconPrefetchSeen.add(url);
  const img = new Image();
  img.src = url;
}

function prefetchChampionIcons(list) {
  (list || []).slice(0, 48).forEach(c => prefetchIcon(c.icon_url || ''));
}

function prefetchChampionSplashes(list) {
  (list || []).slice(0, 10).forEach((c) => prefetchIcon(c.splash_url || ''));
}

function clampNum(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function hslToCss(h, s, l, alpha) {
  const hh = Math.round(((h % 360) + 360) % 360);
  const ss = clampNum(Math.round(s), 0, 100);
  const ll = clampNum(Math.round(l), 0, 100);
  if (typeof alpha === 'number') {
    return `hsla(${hh} ${ss}% ${ll}% / ${clampNum(alpha, 0, 1).toFixed(3)})`;
  }
  return `hsl(${hh} ${ss}% ${ll}%)`;
}

function rgbToHsl(r, g, b) {
  const rn = clampNum(r / 255, 0, 1);
  const gn = clampNum(g / 255, 0, 1);
  const bn = clampNum(b / 255, 0, 1);
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const d = max - min;
  let h = 0;
  let s = 0;
  const l = (max + min) / 2;
  if (d !== 0) {
    s = d / (1 - Math.abs(2 * l - 1));
    switch (max) {
      case rn:
        h = 60 * (((gn - bn) / d) % 6);
        break;
      case gn:
        h = 60 * (((bn - rn) / d) + 2);
        break;
      default:
        h = 60 * (((rn - gn) / d) + 4);
        break;
    }
  }
  return {
    h: ((h % 360) + 360) % 360,
    s: s * 100,
    l: l * 100,
  };
}

function weightedPercentile(samples, key, percentile) {
  const ordered = (samples || []).slice().sort((a, b) => Number(a[key] || 0) - Number(b[key] || 0));
  if (!ordered.length) return 0;
  const total = ordered.reduce((acc, row) => acc + Number(row.weight || 0), 0);
  if (total <= 0) return Number(ordered[Math.floor((ordered.length - 1) * clampNum(percentile, 0, 1))][key] || 0);
  const target = total * clampNum(percentile, 0, 1);
  let seen = 0;
  for (const row of ordered) {
    seen += Number(row.weight || 0);
    if (seen >= target) return Number(row[key] || 0);
  }
  return Number(ordered[ordered.length - 1][key] || 0);
}

function championFallbackPalette(champ) {
  const seed = String((champ && (champ.name || champ.slug)) || '').trim();
  if (!seed) {
    return {
      primaryH: 39,
      primaryS: 84,
      primaryL: 50,
      secondaryH: 198,
      secondaryS: 74,
      secondaryL: 54,
      inkL: 95,
      mutedL: 78,
    };
  }
  let hash = 0;
  for (let i = 0; i < seed.length; i += 1) {
    hash = ((hash * 31) + seed.charCodeAt(i)) >>> 0;
  }
  const primaryH = hash % 360;
  const secondaryH = (primaryH + 34 + (hash % 23)) % 360;
  const primaryS = 58 + (hash % 28);
  const secondaryS = clampNum(primaryS - 6 + (hash % 10), 46, 92);
  const primaryL = 44 + (hash % 13);
  const secondaryL = clampNum(primaryL + 6, 42, 66);
  return {
    primaryH,
    primaryS,
    primaryL,
    secondaryH,
    secondaryS,
    secondaryL,
    inkL: 95,
    mutedL: 78,
  };
}

function hueDistance(a, b) {
  const d = Math.abs(((a - b) % 360 + 360) % 360);
  return Math.min(d, 360 - d);
}

async function extractSplashPalette(url, signal, champ) {
  const fallback = championFallbackPalette(champ);
  try {
    let timeoutHandle = 0;
    const timeout = new Promise((_, reject) => {
      timeoutHandle = window.setTimeout(() => reject(new Error('splash palette timeout')), 5000);
      if (signal) {
        signal.addEventListener('abort', () => {
          clearTimeout(timeoutHandle);
          reject(new DOMException('Aborted', 'AbortError'));
        }, {once: true});
      }
    });

    const responsePromise = fetch(url, {cache: 'force-cache', signal});
    const res = await Promise.race([responsePromise, timeout]);
    clearTimeout(timeoutHandle);
    if (!res || !res.ok) {
      throw new Error('splash fetch failed');
    }
    const blob = await res.blob();
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d', {willReadFrequently: true});
    const sampleSize = 96;
    canvas.width = sampleSize;
    canvas.height = sampleSize;

    if (typeof createImageBitmap === 'function') {
      const bitmap = await createImageBitmap(blob);
      ctx.drawImage(bitmap, 0, 0, sampleSize, sampleSize);
      bitmap.close();
    } else {
      const objectUrl = URL.createObjectURL(blob);
      try {
        const image = await new Promise((resolve, reject) => {
          const img = new Image();
          img.onload = () => resolve(img);
          img.onerror = () => reject(new Error('splash image decode failed'));
          img.src = objectUrl;
        });
        ctx.drawImage(image, 0, 0, sampleSize, sampleSize);
      } finally {
        URL.revokeObjectURL(objectUrl);
      }
    }

    const px = ctx.getImageData(0, 0, sampleSize, sampleSize).data;
    const samples = [];
    for (let i = 0; i < px.length; i += 8) {
      const r = px[i];
      const g = px[i + 1];
      const b = px[i + 2];
      const a = px[i + 3];
      if (a < 120) continue;
      const hsl = rgbToHsl(r, g, b);
      if (hsl.s < 6 || hsl.l < 7 || hsl.l > 94) continue;
      const satWeight = clampNum(hsl.s / 100, 0.15, 1);
      const lightWeight = 1 - (Math.abs(0.5 - (hsl.l / 100)) * 0.8);
      const weight = satWeight * clampNum(lightWeight, 0.25, 1);
      samples.push({...hsl, weight});
    }

    if (!samples.length) {
      return fallback;
    }

    samples.sort((a, b) => b.weight - a.weight);
    const top = samples.slice(0, 700);
    let hueX = 0;
    let hueY = 0;
    let total = 0;
    top.forEach((s) => {
      const rad = (s.h * Math.PI) / 180;
      hueX += Math.cos(rad) * s.weight;
      hueY += Math.sin(rad) * s.weight;
      total += s.weight;
    });

    const primaryH = ((Math.atan2(hueY, hueX) * 180) / Math.PI + 360) % 360;
    const primaryS = clampNum(weightedPercentile(top, 's', 0.62), 40, 96);
    const primaryL = clampNum(weightedPercentile(top, 'l', 0.52), 30, 68);

    const hueBins = new Array(24).fill(0);
    top.forEach((row) => {
      const bin = Math.max(0, Math.min(23, Math.floor(row.h / 15)));
      const hueWeight = Number(row.weight || 0) * clampNum(Number(row.s || 0) / 100, 0.2, 1);
      hueBins[bin] += hueWeight;
    });
    let secondaryH = (primaryH + 34) % 360;
    let bestWeight = -1;
    hueBins.forEach((score, idx) => {
      const candidateHue = (idx * 15) + 7.5;
      const distance = hueDistance(candidateHue, primaryH);
      if (distance < 22) return;
      if (score > bestWeight) {
        bestWeight = score;
        secondaryH = candidateHue % 360;
      }
    });

    const secondaryS = clampNum(weightedPercentile(top, 's', 0.7) - 4, 36, 96);
    const secondaryL = clampNum(weightedPercentile(top, 'l', 0.58) + 6, 32, 74);

    return {
      primaryH,
      primaryS,
      primaryL,
      secondaryH,
      secondaryS,
      secondaryL,
      inkL: 95,
      mutedL: 78,
    };
  } catch (err) {
    if (signal && signal.aborted) {
      throw err;
    }
    console.warn('Splash palette extraction fallback in use', err);
    return fallback;
  }
}

function applyChampionPaletteVars(palette) {
  const root = document.documentElement;
  const p = palette || {};
  const pH = Number(p.primaryH || 39);
  const pS = Number(p.primaryS || 88);
  const pL = Number(p.primaryL || 52);
  const sH = Number(p.secondaryH || ((pH + 34) % 360));
  const sS = Number(p.secondaryS || 76);
  const sL = Number(p.secondaryL || 56);

  root.style.setProperty('--champion-primary', hslToCss(pH, pS, pL));
  root.style.setProperty('--champion-secondary', hslToCss(sH, sS, sL));
  root.style.setProperty('--champion-glow', hslToCss(pH, clampNum(pS + 4, 40, 96), clampNum(pL + 18, 56, 74), 0.42));
  root.style.setProperty('--champion-vignette', hslToCss(pH, clampNum(pS - 18, 25, 74), 11, 0.55));
  root.style.setProperty('--accent', hslToCss(pH, pS, pL));
  root.style.setProperty('--accent-2', hslToCss(sH, sS, sL));
  root.style.setProperty('--line', hslToCss(pH, clampNum(pS - 36, 16, 52), clampNum(pL - 8, 30, 52), 0.88));
  root.style.setProperty('--card', hslToCss(pH, clampNum(pS - 52, 12, 44), 15));
  root.style.setProperty('--card-2', hslToCss(sH, clampNum(sS - 48, 10, 40), 19));
  root.style.setProperty('--bg', hslToCss(pH, clampNum(pS - 56, 10, 34), 8));
  root.style.setProperty('--bg-soft', hslToCss(sH, clampNum(sS - 50, 10, 36), 12));
  root.style.setProperty('--ink', hslToCss(pH, 16, Number(p.inkL || 95)));
  root.style.setProperty('--muted', hslToCss(sH, 24, Number(p.mutedL || 78)));
}

async function applyChampionVisualTheme(champ) {
  const root = document.documentElement;
  const splashUrl = String(champ && champ.splash_url ? champ.splash_url : '').trim();
  splashThemeRequestId += 1;
  const currentRequestId = splashThemeRequestId;

  if (splashThemeController) {
    splashThemeController.abort();
  }
  splashThemeController = new AbortController();

  const banner = document.getElementById('champSplashBanner');

  if (!splashUrl) {
    root.style.setProperty('--champion-splash-url', 'none');
    applyChampionPaletteVars(null);
    document.body.classList.remove('splash-ready');
    if (banner) { banner.style.display = 'none'; banner.classList.remove('visible'); }
    return;
  }

  root.style.setProperty('--champion-splash-url', `url("${splashUrl.replace(/\"/g, '\\\"')}")`);
  document.body.classList.remove('splash-ready');

  // Show splash banner
  if (banner) {
    banner.style.display = 'block';
    const escapedUrl = splashUrl.replace(/\\/g, '\\\\').replace(/\"/g, '\\"');
    banner.style.backgroundImage = `url("${escapedUrl}")`;
    banner.classList.remove('visible');
    // Trigger fade-in on next frame
    requestAnimationFrame(() => { requestAnimationFrame(() => { banner.classList.add('visible'); }); });
  }

  try {
    const palette = await extractSplashPalette(splashUrl, splashThemeController.signal, champ);
    if (currentRequestId !== splashThemeRequestId) return;
    applyChampionPaletteVars(palette);
    document.body.classList.add('splash-ready');
  } catch (err) {
    if (currentRequestId !== splashThemeRequestId) return;
    console.warn('Champion visual theme update failed', err);
    applyChampionPaletteVars(championFallbackPalette(champ));
    document.body.classList.add('splash-ready');
  }
}

function champIconOnError(img) {
  img.style.display = 'none';
}

function renderChampList(list) {
  const container = document.getElementById('champList');
  container.innerHTML = list.map((c, idx) =>
    `<div class="champ-option${c.name===selectedChampion?' selected':''}${idx===highlightedChampIndex?' active':''}" data-champ-index="${idx}" data-champ-name="${escHtml(c.name)}">
      <img src="${c.icon_url}" data-slug="${c.slug || ''}" onerror="champIconOnError(this)" />
      <span>${c.name}</span>
      <span class="champ-check">${c.name===selectedChampion ? 'Selected' : ''}</span>
    </div>`
  ).join('');
}

function updateSelectedInRenderedList() {
  const options = document.querySelectorAll('#champList .champ-option');
  options.forEach((option) => {
    const isSelected = (option.dataset.champName || '') === selectedChampion;
    option.classList.toggle('selected', isSelected);
    const check = option.querySelector('.champ-check');
    if (check) check.textContent = isSelected ? 'Selected' : '';
  });
}

async function loadSelectedChampionScaling(championName) {
  const breakdownEl = document.getElementById('ability_breakdown');
  if (!championName || !breakdownEl) return;
  try {
    breakdownEl.innerHTML = '<p class="muted">Loading selected champion scaling...</p>';
    const res = await fetch(`/api/champion-scaling?champion=${encodeURIComponent(championName)}`);
    const data = await res.json();
    if (!res.ok) {
      breakdownEl.innerHTML = `<p class="err">${escHtml(data.error || 'Failed to load scaling')}</p>`;
      return;
    }
    breakdownEl.innerHTML = formatAbilityBreakdown(data.wiki_scaling);
  } catch (_) {
    breakdownEl.innerHTML = '<p class="err">Failed to load scaling.</p>';
  }
}

document.getElementById('champList').addEventListener('click', (e) => {
  const option = e.target.closest('.champ-option');
  if (!option) return;
  const idx = Number(option.dataset.champIndex);
  const list = filteredChamps();
  if (!Number.isInteger(idx) || idx < 0 || idx >= list.length) return;
  void selectChamp(list[idx]);
});

async function selectChamp(champ) {
  selectedChampion = champ.name;
  saveQuickSelection(CHAMPION_STORAGE_KEY, selectedChampion);
  void applyChampionVisualTheme(champ);
  document.getElementById('champSelName').textContent = champ.name;
  const icon = document.getElementById('champSelIcon');
  icon.src = champ.icon_url;
  icon.dataset.slug = champ.slug || '';
  icon.dataset.fallbackAttempted = '';
  icon.style.display = 'block';
  icon.onerror = function() { champIconOnError(this); };
  closeChampDropdown();
  highlightedChampIndex = -1;
  prefetchIcon(champ.icon_url || '');
  prefetchIcon(champ.splash_url || '');
  updateSelectedInRenderedList();
  showSimPanel(true);
  await loadSelectedChampionScaling(champ.name);
}

function filteredChamps() {
  const q = (document.getElementById('champSearchInput').value || '').toLowerCase();
  return q ? allChampions.filter(c => c.name.toLowerCase().includes(q)) : allChampions;
}

function filterChamps() {
  const list = filteredChamps();
  highlightedChampIndex = list.length > 0 ? 0 : -1;
  renderChampList(list);
}

function champSearchKey(e) {
  const list = filteredChamps();
  if (e.key === 'Escape') {
    closeChampDropdown();
    return;
  }
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (!list.length) return;
    highlightedChampIndex = highlightedChampIndex < 0 ? 0 : (highlightedChampIndex + 1) % list.length;
    renderChampList(list);
    return;
  }
  if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (!list.length) return;
    highlightedChampIndex = highlightedChampIndex < 0 ? list.length - 1 : (highlightedChampIndex - 1 + list.length) % list.length;
    renderChampList(list);
    return;
  }
  if (e.key === 'Enter') {
    e.preventDefault();
    if (!list.length) return;
    const target = highlightedChampIndex >= 0 ? list[highlightedChampIndex] : list[0];
    if (target) void selectChamp(target);
  }
}

function toggleChampDropdown() {
  const dd = document.getElementById('champDropdown');
  dd.classList.toggle('open');
  document.getElementById('champSelArrow').textContent = dd.classList.contains('open') ? '^' : 'v';
  if (dd.classList.contains('open')) {
    document.getElementById('champSearchInput').value = '';
    const selectedIdx = allChampions.findIndex(c => c.name === selectedChampion);
    highlightedChampIndex = selectedIdx >= 0 ? selectedIdx : 0;
    renderChampList(allChampions);
    setTimeout(() => document.getElementById('champSearchInput').focus(), 50);
  }
}

function closeChampDropdown() {
  document.getElementById('champDropdown').classList.remove('open');
  document.getElementById('champSelArrow').textContent = 'v';
}

document.addEventListener('click', (e) => {
  if (!document.getElementById('champPicker').contains(e.target)) closeChampDropdown();
});

// Mode visibility
function onModeChange() {
  const mode = document.getElementById('mode').value;
  const show = mode === 'exhaustive';
  document.getElementById('capLabel').style.opacity = show ? '1' : '0.45';
  document.getElementById('saLabel').style.opacity = show ? '1' : '0.45';
}
onModeChange();

function setActiveQuickChip(rowId, activeBtn) {
  const row = document.getElementById(rowId);
  if (!row) return;
  row.querySelectorAll('.quick-chip').forEach((el) => el.classList.remove('active'));
  if (activeBtn) activeBtn.classList.add('active');
}

function syncRoleQuickChips() {
  const roleEl = document.getElementById('role');
  if (!roleEl) return;
  const role = String(roleEl.value || '').trim();
  const btn = document.querySelector(`#roleQuickRow .quick-chip[data-role="${role}"]`);
  setActiveQuickChip('roleQuickRow', btn);
}

function applyRoleQuick(roleValue, sourceBtn = null) {
  const roleEl = document.getElementById('role');
  if (!roleEl) return;
  roleEl.value = roleValue;
  saveQuickSelection(ROLE_STORAGE_KEY, roleEl.value);
  setActiveQuickChip('roleQuickRow', sourceBtn);
  const status = document.getElementById('status');
  if (status) status.textContent = `Role set to ${roleValue}`;
}

function saveQuickSelection(storageKey, value) {
  try {
    window.localStorage.setItem(storageKey, String(value || ''));
  } catch (_) {
    // Ignore storage failures (private mode, disabled storage, quota).
  }
}

function loadQuickSelection(storageKey) {
  try {
    return String(window.localStorage.getItem(storageKey) || '').trim();
  } catch (_) {
    return '';
  }
}

function restoreQuickSelections() {
  const savedPreset = loadQuickSelection(BUILD_PRESET_STORAGE_KEY);
  if (savedPreset) {
    const presetBtn = document.querySelector(`#buildPresetRow .quick-chip[data-preset="${savedPreset}"]`);
    if (presetBtn) {
      applyBuildPreset(savedPreset, presetBtn);
    }
  }

  const savedScenario = loadQuickSelection(ENEMY_SCENARIO_STORAGE_KEY);
  if (savedScenario) {
    const scenarioBtn = document.querySelector(`#enemyScenarioRow .quick-chip[data-scenario="${savedScenario}"]`);
    if (scenarioBtn) {
      applyEnemyScenario(savedScenario, scenarioBtn);
    }
  }
}

function restorePrimarySelections() {
  const savedChampion = loadQuickSelection(CHAMPION_STORAGE_KEY);
  if (savedChampion) {
    selectedChampion = savedChampion;
  }

  const roleEl = document.getElementById('role');
  if (!roleEl) return;

  const savedRole = loadQuickSelection(ROLE_STORAGE_KEY);
  if (savedRole) {
    roleEl.value = savedRole;
  }

  roleEl.addEventListener('change', () => {
    saveQuickSelection(ROLE_STORAGE_KEY, roleEl.value);
    syncRoleQuickChips();
  });

  syncRoleQuickChips();
}

function applyBuildPreset(presetKey, sourceBtn = null) {
  const presets = {
    burst_assassin: {
      damage: 1.2,
      healing: 0.0,
      tank: 0.0,
      ls: 0.0,
      utility: 0.35,
      consistency: 0.05,
      mode: 'near_exhaustive',
      pool: 18,
      beam: 65,
      deep_search: 'true',
    },
    frontline_bruiser: {
      damage: 0.9,
      healing: 0.35,
      tank: 0.65,
      ls: 0.2,
      utility: 0.3,
      consistency: 0.25,
      mode: 'near_exhaustive',
      pool: 20,
      beam: 75,
      deep_search: 'true',
    },
    extended_fight_dps: {
      damage: 1.0,
      healing: 0.15,
      tank: 0.2,
      ls: 0.35,
      utility: 0.25,
      consistency: 0.45,
      mode: 'near_exhaustive',
      pool: 22,
      beam: 85,
      deep_search: 'true',
    },
  };

  const preset = presets[presetKey];
  if (!preset) return;

  document.getElementById('damage').value = String(preset.damage);
  document.getElementById('healing').value = String(preset.healing);
  document.getElementById('tank').value = String(preset.tank);
  document.getElementById('ls').value = String(preset.ls);
  document.getElementById('utility').value = String(preset.utility);
  document.getElementById('consistency').value = String(preset.consistency);
  document.getElementById('mode').value = preset.mode;
  document.getElementById('pool').value = String(preset.pool);
  document.getElementById('beam').value = String(preset.beam);
  document.getElementById('deep_search').value = preset.deep_search;
  onModeChange();

  setActiveQuickChip('buildPresetRow', sourceBtn);
  saveQuickSelection(BUILD_PRESET_STORAGE_KEY, presetKey);
  const status = document.getElementById('status');
  if (status) status.textContent = `Applied preset: ${presetKey.replaceAll('_', ' ')}`;
}

function applyEnemyScenario(scenarioKey, sourceBtn = null) {
  const scenarios = {
    squishy_backline: {hp: 2300, armor: 70, mr: 55, physicalShare: 0.45},
    balanced_teamfight: {hp: 3200, armor: 120, mr: 95, physicalShare: 0.5},
    double_frontline: {hp: 4600, armor: 210, mr: 165, physicalShare: 0.55},
    heavy_ad: {hp: 3300, armor: 145, mr: 85, physicalShare: 0.72},
    heavy_ap: {hp: 3300, armor: 95, mr: 135, physicalShare: 0.28},
  };

  const scenario = scenarios[scenarioKey];
  if (!scenario) return;

  document.getElementById('enemy_hp').value = String(scenario.hp);
  document.getElementById('enemy_armor').value = String(scenario.armor);
  document.getElementById('enemy_mr').value = String(scenario.mr);
  document.getElementById('enemy_physical_share').value = String(scenario.physicalShare);

  const simHp = document.getElementById('simHp');
  const simArmor = document.getElementById('simArmor');
  const simMr = document.getElementById('simMr');
  if (simHp) {
    simHp.value = String(Math.min(6000, Math.max(500, scenario.hp)));
    document.getElementById('simHpVal').textContent = simHp.value;
  }
  if (simArmor) {
    simArmor.value = String(Math.min(300, Math.max(0, scenario.armor)));
    document.getElementById('simArmorVal').textContent = simArmor.value;
  }
  if (simMr) {
    simMr.value = String(Math.min(200, Math.max(0, scenario.mr)));
    document.getElementById('simMrVal').textContent = simMr.value;
  }

  setActiveQuickChip('enemyScenarioRow', sourceBtn);
  saveQuickSelection(ENEMY_SCENARIO_STORAGE_KEY, scenarioKey);
  const status = document.getElementById('status');
  if (status) status.textContent = `Applied enemy scenario: ${scenarioKey.replaceAll('_', ' ')}`;
}

function buildConfidence(row, rankIndex) {
  const metrics = row && row.metrics ? row.metrics : {};
  const trace = row && row.trace ? row.trace : {};
  let score = 55;

  score += Math.min(15, Number(metrics.consistency || 0) * 0.24);
  score += Math.min(10, Number(metrics.proc_frequency || 0) * 4.5);
  score += Math.min(10, Number(metrics.stack_uptime || 0) * 16);
  if (Number(trace.hit_events || 0) > 0 && Number(trace.spell_casts_est || 0) > 0) score += 8;
  if (Number(metrics.damage || 0) > 0 && Number(metrics.tankiness || 0) > 0) score += 5;
  if (rankIndex < 3) score += 2;

  if (lastMetaComparison && lastMetaComparison.available) {
    score += 6;
    if (Array.isArray(lastMetaComparison.warnings) && lastMetaComparison.warnings.length) score -= 4;
  } else {
    score -= 5;
  }
  if (lastBuildWarning) score -= 6;

  score = Math.max(0, Math.min(100, Math.round(score)));
  if (score >= 78) return {label: `High confidence ${score}`, cls: 'conf-high'};
  if (score >= 60) return {label: `Medium confidence ${score}`, cls: 'conf-medium'};
  return {label: `Low confidence ${score}`, cls: 'conf-low'};
}

// Ollama model picker
async function loadOllamaModels() {
  try {
    const res = await fetch('/api/ollama-models');
    const data = await res.json();
    const models = data.models || [];
    if (models.length > 0) {
      const sel = document.getElementById('ollamaModel');
      sel.innerHTML = models.map(m => `<option value="${m}"${m==='mistral'?' selected':''}>${m}</option>`).join('');
    }
  } catch(e) { /* Ollama not available - default option stays */ }
}

// Optimize
async function runOptimize() {
  if (optimizeRunning) return;
  const body = {
    champion: selectedChampion,
    mode: document.getElementById('mode').value,
    role: document.getElementById('role').value,
    comparison_mode: document.getElementById('comparison_mode').value,
    meta_tier: document.getElementById('meta_tier').value,
    meta_region: document.getElementById('meta_region').value,
    meta_patch: document.getElementById('meta_patch').value,
    build_size: Number(document.getElementById('build_size').value),
    candidate_pool_size: Number(document.getElementById('pool').value),
    beam_width: Number(document.getElementById('beam').value),
    compute_backend: document.getElementById('compute_backend').value,
    deep_search: document.getElementById('deep_search').value === 'true',
    extra_restarts: Number(document.getElementById('extra_restarts').value),
    exhaustive_runtime_cap_seconds: Number(document.getElementById('cap').value),
    order_permutation_cap: Number(document.getElementById('perm_cap').value),
    sa_iterations: Number(document.getElementById('sa_iters').value),
    damage: Number(document.getElementById('damage').value),
    healing: Number(document.getElementById('healing').value),
    tankiness: Number(document.getElementById('tank').value),
    lifesteal: Number(document.getElementById('ls').value),
    utility: Number(document.getElementById('utility').value),
    consistency: Number(document.getElementById('consistency').value),
    must_include: document.getElementById('must_include').value,
    exclude: document.getElementById('exclude').value,
    max_total_gold: document.getElementById('max_gold').value,
    require_boots: document.getElementById('require_boots').value === 'true',
    force_refresh: document.getElementById('force_refresh').value === 'true',
    enemy_hp: Number(document.getElementById('enemy_hp').value),
    enemy_armor: Number(document.getElementById('enemy_armor').value),
    enemy_mr: Number(document.getElementById('enemy_mr').value),
    enemy_physical_share: Number(document.getElementById('enemy_physical_share').value),
    ability_overrides: document.getElementById('ability_overrides').value,
  };

  document.getElementById('error').textContent = '';
  const statusEl = document.getElementById('status');
  statusEl.textContent = 'Starting optimization job...';
  statusEl.classList.remove('thinking');
  document.getElementById('ranked').innerHTML = '<p class="muted">Computing...</p>';
  setOptimizeRunning(true);
  showLoadingPanel(true);

  try {
    const startRes = await fetch('/optimize/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const startData = await startRes.json();
    if (!startRes.ok) {
      document.getElementById('error').textContent = startData.error || 'Failed to start optimization';
      statusEl.textContent = '';
      showLoadingPanel(false);
      return;
    }

    setLoadState({
      phase: 'Job started',
      progress_percent: 1,
      status: 'running',
      eta_seconds: startData.estimated_seconds || 0,
      elapsed_seconds: 0,
    });

    const data = await waitForOptimizeResult(startData.job_id);

    currentPatch = data.patch || currentPatch;
    lastRanked = data.ranked || [];
    lastMetaComparison = data.meta_comparison || null;
    lastBuildWarning = String(data.build_warning || '');

    if (data.steps && data.steps.length) {
      const lis = data.steps.map(s => `<li>${escHtml(s.label)} <span style="color:var(--muted);font-size:0.78rem;">(${s.ms}ms)</span></li>`).join('');
      const backendInfo = data.compute_backend ? `<div class="muted" style="margin-top:6px;">compute backend: <b>${escHtml(String(data.compute_backend))}</b></div>` : '';
      statusEl.innerHTML = `<ul class="step-timeline">${lis}</ul>${backendInfo}`;
    } else {
      statusEl.textContent = `Patch ${data.patch} | ${data.items_considered} items considered | Source: ${data.wiki_scaling ? data.wiki_scaling.source : 'no scaling'}`;
    }

    if (data.saved_ability_overrides_json !== undefined && data.saved_ability_overrides_json) {
      document.getElementById('ability_overrides').value = data.saved_ability_overrides_json;
    }

    document.getElementById('ability_breakdown').innerHTML = formatAbilityBreakdown(data.wiki_scaling);
    const warningHtml = data.build_warning
      ? `<div class="build-warning">Warning: ${escHtml(data.build_warning)}</div>`
      : '';
    renderRankedInsights(data.ranked || []);
    document.getElementById('ranked').innerHTML = warningHtml + formatRows(data.ranked, 'ranked');
    document.getElementById('pareto').innerHTML = formatRows(data.pareto, 'pareto');
    document.getElementById('checkpoints').innerHTML = formatCheckpoints(data.checkpoints, data.patch);
    document.getElementById('metaComparison').innerHTML = formatMetaComparison(data.meta_comparison || {});
    renderScoreChart(data.ranked || []);
    renderBuildStory(data || {});
    if (window.lucide) { window.lucide.createIcons(); }
  } catch (err) {
    if (!document.getElementById('error').textContent) {
      document.getElementById('error').textContent = err && err.message ? err.message : 'Optimization failed';
    }
  } finally {
    setOptimizeRunning(false);
  }
}

let scoreChart = null;
function renderScoreChart(ranked) {
  const canvas = document.getElementById('scoreChart');
  if (!canvas || !window.Chart) return;
  const top = (ranked || []).slice(0, 5);
  if (!top.length) {
    if (scoreChart) {
      scoreChart.destroy();
      scoreChart = null;
    }
    return;
  }

  const labels = top.map((row, idx) => `#${idx + 1}`);
  const values = top.map(row => Number(row?.weighted_score || 0));
  const damage = top.map(row => Number(row?.metrics?.damage || 0));
  const tankiness = top.map(row => Number(row?.metrics?.tankiness || 0));

  if (scoreChart) {
    scoreChart.destroy();
  }

  scoreChart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: 'Weighted Score',
          data: values,
          backgroundColor: 'rgba(244, 163, 0, 0.7)',
          borderColor: 'rgba(244, 163, 0, 1)',
          borderWidth: 1,
        },
        {
          label: 'Damage',
          data: damage,
          backgroundColor: 'rgba(88, 187, 221, 0.5)',
          borderColor: 'rgba(88, 187, 221, 0.9)',
          borderWidth: 1,
        },
        {
          label: 'Tankiness',
          data: tankiness,
          backgroundColor: 'rgba(43, 214, 161, 0.45)',
          borderColor: 'rgba(43, 214, 161, 0.85)',
          borderWidth: 1,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          ticks: { color: '#cfe0ee' },
          grid: { color: 'rgba(90, 120, 145, 0.2)' },
        },
        y: {
          ticks: { color: '#cfe0ee' },
          grid: { color: 'rgba(90, 120, 145, 0.2)' },
        },
      },
      plugins: {
        legend: {
          labels: { color: '#e8f1f8' },
        },
      },
    },
  });
}

function buildArchetype(metrics) {
  const m = metrics || {};
  const burst = Number(m.burst_profile || 0);
  const sustain = Number(m.sustained_profile || 0);
  const tank = Number(m.tankiness || 0);
  const dmg = Number(m.damage || 0);
  const utility = Number(m.utility || 0);

  if (burst >= sustain * 1.18 && burst >= 25) return 'Burst-first spike build';
  if (sustain >= burst * 1.12 && sustain >= 20) return 'Sustained DPS build';
  if (tank >= dmg * 0.95 && tank >= 30) return 'Durable bruiser line';
  if (utility >= 18) return 'Utility-skewed control build';
  return 'Balanced all-rounder build';
}

function formatDelta(v) {
  const n = Number(v || 0);
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}`;
}

function renderBuildStory(result) {
  const panel = document.getElementById('buildStory');
  if (!panel) return;

  const ranked = Array.isArray(result && result.ranked) ? result.ranked : [];
  if (!ranked.length) {
    panel.innerHTML = '<p class="muted">Run an optimization to generate a concise explanation.</p>';
    return;
  }

  const top = ranked[0] || {};
  const second = ranked[1] || null;
  const topMetrics = top.metrics || {};
  const topContrib = top.contributions || {};
  const lead = second ? (Number(top.weighted_score || 0) - Number(second.weighted_score || 0)) : 0;
  const topNames = Array.isArray(top.order) ? top.order.map((x) => String((x && x.name) || '').trim()).filter(Boolean) : [];
  const archetype = buildArchetype(topMetrics);

  const factors = [
    `Damage ${Number(topMetrics.damage || 0).toFixed(1)} with weighted contribution ${Number(topContrib.damage_component || 0).toFixed(2)}`,
    `Tankiness ${Number(topMetrics.tankiness || 0).toFixed(1)} and utility ${Number(topMetrics.utility || 0).toFixed(1)} keep the score stable`,
    `Proc cadence ${Number(topMetrics.proc_frequency || 0).toFixed(2)} and stack uptime ${Number(topMetrics.stack_uptime || 0).toFixed(2)} support reliable fights`,
  ];

  const adjustments = [];
  if (Number(topMetrics.proc_frequency || 0) < 1.5) {
    adjustments.push('If this feels low-tempo in game, increase utility weight slightly and expand candidate pool for more haste or proc options.');
  } else {
    adjustments.push('Current profile already has healthy proc cadence; avoid over-indexing haste unless your matchup demands faster rotations.');
  }
  if (Number(topMetrics.tankiness || 0) < Number(topMetrics.damage || 0) * 0.4) {
    adjustments.push('Into heavy dive teams, raise tankiness weight by about +0.2 to test safer variants.');
  } else {
    adjustments.push('Durability is acceptable for mixed threats; keep enemy physical share accurate to preserve this ranking.');
  }

  const meta = result.meta_comparison || {};
  const bestMatch = meta && meta.best_match ? meta.best_match : null;
  const metaBits = [];
  if (meta && meta.available && bestMatch) {
    metaBits.push(`Best meta overlap: ${Number(bestMatch.similarity || 0).toFixed(3)}`);
    metaBits.push(`Power delta vs best meta: ${formatDelta(bestMatch.score_delta_percent)}%`);
  } else {
    metaBits.push('Meta comparison unavailable for this context; story is based on optimizer signals only.');
  }

  const chips = [
    `Archetype: ${archetype}`,
    second ? `Lead vs #2: ${formatDelta(lead)}` : 'Single candidate result',
    topNames.length ? `Core: ${topNames.slice(0, 3).join(' + ')}` : 'No item list available',
  ];

  panel.innerHTML = `
    <div class="story-box">
      <div class="story-head">${chips.map((c) => `<span class="story-chip">${escHtml(c)}</span>`).join('')}</div>
      <p class="story-lede">
        Rank #1 wins because it combines <b>${escHtml(archetype.toLowerCase())}</b> with a strong weighted profile for this enemy setup.
      </p>
      <div class="story-grid">
        <div class="story-col">
          <h4>Why It Wins</h4>
          <ul class="story-list">${factors.map((f) => `<li>${escHtml(f)}</li>`).join('')}</ul>
        </div>
        <div class="story-col">
          <h4>Compared To Meta</h4>
          <ul class="story-list">${metaBits.map((f) => `<li>${escHtml(f)}</li>`).join('')}</ul>
        </div>
        <div class="story-col">
          <h4>What To Try Next</h4>
          <ul class="story-list">${adjustments.map((f) => `<li>${escHtml(f)}</li>`).join('')}</ul>
        </div>
      </div>
    </div>
  `;
}

function formatMetaComparison(meta) {
  const itemIconUrlByName = (name) => {
    const token = String(name || '').trim().replace(/\s+/g, '_');
    return token ? `/api/icon/item/${encodeURIComponent(token)}.png` : '';
  };
  const metricLabel = (label, helpText, className) =>
    `<span class="compare-label-wrap ${className || ''}">${escHtml(label)}<span class="metric-help" title="${escHtml(helpText)}">?</span></span>`;

  if (!meta || !meta.available) {
    const reason = meta && meta.reason ? escHtml(meta.reason) : 'No comparison data available.';
    const sourceHint = meta && meta.source ? `<span class="ability-chip" style="font-size:0.74rem;">source: ${escHtml(String(meta.source))}</span>` : '';
    const warnings = Array.isArray(meta && meta.warnings) ? meta.warnings : [];
    const warningHtml = warnings.length
      ? `<div class="build-warning" style="margin-bottom:6px;">${warnings.map(x => escHtml(x)).join(' | ')}</div>`
      : '';
    return `${warningHtml}<div class="build-metrics" style="margin-bottom:6px;">${sourceHint}</div><p class="muted">${reason}</p>`;
  }

  const context = meta.comparison_context || {};
  const mode = escHtml(meta.comparison_mode || 'all');
  const ctx = `${escHtml(context.tier || 'emerald_plus')} | ${escHtml(context.region || 'global')} | ${escHtml(context.role || 'jungle')} | ${escHtml(context.patch || 'live')}`;
  const best = meta.best_match || {};
  const bestItems = Array.isArray(best.items) ? best.items.map(x => escHtml(x)).join(', ') : 'n/a';
  const sourceBadge = `<span class="ability-chip" style="font-size:0.74rem;">source: ${escHtml(String(meta.source || 'u.gg'))}</span>`;
  const fallbackSource = escHtml(String(meta.source || 'fallback'));
  const fallbackBadge = meta.fallback_used
    ? `<span class="ability-chip" style="font-size:0.74rem;border:1px solid #f2b14f;color:#ffd7a1;background:rgba(242,177,79,0.12);">Fallback active: ${fallbackSource}</span>`
    : '';

  const renderRows = (rows, tableLabel, modeKey) => {
    const normalizedMode = String(modeKey || 'all').toLowerCase();
    const modeClass = normalizedMode === 'item_overlap'
      ? 'mode-item-overlap'
      : normalizedMode === 'power_delta'
        ? 'mode-power-delta'
        : normalizedMode === 'component_balance'
          ? 'mode-component-balance'
          : 'mode-generic';
    const cards = (rows || []).slice(0, 5).map((row, idx) => {
      const items = Array.isArray(row.items) ? row.items : [];
      const iconItems = items.map((name) => ({name: String(name || ''), icon_url: itemIconUrlByName(name)}));
      const itemIcons = itemIconsHtml(iconItems, currentPatch);
      const sim = Number(row.similarity || 0).toFixed(3);
      const deltaRaw = Number(row.score_delta_percent || 0);
      const delta = `${deltaRaw >= 0 ? '+' : ''}${deltaRaw.toFixed(2)}`;
      const align = Number(row.component_alignment || 0).toFixed(3);
      const runeHtml = runePageHtml(row.rune_page || {});
      return `<div class="build-card compare-card ${modeClass}${idx < 3 ? ' top-rank' : ''}">
        <div class="compare-card-head">
          <h4>#${idx + 1} ${escHtml(row.label || 'meta')}</h4>
          <span class="ability-chip compare-mode-chip">${escHtml(normalizedMode.replaceAll('_', ' '))}</span>
        </div>
        <div class="compare-stat-row">
          <span class="compare-stat-pill compare-stat-sim">${metricLabel('item overlap', 'Shared unique items between optimizer build and parsed meta build. 1.0 means the same item set.', 'compare-metric-sim')} <b class="compare-metric compare-metric-sim">${sim}</b></span>
          <span class="compare-stat-pill compare-stat-delta">${metricLabel('power delta', 'Percent difference between optimizer weighted score and the re-evaluated meta build score. Positive means the optimizer build scores higher.', 'compare-metric-delta')} <b class="compare-metric compare-metric-delta">${delta}%</b></span>
          <span class="compare-stat-pill compare-stat-align">${metricLabel('component align', 'How closely the damage, healing, tankiness, and lifesteal profile matches the optimizer build. Higher is closer.', 'compare-metric-align')} <b class="compare-metric compare-metric-align">${align}</b></span>
        </div>
        <div class="compare-item-panel">
          <div class="build-items" style="margin-top:0;">${itemIcons || ''}</div>
        </div>
        ${runeHtml}
        <div class="build-metrics compare-item-chip-row" style="margin-top:6px;display:flex;flex-wrap:wrap;gap:4px;">${items.length ? items.map(x => `<span class="ability-chip" style="font-size:0.74rem;">${escHtml(x)}</span>`).join(' ') : '<span class="muted">No parsed items.</span>'}</div>
      </div>`;
    }).join('');

    return `
      <div class="build-metrics" style="margin-top:8px;margin-bottom:4px;"><b>${escHtml(tableLabel)}</b></div>
      <div class="result-grid compare-grid">${cards || ''}</div>
      ${cards ? '' : '<p class="muted">No parsed builds.</p>'}
    `;
  };

  const modes = meta.modes || {};
  const overlapRows = Array.isArray(modes.item_overlap) ? modes.item_overlap : [];
  const powerRows = Array.isArray(modes.power_delta) ? modes.power_delta : [];
  const componentRows = Array.isArray(modes.component_balance) ? modes.component_balance : [];
  const warnings = Array.isArray(meta.warnings) ? meta.warnings : [];
  const warningHtml = warnings.length
    ? `<div class="build-warning" style="margin-bottom:6px;">${warnings.map(x => escHtml(x)).join(' | ')}</div>`
    : '';
  const modeRows = (meta.comparison_mode || 'all') === 'all'
    ? [...overlapRows, ...powerRows, ...componentRows]
    : (Array.isArray(meta.meta_builds) ? meta.meta_builds : []);
  if (modeRows.length === 0) {
    return `${warningHtml}<p class="muted">No comparison rows are available for the selected mode and context.</p>`;
  }

  let modeTables = '';
  if ((meta.comparison_mode || 'all') === 'all') {
    modeTables =
      renderRows(overlapRows, 'Item Overlap', 'item_overlap') +
      renderRows(powerRows, 'Power Delta', 'power_delta') +
      renderRows(componentRows, 'Component Balance', 'component_balance');
  } else {
    modeTables = renderRows(meta.meta_builds || [], 'Selected Mode Results', meta.comparison_mode || 'all');
  }

  return `
    ${warningHtml}
    <div class="build-metrics" style="margin-bottom:6px;display:flex;flex-wrap:wrap;gap:6px;align-items:center;">${sourceBadge}${fallbackBadge}</div>
    <div class="build-metrics" style="margin-bottom:6px;">mode: <b>${mode}</b> | context: ${ctx}</div>
    <div class="build-metrics compare-legend" style="margin-bottom:6px;line-height:1.35;">
      <b>Metric guide:</b>
      <span class="compare-metric compare-metric-sim">item overlap</span> = shared unique items (0 to 1),
      <span class="compare-metric compare-metric-delta">power delta</span> = optimizer score advantage over re-evaluated meta build,
      <span class="compare-metric compare-metric-align">component align</span> = closeness of damage/healing/tank/lifesteal profile (0 to 1).
    </div>
    <div class="build-metrics" style="margin-bottom:6px;">best match items: ${bestItems}</div>
    ${modeTables}
  `;
}

async function runUnknownOpSweep() {
  const output = document.getElementById('unknownOpResults');
  output.innerHTML = '<p class="muted thinking">Running full champion sweep...</p>';

  const params = new URLSearchParams({
    threshold_percent: '5',
    role: document.getElementById('role').value,
    meta_tier: document.getElementById('meta_tier').value,
    meta_region: document.getElementById('meta_region').value,
    meta_patch: document.getElementById('meta_patch').value,
    force_refresh: document.getElementById('force_refresh').value,
  });

  try {
    const res = await fetch(`/api/unknown-op-sweep?${params.toString()}`);
    const data = await res.json();
    if (!res.ok) {
      output.innerHTML = `<p class="err">${escHtml(data.error || 'Sweep failed')}</p>`;
      return;
    }

    const rows = (data.unknown_op_candidates || []).slice(0, 20).map((row, idx) => {
      const best = row.optimizer_best || {};
      const order = Array.isArray(best.order) ? best.order : [];
      const names = order.map(x => escHtml(x.name || '')).join(', ');
      return `<div class="build-card${idx < 3 ? ' top-rank' : ''}">
        <h4>#${idx + 1} ${escHtml(row.champion || '')} <code>+${Number(row.advantage_percent || 0).toFixed(2)}%</code></h4>
        <div class="build-metrics">similarity vs best meta: ${Number(row.similarity || 0).toFixed(3)}</div>
        <div class="build-metrics">items: ${names}</div>
      </div>`;
    }).join('');

    output.innerHTML = `
      <div class="build-metrics" style="margin-bottom:8px;">analyzed: <b>${data.champions_analyzed || 0}</b> | unknown-OP: <b>${data.count || 0}</b> | failures: <b>${data.failure_count || 0}</b></div>
      <div class="result-grid">${rows || ''}</div>
      ${rows ? '' : '<p class="muted">No unknown OP candidates found at this threshold.</p>'}
    `;
  } catch (_) {
    output.innerHTML = '<p class="err">Sweep request failed.</p>';
  }
}

function setOptimizeRunning(isRunning) {
  optimizeRunning = isRunning;
  const btn = document.getElementById('optimizeBtn');
  if (btn) {
    btn.disabled = isRunning;
    btn.style.opacity = isRunning ? '0.75' : '1';
    btn.style.cursor = isRunning ? 'not-allowed' : 'pointer';
  }
}

function showLoadingPanel(show) {
  const panel = document.getElementById('loadingPanel');
  if (!panel) return;
  panel.style.display = show ? 'block' : 'none';
  if (show) {
    // Reset step log when a new run starts
    const stepLog = document.getElementById('loadStepLog');
    if (stepLog) stepLog.innerHTML = '';
    _lastLoadPhase = '';
  }
}

function formatEta(seconds) {
  if (!seconds || seconds < 1) return 'ETA <1s';
  if (seconds >= 60) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return `ETA ${mins}m ${secs}s`;
  }
  return `ETA ${Math.round(seconds)}s`;
}

let _lastLoadPhase = '';

function setLoadState(data) {
  const running = data.status === 'running';
  let pct = Number(data.progress_percent || 0);
  if (running) pct = Math.max(1, Math.min(99, pct));
  if (data.status === 'complete') pct = 100;
  if (data.status === 'error') pct = Math.max(1, Math.min(99, pct));

  const fill = document.getElementById('loadFill');
  const phase = document.getElementById('loadPhase');
  const eta = document.getElementById('loadEta');
  const pctEl = document.getElementById('loadPct');
  const stepLog = document.getElementById('loadStepLog');
  if (fill) fill.style.width = `${pct}%`;

  const phaseText = data.phase || 'Running optimization...';
  if (phase) {
    if (running) {
      phase.textContent = phaseText;
    } else {
      phase.textContent = phaseText;
    }
  }

  if (eta) {
    if (data.status === 'complete') {
      const elapsed = Number(data.elapsed_seconds || 0);
      eta.textContent = `Completed in ${elapsed.toFixed(2)}s`;
    } else if (data.status === 'error') {
      eta.textContent = 'Stopped';
    } else {
      eta.textContent = formatEta(Number(data.eta_seconds || 0));
    }
  }
  if (pctEl) pctEl.textContent = `${pct}% complete`;

  // Append new phase step to the step log
  if (stepLog && phaseText && phaseText !== _lastLoadPhase) {
    _lastLoadPhase = phaseText;
    // Mark all existing items as non-current
    stepLog.querySelectorAll('.load-step-item.current').forEach(el => el.classList.remove('current'));
    const item = document.createElement('div');
    item.className = 'load-step-item current';
    item.textContent = `› ${phaseText}`;
    stepLog.appendChild(item);
    // Keep only last 6 steps
    const items = stepLog.querySelectorAll('.load-step-item');
    if (items.length > 6) {
      for (let i = 0; i < items.length - 6; i++) {
        stepLog.removeChild(items[i]);
      }
    }
  }
}

async function waitForOptimizeResult(jobId) {
  const statusEl = document.getElementById('status');
  while (true) {
    const res = await fetch(`/optimize/status/${jobId}`);
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || 'Failed reading optimization status');
    }

    setLoadState(data);
    if (data.status === 'error') {
      document.getElementById('error').textContent = data.error || 'Optimization failed';
      statusEl.textContent = '';
      showLoadingPanel(false);
      throw new Error(data.error || 'Optimization failed');
    }
    if (data.status === 'complete') {
      setTimeout(() => showLoadingPanel(false), 1200);
      return data.result || {};
    }

    await new Promise(resolve => setTimeout(resolve, 320));
  }
}

// Refresh data
async function refreshData() {
  document.getElementById('error').textContent = '';
  document.getElementById('status').textContent = 'Refreshing local cache from remote sources...';

  const res = await fetch('/refresh-data', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({champion: selectedChampion}),
  });
  const data = await res.json();
  if (!res.ok) {
    document.getElementById('error').textContent = data.error || 'Unknown error';
    document.getElementById('status').textContent = '';
    return;
  }
  document.getElementById('status').textContent =
    `Cache refreshed. Patch ${data.patch}. Deleted riot=${data.entries_deleted.riot}, wiki=${data.entries_deleted.wiki}`;
  if (!allChampions.length || (data.patch && data.patch !== currentPatch)) {
    await loadChampions();
  } else {
    await loadSelectedChampionScaling(selectedChampion);
  }
}

// AI suggest
async function runAiSuggest() {
  const model = document.getElementById('ollamaModel').value;
  document.getElementById('aiStatus').textContent = 'Asking AI (this may take 10-30 seconds)...';
  document.getElementById('aiResult').innerHTML = '';
  selectedRating = 0;

  const body = {
    champion: selectedChampion,
    model,
    damage: Number(document.getElementById('damage').value),
    healing: Number(document.getElementById('healing').value),
    tankiness: Number(document.getElementById('tank').value),
    lifesteal: Number(document.getElementById('ls').value),
    enemy_hp: Number(document.getElementById('enemy_hp').value),
    enemy_armor: Number(document.getElementById('enemy_armor').value),
    enemy_mr: Number(document.getElementById('enemy_mr').value),
    enemy_physical_share: Number(document.getElementById('enemy_physical_share').value),
    ranked_context: lastRanked.slice(0, 3),
  };

  const res = await fetch('/api/ai-suggest', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) {
    document.getElementById('aiStatus').textContent = data.error || 'AI unavailable';
    return;
  }
  document.getElementById('aiStatus').textContent = '';
  const best = data.best_candidate || (data.candidates || [])[0] || null;
  currentAiItems = best ? (best.order || []).map(x => x.name || '') : [];
  currentAiReasoning = best ? (best.reasoning || '') : '';
  renderAiResult(data, currentPatch);
}

function renderAiResult(data, patch) {
  const candidates = data.candidates || [];
  if (!candidates.length) {
    document.getElementById('aiResult').innerHTML = '<p class="muted">AI returned no valid candidates. Try a different model or champion.</p>';
    return;
  }

  const renderCandidate = (c, isBest) => {
    const icons = itemIconsHtml(c.order || [], patch);
    const score = Number(c.weighted_score || 0).toFixed(3);
    const runes = (c.rune_hints || []).map(r =>
      `<span style="background:#1a2d1a;border:1px solid #2e5e2e;border-radius:4px;padding:2px 7px;font-size:0.8rem;">${escHtml(r)}</span>`
    ).join(' ');
    const innovation = c.innovation_score != null
      ? `<span class="muted" style="font-size:0.8rem;"> · innovation ${Number(c.innovation_score).toFixed(2)}</span>`
      : '';
    const badge = isBest
      ? '<span style="background:#2e5e1a;color:#a0e060;border-radius:4px;padding:1px 7px;font-size:0.78rem;margin-left:6px;">Best</span>'
      : '';
    const playstyle = c.playstyle_note
      ? `<p style="font-size:0.82rem;color:var(--muted);margin:4px 0 0 0;">${escHtml(c.playstyle_note)}</p>`
      : '';
    const ratingBlock = isBest ? `
      <div style="margin-top:10px;border-top:1px solid #344a5e;padding-top:8px;">
        <span style="font-size:0.85rem;color:var(--muted);">Rate this build:</span>
        <div class="star-row" id="starRow">
          ${[1,2,3,4,5].map(i=>`<button class="star-btn" onclick="setRating(${i})" id="star${i}">&#9733;</button>`).join('')}
        </div>
        <button class="secondary" style="width:auto;padding:0.4rem 1rem;font-size:0.85rem;" onclick="submitRating()">Submit Rating</button>
        <span id="ratingStatus" style="margin-left:8px;font-size:0.82rem;"></span>
      </div>` : '';
    return `<div class="ai-result-box" style="${isBest ? 'border-color:#3a5e2a;' : 'opacity:0.88;'}">
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;">
        <b style="text-transform:capitalize;">${escHtml(c.label || 'candidate')}</b>${badge}
        <code style="font-size:0.85rem;">${score}</code>${innovation}
      </div>
      <div class="build-items" style="margin-bottom:6px;">${icons}</div>
      ${runes ? `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px;">${runes}</div>` : ''}
      <p class="ai-reasoning" style="margin:0 0 4px 0;">${escHtml(c.reasoning || '')}</p>
      ${playstyle}
      ${ratingBlock}
    </div>`;
  };

  document.getElementById('aiResult').innerHTML =
    candidates.map((c, idx) => renderCandidate(c, idx === 0)).join('');
}

function setRating(n) {
  selectedRating = n;
  for (let i=1;i<=5;i++) {
    const btn = document.getElementById('star'+i);
    if (btn) btn.classList.toggle('lit', i<=n);
  }
}

async function submitRating() {
  if (!selectedRating) { document.getElementById('ratingStatus').textContent='Pick a rating first'; return; }
  const res = await fetch('/api/ai-rate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      champion: selectedChampion,
      build_items: currentAiItems,
      rating: selectedRating,
      ai_reasoning: currentAiReasoning,
    }),
  });
  const data = await res.json();
  document.getElementById('ratingStatus').textContent = res.ok ? 'Saved - will influence future AI suggestions' : (data.error||'Failed');
  document.getElementById('ratingStatus').className = res.ok ? 'ok' : 'err';
}

// Build formatters
function itemIconsHtml(items, patch) {
  return (items||[]).map((item, idx) => {
    const url = item.icon_url || '';
    return `<span class="item-icon">
      <img src="${url}" onerror="this.src=''" title="${escHtml(item.name)}" />
      <span class="item-name-tooltip">${escHtml(item.name)}</span>
      <span class="build-order-num">${idx + 1}</span>
    </span>`;
  }).join('');
}

function runePageHtml(runePage) {
  if (!runePage || !Array.isArray(runePage.runes) || runePage.runes.length === 0) return '';
  const primaryTree = String(runePage.primary_tree || 'Primary');
  const secondaryTree = String(runePage.secondary_tree || 'Secondary');
  const selectedRunes = Array.isArray(runePage.runes) ? runePage.runes : [];
  const shardList = Array.isArray(runePage.shards) ? runePage.shards.slice(0, 3) : [];

  const normalizeRuneToken = (value) => String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const selectedKeys = new Set();
  selectedRunes.forEach((r) => {
    selectedKeys.add(normalizeRuneToken(r.id || ''));
    selectedKeys.add(normalizeRuneToken(r.name || ''));
  });
  const selectedShards = new Set(shardList.map((x) => normalizeRuneToken(x)));

  const styleLookup = {};
  const styles = Array.isArray(runeCatalog && runeCatalog.styles) ? runeCatalog.styles : [];
  styles.forEach((style) => {
    const styleName = String(style.name || '');
    if (!styleName) return;
    styleLookup[normalizeRuneToken(styleName)] = style;
  });

  const runeGlyph = (name) => {
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return '?';
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  };

  const renderRuneOption = (rune, isKeystoneRow) => {
    const title = escHtml(String(rune.name || 'Rune'));
    const tokenA = normalizeRuneToken(rune.id || '');
    const tokenB = normalizeRuneToken(rune.name || '');
    const selected = selectedKeys.has(tokenA) || selectedKeys.has(tokenB);
    const iconUrl = String(rune.icon_url || '').trim();
    const iconHtml = iconUrl
      ? `<img class="rune-orb-icon" src="${escHtml(iconUrl)}" alt="${title}" onerror="this.style.display='none';this.nextElementSibling.style.display='inline-flex';" />`
      : '';
    return `<span class="rune-orb${isKeystoneRow ? ' rune-orb-keystone' : ''}${selected ? ' rune-orb-selected' : ''}" title="${title}">
      ${iconHtml}
      <span class="rune-orb-glyph"${iconUrl ? ' style="display:none"' : ''}>${escHtml(runeGlyph(rune.name))}</span>
    </span>`;
  };

  const renderBranch = (label, style, showKeystone) => {
    if (!style || !Array.isArray(style.slots)) {
      const fallbackOrbs = selectedRunes
        .filter((r) => normalizeRuneToken(r.tree || '') === normalizeRuneToken(label))
        .map((r) => renderRuneOption(r, String(r.slot || '').toLowerCase() === 'keystone'))
        .join('');
      return `<div class="rune-branch">
        <div class="rune-branch-title">${escHtml(label)}</div>
        <div class="rune-slot-row"><div class="rune-orb-grid">${fallbackOrbs}</div></div>
      </div>`;
    }

    const slotRows = style.slots
      .filter((slot) => showKeystone || Number(slot.slot_index || 0) > 0)
      .map((slot) => {
        const slotIndex = Number(slot.slot_index || 0);
        const isKeystoneRow = slotIndex === 0;
        const orbs = Array.isArray(slot.runes)
          ? slot.runes.map((r) => renderRuneOption(r, isKeystoneRow)).join('')
          : '';
        return `<div class="rune-slot-row">
          <div class="rune-orb-grid">${orbs}</div>
        </div>`;
      }).join('');

    return `<div class="rune-branch">
      <div class="rune-branch-title">${escHtml(label)}</div>
      ${slotRows}
    </div>`;
  };

  const shardDefaults = [
    [
      {id: '5008', name: 'Adaptive Force'},
      {id: '5005', name: 'Attack Speed'},
      {id: '5007', name: 'Ability Haste'},
    ],
    [
      {id: '5008b', name: 'Adaptive Force'},
      {id: '5002', name: 'Movement Speed'},
      {id: '5003', name: 'Scaling Health'},
    ],
    [
      {id: '5011', name: 'Health'},
      {id: '5013', name: 'Tenacity and Slow Resist'},
      {id: '5001', name: 'Health Scaling'},
    ],
  ];
  const shardRows = Array.isArray(runeCatalog && runeCatalog.shards) && runeCatalog.shards.length
    ? runeCatalog.shards
    : shardDefaults;
  const shardRowsHtml = shardRows.map((row) => {
    const options = Array.isArray(row) ? row : [];
    const chips = options.map((opt) => {
      const optName = String((opt && opt.name) || 'Shard');
      const iconUrl = String((opt && opt.icon_url) || '').trim();
      const selected = selectedShards.has(normalizeRuneToken(optName));
      const iconHtml = iconUrl
        ? `<img class="shard-icon" src="${escHtml(iconUrl)}" alt="${escHtml(optName)}" onerror="this.style.display='none';this.nextElementSibling.style.display='inline'" /><span class="shard-glyph" style="display:none">${escHtml(runeGlyph(optName))}</span>`
        : `<span class="shard-glyph">${escHtml(runeGlyph(optName))}</span>`;
      return `<span class="rune-shard${selected ? ' rune-shard-selected' : ''}" title="${escHtml(optName)}">${iconHtml}</span>`;
    }).join('');
    return `<div class="rune-shard-row">${chips}</div>`;
  }).join('');

  const primaryStyle = styleLookup[normalizeRuneToken(primaryTree)] || null;
  const secondaryStyle = styleLookup[normalizeRuneToken(secondaryTree)] || null;

  return `<div class="rune-tree-wrap rune-tree-complete">
    ${renderBranch(primaryTree, primaryStyle, true)}
    ${renderBranch(secondaryTree, secondaryStyle, false)}
    <div class="rune-branch rune-branch-shards">
      <div class="rune-branch-title">Shards</div>
      ${shardRowsHtml}
    </div>
  </div>`;
}

function formatTraceValue(value, digits = 2) {
  if (value === null || value === undefined || value === '') return 'n/a';
  const num = Number(value);
  if (Number.isFinite(num)) return num.toFixed(digits);
  return escHtml(String(value));
}

function formatSignedValue(value, digits = 2) {
  const num = Number(value || 0);
  if (!Number.isFinite(num)) return 'n/a';
  return `${num >= 0 ? '+' : ''}${num.toFixed(digits)}`;
}

function buildTraceSummary(row) {
  const metrics = row.metrics || {};
  const contributions = row.contributions || {};
  const trace = row.trace || {};
  const interactions = Array.isArray(row.interactions) ? row.interactions : [];
  const labels = {
    damage_component: 'damage pressure',
    healing_component: 'healing conversion',
    tankiness_component: 'durability',
    lifesteal_component: 'sustain loop',
    utility_component: 'utility profile',
    consistency_component: 'reliability',
    order_component: 'item timing',
    interaction_component: 'item synergy',
    gold_efficiency_component: 'gold efficiency',
  };

  const rankedContribs = Object.entries(labels)
    .map(([key, label]) => ({key, label, value: Number(contributions[key] || 0)}))
    .filter((entry) => entry.value > 0.01)
    .sort((left, right) => right.value - left.value)
    .slice(0, 3);

  const summaryBits = rankedContribs.map((entry) => `${entry.label} ${formatSignedValue(entry.value, 2)}`);

  if (Number(trace.realized_damage_amp_total || 0) > 0.03) {
    summaryBits.push(`realized amp ${formatSignedValue(trace.realized_damage_amp_total, 3)}`);
  } else if (Number(trace.max_hp_proc_damage_total || 0) > 40) {
    summaryBits.push(`max-HP proc ${formatTraceValue(trace.max_hp_proc_damage_total, 1)}`);
  }

  if (Number(metrics.stack_uptime || 0) >= 0.55) {
    summaryBits.push(`strong ramp uptime ${formatTraceValue(metrics.stack_uptime, 2)}`);
  } else if (Number(metrics.proc_frequency || 0) >= 1.1) {
    summaryBits.push(`high proc cadence ${formatTraceValue(metrics.proc_frequency, 2)}`);
  }

  if (Number(metrics.burst_profile || 0) > Number(metrics.sustained_profile || 0) * 1.15) {
    summaryBits.push(`burst-leaning profile ${formatTraceValue(metrics.burst_profile, 1)}`);
  } else if (Number(metrics.sustained_profile || 0) > Number(metrics.burst_profile || 0) * 1.15) {
    summaryBits.push(`sustained edge ${formatTraceValue(metrics.sustained_profile, 1)}`);
  }

  if (interactions.length) {
    summaryBits.push(interactions[0]);
  }

  const uniqueBits = [];
  const seen = new Set();
  summaryBits.forEach((bit) => {
    const key = String(bit || '').trim().toLowerCase();
    if (!key || seen.has(key)) return;
    seen.add(key);
    uniqueBits.push(bit);
  });

  return uniqueBits.slice(0, 5);
}

function renderTraceDrawer(row, cardId) {
  const trace = row && row.trace ? row.trace : {};
  if (!trace || typeof trace !== 'object' || Object.keys(trace).length === 0) return '';

  const metricHint = (label, helpText) => `<span>${escHtml(label)} <span class="metric-help" title="${escHtml(helpText)}">?</span></span>`;
  const metricCell = (label, value, helpText, digits = 2) => `<div class="trace-metric">
      <div class="trace-metric-label">${metricHint(label, helpText)}</div>
      <div class="trace-metric-value">${formatTraceValue(value, digits)}</div>
    </div>`;

  const sections = [
    {
      title: 'Proc Realization',
      metrics: [
        metricCell('realized amp', trace.realized_damage_amp_total, 'The amount of damage amplification the optimizer expects this build to actually cash in during the fight window.', 3),
        metricCell('raw amp', trace.damage_amp_total, 'The raw amplification available on paper before uptime and cadence reduce it.', 3),
        metricCell('max HP proc dmg', trace.max_hp_proc_damage_total, 'Estimated maximum-health-based proc damage converted during the fight.', 3),
        metricCell('bonus true dmg', trace.realized_bonus_true_damage, 'Estimated bonus true damage successfully realized from proc-oriented effects.', 3),
      ],
    },
    {
      title: 'Combat Pattern',
      metrics: [
        metricCell('hit events', trace.hit_events, 'Estimated meaningful contact events, combining autos and spell hits, over the modeled fight.', 3),
        metricCell('auto attacks', trace.auto_attacks_est, 'Estimated number of auto attacks in the modeled fight window.', 3),
        metricCell('spell casts', trace.spell_casts_est, 'Estimated number of spell casts contributing to damage pressure.', 3),
        metricCell('proc bias', trace.item_proc_bias, 'Item-derived tendency toward extra proc windows or repeat triggers.', 3),
        metricCell('stack bias', trace.item_stack_bias, 'Item-derived tendency toward ramping and sustaining stacked passives.', 3),
      ],
    },
    {
      title: 'Stat Totals',
      metrics: [
        metricCell('AD', trace.ad_total, 'Final attack damage total used by the evaluator.', 1),
        metricCell('AP', trace.ap_total, 'Final ability power total used by the evaluator.', 1),
        metricCell('AS', trace.attack_speed_total, 'Final attack speed contribution used in proc and DPS realization.', 3),
        metricCell('AH', trace.ability_haste_total, 'Final ability haste total used to estimate spell cadence.', 1),
        metricCell('armor pen', trace.armor_pen_total, 'Percent armor penetration applied in the damage model.', 3),
        metricCell('magic pen', trace.magic_pen_total, 'Percent magic penetration applied in the damage model.', 3),
      ],
    },
    {
      title: 'Enemy Context',
      metrics: [
        metricCell('target HP', trace.enemy_target_hp, 'Enemy HP assumed by the optimizer for this run.', 1),
        metricCell('target armor', trace.enemy_target_armor, 'Enemy armor assumed by the optimizer for this run.', 1),
        metricCell('target MR', trace.enemy_target_mr, 'Enemy magic resist assumed by the optimizer for this run.', 1),
        metricCell('rotation raw', trace.spell_rotation_raw, 'Raw spell-rotation value before weighting and interaction bonuses.', 3),
        metricCell('rune amp', trace.rune_damage_amp, 'Additional damage amplification coming from the selected rune page.', 4),
      ],
    },
  ];

  const summaryBits = buildTraceSummary(row);
  const summaryHtml = summaryBits.length
    ? `<div class="trace-summary-box">
        <div class="trace-summary-title">Why this build won</div>
        <div class="trace-chip-row">${summaryBits.map((bit) => `<span class="trace-chip">${escHtml(bit)}</span>`).join('')}</div>
      </div>`
    : '';

  const sectionsHtml = sections.map((section) => `<div class="trace-section">
      <div class="trace-section-title">${escHtml(section.title)}</div>
      <div class="trace-grid">${section.metrics.join('')}</div>
    </div>`).join('');

  return `<details class="trace-drawer" id="trace_${cardId}">
    <summary class="trace-summary">Trace details</summary>
    <div class="trace-body">${summaryHtml}${sectionsHtml}</div>
  </details>`;
}

function formatRows(rows, id) {
  if (!rows || rows.length === 0) return '<p class="muted">No results.</p>';

  const patch = currentPatch;
  const metricHint = (label, helpText) => `<span>${escHtml(label)} <span class="metric-help" title="${escHtml(helpText)}">?</span></span>`;
  const renderCard = (row, idx) => {
    const m = row.metrics;
    const c = row.contributions || {};
    const interactions = row.interactions && row.interactions.length > 0 ? row.interactions.join('; ') : 'none';
    const runeHtml = runePageHtml(row.rune_page || {});
    const stackUptime = Number(m.stack_uptime || 0).toFixed(2);
    const procFrequency = Number(m.proc_frequency || 0).toFixed(2);
    const confidence = buildConfidence(row, idx);
    const traceHtml = renderTraceDrawer(row, `${id}_${idx}`);
    return `<div class="build-card${idx < 3 ? ' top-rank' : ''}">
      <div class="build-card-head">
        <h4>#${idx+1} <code style="font-size:0.9rem;">${row.weighted_score}</code></h4>
        <span class="build-confidence ${confidence.cls}">${escHtml(confidence.label)}</span>
      </div>
      <div class="build-items">${itemIconsHtml(row.order, patch)}</div>
      ${runeHtml}
      <div class="build-metrics">
        dmg <b>${m.damage}</b> | heal <b>${m.healing}</b> | tank <b>${m.tankiness}</b> | ls <b>${m.lifesteal}</b> | util <b>${m.utility || 0}</b> | consist <b>${m.consistency || 0}</b>
      </div>
      <div class="build-metrics build-metric-help-row" style="margin-top:2px;">${metricHint('proc cadence', 'Estimated number of meaningful passive or burst proc windows the build can trigger in a typical fight. Higher means more frequent proc conversion.')} <b>${procFrequency}</b> | ${metricHint('stack uptime', 'Estimated fraction of the fight spent at or near fully ramped passive strength. Higher means better long-fight realization.')} <b>${stackUptime}</b></div>
      <div class="build-metrics" style="margin-top:2px;">
        explain: dmg ${c.damage_component||0} | heal ${c.healing_component||0} | tank ${c.tankiness_component||0} | ls ${c.lifesteal_component||0} | util ${c.utility_component||0} | consist ${c.consistency_component||0} | order ${c.order_component||0} | synergy ${c.interaction_component||0}
      </div>
      <div class="build-metrics" style="margin-top:2px;">profiles: burst ${m.burst_profile||0} | sustained ${m.sustained_profile||0} | aoe ${m.aoe_pressure||0}</div>
      <div class="build-metrics" style="margin-top:2px;">interactions: ${escHtml(interactions)}</div>
      ${traceHtml}
    </div>`;
  };

  const top3 = rows.slice(0, 3).map((r,i) => renderCard(r, i)).join('');
  const rest = rows.slice(3);
  if (rest.length === 0) return `<div class="result-grid">${top3}</div>`;

  const moreId = `more_${id}`;
  const btnId = `morebtn_${id}`;
  const restHtml = rest.map((r,i) => renderCard(r, i+3)).join('');
  return `<div class="result-grid">${top3}</div>` +
    `<div id="${moreId}" class="result-grid" style="display:none">${restHtml}</div>
     <button class="show-more-btn" id="${btnId}" onclick="toggleMore('${moreId}','${btnId}',${rest.length})">
       Show ${rest.length} more...
     </button>`;
}

function renderRankedInsights(rows) {
  const panel = document.getElementById('rankedInsights');
  if (!panel) return;
  if (!rows || rows.length < 2) {
    panel.innerHTML = '<p class="muted">Run an optimization to compare top builds on proc cadence, stack uptime, and fight profile.</p>';
    return;
  }

  const top = rows.slice(0, 3);
  const first = top[0];
  const second = top[1];
  const third = top[2] || null;
  const lead = Number(first.weighted_score || 0) - Number(second.weighted_score || 0);
  const topProc = top.reduce((best, row) => Number(row.metrics?.proc_frequency || 0) > Number(best.metrics?.proc_frequency || 0) ? row : best, top[0]);
  const topStack = top.reduce((best, row) => Number(row.metrics?.stack_uptime || 0) > Number(best.metrics?.stack_uptime || 0) ? row : best, top[0]);
  const topBurst = top.reduce((best, row) => Number(row.metrics?.burst_profile || 0) > Number(best.metrics?.burst_profile || 0) ? row : best, top[0]);

  const summaryBits = [
    `Top build leads #2 by <b>${lead.toFixed(2)}</b> weighted score.`,
    `Best proc cadence: <b>#${top.indexOf(topProc) + 1}</b> at <b>${Number(topProc.metrics?.proc_frequency || 0).toFixed(2)}</b>.`,
    `Best stack uptime: <b>#${top.indexOf(topStack) + 1}</b> at <b>${Number(topStack.metrics?.stack_uptime || 0).toFixed(2)}</b>.`,
    `Best burst profile: <b>#${top.indexOf(topBurst) + 1}</b> at <b>${Number(topBurst.metrics?.burst_profile || 0).toFixed(1)}</b>.`,
  ];

  const compareRows = [first, second, third].filter(Boolean).map((row, idx) => {
    const m = row.metrics || {};
    return `<div class="insight-row">
      <div class="insight-rank">#${idx + 1}</div>
      <div class="insight-main">
        <div class="insight-score">score ${Number(row.weighted_score || 0).toFixed(2)}</div>
        <div class="insight-stats">
          <span class="insight-pill">proc ${Number(m.proc_frequency || 0).toFixed(2)}</span>
          <span class="insight-pill">stack ${Number(m.stack_uptime || 0).toFixed(2)}</span>
          <span class="insight-pill">burst ${Number(m.burst_profile || 0).toFixed(1)}</span>
          <span class="insight-pill">sustain ${Number(m.sustained_profile || 0).toFixed(1)}</span>
        </div>
      </div>
    </div>`;
  }).join('');

  panel.innerHTML = `
    <div class="insight-summary">${summaryBits.join(' ')}</div>
    <div class="insight-grid">${compareRows}</div>
  `;
}

function toggleMore(divId, btnId, count) {
  const div = document.getElementById(divId);
  const btn = document.getElementById(btnId);
  if (!div || !btn) return;
  const showing = div.style.display !== 'none';
  div.style.display = showing ? 'none' : 'block';
  btn.textContent = showing ? `Show ${count} more...` : 'Show fewer';
}

function formatCheckpoints(points, patch) {
  if (!points || Object.keys(points).length === 0) return '<p class="muted">No checkpoint data.</p>';
  const keys = Object.keys(points).sort((a,b) => Number(a.split('_')[0]) - Number(b.split('_')[0]));
  const cards = keys.map(key => {
    const row = points[key];
    const runeHtml = runePageHtml(row.rune_page || {});
    return `<div class="build-card">
      <h4>${key.replace('_',' ')} <code>${row.weighted_score}</code></h4>
      <div class="build-items">${itemIconsHtml(row.order, patch)}</div>
      ${runeHtml}
    </div>`;
  }).join('');
  return `<div class="result-grid">${cards}</div>`;
}

function formatAbilityBreakdown(scaling) {
  if (!scaling || !scaling.ability_breakdown) return '<p class="muted">No scaling data available.</p>';
  const order = ['passive','q','w','e','r'];
  const keys = order.filter(k => scaling.ability_breakdown[k]).concat(
    Object.keys(scaling.ability_breakdown).filter(k => !order.includes(k))
  );

  const dmgColorClass = {physical:'phys',magic:'magic',true:'true_dmg',mixed:'mixed',none:'no-dmg'};
  const keyLabel = {q:'Q',w:'W',e:'E',r:'R',passive:'P'};

  function ratioBar(val, type) {
    const pct = Math.min(100, Math.round(val * 100));
    return `<div class="ratio-row">
      <span class="ratio-label">${type}</span>
      <div class="ratio-bar-wrap"><div class="ratio-bar ${type}" style="width:${pct}%"></div></div>
      <span class="ratio-value">${val.toFixed(2)}</span>
    </div>`;
  }

  const cards = keys.map(key => {
    const v = scaling.ability_breakdown[key] || {};
    const dmgType = v.damage_type || 'unknown';
    const colorCls = dmgColorClass[dmgType] || 'dmg-unknown';
    const keyStr = keyLabel[key] || key.toUpperCase();
    const name = v.name ? escHtml(v.name) : escHtml(key);
    const baseDmg = Array.isArray(v.base_damage) && v.base_damage.length ? v.base_damage.join('/') : '-';
    const cd = Array.isArray(v.cooldown) && v.cooldown.length ? v.cooldown.join('/') : '-';
    const cost = Array.isArray(v.cost) && v.cost.length ? v.cost.join('/') : null;

    const adRatio = Number(v.ad_ratio || 0);
    const apRatio = Number(v.ap_ratio || 0);
    const healRatio = Number(v.heal_ratio || 0);
    const ratioBars = [
      adRatio   > 0 ? ratioBar(adRatio,   'AD')   : '',
      apRatio   > 0 ? ratioBar(apRatio,   'AP')   : '',
      healRatio > 0 ? ratioBar(healRatio, 'Heal') : '',
    ].filter(Boolean).join('');

    const dmgBadge = `<span class="ability-badge dmg-type ${colorCls}">${escHtml(dmgType)}</span>`;
    const targeting = v.targeting || 'unknown';
    const targetBadge = targeting !== 'unknown'
      ? `<span class="ability-badge targeting">${escHtml(targeting.replaceAll('_',' '))}</span>` : '';
    const extraBadges = [
      v.on_hit           ? '<span class="ability-badge on-hit">on-hit</span>'         : '',
      v.is_channeled     ? '<span class="ability-badge channeled">channel</span>'     : '',
      v.is_conditional   ? '<span class="ability-badge conditional">conditional</span>' : '',
      v.is_stack_scaling ? '<span class="ability-badge stacks">stacks</span>'         : '',
      v.range_units > 0  ? `<span class="ability-badge range">${v.range_units}u</span>` : '',
    ].filter(Boolean).join('');

    return `<div class="ability-card">
      <div class="ability-card-header ${colorCls}">
        <span class="ability-key">${keyStr}</span>
        <span class="ability-name">${name}</span>
        ${dmgBadge}
      </div>
      <div class="ability-card-body">
        ${ratioBars || '<span class="muted" style="font-size:0.75rem;">no scalings</span>'}
        <div class="ability-badges" style="margin-top:5px;">${targetBadge} ${extraBadges}</div>
      </div>
      <div class="ability-card-footer">
        <span class="ability-chip">Time ${escHtml(cd)}</span>
        <span class="ability-chip">Base ${escHtml(baseDmg)}</span>
        ${cost ? `<span class="ability-chip">Cost ${escHtml(cost)}</span>` : ''}
      </div>
    </div>`;
  }).join('');

  const fallbackBlock = scaling.placeholder_used
    ? `<span class="err" style="font-size:0.82rem;">Warning: ${escHtml((scaling.fallback_reasons||[]).join(' | '))}</span>`
    : '<span class="ok" style="font-size:0.82rem;">Live data</span>';
  return `<div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.65rem;flex-wrap:wrap;">
    <code style="font-size:0.76rem;">${escHtml(scaling.source)}</code>
    ${fallbackBlock}
  </div>
  <div class="ability-grid">${cards}</div>`;
}

function escHtml(str) {
  return String(str||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Patch pill
function updatePatchPill(patch) {
  const el = document.getElementById('patchPill');
  if (el && patch) el.textContent = `Patch ${patch}`;
}

// Simulation panel
function showSimPanel(show) {
  const el = document.getElementById('simCard');
  if (el) el.style.display = show ? 'block' : 'none';
}

async function runSimulation(simType) {
  const simEl = document.getElementById('simResultsEl');
  if (!simEl) return;
  simEl.innerHTML = '<p class="muted thinking">Running simulation...</p>';
  const params = new URLSearchParams({
    champion: selectedChampion,
    simulation_type: simType,
    target_hp:    document.getElementById('simHp').value,
    target_armor: document.getElementById('simArmor').value,
    target_mr:    document.getElementById('simMr').value,
    duration:     document.getElementById('simDur').value,
    level:        document.getElementById('simLvl').value,
  });
  try {
    const res = await fetch(`/api/champion-dps-simulation?${params}`);
    const data = await res.json();
    if (!res.ok) { simEl.innerHTML = `<p class="err">${escHtml(data.error||'Simulation failed')}</p>`; return; }
    renderSimResults(data, simEl);
  } catch(e) {
    simEl.innerHTML = '<p class="err">Simulation request failed.</p>';
  }
}

function renderSimResults(data, container) {
  const parts = [];

  if (data.burst) {
    const b = data.burst;
    const vals = Object.values(b.per_ability);
    const maxDmg = vals.length ? Math.max(1, ...vals) : 1;
    const bars = Object.entries(b.per_ability).map(([k, v]) => {
      const pct = Math.round((v / maxDmg) * 100);
      return `<div class="sim-bar-row">
        <span class="sim-bar-label">${k.toUpperCase()}</span>
        <div class="sim-bar-wrap"><div class="sim-bar-fill" style="width:${pct}%"></div></div>
        <span class="sim-bar-val">${v.toLocaleString()}</span>
      </div>`;
    }).join('');
    parts.push(`<h4 style="margin:0.5rem 0 0.3rem;font-size:0.87rem;font-weight:600;">Burst Rotation</h4>
      ${bars}
      <div class="sim-total-banner">
        <div class="sim-metric">
          <span class="sim-metric-label">Total Burst</span>
          <span class="sim-metric-value">${b.total.toLocaleString()}</span>
        </div>
      </div>`);
  }

  if (data.dps) {
    const d = data.dps;
    const castChips = Object.entries(d.cast_counts)
      .map(([k, v]) => `<span class="ability-chip">${k.toUpperCase()} x${v}</span>`).join(' ');
    parts.push(`<h4 style="margin:0.8rem 0 0.3rem;font-size:0.87rem;font-weight:600;">DPS Simulation</h4>
      <div class="sim-total-banner">
        <div class="sim-metric">
          <span class="sim-metric-label">DPS</span>
          <span class="sim-metric-value">${d.dps.toLocaleString()}</span>
        </div>
        <div class="sim-metric">
          <span class="sim-metric-label">Total Damage</span>
          <span class="sim-metric-value">${d.total_damage.toLocaleString()}</span>
        </div>
        <div class="sim-metric">
          <span class="sim-metric-label">Auto Attacks</span>
          <span class="sim-metric-value">${d.auto_attacks}</span>
        </div>
      </div>
      <div style="margin-top:0.5rem;font-size:0.8rem;color:var(--muted);">Casts: ${castChips}</div>`);
  }

  container.innerHTML = parts.join('') || '<p class="muted">No results.</p>';
}

// Boot
restorePrimarySelections();
loadChampions();
loadRuneCatalog();
loadRuntimeCapabilities();
loadOllamaModels();
restoreQuickSelections();

if (window.lucide) { window.lucide.createIcons(); }

