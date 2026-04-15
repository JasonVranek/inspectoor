import { state } from '../state.js';
import { sortForks, getCodeForFork } from '../forks.js';
import { esc, safeId, specBadge, kindBadge, forkBadge, typeLink, githubBtn, codePreview, highlightPython, resolveTypeSpec } from '../utils.js';
import { fuzzyScore, fuzzyHighlight } from '../search.js';
import { computeLineDiff, renderDiffCodeBlocks } from '../diff.js';

export function getAllPRs() {
  const prs = [];
  const overlays = state.catalog.pr_overlays || {};
  for (const spec in overlays) {
    for (const prId in overlays[spec]) {
      const ov = overlays[spec][prId];
      const items = ov.items_changed || {};
      const counts = { added: 0, modified: 0, removed: 0 };
      for (const name in items) {
        const a = items[name].action;
        if (counts[a] !== undefined) counts[a]++;
      }
      prs.push({
        spec,
        prId,
        number: ov.number,
        title: ov.title,
        author: ov.author,
        url: ov.url,
        itemCount: Object.keys(items).length,
        constCount: Object.keys(ov.constants_changed || {}).length,
        counts,
        overlay: ov
      });
    }
  }
  prs.sort((a, b) => b.number - a.number);
  return prs;
}

export function renderPRBrowser(container, params) {
  const allPRs = getAllPRs();
  const selectedPR = params.pr || '';
  const expandItem = params.item || '';

  if (!allPRs.length) {
    container.innerHTML = '<div class="panel-full"><div class="empty-state"><div class="icon">📋</div><div>No PR overlays indexed.</div><div style="font-size:12px;color:var(--text-dim)">Use the MCP <code>index_pr</code> tool to index a spec PR.</div></div></div>';
    return;
  }

  // Group PRs by spec, sort ascending within each group
  const specGroups = {};
  allPRs.forEach(pr => {
    if (!specGroups[pr.spec]) specGroups[pr.spec] = [];
    specGroups[pr.spec].push(pr);
  });
  const specNames = Object.keys(specGroups).sort();
  specNames.forEach(spec => {
    specGroups[spec].sort((a, b) => a.number - b.number);
  });

  // Auto-expand the group containing the selected PR, collapse others
  let activeSpec = null;
  if (selectedPR) {
    const activePR = allPRs.find(p => String(p.number) === String(selectedPR));
    if (activePR) activeSpec = activePR.spec;
  }

  let html = '<div class="panel-sidebar" style="width:300px;min-width:300px">';
  html += '<div style="padding:12px 14px;border-bottom:1px solid var(--border);font-size:12px;color:var(--text-muted)">';
  html += allPRs.length + ' indexed PR' + (allPRs.length !== 1 ? 's' : '');
  html += '</div>';

  specNames.forEach(spec => {
    const groupPRs = specGroups[spec];
    const isExpanded = activeSpec === spec || (!activeSpec && specNames.length === 1);
    const collapsedCls = isExpanded ? '' : ' collapsed';

    html += '<div class="pr-spec-group" data-spec="' + esc(spec) + '">';
    html += '<div class="pr-spec-group-header' + collapsedCls + '" onclick="togglePRSpecGroup(this)">';
    html += '<span class="pr-group-arrow">▼</span>';
    html += specBadge(spec);
    html += '<span class="pr-group-count">' + groupPRs.length + '</span>';
    html += '</div>';
    html += '<div class="pr-spec-group-items" style="display:' + (isExpanded ? 'block' : 'none') + '">';

    groupPRs.forEach(pr => {
      const isActive = String(pr.number) === String(selectedPR);
      html += '<div class="pr-sidebar-item' + (isActive ? ' active' : '') + '" data-pr-number="' + pr.number + '" data-pr-title="' + esc(pr.title) + '" onclick="navigate(\'#/prs?pr=' + pr.number + '\')">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center">';
      html += '<span class="pr-num">#' + pr.number + '</span>';
      html += '</div>';
      html += '<div class="pr-title">' + esc(pr.title) + '</div>';
      html += '<div class="pr-author">by ' + esc(pr.author) + '</div>';
      html += '<div class="pr-stats">';
      if (pr.counts.added) html += '<span class="pr-action-badge added">+' + pr.counts.added + '</span>';
      if (pr.counts.modified) html += '<span class="pr-action-badge modified">~' + pr.counts.modified + '</span>';
      if (pr.counts.removed) html += '<span class="pr-action-badge removed">-' + pr.counts.removed + '</span>';
      html += '</div>';
      html += '</div>';
    });

    html += '</div></div>';
  });
  html += '</div>';

  // Detail panel
  html += '<div class="panel-detail" style="flex:1;overflow-y:auto">';

  if (!selectedPR) {
    html += '<div class="empty-state"><div class="icon">👈</div><div>Select a PR to view changes</div></div>';
  } else {
    const pr = allPRs.find(p => String(p.number) === String(selectedPR));
    if (!pr) {
      html += '<div class="empty-state"><div class="icon">❓</div><div>PR #' + esc(selectedPR) + ' not found</div></div>';
    } else {
      html += renderPRDetail(pr, expandItem);
    }
  }

  html += '</div>';
  container.innerHTML = html;

  // Apply any active search filter
  if (state.searchQuery) filterPRSidebar(state.searchQuery);

  // Auto-expand item if specified
  if (expandItem && selectedPR) {
    setTimeout(function() {
      const itemEl = document.getElementById('pr-item-' + safeId(expandItem));
      if (itemEl) {
        itemEl.click();
        itemEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }, 50);
  }
}

export function togglePRSpecGroup(header) {
  const items = header.nextElementSibling;
  const isCollapsed = header.classList.contains('collapsed');
  if (isCollapsed) {
    header.classList.remove('collapsed');
    items.style.display = 'block';
  } else {
    header.classList.add('collapsed');
    items.style.display = 'none';
  }
}

export function filterPRSidebar(query) {
  const q = query.toLowerCase().trim();
  document.querySelectorAll('.pr-spec-group').forEach(group => {
    const items = group.querySelectorAll('.pr-sidebar-item');
    const header = group.querySelector('.pr-spec-group-header');
    const itemsContainer = group.querySelector('.pr-spec-group-items');
    let anyVisible = false;

    items.forEach(item => {
      const num = item.getAttribute('data-pr-number') || '';
      const title = (item.getAttribute('data-pr-title') || '').toLowerCase();
      const match = !q || num.includes(q) || title.includes(q) || ('#' + num).includes(q);
      item.style.display = match ? '' : 'none';
      if (match) anyVisible = true;
    });

    group.style.display = anyVisible ? '' : 'none';
    // Auto-expand groups with matches when searching
    if (q && anyVisible && header && itemsContainer) {
      header.classList.remove('collapsed');
      itemsContainer.style.display = 'block';
    }
  });

  // Update count display
  const countEl = document.querySelector('.panel-sidebar > div:first-child');
  if (countEl) {
    const total = document.querySelectorAll('.pr-sidebar-item').length;
    if (q) {
      const visible = document.querySelectorAll('.pr-sidebar-item').length -
        document.querySelectorAll('.pr-sidebar-item[style*="display: none"], .pr-sidebar-item[style*="display:none"]').length;
      countEl.textContent = visible + ' of ' + total + ' PR' + (total !== 1 ? 's' : '');
    } else {
      countEl.textContent = total + ' indexed PR' + (total !== 1 ? 's' : '');
    }
  }
}

export function renderPRDetail(pr, expandItem) {
  const ov = pr.overlay;
  const items = ov.items_changed || {};

  let html = '<div class="pr-header">';
  html += '<h2>PR #' + pr.number + ': ' + esc(pr.title) + '</h2>';
  html += '<div class="pr-meta">';
  html += 'by ' + esc(pr.author) + ' · ';
  html += specBadge(pr.spec) + ' · ';
  html += pr.itemCount + ' item' + (pr.itemCount !== 1 ? 's' : '');
  if (pr.constCount) html += ' · ' + pr.constCount + ' constant' + (pr.constCount !== 1 ? 's' : '');
  html += ' &nbsp;';
  html += '<a href="' + esc(pr.url) + '" target="_blank" rel="noopener" style="color:var(--accent)">View on GitHub ↗</a>';
  html += '</div></div>';

  // Group items by action
  const groups = { added: [], modified: [], removed: [] };
  for (const name in items) {
    const action = items[name].action;
    if (groups[action]) groups[action].push(name);
    else groups.modified.push(name); // fallback
  }
  // Sort each group alphabetically
  for (const g in groups) groups[g].sort();

  if (groups.added.length) {
    html += '<div class="diff-group">';
    html += '<h3 style="color:var(--green)">✚ Added <span class="count-badge" style="background:rgba(63,185,80,0.15);color:var(--green)">' + groups.added.length + '</span></h3>';
    groups.added.forEach(name => {
      html += renderPRItemRow(name, items[name], pr, 'added');
    });
    html += '</div>';
  }

  if (groups.modified.length) {
    html += '<div class="diff-group">';
    html += '<h3 style="color:var(--orange)">✎ Modified <span class="count-badge" style="background:rgba(210,153,34,0.15);color:var(--orange)">' + groups.modified.length + '</span></h3>';
    groups.modified.forEach(name => {
      html += renderPRItemRow(name, items[name], pr, 'modified');
    });
    html += '</div>';
  }

  if (groups.removed.length) {
    html += '<div class="diff-group">';
    html += '<h3 style="color:var(--red)">✖ Removed <span class="count-badge" style="background:rgba(248,81,73,0.15);color:var(--red)">' + groups.removed.length + '</span></h3>';
    groups.removed.forEach(name => {
      html += renderPRItemRow(name, items[name], pr, 'removed');
    });
    html += '</div>';
  }

  return html;
}

export function renderPRItemRow(name, item, pr, action) {
  const sid = safeId(name);
  const previewId = 'pr-preview-' + sid;
  const hasContent = item.code || (action === 'removed' && state.catalog.items[name]);

  let html = '<div class="pr-item-row" id="pr-item-' + sid + '"';
  if (hasContent) {
    html += ' onclick="togglePRPreview(this, \'' + esc(name) + '\', ' + pr.number + ', \'' + esc(pr.spec) + '\')"';
  }
  html += '>';

  // Action prefix
  const prefixMap = { added: '+', modified: '~', removed: '-' };
  const colorMap = { added: 'var(--green)', modified: 'var(--orange)', removed: 'var(--red)' };
  html += '<span style="color:' + colorMap[action] + ';font-family:var(--font-mono);width:14px;flex-shrink:0">' + prefixMap[action] + '</span>';

  // Name (strikethrough for removed)
  const nameStyle = action === 'removed' ? 'text-decoration:line-through;color:var(--red)' : '';
  html += '<span class="pr-item-name" style="' + nameStyle + '">' + esc(name) + '</span>';

  // Kind badge
  if (item.kind) html += kindBadge(item.kind);

  // Domain
  if (item.domain) html += '<span style="font-size:11px;color:var(--text-dim)">' + esc(item.domain) + '</span>';

  // Field delta for modified items
  if (action === 'modified' && item.diff_summary) {
    const ds = item.diff_summary;
    const parts = [];
    if (ds.fields_added && ds.fields_added.length) parts.push('+' + ds.fields_added.length + ' field');
    if (ds.fields_removed && ds.fields_removed.length) parts.push('-' + ds.fields_removed.length + ' field');
    if (ds.fields_modified && ds.fields_modified.length) parts.push('~' + ds.fields_modified.length + ' field');
    if (parts.length) {
      html += '<span class="pr-field-delta">' + parts.join(', ') + '</span>';
    }
  }

  // Expand indicator
  if (hasContent) html += '<span style="color:var(--text-dim);font-size:10px;margin-left:auto;flex-shrink:0">▶</span>';

  html += '</div>';
  html += '<div class="pr-item-preview" id="' + previewId + '"></div>';
  return html;
}

export function togglePRPreview(el, name, prNumber, specName) {
  const sid = safeId(name);
  const preview = document.getElementById('pr-preview-' + sid);
  if (!preview) return;

  if (preview.style.display !== 'none' && preview.style.display !== '') {
    preview.style.display = 'none';
    el.classList.remove('expanded');
    const arrow = el.querySelector('span:last-child');
    if (arrow && arrow.textContent === '▼') arrow.textContent = '▶';
    return;
  }

  el.classList.add('expanded');
  preview.style.display = 'block';
  const arrow = el.querySelector('span:last-child');
  if (arrow && arrow.textContent === '▶') arrow.textContent = '▼';

  // Get PR overlay data
  const overlays = state.catalog.pr_overlays || {};
  const specOverlays = overlays[specName] || {};
  const overlay = specOverlays[String(prNumber)];
  if (!overlay) { preview.innerHTML = '<div style="padding:8px;color:var(--text-dim)">Overlay not found.</div>'; return; }

  const prItem = overlay.items_changed[name];
  if (!prItem) { preview.innerHTML = '<div style="padding:8px;color:var(--text-dim)">Item not found in overlay.</div>'; return; }

  const action = prItem.action;
  let ph = '';

  // Meta line
  const meta = [];
  if (prItem.kind) meta.push(prItem.kind);
  if (prItem.domain) meta.push(prItem.domain);
  if (prItem.fork) meta.push(prItem.fork);
  const mainItem = state.catalog.items[name];
  ph += '<div style="font-size:11px;color:var(--text-muted);margin-bottom:8px">' + meta.join(' · ');
  if (mainItem) ph += ' <a href="#/type/' + encodeURIComponent(name) + '" style="color:var(--accent);font-size:10px" onclick="event.stopPropagation()">open type →</a>';
  if (prItem.github_url) ph += ' <a href="' + esc(prItem.github_url) + '" target="_blank" rel="noopener" style="color:var(--text-muted);font-size:10px" onclick="event.stopPropagation()">source ↗</a>';
  ph += '</div>';

  if (action === 'modified') {
    // Side-by-side code diff
    const prCode = prItem.code || '';
    const mainCode = mainItem ? getCodeForFork(mainItem, prItem.fork || '') : '';

    if (prCode && mainCode && prCode !== mainCode) {
      ph += renderDiffCodeBlocks(mainCode, prCode, 'mainline (' + esc(prItem.fork || '') + ')', 'PR #' + prNumber);
    } else if (prCode) {
      ph += '<pre class="code-block" style="max-height:400px;font-size:11px">' + highlightPython(prCode) + '</pre>';
    } else {
      ph += '<div style="padding:8px;color:var(--text-dim);font-size:12px">Code changed but no definition available.</div>';
    }

    // Field diff table
    const ds = prItem.diff_summary;
    if (ds && (ds.fields_added && ds.fields_added.length || ds.fields_removed && ds.fields_removed.length || ds.fields_modified && ds.fields_modified.length)) {
      ph += '<table class="data-table" style="margin-top:8px;font-size:12px"><thead><tr><th>Field</th><th>Change</th></tr></thead><tbody>';
      if (ds.fields_added) ds.fields_added.forEach(f => {
        ph += '<tr class="field-diff-added"><td style="color:var(--green)">+ ' + esc(f) + '</td><td>added</td></tr>';
      });
      if (ds.fields_removed) ds.fields_removed.forEach(f => {
        ph += '<tr class="field-diff-removed"><td style="color:var(--red)">- ' + esc(f) + '</td><td>removed</td></tr>';
      });
      if (ds.fields_modified) ds.fields_modified.forEach(f => {
        ph += '<tr><td style="color:var(--orange)">~ ' + esc(f) + '</td><td>modified</td></tr>';
      });
      ph += '</tbody></table>';
    }

  } else if (action === 'added') {
    // New item: single code block
    if (prItem.code) {
      ph += '<pre class="code-block" style="max-height:400px;font-size:11px">' + highlightPython(prItem.code) + '</pre>';
    }
    // Fields table
    if (prItem.fields && prItem.fields.length) {
      ph += '<table class="data-table" style="margin-top:8px;font-size:12px"><thead><tr><th>Field</th><th>Type</th></tr></thead><tbody>';
      prItem.fields.forEach(f => {
        ph += '<tr><td>' + esc(f.name) + '</td><td>' + typeLink(f.type, specName) + '</td></tr>';
      });
      ph += '</tbody></table>';
    }

  } else if (action === 'removed') {
    // Show mainline code being removed
    if (mainItem) {
      const removedCode = getCodeForFork(mainItem, prItem.fork || '');
      if (removedCode) {
        ph += '<div style="font-size:10px;color:var(--red);margin-bottom:4px">Removed definition:</div>';
        ph += '<pre class="code-block" style="max-height:400px;font-size:11px;opacity:0.7;border-color:rgba(248,81,73,0.3)">' + highlightPython(removedCode) + '</pre>';
      }
    } else {
      ph += '<div style="padding:8px;color:var(--text-dim);font-size:12px">Removed (no mainline definition available).</div>';
    }
  }

  // References
  if (prItem.references && prItem.references.length) {
    ph += '<div style="margin-top:8px;font-size:11px;color:var(--text-muted)">refs: ';
    prItem.references.forEach((ref, i) => {
      ph += typeLink(ref, specName);
      if (i < prItem.references.length - 1) ph += ', ';
    });
    ph += '</div>';
  }

  preview.innerHTML = ph;
}

export function findPRsForType(typeName) {
  const results = [];
  const overlays = state.catalog.pr_overlays || {};
  for (const spec in overlays) {
    for (const prId in overlays[spec]) {
      const ov = overlays[spec][prId];
      if (ov.items_changed && ov.items_changed[typeName]) {
        const item = ov.items_changed[typeName];
        const ds = item.diff_summary || {};
        let summary = item.action;
        if (ds.fields_added && ds.fields_added.length) summary += ', +' + ds.fields_added.length + ' field';
        if (ds.fields_removed && ds.fields_removed.length) summary += ', -' + ds.fields_removed.length + ' field';
        results.push({
          number: ov.number,
          title: ov.title,
          action: item.action,
          summary: summary,
          spec: spec
        });
      }
    }
  }
  return results;
}
