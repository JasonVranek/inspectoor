import { state } from '../state.js';
import { navigate } from '../router.js';
import { esc, highlightPython } from '../utils.js';
import { computeLineDiff } from '../diff.js';
import { ALL_FORK_ORDER } from '../constants.js';
import { getEipsByFork, getItemsBySpec, getUniqueForks } from '../eip-utils.js';
export { getEipsByFork, getItemsBySpec, getUniqueForks };

// Forks deployed on mainnet -- status badge is noise for these
const DEPLOYED_FORKS = new Set([
  'phase0','altair','bellatrix','capella','deneb','electra','fulu',
  'frontier','homestead','byzantium','constantinople','istanbul','berlin','london','paris','shanghai','cancun','prague','osaka'
]);

function filterEips(eipIndex, query) {
  if (!query) return eipIndex;
  const q = query.toLowerCase();
  const filtered = {};
  for (const [num, eip] of Object.entries(eipIndex)) {
    const haystack = [
      String(eip.number), `eip-${eip.number}`,
      eip.title || '', eip.authors || '',
    ].join(' ').toLowerCase();
    if (haystack.includes(q)) filtered[num] = eip;
  }
  return filtered;
}

function statusClass(status) {
  return status ? status.toLowerCase().replace(/\s+/g, '-') : '';
}

/**
 * Get the effective code for an item at a given fork.
 * Walks backwards through forks to find the most recent code (handles dedup).
 */
function getCodeAtFork(item, targetFork) {
  const forkKeys = Object.keys(item.forks);
  // Build ordered list using ALL_FORK_ORDER, then append unknowns
  const ordered = ALL_FORK_ORDER.filter(f => forkKeys.includes(f));
  for (const f of forkKeys) {
    if (!ordered.includes(f)) ordered.push(f);
  }
  let lastCode = '';
  for (const f of ordered) {
    const fd = item.forks[f];
    if (fd && fd.code) lastCode = fd.code;
    if (f === targetFork) return lastCode;
  }
  return lastCode;
}

/**
 * Get the code from the fork immediately before the target fork.
 */
function getCodeBeforeFork(item, targetFork) {
  const forkKeys = Object.keys(item.forks);
  const ordered = ALL_FORK_ORDER.filter(f => forkKeys.includes(f));
  for (const f of forkKeys) {
    if (!ordered.includes(f)) ordered.push(f);
  }
  let lastCode = '';
  for (const f of ordered) {
    if (f === targetFork) return lastCode;
    const fd = item.forks[f];
    if (fd && fd.code) lastCode = fd.code;
  }
  return lastCode;
}

/**
 * Render a compact unified diff (not side-by-side, saves space).
 */
function renderUnifiedDiff(oldCode, newCode) {
  if (!oldCode && newCode) {
    // Entirely new -- just show the code
    return `<pre class="code-block" style="font-size:11px;max-height:300px;overflow:auto;border-left:3px solid #4ade80">${highlightPython(newCode)}</pre>`;
  }
  if (!newCode) return '';
  
  const oldLines = oldCode.split('\n');
  const newLines = newCode.split('\n');
  const ops = computeLineDiff(oldLines, newLines);
  
  let html = '';
  for (const op of ops) {
    if (op.type === 'equal') {
      html += `<span class="diff-line-unchanged">${highlightPython(oldLines[op.oldIdx])}\n</span>`;
    } else if (op.type === 'remove') {
      html += `<span class="diff-line-removed">${highlightPython(oldLines[op.oldIdx])}\n</span>`;
    } else if (op.type === 'add') {
      html += `<span class="diff-line-added">${highlightPython(newLines[op.newIdx])}\n</span>`;
    } else if (op.type === 'change') {
      html += `<span class="diff-line-removed">${highlightPython(oldLines[op.oldIdx])}\n</span>`;
      html += `<span class="diff-line-added">${highlightPython(newLines[op.newIdx])}\n</span>`;
    }
  }
  return `<pre class="code-block diff-code-block" style="font-size:11px;max-height:300px;overflow:auto">${html}</pre>`;
}

export function renderEIPBrowser(container, params) {
  const eipIndex = state.catalog.eip_index || {};
  const filtered = filterEips(eipIndex, state.searchQuery);
  const byFork = getEipsByFork(filtered);
  const allForks = getUniqueForks(eipIndex);
  const activeFork = params.fork || null;
  const selectedEip = params.eip || null;

  // --- Sidebar ---
  const sidebar = document.createElement('div');
  sidebar.className = 'panel-sidebar eip-sidebar';
  const section = document.createElement('div');
  section.className = 'sidebar-section';
  const heading = document.createElement('h3');
  heading.textContent = 'Forks';
  section.appendChild(heading);

  const allBtn = document.createElement('button');
  allBtn.className = 'filter-btn' + (!activeFork ? ' active' : '');
  allBtn.innerHTML = `All <span class="count">${Object.keys(filtered).length}</span>`;
  allBtn.onclick = () => navigate('#/eips');
  section.appendChild(allBtn);

  for (const fork of allForks) {
    const filteredInFork = (getEipsByFork(filtered))[fork] || [];
    const btn = document.createElement('button');
    btn.className = 'filter-btn' + (activeFork === fork ? ' active' : '');
    btn.innerHTML = `${esc(fork)} <span class="count">${filteredInFork.length}</span>`;
    if (filteredInFork.length === 0 && activeFork !== fork) btn.style.opacity = '0.4';
    btn.onclick = () => navigate(`#/eips?fork=${fork}`);
    section.appendChild(btn);
  }
  sidebar.appendChild(section);

  // --- List ---
  const list = document.createElement('div');
  list.className = 'panel-list';
  const forksToShow = activeFork ? [activeFork] : allForks;
  let hasAny = false;

  for (const fork of forksToShow) {
    const eips = (getEipsByFork(filtered))[fork];
    if (!eips || eips.length === 0) continue;
    hasAny = true;
    if (!activeFork) {
      const header = document.createElement('div');
      header.className = 'fork-group-header';
      header.textContent = fork;
      list.appendChild(header);
    }
    for (const eip of eips) {
      const card = document.createElement('div');
      const isActive = selectedEip === String(eip.number);
      card.className = 'list-item' + (isActive ? ' active' : '');
      const title = eip.title || `EIP-${eip.number}`;
      const showBadge = !DEPLOYED_FORKS.has(eip.fork) && eip.status;
      const statusBadge = showBadge
        ? ` <span class="eip-status ${statusClass(eip.status)}">${esc(eip.status)}</span>` : '';
      card.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center">
          <strong>EIP-${eip.number}</strong>${statusBadge}
        </div>
        <div style="margin:4px 0;color:#ccc">${esc(title)}</div>
        <div style="font-size:0.8em;color:#888">
          ${eip.summary.total} items across ${eip.summary.specs.length} spec${eip.summary.specs.length > 1 ? 's' : ''}
        </div>`;
      card.onclick = () => {
        const fp = activeFork ? `&fork=${activeFork}` : '';
        navigate(`#/eips?eip=${eip.number}${fp}`);
      };
      list.appendChild(card);
    }
  }
  if (!hasAny) {
    const empty = document.createElement('div');
    empty.className = 'list-item';
    empty.style.color = '#888';
    empty.textContent = state.searchQuery ? 'No EIPs match your search.' : 'No EIPs found.';
    list.appendChild(empty);
  }

  // --- Detail ---
  const detail = document.createElement('div');
  detail.className = 'panel-detail';
  if (selectedEip && eipIndex[selectedEip]) {
    renderEIPDetail(detail, eipIndex[selectedEip]);
  } else {
    detail.innerHTML = '<div style="color:#888;padding:24px">Select an EIP to see details</div>';
  }

  container.innerHTML = '';
  container.appendChild(sidebar);
  container.appendChild(list);
  container.appendChild(detail);
}

function renderEIPDetail(detail, eip) {
  const title = eip.title || `EIP-${eip.number}`;
  const showBadge = !DEPLOYED_FORKS.has(eip.fork) && eip.status;
  const statusBadge = showBadge
    ? ` <span class="eip-status ${statusClass(eip.status)}">${esc(eip.status)}</span>` : '';

  // Back button (mobile)
  const backBtn = document.createElement('button');
  backBtn.className = 'back-btn';
  backBtn.textContent = '\u2190 All EIPs';
  backBtn.onclick = () => navigate('#/eips');
  detail.appendChild(backBtn);

  // Header
  const header = document.createElement('div');
  header.style.padding = '20px 20px 0';
  header.innerHTML = `
    <h2 style="margin:0 0 4px">EIP-${eip.number}${statusBadge}</h2>
    <div style="font-size:1.1em;color:#ccc;margin-bottom:8px">${esc(title)}</div>
    <div class="eip-meta">
      ${eip.authors ? `<span>Authors: ${esc(eip.authors)}</span>` : ''}
      ${eip.category ? `<span>Category: ${esc(eip.category)}</span>` : ''}
      ${eip.created ? `<span>Created: ${esc(eip.created)}</span>` : ''}
      <span>Fork: ${esc(eip.fork)}</span>
      <a href="${eip.url}" target="_blank" rel="noopener">eips.ethereum.org \u2197</a>
    </div>`;
  detail.appendChild(header);

  // Summary bar
  const summary = document.createElement('div');
  summary.className = 'eip-summary-bar';
  summary.innerHTML = `
    <div class="eip-summary-stat"><span class="eip-summary-num">${eip.summary.total}</span> items</div>
    ${eip.summary.new > 0 ? `<div class="eip-summary-stat"><span class="eip-summary-num eip-new">${eip.summary.new}</span> new</div>` : ''}
    ${eip.summary.modified > 0 ? `<div class="eip-summary-stat"><span class="eip-summary-num eip-mod">${eip.summary.modified}</span> modified</div>` : ''}
    <div class="eip-summary-stat"><span class="eip-summary-num">${eip.summary.specs.length}</span> spec${eip.summary.specs.length > 1 ? 's' : ''}</div>`;
  detail.appendChild(summary);

  // Items grouped by spec with inline diffs
  const bySpec = getItemsBySpec(eip);
  const itemsContainer = document.createElement('div');
  itemsContainer.style.padding = '0 20px 20px';

  for (const [spec, items] of Object.entries(bySpec)) {
    const section = document.createElement('div');
    section.className = 'eip-spec-section';
    const specHeader = document.createElement('h4');
    specHeader.textContent = spec;
    section.appendChild(specHeader);

    const kindOrder = { class: 0, dataclass: 0, def: 1 };
    const sorted = [...items].sort((a, b) => {
      if (a.change !== b.change) return a.change === 'new' ? -1 : 1;
      const ka = kindOrder[a.kind] ?? 2, kb = kindOrder[b.kind] ?? 2;
      if (ka !== kb) return ka - kb;
      return a.name.localeCompare(b.name);
    });

    for (const item of sorted) {
      const row = document.createElement('div');
      row.className = 'eip-item-row';
      const isNew = item.change === 'new';
      const indicator = isNew ? '+' : '~';
      const indicatorClass = isNew ? 'eip-new' : 'eip-mod';
      const kindBadge = (item.kind === 'def') ? '<span class="eip-kind-badge">fn</span>' : '';

      row.innerHTML = `<span class="${indicatorClass}">${indicator}</span> ${kindBadge}<span class="eip-item-name">${esc(item.name)}</span> <span class="eip-expand-hint">\u25B6</span>`;

      // Diff container (hidden by default)
      const diffContainer = document.createElement('div');
      diffContainer.className = 'eip-diff-container';
      diffContainer.style.display = 'none';
      let diffLoaded = false;

      row.onclick = () => {
        const isOpen = diffContainer.style.display !== 'none';
        if (isOpen) {
          diffContainer.style.display = 'none';
          row.querySelector('.eip-expand-hint').textContent = '\u25B6';
          return;
        }
        row.querySelector('.eip-expand-hint').textContent = '\u25BC';
        diffContainer.style.display = 'block';

        if (!diffLoaded) {
          diffLoaded = true;
          const catalogItem = state.catalog.items[item.name];
          if (!catalogItem) {
            diffContainer.innerHTML = '<div style="color:#888;padding:8px;font-size:12px">Item not found in catalog</div>';
            return;
          }
          const newCode = getCodeAtFork(catalogItem, eip.fork);
          const oldCode = getCodeBeforeFork(catalogItem, eip.fork);

          if (!newCode) {
            diffContainer.innerHTML = '<div style="color:#888;padding:8px;font-size:12px">No code available</div>';
          } else {
            const linkRow = document.createElement('div');
            linkRow.style.cssText = 'text-align:right;margin-bottom:4px';
            const link = document.createElement('a');
            link.href = `#/type/${encodeURIComponent(item.name)}?fork=${eip.fork}`;
            link.style.cssText = 'font-size:11px;color:var(--accent)';
            link.textContent = 'Open in Types \u2197';
            link.onclick = (e) => { e.stopPropagation(); };
            linkRow.appendChild(link);
            diffContainer.appendChild(linkRow);
            diffContainer.insertAdjacentHTML('beforeend', renderUnifiedDiff(oldCode, newCode));
          }
        }
      };

      section.appendChild(row);
      section.appendChild(diffContainer);
    }
    itemsContainer.appendChild(section);
  }
  detail.appendChild(itemsContainer);
}
