#!/usr/bin/env python3
"""
The Inspectoor MCP Server.

Serves Ethereum spec data over MCP (Model Context Protocol).
Loads pre-built indexes on startup, answers structured queries
about types, functions, constants, endpoints, and cross-spec references.

Usage:
    # stdio transport (for agent integration)
    python3 server.py

    # With custom indexes directory
    python3 server.py --indexes-dir /path/to/indexes

    # Rebuild indexes before starting (requires repo paths)
    python3 server.py --rebuild --repos-dir /path/to/repos
"""

import argparse
import asyncio
import json
import os
import sys
import subprocess
from pathlib import Path
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent


# ── Index Store ────────────────────────────────────────────────────────────

class SpecStore:
    """In-memory store for all spec indexes and cross-references."""

    def __init__(self):
        self.indexes: dict = {}       # spec_name -> index data
        self.cross_refs: dict = {}    # cross-spec reference data
        self.type_map: dict = {}      # unified type map
        self.all_items: dict = {}     # flat: name -> (spec, item)
        self.all_endpoints: dict = {} # flat: key -> (spec, endpoint)

    def load(self, indexes_dir: str):
        """Load all indexes from disk."""
        indexes_path = Path(indexes_dir)
        if not indexes_path.is_dir():
            raise FileNotFoundError(f"Indexes directory not found: {indexes_dir}")

        self.indexes = {}
        for path in sorted(indexes_path.glob("*_index.json")):
            with open(path) as f:
                data = json.load(f)
            name = path.stem.replace("_index", "")
            self.indexes[name] = data

        # Load cross-refs if available
        xref_path = indexes_path / "_cross_refs.json"
        if xref_path.exists():
            with open(xref_path) as f:
                self.cross_refs = json.load(f)
            self.type_map = self.cross_refs.get("type_map", {})

        # Build flat indexes for fast lookup
        self._build_flat_indexes()

    def _build_flat_indexes(self):
        """Build flat lookup tables across all specs."""
        self.all_items = {}
        self.all_endpoints = {}

        for spec_name, data in self.indexes.items():
            for item_name, item in data.get("items", {}).items():
                # First spec to define it wins (canonical source)
                if item_name not in self.all_items:
                    self.all_items[item_name] = (spec_name, item)

            for ep_key, endpoint in data.get("endpoints", {}).items():
                self.all_endpoints[ep_key] = (spec_name, endpoint)

    def specs_summary(self) -> list:
        """Return summary of loaded specs."""
        result = []
        for name, data in self.indexes.items():
            meta = data.get("_meta", {})
            result.append({
                "name": name,
                "source": meta.get("source", ""),
                "items": meta.get("total_items", 0),
                "constants": meta.get("total_constants", 0),
                "endpoints": len(data.get("endpoints", {})),
                "forks": meta.get("fork_order", []),
            })
        return result

    def lookup_type(self, name: str, fork: Optional[str] = None, spec: Optional[str] = None) -> Optional[dict]:
        """Look up a type/function/item by name."""
        # Search in specific spec or across all
        if spec and spec in self.indexes:
            item = self.indexes[spec].get("items", {}).get(name)
            if item:
                return self._format_item(name, spec, item, fork)
        elif name in self.all_items:
            spec_name, item = self.all_items[name]
            return self._format_item(name, spec_name, item, fork)

        # Fuzzy search fallback
        matches = self._fuzzy_match(name, list(self.all_items.keys()), limit=5)
        if matches:
            return {"error": f"Type '{name}' not found", "suggestions": matches}
        return {"error": f"Type '{name}' not found"}

    def _format_item(self, name: str, spec_name: str, item: dict, fork: Optional[str] = None) -> dict:
        """Format an item for output, optionally filtered to a fork."""
        result = {
            "name": name,
            "spec": spec_name,
            "kind": item.get("kind", ""),
            "domain": item.get("domain", ""),
            "introduced": item.get("introduced", ""),
            "modified_in": item.get("modified_in", []),
            "forks_available": list(item.get("forks", {}).keys()),
        }

        # Cross-spec info
        if name in self.type_map:
            result["canonical_source"] = self.type_map[name]["source"]

        # Dependents
        for spec_data in self.indexes.values():
            refs = spec_data.get("_references", {})
            if name in refs:
                result.setdefault("used_by", []).extend(refs[name])

        if fork:
            fork_data = item.get("forks", {}).get(fork)
            if fork_data:
                result["fork"] = fork
                result["fields"] = fork_data.get("fields", [])
                result["code"] = fork_data.get("code", "")
                result["github_url"] = fork_data.get("github_url", "")
                result["references"] = fork_data.get("references", [])
                result["params"] = fork_data.get("params", [])
                result["return_type"] = fork_data.get("return_type", "")
                result["eips"] = fork_data.get("eips", [])
                result["prose"] = fork_data.get("prose", "")
                # Remove empty fields
                result = {k: v for k, v in result.items() if v or v == 0}
            else:
                result["error"] = f"Type '{name}' exists but not at fork '{fork}'"
                result["forks_available"] = list(item.get("forks", {}).keys())
        else:
            # Return latest fork data
            forks = item.get("forks", {})
            if forks:
                latest_fork = list(forks.keys())[-1]
                latest = forks[latest_fork]
                result["latest_fork"] = latest_fork
                result["fields"] = latest.get("fields", [])
                result["code"] = latest.get("code", "")
                result["github_url"] = latest.get("github_url", "")
                result["references"] = latest.get("references", [])
                result = {k: v for k, v in result.items() if v or v == 0}

        return result

    @staticmethod
    def _normalize(s: str) -> str:
        """Normalize for matching: lowercase, strip underscores/hyphens, collapse camelCase."""
        import re
        # Insert separator before uppercase runs (camelCase -> camel_case)
        s = re.sub(r'([a-z])([A-Z])', r'\1_\2', s)
        # Lowercase and strip separators
        return s.lower().replace("_", "").replace("-", "")

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
                }

                if endpoint.get("request_body"):
                    result["request_body"] = endpoint["request_body"]
                if endpoint.get("errors"):
                    result["errors"] = endpoint["errors"]

                results.append(result)

        return results

    def what_changed(self, fork: str, spec: Optional[str] = None) -> dict:
        """Return what changed in a specific fork."""
        result = {}

        specs_to_check = [spec] if spec and spec in self.indexes else list(self.indexes.keys())

        for spec_name in specs_to_check:
            data = self.indexes[spec_name]
            fs = data.get("fork_summary", {}).get(fork)
            if fs:
                result[spec_name] = {
                    "new": fs.get("new", []),
                    "modified": fs.get("modified", []),
                    "total": fs.get("total_definitions", 0),
                }

            # Also check EIP index
            eip_index = data.get("_eip_index", {})
            fork_eips = {}
            for eip_num, eip_data in eip_index.items():
                fork_items = [i for i in eip_data["items"] if i["fork"] == fork]
                if fork_items:
                    fork_eips[f"EIP-{eip_num}"] = fork_items
            if fork_eips:
                result.setdefault(spec_name, {})["eips"] = fork_eips

        if not result:
            # List available forks
            all_forks = set()
            for data in self.indexes.values():
                all_forks.update(data.get("_meta", {}).get("fork_order", []))
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
        for spec_name, data in self.indexes.items():
            if name in data.get("items", {}):
                item = data["items"][name]
                result["defined_in"].append({
                    "spec": spec_name,
                    "kind": item.get("kind", ""),
                    "introduced": item.get("introduced", ""),
                    "forks": list(item.get("forks", {}).keys()),
                })

        # Who uses it? (reverse references)
        for spec_name, data in self.indexes.items():
            refs = data.get("_references", {})
            if name in refs:
                for user in refs[name]:
                    result["used_by"].append({
                        "spec": spec_name,
                        "item": user,
                    })

        # Cross-spec references
        if self.cross_refs:
            for ref_key, ref_data in self.cross_refs.get("cross_refs", {}).items():
                if ref_data["to_type"] == name or ref_data["from_item"] == name:
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

        # Search items
        for spec_name, data in self.indexes.items():
            for item_name, item in data.get("items", {}).items():
                if (query_lower in item_name.lower()
                    or query_norm in self._normalize(item_name)
                    or query_lower in item.get("domain", "").lower()):
                    results["items"].append({
                        "name": item_name,
                        "spec": spec_name,
                        "kind": item.get("kind", ""),
                        "domain": item.get("domain", ""),
                        "introduced": item.get("introduced", ""),
                    })

            # Search constants
            for const_name, entries in data.get("constants", {}).items():
                if query_lower in const_name.lower() or query_norm in self._normalize(const_name):
                    results["constants"].append({
                        "name": const_name,
                        "spec": spec_name,
                        "value": entries[0].get("value", "") if entries else "",
                    })

            # Search type aliases
            for alias_name, entries in data.get("type_aliases", {}).items():
                if query_lower in alias_name.lower() or query_norm in self._normalize(alias_name):
                    results["type_aliases"].append({
                        "name": alias_name,
                        "spec": spec_name,
                        "ssz_equivalent": entries[0].get("ssz_equivalent", "") if entries else "",
                    })

        # Search endpoints
        for ep_key, (spec_name, ep) in self.all_endpoints.items():
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

        # Trim to limit
        for key in results:
            results[key] = results[key][:limit]

        results["total"] = sum(len(v) for v in results.values())
        return results

    def _fuzzy_match(self, query: str, candidates: list, limit: int = 5) -> list:
        """Simple subsequence fuzzy match."""
        query_lower = query.lower()
        scored = []
        for name in candidates:
            name_lower = name.lower()
            if query_lower in name_lower:
                # Substring match -- score by position and length difference
                pos = name_lower.index(query_lower)
                score = (100 - pos) + (100 - abs(len(name) - len(query)))
                scored.append((score, name))
            else:
                # Subsequence match
                qi = 0
                for c in name_lower:
                    if qi < len(query_lower) and c == query_lower[qi]:
                        qi += 1
                if qi == len(query_lower):
                    scored.append((qi * 10, name))

        scored.sort(reverse=True)
        return [name for _, name in scored[:limit]]


# ── MCP Server ─────────────────────────────────────────────────────────────

def create_server(store: SpecStore, indexes_dir: str, repos_dir: Optional[str] = None) -> Server:
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
                    store, indexes_dir, repos_dir,
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
    if name not in store.all_items:
        return {"error": f"Type '{name}' not found"}

    spec_name, item = store.all_items[name]
    forks = item.get("forks", {})

    if from_fork not in forks:
        return {"error": f"'{name}' not found at fork '{from_fork}'", "available": list(forks.keys())}
    if to_fork not in forks:
        return {"error": f"'{name}' not found at fork '{to_fork}'", "available": list(forks.keys())}

    from_data = forks[from_fork]
    to_data = forks[to_fork]

    result = {
        "name": name,
        "spec": spec_name,
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

    # Code diff (just show both -- let the agent reason about it)
    from_code = from_data.get("code", "")
    to_code = to_data.get("code", "")
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


async def _reindex(store: SpecStore, indexes_dir: str, repos_dir: Optional[str], specs: Optional[list] = None) -> dict:
    """Rebuild indexes by shelling out to build.py and link.py."""
    if not repos_dir:
        return {"error": "No repos directory configured. Start server with --repos-dir."}

    build_script = str(Path(__file__).parent / "build.py")
    link_script = str(Path(__file__).parent / "link.py")

    # Map spec names to repo subdirectories
    spec_repo_map = {
        "consensus-specs": "specs/consensus-specs",
        "builder-specs": "specs/builder-specs",
        "relay-specs": "specs/relay-specs",
        "beacon-apis": "specs/beacon-APIs",
    }

    # Branch overrides
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

    # Reload
    store.load(indexes_dir)
    results["_reload"] = {"status": "ok", "specs_loaded": len(store.indexes)}

    return results


# ── Main ───────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="The Inspectoor MCP Server")
    parser.add_argument("--indexes-dir", default="./indexes",
                        help="Directory containing pre-built indexes")
    parser.add_argument("--repos-dir",
                        help="Directory containing spec repo clones (for reindex)")
    parser.add_argument("--rebuild", action="store_true",
                        help="Rebuild all indexes before starting")
    args = parser.parse_args()

    store = SpecStore()

    # Rebuild if requested
    if args.rebuild and args.repos_dir:
        print("Rebuilding indexes...", file=sys.stderr)
        result = await _reindex(store, args.indexes_dir, args.repos_dir)
        for spec, status in result.items():
            print(f"  {spec}: {status}", file=sys.stderr)

    # Load indexes
    try:
        store.load(args.indexes_dir)
        total_items = sum(d.get("_meta", {}).get("total_items", 0) for d in store.indexes.values())
        total_endpoints = sum(len(d.get("endpoints", {})) for d in store.indexes.values())
        print(f"Loaded {len(store.indexes)} specs: {total_items} items, {total_endpoints} endpoints", file=sys.stderr)
    except FileNotFoundError:
        print(f"Warning: No indexes found at {args.indexes_dir}", file=sys.stderr)
        print("Start with --rebuild --repos-dir to build indexes", file=sys.stderr)

    server = create_server(store, args.indexes_dir, args.repos_dir)

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
