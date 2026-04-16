import { state } from './state.js';
import { parseParams } from './url.js';
import { renderSpecsOverview } from './views/home.js';
import { renderTypeBrowser } from './views/types.js';
import { renderEndpointBrowser } from './views/endpoints.js';
import { renderPRBrowser } from './views/prs.js';
import { renderDiffView } from './views/diff-view.js';
import { renderEIPBrowser } from './views/eips.js';

export function navigate(hash) {
  window.location.hash = hash;
}

// parseParams re-exported from url.js
export { parseParams };

export function route() {
  const hash = window.location.hash || '#/';
  const main = document.getElementById('main-content');
  const hasDetail = hash.startsWith('#/type/') || hash.startsWith('#/endpoint/') || (hash.startsWith('#/prs') && hash.includes('pr=')) || (hash.startsWith('#/eips') && hash.includes('eip='));
  main.className = 'main' + (hasDetail ? ' show-detail' : '');

  // Update nav tabs
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
  if (hash === '#/' || hash === '#/specs' || hash === '') {
    document.querySelector('[data-route="#/"]').classList.add('active');
  } else if (hash.startsWith('#/type') || hash.startsWith('#/types')) {
    document.querySelector('[data-route="#/types"]').classList.add('active');
  } else if (hash.startsWith('#/endpoint') || hash.startsWith('#/endpoints')) {
    document.querySelector('[data-route="#/endpoints"]').classList.add('active');
  } else if (hash.startsWith('#/diff')) {
    document.querySelector('[data-route="#/diff"]').classList.add('active');
  } else if (hash.startsWith('#/eips') || hash.startsWith('#/eip')) {
    document.querySelector('[data-route="#/eips"]').classList.add('active');
  } else if (hash.startsWith('#/prs')) {
    document.querySelector('[data-route="#/prs"]').classList.add('active');
  }

  // Update search placeholder per tab
  const searchInput = document.getElementById('search-input');
  if (hash.startsWith('#/eips')) {
    searchInput.placeholder = 'Search EIPs by title or number...  (/ to focus, Esc to clear)';
  } else if (hash.startsWith('#/prs')) {
    searchInput.placeholder = 'Search PRs by title or number...  (/ to focus, Esc to clear)';
  } else {
    searchInput.placeholder = 'Search types, endpoints...  (/ to focus, Esc to clear)';
  }

  // Sync search query with URL params
  const _params = parseParams(hash);
  if (_params.q) {
    state.searchQuery = decodeURIComponent(_params.q);
    const si = document.getElementById('search-input');
    if (si) si.value = state.searchQuery;
  } else if (!state.searchQuery) {
    // Clear input if state was already cleared (e.g. by onSearchInput(''))
    const si = document.getElementById('search-input');
    if (si && si.value) si.value = '';
  }

  // Parse route
  if (hash === '#/' || hash === '#/specs' || hash === '') {
    renderSpecsOverview(main);
  } else if (hash.startsWith('#/type/')) {
    const name = decodeURIComponent(hash.replace('#/type/', '').split('?')[0]);
    renderTypeBrowser(main, parseParams(hash), { name });
  } else if (hash.startsWith('#/types')) {
    renderTypeBrowser(main, parseParams(hash), null);
  } else if (hash.startsWith('#/endpoint/')) {
    const parts = hash.replace('#/endpoint/', '').split('/');
    const spec = decodeURIComponent(parts[0]);
    const name = decodeURIComponent(parts.slice(1).join('/'));
    renderEndpointBrowser(main, parseParams(hash), { spec, name });
  } else if (hash.startsWith('#/endpoints')) {
    renderEndpointBrowser(main, parseParams(hash), null);
  } else if (hash.startsWith('#/diff')) {
    renderDiffView(main, parseParams(hash));
  } else if (hash.startsWith('#/eips')) {
    renderEIPBrowser(main, parseParams(hash));
  } else if (hash.startsWith('#/prs')) {
    renderPRBrowser(main, parseParams(hash));
  } else {
    renderSpecsOverview(main);
  }
}
