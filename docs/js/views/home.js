import { state } from '../state.js';
import { sortForks } from '../forks.js';
import { esc, specBadge, forkBadge } from '../utils.js';

export function renderSpecsOverview(container) {
  const meta = state.catalog._meta;
  let totalItems = 0, totalEps = 0, totalConst = 0, totalAliases = 0;
  for (const [k,v] of Object.entries(meta.specs)) {
    totalItems += v.items;
    totalEps += v.endpoints;
    totalConst += v.constants;
    totalAliases += v.type_aliases;
  }

  let html = '<div class="panel-full">';

  // Hero
  html += '<h1 style="font-size:24px;margin-bottom:8px;font-family:var(--font-mono)"><img src="logo.svg" alt="" style="height:28px;vertical-align:middle;margin-right:6px"> The <span style="color:var(--accent)">Ethspectoor</span></h1>';
  html += '<p style="color:var(--text-muted);margin-bottom:20px;font-size:14px">Deterministic extraction and exploration of all Ethereum specification data.</p>';

  // Stats bar
  html += '<div class="overview-stats">';
  html += '<div class="overview-stat"><div class="val">' + Object.keys(meta.specs).length + '</div><div class="label">Specs</div></div>';
  html += '<div class="overview-stat"><div class="val">' + totalItems + '</div><div class="label">Types</div></div>';
  html += '<div class="overview-stat"><div class="val">' + totalEps + '</div><div class="label">Endpoints</div></div>';
  html += '<div class="overview-stat"><div class="val">' + totalConst + '</div><div class="label">Constants</div></div>';
  html += '<div class="overview-stat"><div class="val">' + totalAliases + '</div><div class="label">Type Aliases</div></div>';
  const eipCount = Object.keys(state.catalog.eip_index || {}).length;
  if (eipCount > 0) {
    html += '<div class="overview-stat" style="cursor:pointer" onclick="navigate(\'#/eips\')"><div class="val">' + eipCount + '</div><div class="label">EIPs</div></div>';
  }
  html += '</div>';

  const SPEC_DESC = {
    'consensus-specs': 'The core protocol: fork choice, state transitions, validator duties, and the beacon chain mechanics that keep Ethereum in consensus.',
    'execution-specs': 'Execution layer logic: EVM opcodes, state trie, transaction processing, and the rules that turn transactions into state changes.',
    'execution-apis': 'JSON-RPC for wallets and nodes, plus the Engine API that drives block building between consensus and execution clients.',
    'beacon-apis': 'The REST API exposed by beacon nodes. Used by validators, tooling, and the consensus client itself to query chain state and submit duties.',
    'builder-specs': 'The proposer-builder separation API. Defines how validators request execution payload headers from builders via a sidecar like mev-boost.',
    'relay-specs': 'The relay API that sits between builders and proposers. Handles block submission, bid delivery, and payload revelation.',
    'remote-signing-api': 'The API for delegating validator signing to a remote signer (Web3Signer, Dirk). Keeps keys off the beacon node.'
  };

  // Spec Cards
  html += '<div class="cards-grid">';
  for (const [specName, specData] of Object.entries(state.catalog.specs)) {
    const m = specData.meta || {};
    const sm = state.catalog._meta.specs[specName] || {};
    const forkCount = sortForks(m.fork_order || []).length;
    html += '<div class="spec-card" onclick="navigate(\'#/types?spec=' + encodeURIComponent(specName) + '\')">';
    html += '<h3>' + specBadge(specName) + ' ' + esc(specName) + '</h3>';
    if (SPEC_DESC[specName]) html += '<p class="spec-desc">' + SPEC_DESC[specName] + '</p>';
    html += '<div class="stats">';
    html += '<div class="stat"><strong>' + (sm.items || 0) + '</strong>Types</div>';
    html += '<div class="stat"><strong>' + (sm.endpoints || 0) + '</strong>Endpoints</div>';
    html += '<div class="stat"><strong>' + (sm.constants || 0) + '</strong>Constants</div>';
    html += '<div class="stat"><strong>' + forkCount + '</strong>Forks</div>';
    html += '</div>';
    if (m.fork_order && m.fork_order.length) {
      html += '<div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:3px">';
      sortForks(m.fork_order || []).forEach(f => { html += forkBadge(f); });
      html += '</div>';
    }
    html += '</div>';
  }
  html += '</div>';

  // MCP Server
  html += '<div class="home-section">';
  html += '<h2>MCP Server</h2>';
  html += '<p>The Ethspectoor includes a Model Context Protocol (MCP) server so AI agents (Claude, Cursor, Windsurf, etc.) can query Ethereum spec data programmatically. Connect it to any MCP-compatible client and your agent gets direct access to the full spec index.</p>';
  html += '<div class="tool-grid">';
  var tools = [
    ['lookup_type', 'Get full definition of a type by name and fork'],
    ['diff_type', 'Compare a type between two forks'],
    ['what_changed', 'List all type changes between two forks'],
    ['search', 'Full-text search across all specs and types'],
    ['trace_type', 'Get the full dependency tree of a type'],
    ['lookup_endpoint', 'Get details of an API endpoint by path'],
    ['list_specs', 'List all indexed spec repos and their forks'],
    ['list_prs', 'List open PRs indexed against spec repos'],
    ['index_pr', 'Index a specific PR to see what it changes']
  ];
  tools.forEach(function(t) {
    html += '<div class="tool-item"><div class="tool-name">' + t[0] + '</div><div class="tool-desc">' + t[1] + '</div></div>';
  });
  html += '</div></div>';

  // Setup
  html += '<div class="home-section">';
  html += '<h2>Setup</h2>';
  html += '<p>Requires Python 3.10+ and git. One command builds everything.</p>';
  html += '<pre>git clone https://github.com/JasonVranek/ethspectoor\ncd ethspectoor\n\n# Install dependencies\npip install pyyaml mcp\n\n# Build everything: clones all 7 spec repos, extracts, links, builds catalog\npython3 build.py --all\n\n# Open the explorer\nopen docs/index.html</pre>';
  html += '<p>That\'s it. <code>build.py --all</code> clones all spec repos to <code>./repos/specs/</code>, builds per-spec indexes, runs cross-reference linking, and assembles the catalog. First run takes a few minutes to clone. Subsequent runs pull updates and rebuild.</p>';
  html += '<h3>MCP Server</h3>';
  html += '<p>Start the MCP server for AI agent integration:</p>';
  html += '<pre># Start the server (stdio transport)\npython3 server.py --catalog docs/catalog.json</pre>';
  html += '<p>For MCP client configuration (Claude Desktop, Hermes, Cursor), add to your config:</p>';
  html += '<pre>mcp:\n  ethspectoor:\n    command: "uv"\n    args:\n      - "run"\n      - "--with"\n      - "mcp"\n      - "--with"\n      - "pyyaml"\n      - "python3"\n      - "/path/to/ethspectoor/server.py"\n      - "--catalog"\n      - "/path/to/ethspectoor/docs/catalog.json"</pre>';
  html += '<h3>Including PR Data</h3>';
  html += '<p>To track open pull requests against spec repos (requires a <code>GITHUB_TOKEN</code>):</p>';
  html += '<pre>GITHUB_TOKEN=*** python3 build.py --all --include-prs</pre>';
  html += '</div>';

  // Agent Skill
  html += '<div class="home-section">';
  html += '<h2>Agent Skill</h2>';
  html += '<p>Teach your AI agent how to use the Ethspectoor MCP effectively. This skill document covers tool workflows, query patterns, and the mental model for navigating Ethereum specs.</p>';
  html += '<div class="skill-card"><div class="skill-card-header"><div>';
  html += '<div class="skill-card-title">Ethspectoor MCP Skill</div>';
  html += '<div class="skill-card-desc">Complete guide for AI agents: 10 tools, 5 workflow patterns, spec navigation tips</div>';
  html += '</div><div class="skill-card-actions">';
  html += '<button class="skill-btn" onclick="viewSkill()">View</button>';
  html += '<button class="skill-btn skill-btn-copy" onclick="copySkill(this)">Copy to Clipboard</button>';
  html += '</div></div></div>';
  html += '</div>';

  // GitHub link
  html += '<div class="home-section">';
  html += '<h2>Source</h2>';
  html += '<a class="home-github-link" href="https://github.com/JasonVranek/ethspectoor" target="_blank" rel="noopener">';
  html += '<svg width="20" height="20" viewBox="0 0 16 16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>';
  html += 'github.com/JasonVranek/ethspectoor';
  html += '</a>';
  html += '</div>';

  html += '</div>';
  container.innerHTML = html;
}
