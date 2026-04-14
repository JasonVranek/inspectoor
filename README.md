# The Inspectoor

Deterministic extraction and exploration of Ethereum specification data. Parses
every spec repo (consensus, execution, builder, relay, beacon APIs, execution
APIs, remote signing) into structured indexes, then serves them over MCP and a
static explorer UI.

**986 types, 168 endpoints, 355 constants, 47 type aliases across 7 specs.**

## Explorer UI

Open `docs/index.html` in a browser. No build step, no dependencies.

- **Types** -- browse and search all types/functions/classes with fuzzy matching,
  fork-aware code display with syntax highlighting and clickable cross-references
- **Endpoints** -- REST and JSON-RPC endpoints with parameters, response types, and
  fork variants
- **Fork Diff** -- compare what changed between any two forks per spec, with inline
  side-by-side code previews
- **Visualizer** (`docs/visualizer.html`) -- interactive transaction lifecycle diagram
  showing how data flows between consensus, execution, builder, relay, and signer
  across 18 protocol endpoints

## MCP Server

```bash
# stdio transport (for agent integration)
python3 server.py

# custom catalog and repos directory (enables reindex)
python3 server.py --catalog docs/catalog.json --repos-dir ./repos

# rebuild everything before starting
python3 server.py --rebuild --repos-dir ./repos
```

8 tools: `list_specs`, `lookup_type`, `lookup_endpoint`, `what_changed`,
`trace_type`, `search`, `diff_type`, `reindex`.

Dependencies: `pyyaml`, `mcp`.

## Data Flow

Both the MCP server and the explorer UI read the same artifact: `catalog.json`.
Types that appear in multiple specs are merged with canonical-source attribution
(e.g. `BeaconState` resolves to consensus-specs, not beacon-apis). No drift
between what agents see and what the UI shows.

```
repos/ --> build.py --> indexes/ (per-spec, intermediate)
                            |
                        link.py --> _cross_refs.json
                            |
                    build_catalog.py --> catalog.json (canonical)
                                            |
                                    +-------+-------+
                                    |               |
                              server.py (MCP)   docs/ (UI)
```

Per-spec indexes under `indexes/` are intermediate build artifacts. The
canonical data lives in `docs/catalog.json`.

## Spec Coverage

| Spec | Items | Endpoints | Constants | Extractor | Forks |
|------|------:|----------:|----------:|-----------|-------|
| consensus-specs | 528 | -- | 218 | Python AST | phase0 through heze |
| execution-specs | 298 | -- | 135 | Python AST | frontier through amsterdam |
| execution-apis | 93 | 72 | -- | OpenRPC | paris through amsterdam |
| beacon-apis | 77 | 84 | -- | OpenAPI + Markdown | phase0 through gloas |
| remote-signing-api | 59 | 2 | -- | OpenAPI | phase0 through fulu |
| builder-specs | 16 | 5 | 2 | OpenAPI + Markdown | bellatrix through fulu |
| relay-specs | 12 | 5 | -- | OpenAPI + Markdown | bellatrix through fulu |

## Build

Requires local clones of the spec repos.

```bash
# fetch all spec repos
./fetch_repos.sh

# build individual spec indexes
python3 build.py --profile consensus-specs    --repo-dir ./repos/specs/consensus-specs
python3 build.py --profile execution-specs    --repo-dir ./repos/specs/execution-specs
python3 build.py --profile execution-apis     --repo-dir ./repos/specs/execution-apis
python3 build.py --profile beacon-apis        --repo-dir ./repos/specs/beacon-APIs
python3 build.py --profile builder-specs      --repo-dir ./repos/specs/builder-specs
python3 build.py --profile relay-specs        --repo-dir ./repos/specs/relay-specs
python3 build.py --profile remote-signing-api --repo-dir ./repos/specs/remote-signing-api

# cross-reference linking
python3 link.py --indexes-dir ./indexes

# build catalog (consumed by both MCP server and UI)
python3 build_catalog.py --indexes-dir ./indexes --output docs/catalog.json
```

Each `build.py` run extracts types, endpoints, constants, and fork metadata from
the source repo and writes a `{spec}_index.json` to `./indexes/`.

`link.py` resolves cross-spec type references (e.g. beacon-apis types referencing
consensus-specs containers).

`build_catalog.py` merges all indexes into `catalog.json`, deduplicating shared
types across specs using canonical-source attribution. This is the single artifact
consumed by both the MCP server and the explorer UI.

## Architecture

```
.
├── build.py                  # orchestrates extraction per spec profile
├── build_catalog.py          # merges indexes into catalog.json (canonical artifact)
├── link.py                   # cross-spec reference resolution
├── server.py                 # MCP server (8 tools, reads catalog.json)
├── fetch_repos.sh            # clones all spec repos
├── extractors/
│   ├── profiles.py           # spec profiles (paths, fork orders, extractor config)
│   ├── extract_python.py     # Python AST extractor (consensus-specs, execution-specs)
│   ├── extract_openapi.py    # OpenAPI extractor (beacon-apis, builder-specs, relay-specs, remote-signing-api)
│   ├── extract_openrpc.py    # OpenRPC extractor (execution-apis)
│   ├── extract_markdown.py   # Markdown type/endpoint extractor (beacon-apis, builder-specs)
│   ├── enrich.py             # structural annotation (fields, params, references, domains)
│   └── fetch_examples.py     # test fixture fetcher (standalone)
├── indexes/                  # generated per-spec indexes (intermediate build artifacts)
├── docs/
│   ├── index.html            # explorer SPA (types, endpoints, diff, search)
│   ├── visualizer.html       # transaction lifecycle diagram
│   └── catalog.json          # canonical data (from build_catalog.py)
├── SCHEMA.md                 # index JSON schema documentation
└── PLAN.md                   # development roadmap
```

### Extractors

Each extractor handles one source format:

- **Python AST** (`extract_python.py`): Walks Python source files, extracts
  class/function definitions with full code, tracks fork modifications via
  `[New in fork]` / `[Modified in fork]` annotations.
- **OpenAPI** (`extract_openapi.py`): Parses OpenAPI YAML, resolves `$ref`
  chains, extracts endpoints with parameters, response types, SSZ support,
  and fork variants.
- **OpenRPC** (`extract_openrpc.py`): Parses OpenRPC JSON, extracts JSON-RPC
  methods with params, results, error codes, and content descriptors.
- **Markdown** (`extract_markdown.py`): Extracts type definitions and endpoint
  descriptions from Markdown spec pages (used alongside OpenAPI for specs that
  document types in prose).

### Enrichment

`enrich.py` adds structural metadata after extraction: field lists for containers,
function signatures, reference graphs between types, domain classification, and
fork diff annotations (is_new, is_modified).

### Profiles

`profiles.py` defines the extraction configuration for each spec: which
extractors to run, directory paths within the repo, fork ordering, GitHub URL
templates, and any spec-specific extraction options.
