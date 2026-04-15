// --- The Ethspectoor — App Entry Point ---
import { state } from './state.js';
import { navigate, route, parseParams } from './router.js';
import { fuzzyScore, filterDiffItems } from './search.js';
import { closeSkillModal, viewSkill, copySkill } from './views/skill-modal.js';
import { setTypeFilter, typeFilterBtn, switchForkTab, toggleForkDiff } from './views/types.js';
import { setEpFilter, epFilterBtn, copyCurl, rebuildCurl, buildCurlFromInputs } from './views/endpoints.js';
import { togglePRSpecGroup, filterPRSidebar, togglePRPreview, findPRsForType } from './views/prs.js';
import { toggleDiffPreview, diffUpdateFrom, navigateDiff } from './views/diff-view.js';

/* ── Init ──────────────────────────────────────────────────── */

function buildIndexes() {
  state.allItems = [];
  state.allEndpoints = [];
  for (const [name, item] of Object.entries(state.catalog.items)) {
    state.allItems.push({ ...item, _type: 'item' });
  }
  for (const [specName, spec] of Object.entries(state.catalog.specs)) {
    if (spec.endpoints) {
      for (const [name, ep] of Object.entries(spec.endpoints)) {
        state.allEndpoints.push({ ...ep, _spec: specName, _key: name, _type: 'endpoint' });
      }
    }
  }
}

function updateHeaderStats() {
  const totalItems = Object.keys(state.catalog.items).length;
  let totalEps = 0;
  for (const s of Object.values(state.catalog._meta.specs)) { totalEps += s.endpoints; }
  document.getElementById('header-stats').textContent =
    totalItems + ' types · ' + totalEps + ' endpoints · ' + Object.keys(state.catalog._meta.specs).length + ' specs';
}

async function init() {
  try {
    const resp = await fetch('catalog.json');
    state.catalog = await resp.json();
    buildIndexes();
    updateHeaderStats();
    window.addEventListener('hashchange', route);
    route();
  } catch(e) {
    document.getElementById('loading').innerHTML =
      '<div style="color:var(--red)">Failed to load catalog.json</div><div style="color:var(--text-dim);font-size:12px">'+e.message+'</div>';
  }
}

/* ── Search ────────────────────────────────────────────────── */

function onSearchInput(val) {
  state.searchQuery = val;
  const hash = window.location.hash || '#/';
  if (hash.startsWith('#/diff')) {
    filterDiffItems(val);
  } else if (hash.startsWith('#/prs')) {
    filterPRSidebar(val);
  } else if (hash.startsWith('#/types') || hash.startsWith('#/endpoints')) {
    route();
  } else if (val.length > 0) {
    navigate('#/types');
  }
}

/* ── Keyboard Shortcuts ────────────────────────────────────── */

document.addEventListener('keydown', (e) => {
  const searchInput = document.getElementById('search-input');
  const isTyping = document.activeElement === searchInput;

  if (e.key === '/' && !isTyping) {
    e.preventDefault();
    searchInput.focus();
    return;
  }

  if (e.key === 'Escape') {
    const skillModal = document.getElementById('skill-modal');
    if (skillModal && skillModal.classList.contains('open')) {
      closeSkillModal();
      return;
    }
    if (isTyping) {
      searchInput.blur();
    } else if (state.searchQuery) {
      state.searchQuery = '';
      searchInput.value = '';
      if ((window.location.hash || '').startsWith('#/diff')) {
        filterDiffItems('');
      } else if ((window.location.hash || '').startsWith('#/prs')) {
        filterPRSidebar('');
      } else {
        route();
      }
    } else {
      history.back();
    }
    return;
  }

  if (isTyping) return;

  if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'j' || e.key === 'k') {
    e.preventDefault();
    const rows = document.querySelectorAll('.list-item');
    if (rows.length === 0) return;
    let currentIdx = -1;
    rows.forEach((row, i) => {
      if (row.classList.contains('active')) currentIdx = i;
    });
    let nextIdx;
    if (e.key === 'ArrowDown' || e.key === 'j') {
      nextIdx = currentIdx < rows.length - 1 ? currentIdx + 1 : 0;
    } else {
      nextIdx = currentIdx > 0 ? currentIdx - 1 : rows.length - 1;
    }
    rows[nextIdx].click();
    rows[nextIdx].scrollIntoView({ block: 'nearest' });
    return;
  }

  if (e.key === 'Enter') {
    const active = document.querySelector('.list-item.active');
    if (active) active.click();
    return;
  }

  if (e.key === 'h' || e.key === 'ArrowLeft') {
    const hash = window.location.hash || '';
    if (hash.startsWith('#/type/') || hash.startsWith('#/endpoint/')) {
      history.back();
    }
    return;
  }
});

/* ── Window Bindings (for inline onclick handlers) ─────────── */

window.navigate = navigate;
window.route = route;
window.onSearchInput = onSearchInput;
window.closeSkillModal = closeSkillModal;
window.viewSkill = viewSkill;
window.copySkill = copySkill;
window.setTypeFilter = setTypeFilter;
window.setEpFilter = setEpFilter;
window.typeFilterBtn = typeFilterBtn;
window.epFilterBtn = epFilterBtn;
window.copyCurl = copyCurl;
window.rebuildCurl = rebuildCurl;
window.buildCurlFromInputs = buildCurlFromInputs;
window.switchForkTab = switchForkTab;
window.toggleForkDiff = toggleForkDiff;
window.togglePRSpecGroup = togglePRSpecGroup;
window.filterPRSidebar = filterPRSidebar;
window.togglePRPreview = togglePRPreview;
window.findPRsForType = findPRsForType;
window.toggleDiffPreview = toggleDiffPreview;
window.diffUpdateFrom = diffUpdateFrom;
window.navigateDiff = navigateDiff;

/* ── Boot ──────────────────────────────────────────────────── */

init();

// Close hamburger menu on nav click (mobile)
document.querySelectorAll('.nav-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelector('.nav-tabs')?.classList.remove('open');
    document.getElementById('hamburger')?.classList.remove('open');
  });
});

