import { state } from '../state.js';
import { parseParams } from '../url.js';
import { sortForks, sortForksRaw, getCodeForFork } from '../forks.js';
import { esc, safeId, specBadge, kindBadge, forkBadge, typeLink, highlightPython } from '../utils.js';
import { fuzzyScore, fuzzyHighlight } from '../search.js';
import { computeLineDiff, renderDiffCodeBlocks } from '../diff.js';

export function renderDiffView(container, params) {
  const specName = params.spec || Object.keys(state.catalog.specs)[0];
  const specData = state.catalog.specs[specName];
  const forkOrder = sortForks((specData && specData.meta && specData.meta.fork_order) || []);
  const fromFork = params.from || (forkOrder.length > 1 ? forkOrder[forkOrder.length - 2] : forkOrder[0] || '');
  const toFork = params.to || forkOrder[forkOrder.length - 1] || '';
  const fromIdx = forkOrder.indexOf(fromFork);

  let html = '<div class="panel-full">';
  html += '<h2 style="font-size:18px;margin-bottom:16px;font-family:var(--font-mono)">Fork Diff</h2>';

  // Controls
  html += '<div class="diff-controls">';
  html += '<label style="font-size:12px;color:var(--text-muted)">Spec:</label>';
  html += '<select onchange="navigateDiff(this.value, null, null)">';
  for (const s of Object.keys(state.catalog.specs)) {
    html += '<option value="' + esc(s) + '"' + (s === specName ? ' selected' : '') + '>' + esc(s) + '</option>';
  }
  html += '</select>';

  html += '<label style="font-size:12px;color:var(--text-muted)">From:</label>';
  html += '<select onchange="diffUpdateFrom(this.value)" id="diff-from">';
  forkOrder.forEach(f => {
    html += '<option value="' + esc(f) + '"' + (f === fromFork ? ' selected' : '') + '>' + esc(f) + '</option>';
  });
  html += '</select>';

  html += '<span style="color:var(--text-dim)">→</span>';

  html += '<label style="font-size:12px;color:var(--text-muted)">To:</label>';
  html += '<select onchange="navigateDiff(null, null, this.value)" id="diff-to">';
  forkOrder.forEach((f, i) => {
    const disabled = i <= fromIdx;
    html += '<option value="' + esc(f) + '"' + (f === toFork ? ' selected' : '') + (disabled ? ' disabled style="color:var(--text-dim)"' : '') + '>' + esc(f) + '</option>';
  });
  // Add PR forks as options
  const prOverlays = (state.catalog.pr_overlays || {})[specName] || {};
  const prIds = Object.keys(prOverlays);
  if (prIds.length) {
    html += '<option disabled>──── PRs ────</option>';
    prIds.forEach(prId => {
      const pov = prOverlays[prId];
      const prFork = 'pr-' + pov.number;
      const label = 'PR #' + pov.number + ': ' + (pov.title || '').slice(0, 40) + (pov.title && pov.title.length > 40 ? '...' : '');
      html += '<option value="' + esc(prFork) + '"' + (prFork === toFork ? ' selected' : '') + '>' + esc(label) + '</option>';
    });
  }
  html += '</select>';
  html += '</div>';

  // Compute diff
  // Handle PR forks
  if (toFork.startsWith('pr-')) {
    const prNum = toFork.slice(3);
    const prOv = ((state.catalog.pr_overlays || {})[specName] || {})[prNum];
    if (prOv && prOv.items_changed) {
      const prItems = prOv.items_changed;
      const groups = { added: [], modified: [], removed: [] };
      for (const n in prItems) {
        const a = prItems[n].action;
        if (groups[a]) groups[a].push(n);
      }
      for (const g in groups) groups[g].sort();

      html += '<div style="font-size:13px;color:var(--text-muted);margin-bottom:12px">';
      html += '<a href="' + esc(prOv.url || '') + '" target="_blank" rel="noopener" style="color:var(--accent)">PR #' + prNum + '</a>: ' + esc(prOv.title || '') + ' by ' + esc(prOv.author || '');
      html += '</div>';

      if (groups.added.length) {
        html += '<div class="diff-group"><h3 style="color:var(--green)">✚ Added <span class="count-badge" style="background:rgba(63,185,80,0.15);color:var(--green)">' + groups.added.length + '</span></h3>';
        groups.added.forEach(n => {
          const itemId = 'diff-preview-' + safeId(n);
          html += '<div class="diff-item" onclick="toggleDiffPreview(this, \'' + esc(n) + '\', \'' + esc(toFork) + '\', \'' + esc(specName) + '\', null)" style="color:var(--green)">+ ' + esc(n) + '</div>';
          html += '<div class="diff-item-preview" id="' + itemId + '" style="display:none"></div>';
        });
        html += '</div>';
      }
      if (groups.modified.length) {
        html += '<div class="diff-group"><h3 style="color:var(--orange)">✎ Modified <span class="count-badge" style="background:rgba(210,153,34,0.15);color:var(--orange)">' + groups.modified.length + '</span></h3>';
        groups.modified.forEach(n => {
          const itemId = 'diff-preview-' + safeId(n);
          html += '<div class="diff-item" onclick="toggleDiffPreview(this, \'' + esc(n) + '\', \'' + esc(toFork) + '\', \'' + esc(specName) + '\', \'' + esc(fromFork) + '\')" style="color:var(--orange)">~ ' + esc(n) + '</div>';
          html += '<div class="diff-item-preview" id="' + itemId + '" style="display:none"></div>';
        });
        html += '</div>';
      }
      if (groups.removed.length) {
        html += '<div class="diff-group"><h3 style="color:var(--red)">✖ Removed <span class="count-badge" style="background:rgba(248,81,73,0.15);color:var(--red)">' + groups.removed.length + '</span></h3>';
        groups.removed.forEach(n => {
          const itemId = 'diff-preview-' + safeId(n);
          html += '<div class="diff-item" style="color:var(--red);text-decoration:line-through">- ' + esc(n) + '</div>';
          html += '<div class="diff-item-preview" id="' + itemId + '" style="display:none"></div>';
        });
        html += '</div>';
      }

      if (!groups.added.length && !groups.modified.length && !groups.removed.length) {
        html += '<div style="color:var(--text-dim);font-size:13px;margin-top:20px">No changes in this PR.</div>';
      }
    } else {
      html += '<div style="color:var(--text-dim);font-size:13px;margin-top:20px">PR overlay not found.</div>';
    }
  } else {

  // Standard fork diff
  const forkSummary = specData && specData.fork_summary;
  if (forkSummary && forkSummary[toFork]) {
    const fs = forkSummary[toFork];
    const newItems = fs.new || [];
    const modifiedItems = fs.modified || [];
    const newMethods = fs.new_methods || [];

    if (newItems.length) {
      html += '<div class="diff-group">';
      html += '<h3 style="color:var(--green)">✚ New in ' + esc(toFork) + ' <span class="count-badge" style="background:rgba(63,185,80,0.15);color:var(--green)">' + newItems.length + '</span></h3>';
      newItems.forEach(name => {
        const itemId = 'diff-preview-' + safeId(name);
        html += '<div class="diff-item" onclick="toggleDiffPreview(this, \'' + esc(name) + '\', \'' + esc(toFork) + '\', \'' + esc(specName) + '\', null)" style="color:var(--green)">' +
          '+ ' + esc(name) + '</div>';
        html += '<div class="diff-item-preview" id="' + itemId + '" style="display:none"></div>';
      });
      html += '</div>';
    }

    if (modifiedItems.length) {
      html += '<div class="diff-group">';
      html += '<h3 style="color:var(--orange)">✎ Modified in ' + esc(toFork) + ' <span class="count-badge" style="background:rgba(210,153,34,0.15);color:var(--orange)">' + modifiedItems.length + '</span></h3>';
      modifiedItems.forEach(name => {
        const itemId = 'diff-preview-' + safeId(name);
        html += '<div class="diff-item" onclick="toggleDiffPreview(this, \'' + esc(name) + '\', \'' + esc(toFork) + '\', \'' + esc(specName) + '\', \'' + esc(fromFork) + '\')" style="color:var(--orange)">' +
          '~ ' + esc(name) + '</div>';
        html += '<div class="diff-item-preview" id="' + itemId + '" style="display:none"></div>';
      });
      html += '</div>';
    }

    if (newMethods && newMethods.length) {
      html += '<div class="diff-group">';
      html += '<h3 style="color:var(--cyan)">⚡ New Methods in ' + esc(toFork) + ' <span class="count-badge" style="background:rgba(57,210,192,0.15);color:var(--cyan)">' + newMethods.length + '</span></h3>';
      newMethods.forEach(name => {
        const itemId = 'diff-preview-' + safeId(name);
        html += '<div class="diff-item" onclick="toggleDiffPreview(this, \'' + esc(name) + '\', \'' + esc(toFork) + '\', \'' + esc(specName) + '\', null)" style="color:var(--cyan)">' +
          '+ ' + esc(name) + '</div>';
        html += '<div class="diff-item-preview" id="' + itemId + '" style="display:none"></div>';
      });
      html += '</div>';
    }

    if (!newItems.length && !modifiedItems.length && !newMethods.length) {
      html += '<div style="color:var(--text-dim);font-size:13px;margin-top:20px">No changes recorded for ' + esc(toFork) + ' in this spec.</div>';
    }

    // Also show EIPs for this fork if any
    if (fs.eips && fs.eips.length) {
      html += '<div class="diff-group">';
      html += '<h3 style="color:var(--accent)">📜 Related EIPs</h3>';
      html += '<div class="pill-group">';
      fs.eips.forEach(eip => {
        const num = String(eip).replace(/^eip-?/i, '');
        html += '<a href="https://eips.ethereum.org/EIPS/eip-' + num + '" target="_blank" class="badge" style="background:rgba(88,166,255,0.1);color:var(--accent)">EIP-' + esc(num) + '</a> ';
      });
      html += '</div></div>';
    }

  } else {
    html += '<div style="color:var(--text-dim);font-size:13px;margin-top:20px">No fork summary data available for this selection.</div>';
  }

  // Show all forks summary
  if (forkSummary) {
    html += '<div class="detail-section" style="margin-top:24px"><h3>All Forks Summary for ' + esc(specName) + '</h3>';
    html += '<table class="data-table"><thead><tr><th>Fork</th><th>New</th><th>Modified</th><th>Total Defs</th></tr></thead><tbody>';
    forkOrder.forEach(f => {
      const fs = forkSummary[f];
      if (fs) {
        html += '<tr onclick="navigate(\'#/diff/' + encodeURIComponent(specName) + '?spec=' + encodeURIComponent(specName) + '&to=' + encodeURIComponent(f) + '\')" style="cursor:pointer">' +
          '<td>' + forkBadge(f) + '</td>' +
          '<td style="color:var(--green)">' + (fs.new ? fs.new.length : 0) + '</td>' +
          '<td style="color:var(--orange)">' + (fs.modified ? fs.modified.length : 0) + '</td>' +
          '<td>' + (fs.total_definitions || '—') + '</td></tr>';
      }
    });
    html += '</tbody></table></div>';
  }

  html += '</div>';
  } // close PR else block
  container.innerHTML = html;
}


export function toggleDiffPreview(el, name, toFork, specName, fromFork) {
  const previewId = 'diff-preview-' + safeId(name);
  const preview = document.getElementById(previewId);
  if (!preview) return;

  // Toggle
  if (preview.style.display !== 'none') {
    preview.style.display = 'none';
    el.classList.remove('expanded');
    return;
  }

  el.classList.add('expanded');
  preview.style.display = 'block';

  // Build preview content
  const item = state.catalog.items[name];
  if (!item) {
    // Check if it's a PR-only item
    if (toFork && toFork.startsWith('pr-')) {
      const prNum = toFork.slice(3);
      const overlays = state.catalog.pr_overlays || {};
      for (const spec in overlays) {
        const ov = overlays[spec][prNum];
        if (ov && ov.items_changed && ov.items_changed[name] && ov.items_changed[name].code) {
          const prItem = ov.items_changed[name];
          let prHtml = '<div class="diff-preview-meta">' + (prItem.kind || '') + ' · ' + (prItem.domain || '') + '</div>';
          prHtml += '<pre class="code-block" style="max-height:400px;font-size:11px">' + highlightPython(prItem.code) + '</pre>';
          preview.innerHTML = prHtml;
          return;
        }
      }
    }
    preview.innerHTML = '<div style="padding:8px;color:var(--text-dim);font-size:12px">Type not found in catalog.</div>';
    return;
  }

  let ph = '';

  // Meta line: kind, spec, domain
  const meta = [];
  if (item.kind) meta.push(item.kind);
  if (item.domain) meta.push(item.domain);
  ph += '<div class="diff-preview-meta">' + meta.join(' · ') +
    ' <a href="#/type/' + encodeURIComponent(name) + '" style="color:var(--accent);margin-left:8px;font-size:10px" onclick="event.stopPropagation()">open full →</a></div>';

  // Code for the target fork
  const toCode = getCodeForFork(item, toFork);

  if (fromFork && toCode) {
    // Modified item: show from-code and to-code side by side or stacked
    const fromCode = getCodeForFork(item, fromFork);
    if (fromCode && fromCode !== toCode) {
      ph += renderDiffCodeBlocks(fromCode, toCode, esc(fromFork), esc(toFork));
    } else {
      // Same code or no from-code, just show the target
      ph += '<pre class="code-block" style="max-height:400px;font-size:11px">' + highlightPython(toCode) + '</pre>';
    }
  } else if (toCode) {
    // New item: just show the code
    ph += '<pre class="code-block" style="max-height:400px;font-size:11px">' + highlightPython(toCode) + '</pre>';
  } else {
    ph += '<div style="padding:8px;color:var(--text-dim);font-size:12px">No code definition available.</div>';
  }

  // References
  const latestForks = sortForksRaw(Object.keys(item.forks || {}));
  const latestFork = latestForks[latestForks.length - 1];
  const latestData = (latestFork && item.forks[latestFork]) || {};
  if (latestData.references && latestData.references.length) {
    ph += '<div class="diff-preview-refs">refs: ';
    latestData.references.forEach((ref, i) => {
      ph += typeLink(ref, specName);
      if (i < latestData.references.length - 1) ph += ', ';
    });
    ph += '</div>';
  }

  preview.innerHTML = ph;
}

export function diffUpdateFrom(fromVal) {
  // When "From" changes, constrain "To" and navigate
  const specName = parseParams(window.location.hash).spec || Object.keys(state.catalog.specs)[0];
  const specData = state.catalog.specs[specName];
  const forkOrder = sortForks((specData && specData.meta && specData.meta.fork_order) || []);
  const fromIdx = forkOrder.indexOf(fromVal);
  const toSel = document.getElementById('diff-to');
  let currentTo = toSel ? toSel.value : '';
  const currentToIdx = forkOrder.indexOf(currentTo);

  // If current "To" is at or before new "From", bump it to the next fork
  if (currentToIdx <= fromIdx) {
    currentTo = forkOrder[fromIdx + 1] || forkOrder[forkOrder.length - 1];
  }
  navigateDiff(null, fromVal, currentTo);
}

export function navigateDiff(spec, from, to) {
  const hash = window.location.hash;
  const params = parseParams(hash);
  if (spec !== null) params.spec = spec;
  if (from !== null) params.from = from;
  if (to !== null) params.to = to;
  let qs = Object.entries(params).map(([k,v]) => k + '=' + encodeURIComponent(v)).join('&');
  window.navigate('#/diff?' + qs);
}
