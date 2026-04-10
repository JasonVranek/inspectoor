#!/usr/bin/env python3
"""
OpenRPC extractor for execution-apis.

Parses OpenRPC YAML method definitions and JSON Schema type definitions
from the execution-apis repo. Produces endpoints (JSON-RPC methods) and
items (type schemas) conforming to the unified schema.

The repo uses split YAML files:
  src/eth/*.yaml           - eth_* method definitions
  src/engine/openrpc/methods/*.yaml  - engine_* method definitions
  src/debug/*.yaml         - debug_* method definitions
  src/schemas/*.yaml       - eth/debug type schemas
  src/engine/openrpc/schemas/*.yaml  - engine type schemas
  src/engine/{fork}.md     - per-fork engine API documentation

Fork assignment:
  - engine_* methods have version suffixes (V1, V2, ...) mapped to forks
    via the markdown docs in src/engine/{fork}.md
  - engine schemas have version suffixes mapped the same way
  - eth_* methods and schemas are unversioned (stable across forks)
"""

import json
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    print("pyyaml required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))
from profiles import SpecProfile


# ── Fork detection from engine markdown docs ─────────────────────────────

# Method version -> fork mapping (derived from engine docs)
# Built dynamically from the markdown, but these are the known patterns
ENGINE_FORK_ORDER = ["paris", "shanghai", "cancun", "prague", "osaka", "amsterdam"]


def build_method_fork_map(repo_dir: Path) -> dict:
    """Build {method_name: introduced_fork} from engine markdown docs.

    Each engine/{fork}.md has section headers like ### engine_newPayloadV3
    which tell us which method was introduced in that fork.
    """
    method_fork = {}
    engine_dir = repo_dir / "src" / "engine"

    for fork in ENGINE_FORK_ORDER:
        md_file = engine_dir / f"{fork}.md"
        if not md_file.exists():
            continue
        text = md_file.read_text()

        # Find method names in section headers
        for m in re.finditer(r'^###\s+(engine_\w+V\d+)', text, re.MULTILINE):
            method = m.group(1)
            if method not in method_fork:
                method_fork[method] = fork

    return method_fork


def build_schema_fork_map(repo_dir: Path) -> dict:
    """Build {schema_name: introduced_fork} from engine markdown docs.

    Engine docs define structures like ### ExecutionPayloadV3 under each fork.
    """
    schema_fork = {}
    engine_dir = repo_dir / "src" / "engine"

    for fork in ENGINE_FORK_ORDER:
        md_file = engine_dir / f"{fork}.md"
        if not md_file.exists():
            continue
        text = md_file.read_text()

        # Find schema names in section headers (capitalized, with optional Vn)
        for m in re.finditer(r'^###\s+([A-Z]\w+V\d+)', text, re.MULTILINE):
            schema = m.group(1)
            if schema not in schema_fork:
                schema_fork[schema] = fork

    return schema_fork


def extract_eips_from_engine_docs(repo_dir: Path) -> dict:
    """Extract {fork: [eip_numbers]} from engine markdown docs."""
    eip_map = {}
    engine_dir = repo_dir / "src" / "engine"

    for fork in ENGINE_FORK_ORDER:
        md_file = engine_dir / f"{fork}.md"
        if not md_file.exists():
            continue
        text = md_file.read_text()
        eips = sorted(set(int(m) for m in re.findall(r'EIP-(\d+)', text)))
        if eips:
            eip_map[fork] = eips

    return eip_map


# ── YAML loading ─────────────────────────────────────────────────────────

def load_yaml(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    with open(path) as f:
        return yaml.safe_load(f)


def load_yaml_list(path: Path) -> list:
    """Load YAML that is a list (method files are arrays)."""
    if not path.exists():
        return []
    with open(path) as f:
        data = yaml.safe_load(f)
    if isinstance(data, list):
        return data
    return []


# ── Schema extraction ────────────────────────────────────────────────────

def extract_type_from_schema(schema: dict) -> str:
    """Extract a readable type string from a JSON Schema object."""
    if not schema:
        return "unknown"
    if "$ref" in schema:
        ref = schema["$ref"]
        return ref.split("/")[-1]
    if "type" in schema:
        t = schema["type"]
        if t == "array" and "items" in schema:
            inner = extract_type_from_schema(schema["items"])
            return f"Array[{inner}]"
        if t == "object":
            return schema.get("title", "object")
        return t
    if "oneOf" in schema:
        types = [extract_type_from_schema(s) for s in schema["oneOf"]]
        return " | ".join(types)
    if "anyOf" in schema:
        types = [extract_type_from_schema(s) for s in schema["anyOf"]]
        return " | ".join(types)
    if "allOf" in schema:
        types = [extract_type_from_schema(s) for s in schema["allOf"]]
        return " & ".join(types)
    return schema.get("title", "unknown")


def extract_fields_from_properties(schema: dict) -> list:
    """Extract fields from a JSON Schema object's properties."""
    fields = []
    props = schema.get("properties", {})
    required = set(schema.get("required", []))

    for name, prop in props.items():
        ftype = extract_type_from_schema(prop)
        title = prop.get("title", "")
        desc = prop.get("description", "")
        comment = title if title else desc

        fields.append({
            "name": name,
            "type": ftype,
            "comment": comment,
            "required": name in required,
        })

    return fields


def extract_references_from_schema(schema: dict) -> list:
    """Extract type references from a JSON Schema."""
    refs = set()

    def walk(obj):
        if isinstance(obj, dict):
            if "$ref" in obj:
                ref_name = obj["$ref"].split("/")[-1]
                refs.add(ref_name)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(schema)
    return sorted(refs)


# ── Method extraction ────────────────────────────────────────────────────

def extract_methods(profile: SpecProfile, repo_dir: str, branch: str) -> dict:
    """Extract JSON-RPC method definitions as endpoints."""
    repo_path = Path(repo_dir)
    github_web = profile.github_web.format(branch=branch)
    endpoints = {}

    method_fork_map = build_method_fork_map(repo_path)

    # Collect method YAML files
    method_files = []
    # eth_* methods
    eth_dir = repo_path / "src" / "eth"
    if eth_dir.is_dir():
        method_files.extend(sorted(eth_dir.glob("*.yaml")))
    # engine_* methods
    engine_methods_dir = repo_path / "src" / "engine" / "openrpc" / "methods"
    if engine_methods_dir.is_dir():
        method_files.extend(sorted(engine_methods_dir.glob("*.yaml")))
    # debug_* methods
    debug_dir = repo_path / "src" / "debug"
    if debug_dir.is_dir():
        method_files.extend(sorted(debug_dir.glob("*.yaml")))

    for yaml_file in method_files:
        rel_path = str(yaml_file.relative_to(repo_path))
        methods = load_yaml_list(yaml_file)

        for method in methods:
            name = method.get("name", "")
            if not name:
                continue

            summary = method.get("summary", "")
            ext_docs = method.get("externalDocs", {})

            # Extract params
            params = []
            for p in method.get("params", []):
                pname = p.get("name", "")
                prequired = p.get("required", False)
                pschema = p.get("schema", {})
                ptype = extract_type_from_schema(pschema)
                params.append({
                    "name": pname,
                    "type": ptype,
                    "required": prequired,
                    "in": "params",
                })

            # Extract result
            result = method.get("result", {})
            result_name = result.get("name", "")
            result_schema = result.get("schema", {})
            result_type = extract_type_from_schema(result_schema)

            # Extract errors
            errors = []
            for err in method.get("errors", []) or []:
                if err:
                    errors.append({
                        "code": err.get("code", 0),
                        "message": err.get("message", ""),
                    })

            # Extract examples
            examples = []
            for ex in method.get("examples", []) or []:
                if ex:
                    examples.append({
                        "name": ex.get("name", ""),
                        "params": ex.get("params", []),
                        "result": ex.get("result", {}),
                    })

            # Determine fork and domain
            is_engine = name.startswith("engine_")
            is_debug = name.startswith("debug_")

            if is_engine:
                domain = "engine"
                fork = method_fork_map.get(name, "paris")
            elif is_debug:
                domain = "debug"
                fork = None  # debug methods are unversioned
            else:
                domain = "eth"
                fork = None  # eth methods are stable

            github_url = f"{github_web}/{rel_path}"

            endpoint_key = name
            endpoints[endpoint_key] = {
                "method": "JSON-RPC",
                "path": name,
                "summary": summary,
                "parameters": params,
                "result": {
                    "name": result_name,
                    "type": result_type,
                    "schema_ref": extract_type_from_schema(result_schema),
                },
                "errors": errors,
                "examples": examples,
                "domain": domain,
                "introduced_fork": fork,
                "source_file": rel_path,
                "github_url": github_url,
                "external_docs": ext_docs.get("url", ""),
                "fork_versioned": is_engine,
            }

    return endpoints


# ── Schema (type) extraction ─────────────────────────────────────────────

def extract_schemas(profile: SpecProfile, repo_dir: str, branch: str) -> dict:
    """Extract type definitions from schema YAML files."""
    repo_path = Path(repo_dir)
    github_web = profile.github_web.format(branch=branch)
    items = OrderedDict()

    schema_fork_map = build_schema_fork_map(repo_path)

    # Collect schema files
    schema_files = []
    # eth schemas
    schemas_dir = repo_path / "src" / "schemas"
    if schemas_dir.is_dir():
        schema_files.extend(sorted(schemas_dir.glob("*.yaml")))
    # engine schemas
    engine_schemas_dir = repo_path / "src" / "engine" / "openrpc" / "schemas"
    if engine_schemas_dir.is_dir():
        schema_files.extend(sorted(engine_schemas_dir.glob("*.yaml")))

    for yaml_file in schema_files:
        rel_path = str(yaml_file.relative_to(repo_path))
        data = load_yaml(yaml_file)
        if not data or not isinstance(data, dict):
            continue

        is_engine = "engine" in rel_path

        for name, schema in data.items():
            if not isinstance(schema, dict):
                continue

            # Determine kind
            stype = schema.get("type", "")
            if stype == "object":
                kind = "class"
            elif stype == "string" and "enum" in schema:
                kind = "enum"
            elif stype == "array":
                kind = "class"
            else:
                kind = "class"

            # Determine fork
            if is_engine:
                # Check schema_fork_map first, then infer from version suffix
                fork = schema_fork_map.get(name)
                if not fork:
                    vm = re.search(r'V(\d+)$', name)
                    if vm:
                        v = int(vm.group(1))
                        # V1=paris, V2=shanghai, V3=cancun, V4=prague, V5=osaka, V6=amsterdam
                        fork_idx = min(v - 1, len(ENGINE_FORK_ORDER) - 1)
                        fork = ENGINE_FORK_ORDER[fork_idx]
                    else:
                        fork = "paris"
                domain = "engine"
            else:
                fork = None
                domain = Path(yaml_file.stem).stem
                # Classify domain from filename
                domain_map = {
                    "base-types": "base-types",
                    "block": "block",
                    "transaction": "transaction",
                    "receipt": "receipt",
                    "state": "state",
                    "filter": "filter",
                    "execute": "execute",
                    "client": "client",
                    "withdrawal": "withdrawal",
                    "block-access-list": "block-access-list",
                }
                domain = domain_map.get(yaml_file.stem, yaml_file.stem)

            fields = extract_fields_from_properties(schema)
            refs = extract_references_from_schema(schema)
            title = schema.get("title", "")
            description = schema.get("description", "")
            github_url = f"{github_web}/{rel_path}"

            # EIPs from description
            eips = sorted(set(int(m) for m in re.findall(r'EIP-(\d+)', description + title)))

            # Build code representation
            code_lines = [f"{name}:"]
            if title:
                code_lines.append(f"  title: {title}")
            if stype:
                code_lines.append(f"  type: {stype}")
            for field in fields:
                req = " (required)" if field.get("required") else ""
                code_lines.append(f"  {field['name']}: {field['type']}{req}")
            code = "\n".join(code_lines)

            # Determine the effective fork name for keying
            fork_key = fork if fork else "unversioned"

            items[name] = {
                "introduced": fork_key,
                "domain": domain,
                "kind": kind,
                "forks": {
                    fork_key: {
                        "kind": kind,
                        "code": code,
                        "file": rel_path,
                        "github_url": github_url,
                        "fields": fields,
                        "references": refs,
                        "eips": eips,
                        "prose": description or title,
                        "is_new": True,
                        "is_modified": False,
                    }
                },
            }

    return items


# ── Build output ─────────────────────────────────────────────────────────

def build_output(profile: SpecProfile, items: dict, endpoints: dict,
                 eip_map: dict, branch: str) -> dict:
    """Build unified schema output."""

    # Determine fork order
    forks_seen = set()
    for item in items.values():
        forks_seen.update(item["forks"].keys())
    for ep in endpoints.values():
        if ep.get("introduced_fork"):
            forks_seen.add(ep["introduced_fork"])

    # Order: known engine forks first, then 'unversioned' at end
    fork_order = [f for f in ENGINE_FORK_ORDER if f in forks_seen]
    if "unversioned" in forks_seen:
        fork_order.append("unversioned")

    # Build domains
    domain_items = {}
    for name, item in items.items():
        d = item["domain"]
        if d not in domain_items:
            domain_items[d] = {"classes": [], "functions": [], "other": []}
        domain_items[d]["classes"].append(name)

    # Build fork_summary
    fork_summary = {}
    for fork in fork_order:
        new_items = [n for n, it in items.items() if it["introduced"] == fork]
        new_methods = [n for n, ep in endpoints.items() if ep.get("introduced_fork") == fork]
        eips = eip_map.get(fork, [])

        if new_items or new_methods or eips:
            fork_summary[fork] = {
                "new": sorted(new_items),
                "new_methods": sorted(new_methods),
                "modified": [],
                "eips": eips,
                "total_definitions": len(new_items) + len(new_methods),
            }

    # Build reverse references
    ref_index = {}
    for name, item in items.items():
        for fdata in item["forks"].values():
            for ref in fdata.get("references", []):
                if ref not in ref_index:
                    ref_index[ref] = set()
                ref_index[ref].add(name)

    # Build type_map
    type_map = {}
    for name, item in items.items():
        type_map[name] = {
            "source": profile.repo,
            "introduced": item["introduced"],
            "kind": item["kind"],
        }

    # Build _eip_index
    eip_index = {}
    for fork, eips in eip_map.items():
        for eip in eips:
            eip_str = str(eip)
            if eip_str not in eip_index:
                eip_index[eip_str] = {"items": []}

    output = {
        "_meta": {
            "schema_version": "1.0.0",
            "generated_by": "extract_openrpc.py",
            "source": profile.repo,
            "source_format": profile.source_format,
            "branch": branch,
            "fork_order": fork_order,
            "features": [],
            "files_processed": [],
            "total_items": len(items),
            "total_constants": 0,
            "total_type_aliases": 0,
            "total_endpoints": len(endpoints),
        },
        "items": {name: item for name, item in sorted(items.items())},
        "constants": {},
        "type_aliases": {},
        "endpoints": {name: ep for name, ep in sorted(endpoints.items())},
        "domains": {
            domain: {
                "classes": sorted(info["classes"]),
                "functions": sorted(info["functions"]),
                "other": sorted(info["other"]),
            }
            for domain, info in sorted(domain_items.items())
        },
        "fork_summary": fork_summary,
        "_references": {k: sorted(v) for k, v in sorted(ref_index.items())},
        "_field_index": {},
        "_eip_index": eip_index,
        "_type_map": type_map,
    }

    return output


# ── Top-level orchestrator ───────────────────────────────────────────────

def extract_all(profile: SpecProfile, repo_dir: str, branch: str = "main"):
    """Extract everything from execution-apis.

    Returns (items, endpoints, eip_map)
    """
    repo_path = Path(repo_dir)

    print("  Extracting JSON-RPC methods...", file=sys.stderr)
    endpoints = extract_methods(profile, repo_dir, branch)
    print(f"    {len(endpoints)} methods", file=sys.stderr)

    print("  Extracting type schemas...", file=sys.stderr)
    items = extract_schemas(profile, repo_dir, branch)
    print(f"    {len(items)} types", file=sys.stderr)

    eip_map = extract_eips_from_engine_docs(repo_path)
    print(f"  EIP map: {sum(len(v) for v in eip_map.values())} EIP refs across {len(eip_map)} forks", file=sys.stderr)

    return items, endpoints, eip_map


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extract execution-apis OpenRPC")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--output-dir", default="./indexes")
    parser.add_argument("--branch", default="main")
    args = parser.parse_args()

    from profiles import get_profile
    profile = get_profile(args.profile)

    items, endpoints, eip_map = extract_all(profile, args.repo_dir, args.branch)
    output = build_output(profile, items, endpoints, eip_map, args.branch)

    os.makedirs(args.output_dir, exist_ok=True)
    output_path = os.path.join(args.output_dir, f"{profile.name}_index.json")
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nDone: {output_path}", file=sys.stderr)
    print(f"  Items: {output['_meta']['total_items']}", file=sys.stderr)
    print(f"  Endpoints: {output['_meta']['total_endpoints']}", file=sys.stderr)


if __name__ == "__main__":
    main()
