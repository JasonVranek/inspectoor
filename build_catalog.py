#!/usr/bin/env python3
"""
Build a slim catalog.json from full spec indexes for the Explorer UI.

Merges types that appear in multiple specs into a single entry using
the canonical spec (from type_map) as the primary source. Deduplicates
code across forks (only stores code when it changes between forks).
"""

import argparse
import json
import sys
from pathlib import Path


def normalize_constant(name, value):
    entry = value[-1] if isinstance(value, list) and value else value if isinstance(value, dict) else None
    if not entry:
        return None
    return {k: entry.get(k, "") for k in ["name", "value", "type", "description", "section", "category", "fork", "github_url"]}


def normalize_type_alias(name, value):
    entry = value[-1] if isinstance(value, list) and value else value if isinstance(value, dict) else None
    if not entry:
        return None
    return {k: entry.get(k, "") for k in ["name", "ssz_equivalent", "description", "fork", "github_url"]}


def build_item_forks(item, fork_order):
    """Build fork entries with deduped code."""
    forks_data = item.get("forks", {})
    ordered = [f for f in fork_order if f in forks_data]
    for f in forks_data:
        if f not in ordered:
            ordered.append(f)
    if not ordered:
        return {}

    result = {}
    prev_code = None
    for fork in ordered:
        fdata = forks_data[fork]
        entry = {}
        code = fdata.get("code", "")
        if code and code != prev_code:
            entry["code"] = code
        prev_code = code if code else prev_code

        for key in ["fields", "params", "references", "eips"]:
            if fdata.get(key):
                entry[key] = fdata[key]
        if fdata.get("return_type"):
            entry["return_type"] = fdata["return_type"]
        prose = fdata.get("prose") or fdata.get("description") or ""
        if prose:
            entry["prose"] = prose
        if fdata.get("github_url"):
            entry["github_url"] = fdata["github_url"]
        if fdata.get("is_new"):
            entry["is_new"] = True
        if fdata.get("is_modified"):
            entry["is_modified"] = True

        result[fork] = entry
    return result


def merge_items(primary_item, primary_forks, secondary_item, secondary_forks):
    """Merge a secondary spec's data into the primary item.
    Secondary fills in missing fields/prose but doesn't overwrite primary code."""
    for fork in secondary_forks:
        if fork not in primary_forks:
            primary_forks[fork] = secondary_forks[fork]
        else:
            pf = primary_forks[fork]
            sf = secondary_forks[fork]
            # Fill in missing structural data from secondary
            if not pf.get("fields") and sf.get("fields"):
                pf["fields"] = sf["fields"]
            if not pf.get("prose") and sf.get("prose"):
                pf["prose"] = sf["prose"]
            if not pf.get("references") and sf.get("references"):
                pf["references"] = sf["references"]


def slim_endpoint(name, ep):
    slim = {
        "method": ep.get("method", ""),
        "path": ep.get("path", name),
        "summary": ep.get("summary", ""),
        "domain": ep.get("domain", ""),
        "github_url": ep.get("github_url", ""),
    }
    for key in ["operation_id", "description", "tags", "parameters", "request_body",
                 "responses", "fork_versioned", "fork_variants", "content_negotiation",
                 "errors", "result", "introduced_fork", "external_docs"]:
        if ep.get(key):
            slim[key] = ep[key]
    if ep.get("examples"):
        slim["has_examples"] = True
    return slim


def resolve_canonical_spec(type_map, name, repo_to_spec):
    """Resolve canonical spec name from type_map source field."""
    if name not in type_map:
        return None
    source = type_map[name].get("source", "")
    for repo_frag, spec_name in repo_to_spec.items():
        if repo_frag in source:
            return spec_name
    return None


def build_catalog(indexes_dir, output_path):
    indexes_dir = Path(indexes_dir)
    catalog = {
        "_meta": {"generator": "build_catalog.py", "specs": {}},
        "specs": {},
        "items": {},       # unified deduped items
        "cross_refs": {},
        "type_map": {},
    }

    # Load cross-refs
    cr_path = indexes_dir / "_cross_refs.json"
    if cr_path.exists():
        with open(cr_path) as f:
            cr = json.load(f)
        catalog["type_map"] = cr.get("type_map", {})
        catalog["cross_refs"] = cr.get("cross_refs", {})

    # Map from repo URL fragments to spec names
    repo_to_spec = {}

    # First pass: load all spec data
    all_spec_data = {}
    for idx_path in sorted(indexes_dir.glob("*_index.json")):
        spec_name = idx_path.stem.replace("_index", "")
        with open(idx_path) as f:
            data = json.load(f)
        all_spec_data[spec_name] = data
        meta = data.get("_meta", {})
        repo = meta.get("repo", "")
        if repo:
            # "ethereum/consensus-specs" -> map fragment
            repo_to_spec[repo] = spec_name

    # Build per-spec metadata (endpoints, constants, aliases, fork info)
    for spec_name, data in all_spec_data.items():
        meta = data.get("_meta", {})
        fork_order = meta.get("fork_order", [])

        spec = {
            "meta": {
                "repo": meta.get("repo", ""),
                "fork_order": fork_order,
                "total_items": meta.get("total_items", 0),
                "total_endpoints": meta.get("total_endpoints", 0),
                "features": meta.get("features", []),
                "github_web": meta.get("github_web", ""),
            },
            "endpoints": {},
            "constants": {},
            "type_aliases": {},
            "domains": data.get("domains", {}),
            "fork_summary": data.get("fork_summary", {}),
            "eip_index": data.get("_eip_index", {}),
        }

        for name, ep in data.get("endpoints", {}).items():
            spec["endpoints"][name] = slim_endpoint(name, ep)

        for name, value in data.get("constants", {}).items():
            normed = normalize_constant(name, value)
            if normed:
                spec["constants"][name] = normed

        for name, value in data.get("type_aliases", {}).items():
            normed = normalize_type_alias(name, value)
            if normed:
                spec["type_aliases"][name] = normed

        catalog["specs"][spec_name] = spec

    # Second pass: build unified items with dedup
    # Group items by name across specs
    from collections import defaultdict
    items_by_name = defaultdict(dict)  # name -> {spec_name: item_data}
    for spec_name, data in all_spec_data.items():
        fork_order = data.get("_meta", {}).get("fork_order", [])
        for name, item in data.get("items", {}).items():
            items_by_name[name][spec_name] = {
                "raw": item,
                "fork_order": fork_order,
            }

    for name, spec_entries in items_by_name.items():
        specs_list = list(spec_entries.keys())

        # Pick canonical/primary spec
        canonical = resolve_canonical_spec(catalog["type_map"], name, repo_to_spec)
        if canonical and canonical in spec_entries:
            primary_spec = canonical
        else:
            # Prefer spec with code
            primary_spec = specs_list[0]
            for s in specs_list:
                raw = spec_entries[s]["raw"]
                if any(fd.get("code") for fd in raw.get("forks", {}).values()):
                    primary_spec = s
                    break

        primary_raw = spec_entries[primary_spec]["raw"]
        primary_fo = spec_entries[primary_spec]["fork_order"]
        primary_forks = build_item_forks(primary_raw, primary_fo)

        # Merge secondary specs
        for s in specs_list:
            if s == primary_spec:
                continue
            sec_raw = spec_entries[s]["raw"]
            sec_fo = spec_entries[s]["fork_order"]
            sec_forks = build_item_forks(sec_raw, sec_fo)
            merge_items(primary_raw, primary_forks, sec_raw, sec_forks)

        unified = {
            "name": name,
            "kind": primary_raw.get("kind", ""),
            "domain": primary_raw.get("domain", ""),
            "introduced": primary_raw.get("introduced", ""),
            "spec": primary_spec,
            "specs": sorted(specs_list),
            "forks": primary_forks,
        }
        if primary_raw.get("modified_in"):
            unified["modified_in"] = primary_raw["modified_in"]

        catalog["items"][name] = unified

    # Update spec metadata with item counts
    for spec_name in catalog["specs"]:
        items_in_spec = sum(1 for item in catalog["items"].values() if spec_name in item["specs"])
        catalog["_meta"]["specs"][spec_name] = {
            "items": items_in_spec,
            "endpoints": len(catalog["specs"][spec_name]["endpoints"]),
            "constants": len(catalog["specs"][spec_name]["constants"]),
            "type_aliases": len(catalog["specs"][spec_name]["type_aliases"]),
        }

    # Write
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(catalog, f, separators=(",", ":"))

    size_kb = output_path.stat().st_size / 1024
    print(f"\nCatalog: {output_path} ({size_kb:.0f} KB)", file=sys.stderr)
    print(f"Unified items: {len(catalog['items'])} (deduped from {sum(len(d.get('items',{})) for d in all_spec_data.values())})", file=sys.stderr)
    for sname, counts in catalog["_meta"]["specs"].items():
        print(f"  {sname}: {counts['items']} items, {counts['endpoints']} ep", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--indexes-dir", default="indexes")
    parser.add_argument("--output", default="docs/catalog.json")
    args = parser.parse_args()
    build_catalog(args.indexes_dir, args.output)
