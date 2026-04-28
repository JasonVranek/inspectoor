import { state } from '../state.js';
import { sortForks, sortForksRaw, getCodeForFork, itemExistsInFork } from '../forks.js';
import { esc, safeId, codePreview, kindBadge, specBadge, forkBadge, typeLink, githubBtn, resolveTypeSpec, highlightPython } from '../utils.js';
import { fuzzyScore, fuzzyHighlight } from '../search.js';
import { computeLineDiff, renderDiffCodeBlocks } from '../diff.js';
import { findPRsForType } from './prs.js';
import { ALL_FORK_ORDER, HIDDEN_FORKS, SPEC_COLORS, KIND_BADGES } from '../constants.js';

export function setTypeFilter(key, value) {
  state.typeFilters[key] = value;
  // Re-render in place (keep current selection if any)
  window.route();
}

export function renderTypeBrowser(container, params, selected) {
  // Apply URL params to filters only when first entering types view (not from detail nav)
  if (params.spec !== undefined && !selected) {
    if (params.spec) state.typeFilters.spec = params.spec;
    if (params.kind) state.typeFilters.kind = params.kind;
    if (params.domain) state.typeFilters.domain = params.domain;
    if (params.fork) state.typeFilters.fork = params.fork;
  }

  const { spec: filterSpec, kind: filterKind, domain: filterDomain, fork: filterFork } = state.typeFilters;

  // Collect filter facets
  const specs = {}, kinds = {}, domains = {}, forkCounts = {};
  state.allItems.forEach(item => {
    // Count in each spec this item belongs to
    if (item.specs) item.specs.forEach(s => { specs[s] = (specs[s]||0) + 1; });
    if (item.kind) kinds[item.kind] = (kinds[item.kind]||0) + 1;
    if (item.domain) domains[item.domain] = (domains[item.domain]||0) + 1;
    // Count forks where this item was actually introduced or modified
    if (item.forks) {
      for (const [f, fd] of Object.entries(item.forks)) {
        if (HIDDEN_FORKS.has(f)) continue;
        if (fd.is_new || fd.is_modified) {
          forkCounts[f] = (forkCounts[f] || 0) + 1;
        }
      }
    }
  });

  // Filter
  let filtered = state.allItems.filter(item => {
    if (filterSpec && (!item.specs || !item.specs.includes(filterSpec))) return false;
    if (filterKind && item.kind !== filterKind) return false;
    if (filterDomain && item.domain !== filterDomain) return false;
    if (filterFork && !itemExistsInFork(item, filterFork)) return false;
    if (state.searchQuery && fuzzyScore(item.name, state.searchQuery) === null) return false;
    return true;
  });

  if (state.searchQuery) {
    filtered.sort((a, b) => fuzzyScore(b.name, state.searchQuery) - fuzzyScore(a.name, state.searchQuery));
  } else {
    filtered.sort((a,b) => a.name.localeCompare(b.name));
  }

  // Sidebar
  let sideHtml = '';
  sideHtml += '<div class="sidebar-section"><h3>Specs</h3>';
  sideHtml += typeFilterBtn('spec', '', 'All specs', state.allItems.length, filterSpec === '');
  for (const [s, c] of Object.entries(specs).sort((a,b)=>b[1]-a[1])) {
    sideHtml += typeFilterBtn('spec', s, s, c, filterSpec === s);
  }
  sideHtml += '</div>';
  sideHtml += '<div class="sidebar-section"><h3>Kind</h3>';
  sideHtml += typeFilterBtn('kind', '', 'All kinds', '', filterKind === '');
  for (const [k, c] of Object.entries(kinds).sort((a,b)=>b[1]-a[1])) {
    sideHtml += typeFilterBtn('kind', k, k, c, filterKind === k);
  }
  sideHtml += '</div>';
  sideHtml += '<div class="sidebar-section"><h3>Fork</h3>';
  sideHtml += typeFilterBtn('fork', '', 'All forks', state.allItems.length, filterFork === '');
  for (const f of sortForks(Object.keys(forkCounts))) {
    sideHtml += typeFilterBtn('fork', f, f, forkCounts[f], filterFork === f);
  }
  sideHtml += '</div>';
  sideHtml += '<div class="sidebar-section"><h3>Domain</h3>';
  sideHtml += typeFilterBtn('domain', '', 'All domains', '', filterDomain === '');
  for (const [d, c] of Object.entries(domains).sort((a,b)=>b[1]-a[1])) {
    sideHtml += typeFilterBtn('domain', d, d, c, filterDomain === d);
  }
  sideHtml += '</div>';

  // List
  const selName = selected ? selected.name : null;
  let listHtml = '<div class="list-header"><span>' + filtered.length + ' types</span></div>';
  const renderCount = Math.min(filtered.length, 500);
  for (let i = 0; i < renderCount; i++) {
    const item = filtered[i];
    const isActive = (item.name === selName);
    listHtml += '<div class="list-item' + (isActive ? ' active' : '') + '" onclick="navigate(\'#/type/' +
      encodeURIComponent(item.name) + '\')">' +
      '<span class="item-name">' + (state.searchQuery ? fuzzyHighlight(item.name, state.searchQuery) : esc(item.name)) + '</span>' +
      '<span class="item-meta">' + kindBadge(item.kind) + specBadge(item.spec) +
      (item.specs && item.specs.length > 1 ? '<span style="font-size:9px;color:var(--text-dim)">+' + (item.specs.length-1) + '</span>' : '') +
      '</span></div>';
  }
  if (filtered.length > renderCount) {
    listHtml += '<div style="padding:12px;color:var(--text-dim);font-size:12px;text-align:center">… and ' + (filtered.length - renderCount) + ' more. Use search to narrow.</div>';
  }

  // Detail
  let detailHtml = selected
    ? renderTypeDetailContent(null, selected.name, filterFork || null)
    : '<div class="empty-state"><div class="icon">📋</div><div>Select a type to view details</div></div>';

  container.innerHTML =
    '<div class="panel-sidebar">' + sideHtml + '</div>' +
    '<div class="panel-list">' + listHtml + '</div>' +
    '<div class="panel-detail">' + detailHtml + '</div>';

  if (selected) {
    const activeRow = container.querySelector('.list-item.active');
    if (activeRow) activeRow.scrollIntoView({ block: 'nearest' });
  }
}

export function typeFilterBtn(key, value, label, count, active) {
  return '<button class="filter-btn' + (active ? ' active' : '') + '" onclick="setTypeFilter(\'' + key + '\', \'' + esc(value) + '\')">' +
    '<span>' + esc(label) + '</span>' +
    (count ? '<span class="count">' + count + '</span>' : '') +
    '</button>';
}



/* VIEW 3: TYPE DETAIL */

export function renderTypeDetailContent(selectedSpec, name, preferredFork = null) {
  // Items are unified in state.catalog.items (already merged across specs)
  const item = state.catalog.items[name];
  if (!item) {
    return '<div class="empty-state"><div class="icon">❓</div><div>Type not found: ' + esc(name) + '</div></div>';
  }
  const spec = item.spec;
  const forkNames = Object.keys(item.forks);
  const latestFork = forkNames[forkNames.length - 1];

  let html = '<div class="detail-content">';

  // Header
  html += '<div class="detail-header">';
  html += '<h2>' + esc(item.name) + '</h2>';
  html += '<div class="badges">';
  html += kindBadge(item.kind);
  // Show all specs this type appears in
  if (item.specs) item.specs.forEach(s => { html += specBadge(s); });
  if (item.domain) html += '<span class="badge-spec">' + esc(item.domain) + '</span>';
  if (item.introduced) html += forkBadge(item.introduced, 'new');
  html += '</div></div>';

  // PRs affecting this type
  const affectingPRs = findPRsForType(name);
  if (affectingPRs.length) {
    html += '<div class="pr-type-notice">';
    html += '<div style="font-size:11px;color:var(--text-muted);margin-bottom:4px">PRs affecting this type:</div>';
    affectingPRs.forEach(pr => {
      const actionColor = pr.action === 'added' ? 'var(--green)' : pr.action === 'removed' ? 'var(--red)' : 'var(--orange)';
      html += '<div style="margin-top:2px">';
      html += '<a href="#/prs?pr=' + pr.number + '&item=' + encodeURIComponent(name) + '" style="color:var(--accent)">';
      html += 'PR #' + pr.number + ': ' + esc(pr.title);
      html += '</a>';
      html += ' <span style="color:' + actionColor + ';font-size:11px">(' + esc(pr.summary) + ')</span>';
      html += '</div>';
    });
    html += '</div>';
  }

  // GitHub + EIPs row
  const latestData = item.forks[latestFork] || {};
  html += '<div style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;align-items:center">';
  html += githubBtn(latestData.github_url, 'source-btn');
  if (latestData.eips && latestData.eips.length) {
    latestData.eips.forEach(eip => {
      const num = String(eip).replace(/^eip-?/i, '');
      html += '<a href="https://eips.ethereum.org/EIPS/eip-' + num + '" target="_blank" rel="noopener" class="badge" style="background:rgba(88,166,255,0.1);color:var(--accent)">EIP-' + esc(num) + '</a>';
    });
  }
  html += '</div>';

  // Fork code tabs (shown first -- the main content) -- use item (may be from a different spec if this spec has no code)
  // Build fork tab list: only show forks where something actually changed
  function fieldsSignature(fields) {
    if (!fields || !fields.length) return '';
    return fields.map(f => f.name + ':' + f.type).join('|');
  }
  function hasDiff(item, fork, allSorted) {
    const fd = item.forks[fork];
    if (!fd) return false;
    if (fd.code || fd.is_new || fd.is_modified) return true;
    // If this fork only has fields, check if they differ from the previous fork
    if (fd.fields && fd.fields.length) {
      const idx = allSorted.indexOf(fork);
      if (idx <= 0) return true; // first fork with fields = always show
      // Walk backward to find previous fork with fields
      for (let i = idx - 1; i >= 0; i--) {
        const prev = item.forks[allSorted[i]];
        if (prev && prev.fields && prev.fields.length) {
          return fieldsSignature(fd.fields) !== fieldsSignature(prev.fields);
        }
      }
      return true; // no previous fork with fields found
    }
    return false;
  }
  const allSortedRaw = sortForksRaw(Object.keys(item.forks));
  let codeForkNames = sortForks(Object.keys(item.forks)).filter(f => hasDiff(item, f, allSortedRaw));
  // If all forks were filtered out, fall back to unfiltered (e.g. types only in 'unversioned')
  if (codeForkNames.length === 0) {
    codeForkNames = allSortedRaw.filter(f => hasDiff(item, f, allSortedRaw));
  }
  // Last resort: show the last fork that has any content at all
  if (codeForkNames.length === 0) {
    const lastWithContent = allSortedRaw.filter(f => {
      const fd = item.forks[f];
      return fd && (fd.code || fd.fields || fd.is_new);
    });
    if (lastWithContent.length) codeForkNames = [lastWithContent[lastWithContent.length - 1]];
  }
  const hasCode = codeForkNames.some(f => getCodeForFork(item, f));

  if (hasCode && codeForkNames.length > 0) {
    const tabId = 'fork-tabs-' + safeId(name);

    // Determine which fork tab to activate by default
    let activeFork = codeForkNames[codeForkNames.length - 1];
    if (preferredFork) {
      if (codeForkNames.includes(preferredFork)) {
        activeFork = preferredFork;
      } else {
        const prefIdx = ALL_FORK_ORDER.indexOf(preferredFork);
        if (prefIdx !== -1) {
          for (let i = codeForkNames.length - 1; i >= 0; i--) {
            const fIdx = ALL_FORK_ORDER.indexOf(codeForkNames[i]);
            if (fIdx !== -1 && fIdx <= prefIdx) {
              activeFork = codeForkNames[i];
              break;
            }
          }
        }
      }
    }

    html += '<div class="detail-section"><h3>Definition' +
      '' +
      '</h3>';

    // Fork tab pills
    html += '<div class="pill-group" style="margin-bottom:8px">';
    codeForkNames.forEach((f, i) => {
      let cls = '';
      const fd = item.forks[f];
      if (fd.is_new) cls = 'new';
      else if (fd.is_modified) cls = 'modified';
      const isActive = f === activeFork;
      html += '<button class="badge-fork ' + cls + '" style="cursor:pointer;' + (isActive ? 'border-color:var(--accent);color:var(--accent)' : '') + '" onclick="switchForkTab(\'' + esc(tabId) + '\', \'' + esc(f) + '\', this)">' + esc(f) + '</button>';
    });
    html += '</div>';

    // Code blocks (one per fork, active fork visible initially)
    codeForkNames.forEach((f, i) => {
      const code = getCodeForFork(item, f);
      const fd = item.forks[f];
      const isActive = f === activeFork;
      const forkGithubUrl = (item.forks[f] && item.forks[f].github_url) || '';
      // Find previous fork's code for diffing
      const prevFork = i > 0 ? codeForkNames[i - 1] : null;
      const prevCode = prevFork ? getCodeForFork(item, prevFork) : null;
      const hasDiffable = prevCode && code && prevCode !== code;
      const diffBtnId = 'diff-btn-' + safeId(name) + '-' + safeId(f);
      const codeViewId = 'code-view-' + safeId(name) + '-' + safeId(f);
      const diffViewId = 'diff-view-' + safeId(name) + '-' + safeId(f);

      html += '<div class="fork-code-block" data-tabs="' + esc(tabId) + '" data-fork="' + esc(f) + '" data-github-url="' + esc(forkGithubUrl) + '" style="' + (isActive ? '' : 'display:none') + '">';

      if (hasDiffable) {
        html += '<div style="display:flex;justify-content:flex-end;margin-bottom:6px">';
        html += '<button id="' + diffBtnId + '" class="fork-diff-toggle" onclick="toggleForkDiff(\'' + esc(codeViewId) + '\', \'' + esc(diffViewId) + '\', this)">';
        html += 'diff vs ' + esc(prevFork);
        html += '</button>';
        html += '</div>';
      }

      if (code) {
        html += '<div id="' + codeViewId + '"><pre class="code-block" style="max-height:600px">' + highlightPython(code) + '</pre></div>';
        if (hasDiffable) {
          html += '<div id="' + diffViewId + '" style="display:none">' + renderDiffCodeBlocks(prevCode, code, esc(prevFork), esc(f)) + '</div>';
        }
      } else {
        html += '<div style="padding:12px;color:var(--text-dim);font-size:12px">No definition in this fork.</div>';
      }
      html += '</div>';
    });
    html += '</div>';
  } else if (latestData.fields && latestData.fields.length) {
    // No code anywhere -- show fields table + pseudo-definition
    html += '<div class="detail-section"><h3>Definition <span style="font-size:10px;font-weight:400;text-transform:none;letter-spacing:0;color:var(--text-dim)">(from schema)</span></h3>';
    html += '<table class="data-table" style="margin-bottom:12px"><thead><tr><th>Name</th><th>Type</th><th>Description</th></tr></thead><tbody>';
    latestData.fields.forEach(f => {
      html += '<tr><td>' + esc(f.name) + '</td><td>' + typeLink(f.type, spec) + '</td><td style="font-family:var(--font-sans);font-size:12px;color:var(--text-muted)">' + esc(f.description || '') + '</td></tr>';
    });
    html += '</tbody></table>';
    let pseudo = 'class ' + name + '(Container):\n';
    latestData.fields.forEach(f => {
      pseudo += '    ' + f.name + ': ' + (f.type || 'unknown');
      if (f.description) pseudo += '  # ' + f.description;
      pseudo += '\n';
    });
    html += '<pre class="code-block">' + highlightPython(pseudo) + '</pre>';
    html += '</div>';
  }

  // Prose/description (after code so it doesn't push code below the fold)
  if (latestData.prose) {
    html += '<div class="detail-section"><h3>Description</h3><div class="prose" style="border-left:3px solid var(--cyan);padding-left:12px;font-size:13px;color:var(--text-muted);line-height:1.7;white-space:pre-wrap">' + esc(latestData.prose) + '</div></div>';
  }

  // Params + return (for functions, shown after definition)
  if (latestData.params && latestData.params.length) {
    html += '<div class="detail-section"><h3>Parameters</h3>';
    html += '<table class="data-table"><thead><tr><th>Name</th><th>Type</th></tr></thead><tbody>';
    latestData.params.forEach(p => {
      const pName = typeof p === 'string' ? p : (p.name || p);
      const pType = typeof p === 'object' ? (p.type || '') : '';
      html += '<tr><td>' + esc(pName) + '</td><td>' + typeLink(pType, spec) + '</td></tr>';
    });
    html += '</tbody></table></div>';
  }
  if (latestData.return_type) {
    html += '<div class="detail-section"><h3>Returns</h3><div>' + typeLink(latestData.return_type, spec) + '</div></div>';
  }

  // References
  if (latestData.references && latestData.references.length) {
    html += '<div class="detail-section"><h3>References</h3>';
    html += '<div class="pill-group">';
    latestData.references.forEach((ref, i) => { html += typeLink(ref, spec); if (i < latestData.references.length - 1) html += ', '; });
    html += '</div></div>';
  }

  html += '</div>';
  return html;
}

export function switchForkTab(tabId, fork, btn) {
  let githubUrl = '';
  document.querySelectorAll('.fork-code-block[data-tabs="' + tabId + '"]').forEach(el => {
    const show = el.dataset.fork === fork;
    el.style.display = show ? '' : 'none';
    if (show && el.dataset.githubUrl) githubUrl = el.dataset.githubUrl;
    if (show) {
      const diffToggle = el.querySelector('.fork-diff-toggle');
      const codeDiv = el.querySelector('[id^="code-view-"]');
      const diffDiv = el.querySelector('[id^="diff-view-"]');
      if (diffToggle) diffToggle.classList.remove('active');
      if (codeDiv) codeDiv.style.display = '';
      if (diffDiv) diffDiv.style.display = 'none';
    }
  });
  const sourceBtn = document.getElementById('source-btn');
  if (sourceBtn && githubUrl) {
    sourceBtn.href = githubUrl;
  }
  const parent = btn.parentElement;
  parent.querySelectorAll('.badge-fork').forEach(b => {
    b.style.borderColor = '';
    b.style.color = '';
  });
  btn.style.borderColor = 'var(--accent)';
  btn.style.color = 'var(--accent)';
}

export function toggleForkDiff(codeViewId, diffViewId, btn) {
  const codeView = document.getElementById(codeViewId);
  const diffView = document.getElementById(diffViewId);
  if (!codeView || !diffView) return;
  const showingDiff = diffView.style.display !== 'none';
  if (showingDiff) {
    codeView.style.display = '';
    diffView.style.display = 'none';
    btn.classList.remove('active');
  } else {
    codeView.style.display = 'none';
    diffView.style.display = '';
    btn.classList.add('active');
  }
}
