#!/usr/bin/env python3
"""
The Inspectoor MCP Server.

Serves Ethereum spec data over MCP (Model Context Protocol).
Loads a pre-built catalog.json on startup, answers structured queries
about types, functions, constants, endpoints, and cross-spec references.

Usage:
    # stdio transport (for agent integration)
    python3 server.py

    # With custom catalog path
    python3 server.py --catalog docs/catalog.json

    # Rebuild indexes before starting (requires repo paths)
    python3 server.py --rebuild --repos-dir /path/to/repos
"""

import argparse
import asyncio
import json
import re
import os
import sys
from pathlib import Path
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent


# ── Catalog Store ──────────────────────────────────────────────────────────

class SpecStore:
    """In-memory store backed by catalog.json -- the single source of truth
    shared with the explorer UI."""

    def __init__(self):
        self.catalog: dict = {}
        self.items: dict = {}             # name -> item (already merged/deduped)
        self.specs: dict = {}             # spec_name -> spec data
        self.type_map: dict = {}          # name -> canonical source info
        self.cross_refs: dict = {}        # cross-spec references
        self.all_endpoints: dict = {}     # ep_key -> (spec_name, endpoint)

    def load(self, catalog_path: str):
        """Load the unified catalog."""
        with open(catalog_path) as f:
            self.catalog = json.load(f)

        self.items = self.catalog.get("items", {})
        self.specs = self.catalog.get("specs", {})
        self.type_map = self.catalog.get("type_map", {})
        self.cross_refs = self.catalog.get("cross_refs", {})

        # Build flat endpoint index
        self.all_endpoints = {}
        for spec_name, spec_data in self.specs.items():
            for ep_key, endpoint in spec_data.get("endpoints", {}).items():
                self.all_endpoints[ep_key] = (spec_name, endpoint)

    def specs_summary(self) -> list:
        """Return summary of loaded specs."""
        result = []
        for name, spec_data in self.specs.items():
            meta = spec_data.get("meta", {})
            result.append({
                "name": name,
                "source": meta.get("repo", ""),
                "items": meta.get("total_items", 0),
                "constants": len(spec_data.get("constants", {})),
                "endpoints": len(spec_data.get("endpoints", {})),
                "forks": meta.get("fork_order", []),
            })
        return result

    def lookup_type(self, name: str, fork: Optional[str] = None, spec: Optional[str] = None) -> Optional[dict]:
        """Look up a type/function/item by name."""
        item = None

        if spec:
            # Filter to items whose primary or secondary spec matches
            candidate = self.items.get(name)
            if candidate and spec in candidate.get("specs", []):
                item = candidate
        else:
            item = self.items.get(name)

        if not item:
            # Fuzzy fallback
            matches = self._fuzzy_match(name, list(self.items.keys()), limit=5)
            if matches:
                return {"error": f"Type '{name}' not found", "suggestions": matches}
            return {"error": f"Type '{name}' not found"}

        return self._format_item(item, fork)

    def _format_item(self, item: dict, fork: Optional[str] = None) -> dict:
        """Format an item for output."""
        name = item["name"]
        spec_name = item.get("spec", "")

        result = {
            "name": name,
            "spec": spec_name,
            "kind": item.get("kind", ""),
            "domain": item.get("domain", ""),
            "introduced": item.get("introduced", ""),
            "modified_in": item.get("modified_in", []),
            "forks_available": list(item.get("forks", {}).keys()),
        }

        # Canonical source from type_map
        if name in self.type_map:
            result["canonical_source"] = self.type_map[name]["source"]

        # Reverse references (who uses this type)
        used_by = []
        for sname, sdata in self.specs.items():
            refs = sdata.get("references", {})
            if name in refs:
                for user in refs[name]:
                    used_by.append({"spec": sname, "item": user})
        if used_by:
            result["used_by"] = used_by

        forks = item.get("forks", {})

        if fork:
            fork_data = forks.get(fork)
            if fork_data:
                result["fork"] = fork
                result.update(self._extract_fork_fields(fork_data, forks, fork))
                result = {k: v for k, v in result.items() if v or v == 0}
            else:
                result["error"] = f"Type '{name}' exists but not at fork '{fork}'"
                result["forks_available"] = list(forks.keys())
        else:
            # Return latest fork data
            if forks:
                latest_fork = list(forks.keys())[-1]
                latest = forks[latest_fork]
                result["latest_fork"] = latest_fork
                result.update(self._extract_fork_fields(latest, forks, latest_fork))
                result = {k: v for k, v in result.items() if v or v == 0}

        return result

    def _extract_fork_fields(self, fork_data: dict, all_forks: dict, fork_name: str) -> dict:
        """Extract displayable fields from a fork entry.
        Handles deduped code: if this fork has no 'code' key, inherit from
        the most recent prior fork that does."""
        out = {}
        for key in ["fields", "references", "params", "eips"]:
            if fork_data.get(key):
                out[key] = fork_data[key]
        if fork_data.get("return_type"):
            out["return_type"] = fork_data["return_type"]
        prose = fork_data.get("prose") or fork_data.get("description") or ""
        if prose:
            out["prose"] = prose
        if fork_data.get("github_url"):
            out["github_url"] = fork_data["github_url"]

        # Resolve code: catalog dedupes by only storing code when it changes.
        # Walk backwards through forks to find the most recent code.
        code = fork_data.get("code", "")
        if not code:
            fork_keys = list(all_forks.keys())
            idx = fork_keys.index(fork_name) if fork_name in fork_keys else -1
            for i in range(idx - 1, -1, -1):
                prev_code = all_forks[fork_keys[i]].get("code", "")
                if prev_code:
                    code = prev_code
                    break
        if code:
            out["code"] = code

        return out

    def lookup_endpoint(self, query: str) -> list:
        """Search endpoints by path, operation, or keyword."""
        query_lower = query.lower()
        query_norm = self._normalize(query)
        results = []

        for ep_key, (spec_name, endpoint) in self.all_endpoints.items():
            path = endpoint.get("path", "")
            op_id = endpoint.get("operation_id", "")
            summary = endpoint.get("summary", "")
            tags = endpoint.get("tags", [])

            if (query_lower in path.lower()
                or query_lower in op_id.lower()
                or query_lower in summary.lower()
                or any(query_lower in t.lower() for t in tags)
                or query_norm in self._normalize(path)
                or query_norm in self._normalize(op_id)):

                result = {
                    "spec": spec_name,
                    "method": endpoint.get("method", ""),
                    "path": path,
                    "operation_id": op_id,
                    "summary": summary,
                    "tags": tags,
                    "parameters": endpoint.get("parameters", []),
                    "fork_versioned": endpoint.get("fork_versioned", False),
                    "fork_variants": endpoint.get("fork_variants", {}),
                    "ssz_support": endpoint.get("content_negotiation", {}).get("ssz_support", False),
                    "github_url": endpoint.get("github_url", ""),
                    "result": endpoint.get("result"),
                    "errors": endpoint.get("errors"),
                    "examples": endpoint.get("examples"),
                    "domain": endpoint.get("domain"),
                    "introduced_fork": endpoint.get("introduced_fork"),
                }

                if endpoint.get("request_body"):
                    result["request_body"] = endpoint["request_body"]

                results.append(result)

        return results

    def what_changed(self, fork: str, spec: Optional[str] = None) -> dict:
        """Return what changed in a specific fork."""
        result = {}

        specs_to_check = [spec] if spec and spec in self.specs else list(self.specs.keys())

        for spec_name in specs_to_check:
            spec_data = self.specs[spec_name]
            fs = spec_data.get("fork_summary", {}).get(fork)
            if fs:
                entry = {
                    "new": fs.get("new", []),
                    "modified": fs.get("modified", []),
                    "total": fs.get("total_definitions", 0),
                }
                if fs.get("new_methods"):
                    entry["new_methods"] = fs["new_methods"]
                if fs.get("new_constants"):
                    entry["new_constants"] = fs["new_constants"]
                if fs.get("eips"):
                    entry["fork_eips"] = fs["eips"]
                result[spec_name] = entry

            # EIP index
            eip_index = spec_data.get("eip_index", {})
            fork_eips = {}
            for eip_num, eip_data in eip_index.items():
                fork_items = [i for i in eip_data.get("items", []) if i.get("fork") == fork]
                if fork_items:
                    fork_eips[f"EIP-{eip_num}"] = fork_items
            if fork_eips:
                result.setdefault(spec_name, {})["eips"] = fork_eips

        if not result:
            all_forks = set()
            for sdata in self.specs.values():
                all_forks.update(sdata.get("meta", {}).get("fork_order", []))
            return {"error": f"No changes found for fork '{fork}'", "available_forks": sorted(all_forks)}

        return result

    def trace_type(self, name: str) -> dict:
        """Trace a type across spec boundaries."""
        result = {
            "name": name,
            "defined_in": [],
            "used_by": [],
            "cross_spec_refs": [],
        }

        # Where is it defined?
        item = self.items.get(name)
        if item:
            for s in item.get("specs", []):
                result["defined_in"].append({
                    "spec": s,
                    "kind": item.get("kind", ""),
                    "introduced": item.get("introduced", ""),
                    "forks": list(item.get("forks", {}).keys()),
                })

        # Reverse references
        for spec_name, spec_data in self.specs.items():
            refs = spec_data.get("references", {})
            if name in refs:
                for user in refs[name]:
                    result["used_by"].append({
                        "spec": spec_name,
                        "item": user,
                    })

        # Cross-spec references
        for ref_key, ref_data in self.cross_refs.items():
            if isinstance(ref_data, dict):
                if ref_data.get("to_type") == name or ref_data.get("from_item") == name:
                    result["cross_spec_refs"].append(ref_data)

        # Canonical source
        if name in self.type_map:
            result["canonical_source"] = self.type_map[name]

        return result

    def search(self, query: str, limit: int = 20) -> dict:
        """Search across all items, constants, endpoints."""
        query_lower = query.lower()
        query_norm = self._normalize(query)
        results = {"items": [], "constants": [], "endpoints": [], "type_aliases": []}

        # Search unified items
        for item_name, item in self.items.items():
            if (query_lower in item_name.lower()
                or query_norm in self._normalize(item_name)
                or query_lower in item.get("domain", "").lower()):
                results["items"].append({
                    "name": item_name,
                    "spec": item.get("spec", ""),
                    "kind": item.get("kind", ""),
                    "domain": item.get("domain", ""),
                    "introduced": item.get("introduced", ""),
                })

        # Search per-spec constants, type_aliases, endpoints
        for spec_name, spec_data in self.specs.items():
            for const_name, entry in spec_data.get("constants", {}).items():
                if query_lower in const_name.lower() or query_norm in self._normalize(const_name):
                    results["constants"].append({
                        "name": const_name,
                        "spec": spec_name,
                        "value": entry.get("value", "") if isinstance(entry, dict) else "",
                    })

            for alias_name, entry in spec_data.get("type_aliases", {}).items():
                if query_lower in alias_name.lower() or query_norm in self._normalize(alias_name):
                    results["type_aliases"].append({
                        "name": alias_name,
                        "spec": spec_name,
                        "ssz_equivalent": entry.get("ssz_equivalent", "") if isinstance(entry, dict) else "",
                    })

            for ep_key, ep in spec_data.get("endpoints", {}).items():
                if (query_lower in ep.get("path", "").lower()
                    or query_lower in ep.get("summary", "").lower()
                    or query_lower in ep.get("operation_id", "").lower()
                    or query_norm in self._normalize(ep.get("path", ""))
                    or query_norm in self._normalize(ep.get("operation_id", ""))):
                    results["endpoints"].append({
                        "spec": spec_name,
                        "method": ep.get("method", ""),
                        "path": ep.get("path", ""),
                        "summary": ep.get("summary", ""),
                    })

        # Trim
        for key in results:
            results[key] = results[key][:limit]
        results["total"] = sum(len(v) for v in results.values())
        return results

    @staticmethod
    def _normalize(s: str) -> str:
        """Normalize for matching."""
        s = re.sub(r'([a-z])([A-Z])', r'\1_\2', s)
        return s.lower().replace("_", "").replace("-", "")

    def _fuzzy_match(self, query: str, candidates: list, limit: int = 5) -> list:
        """Simple subsequence fuzzy match."""
        query_lower = query.lower()
        scored = []
        for name in candidates:
            name_lower = name.lower()
            if query_lower in name_lower:
                pos = name_lower.index(query_lower)
                score = (100 - pos) + (100 - abs(len(name) - len(query)))
                scored.append((score, name))
            else:
                qi = 0
                for c in name_lower:
                    if qi < len(query_lower) and c == query_lower[qi]:
                        qi += 1
                if qi == len(query_lower):
                    scored.append((qi * 10, name))
        scored.sort(reverse=True)
        return [name for _, name in scored[:limit]]


# ── MCP Server ─────────────────────────────────────────────────────────────

def create_server(store: SpecStore, catalog_path: str, indexes_dir: Optional[str] = None, repos_dir: Optional[str] = None) -> Server:
    """Create the MCP server with all tool registrations."""

    server = Server("inspectoor")

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        return [
            Tool(
                name="list_specs",
                description="List all loaded Ethereum spec indexes with item counts, endpoint counts, and available forks.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="lookup_type",
                description="Look up an Ethereum spec type, function, or container by name. Returns fields, code, source link, references, and EIP associations. Supports fuzzy matching.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Type or function name (e.g., 'BuilderBid', 'process_block', 'BeaconState')",
                        },
                        "fork": {
                            "type": "string",
                            "description": "Optional fork to get definition at (e.g., 'electra', 'deneb'). Omit for latest.",
                        },
                        "spec": {
                            "type": "string",
                            "description": "Optional spec to search in (e.g., 'consensus-specs', 'builder-specs'). Omit to search all.",
                        },
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="lookup_endpoint",
                description="Search Ethereum API endpoints by path, operation name, or keyword. Returns parameters, response types, SSZ support, and fork variants.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query -- path fragment (e.g., 'header', 'blinded_blocks'), operation ID (e.g., 'getHeader'), or keyword (e.g., 'validator', 'status')",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="what_changed",
                description="Show what types, functions, and constants were added or modified in a specific fork. Includes EIP associations.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "fork": {
                            "type": "string",
                            "description": "Fork name (e.g., 'electra', 'deneb', 'fulu')",
                        },
                        "spec": {
                            "type": "string",
                            "description": "Optional: limit to a specific spec (e.g., 'builder-specs'). Omit for all specs.",
                        },
                    },
                    "required": ["fork"],
                },
            ),
            Tool(
                name="trace_type",
                description="Trace a type across spec boundaries. Shows where it's defined, who uses it, and cross-spec references. Essential for understanding data flow between protocol layers.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Type name to trace (e.g., 'ExecutionPayloadHeader', 'SignedBuilderBid')",
                        },
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="search",
                description="Fuzzy search across all Ethereum spec items, constants, type aliases, and endpoints.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (e.g., 'blob', 'attestation', 'withdrawal', 'ssz')",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results per category (default: 20)",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="diff_type",
                description="Compare a type or function between two forks. Shows field additions, removals, and code changes.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Type or function name",
                        },
                        "from_fork": {
                            "type": "string",
                            "description": "Earlier fork (e.g., 'deneb')",
                        },
                        "to_fork": {
                            "type": "string",
                            "description": "Later fork (e.g., 'electra')",
                        },
                    },
                    "required": ["name", "from_fork", "to_fork"],
                },
            ),
            Tool(
                name="reindex",
                description="Rebuild spec indexes from source repos and reload. Requires repos directory to be configured.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "specs": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional: list of specs to rebuild (e.g., ['builder-specs', 'consensus-specs']). Omit to rebuild all.",
                        },
                    },
                },
            ),
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            if name == "list_specs":
                result = store.specs_summary()

            elif name == "lookup_type":
                result = store.lookup_type(
                    name=arguments["name"],
                    fork=arguments.get("fork"),
                    spec=arguments.get("spec"),
                )

            elif name == "lookup_endpoint":
                result = store.lookup_endpoint(arguments["query"])

            elif name == "what_changed":
                result = store.what_changed(
                    fork=arguments["fork"],
                    spec=arguments.get("spec"),
                )

            elif name == "trace_type":
                result = store.trace_type(arguments["name"])

            elif name == "search":
                result = store.search(
                    query=arguments["query"],
                    limit=arguments.get("limit", 20),
                )

            elif name == "diff_type":
                result = _diff_type(
                    store,
                    name=arguments["name"],
                    from_fork=arguments["from_fork"],
                    to_fork=arguments["to_fork"],
                )

            elif name == "reindex":
                result = await _reindex(
                    store, catalog_path, indexes_dir, repos_dir,
                    specs=arguments.get("specs"),
                )

            else:
                result = {"error": f"Unknown tool: {name}"}

            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        except Exception as e:
            return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    return server


def _diff_type(store: SpecStore, name: str, from_fork: str, to_fork: str) -> dict:
    """Compare a type between two forks."""
    item = store.items.get(name)
    if not item:
        return {"error": f"Type '{name}' not found"}

    forks = item.get("forks", {})
    if from_fork not in forks:
        return {"error": f"'{name}' not found at fork '{from_fork}'", "available": list(forks.keys())}
    if to_fork not in forks:
        return {"error": f"'{name}' not found at fork '{to_fork}'", "available": list(forks.keys())}

    from_data = forks[from_fork]
    to_data = forks[to_fork]

    result = {
        "name": name,
        "spec": item.get("spec", ""),
        "from_fork": from_fork,
        "to_fork": to_fork,
    }

    # Diff fields
    from_fields = {f["name"]: f for f in from_data.get("fields", [])}
    to_fields = {f["name"]: f for f in to_data.get("fields", [])}

    added = [to_fields[f] for f in to_fields if f not in from_fields]
    removed = [from_fields[f] for f in from_fields if f not in to_fields]
    changed = []
    for f in from_fields:
        if f in to_fields and from_fields[f].get("type") != to_fields[f].get("type"):
            changed.append({
                "name": f,
                "from_type": from_fields[f].get("type"),
                "to_type": to_fields[f].get("type"),
            })

    if added or removed or changed:
        result["fields_added"] = added
        result["fields_removed"] = removed
        result["fields_changed"] = changed

    # Code diff -- resolve deduped code for both forks
    fork_keys = list(forks.keys())

    def resolve_code(fork_name):
        code = forks[fork_name].get("code", "")
        if code:
            return code
        idx = fork_keys.index(fork_name) if fork_name in fork_keys else -1
        for i in range(idx - 1, -1, -1):
            prev = forks[fork_keys[i]].get("code", "")
            if prev:
                return prev
        return ""

    from_code = resolve_code(from_fork)
    to_code = resolve_code(to_fork)

    if from_code != to_code:
        result["code_changed"] = True
        result["from_code"] = from_code
        result["to_code"] = to_code
    else:
        result["code_changed"] = False

    # Source links
    result["from_url"] = from_data.get("github_url", "")
    result["to_url"] = to_data.get("github_url", "")

    return result


async def _reindex(store: SpecStore, catalog_path: str, indexes_dir: Optional[str],
                   repos_dir: Optional[str], specs: Optional[list] = None) -> dict:
    """Rebuild indexes, rebuild catalog, and reload."""
    if not repos_dir:
        return {"error": "No repos directory configured. Start server with --repos-dir."}
    if not indexes_dir:
        return {"error": "No indexes directory configured. Start server with --indexes-dir."}

    base_dir = Path(__file__).parent
    build_script = str(base_dir / "build.py")
    link_script = str(base_dir / "link.py")
    catalog_script = str(base_dir / "build_catalog.py")

    spec_repo_map = {
        "consensus-specs": "specs/consensus-specs",
        "builder-specs": "specs/builder-specs",
        "relay-specs": "specs/relay-specs",
        "beacon-apis": "specs/beacon-APIs",
        "remote-signing-api": "specs/remote-signing-api",
        "execution-specs": "specs/execution-specs",
        "execution-apis": "specs/execution-apis",
    }

    spec_branches = {
        "consensus-specs": "dev",
    }

    specs_to_build = specs or list(spec_repo_map.keys())
    results = {}

    for spec_name in specs_to_build:
        if spec_name not in spec_repo_map:
            results[spec_name] = {"status": "error", "message": f"Unknown spec: {spec_name}"}
            continue

        repo_path = os.path.join(repos_dir, spec_repo_map[spec_name])
        if not os.path.isdir(repo_path):
            results[spec_name] = {"status": "error", "message": f"Repo not found: {repo_path}"}
            continue

        branch = spec_branches.get(spec_name, "main")
        cmd = [
            sys.executable, build_script,
            "--profile", spec_name,
            "--repo-dir", repo_path,
            "--output-dir", indexes_dir,
            "--branch", branch,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0:
                results[spec_name] = {"status": "ok"}
            else:
                results[spec_name] = {"status": "error", "message": stderr.decode()[-500:]}
        except Exception as e:
            results[spec_name] = {"status": "error", "message": str(e)}

    # Run linker
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, link_script, "--indexes-dir", indexes_dir,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        results["_linker"] = {"status": "ok" if proc.returncode == 0 else "error"}
    except Exception as e:
        results["_linker"] = {"status": "error", "message": str(e)}

    # Rebuild catalog
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, catalog_script,
            "--indexes-dir", indexes_dir,
            "--output", catalog_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        results["_catalog"] = {"status": "ok" if proc.returncode == 0 else "error"}
    except Exception as e:
        results["_catalog"] = {"status": "error", "message": str(e)}

    # Reload from freshly built catalog
    store.load(catalog_path)
    results["_reload"] = {"status": "ok", "items": len(store.items), "endpoints": len(store.all_endpoints)}

    return results


# ── Main ───────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="The Inspectoor MCP Server")
    parser.add_argument("--catalog", default="./docs/catalog.json",
                        help="Path to catalog.json (default: ./docs/catalog.json)")
    parser.add_argument("--indexes-dir", default="./indexes",
                        help="Directory containing per-spec indexes (for reindex)")
    parser.add_argument("--repos-dir",
                        help="Directory containing spec repo clones (for reindex)")
    parser.add_argument("--rebuild", action="store_true",
                        help="Rebuild all indexes before starting")
    args = parser.parse_args()

    store = SpecStore()

    # Rebuild if requested
    if args.rebuild and args.repos_dir:
        print("Rebuilding indexes...", file=sys.stderr)
        result = await _reindex(store, args.catalog, args.indexes_dir, args.repos_dir)
        for spec, status in result.items():
            print(f"  {spec}: {status}", file=sys.stderr)

    # Load catalog
    try:
        store.load(args.catalog)
        print(f"Loaded catalog: {len(store.items)} items, {len(store.all_endpoints)} endpoints, {len(store.specs)} specs", file=sys.stderr)
    except FileNotFoundError:
        print(f"Warning: Catalog not found at {args.catalog}", file=sys.stderr)
        print("Run build_catalog.py first, or start with --rebuild --repos-dir", file=sys.stderr)

    server = create_server(store, args.catalog, args.indexes_dir, args.repos_dir)

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
