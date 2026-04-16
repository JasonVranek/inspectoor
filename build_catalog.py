#!/usr/bin/env python3
"""
Build a slim catalog.json from full spec indexes for the Explorer UI.

Merges types that appear in multiple specs into a single entry using
the canonical spec (from type_map) as the primary source. Deduplicates
code across forks (only stores code when it changes between forks).
"""

import argparse
import json
import os
import re
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


def parse_eip_frontmatter(eips_dir, eip_number):
    """Parse YAML frontmatter from an EIP markdown file."""
    eip_path = os.path.join(eips_dir, "EIPS", f"eip-{eip_number}.md")
    if not os.path.isfile(eip_path):
        return None
    with open(eip_path) as f:
        text = f.read()
    if not text.startswith("---"):
        return None
    try:
        end = text.index("---", 3)
    except ValueError:
        return None
    meta = {}
    for line in text[3:end].strip().split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip()
    return meta


def build_eip_index(items, fork_orders, eips_dir=None):
    """Extract EIP references from item code annotations and build an index.

    Scans all items for [New in Fork:EIPXXXX] and [Modified in Fork:EIPXXXX]
    patterns in code strings. Groups by EIP number. Optionally enriches with
    metadata from the ethereum/EIPs repo.

    Args:
        items: catalog items dict (name -> item with forks)
        fork_orders: dict of spec_name -> fork_order list
        eips_dir: path to cloned ethereum/EIPs repo (optional)

    Returns:
        dict keyed by EIP number string (e.g. "7549")
    """
    # Matches both "[New in Electra:EIP7549]" and "[New in EIP7928]" (no fork prefix)
    # Also handles multi-EIP: "[New in Electra:EIP7002:EIP7251]"
    pattern = re.compile(r"\[(New|Modified) in ([^\]]+)\]")
    eip_pattern = re.compile(r"EIP(\d+)")
    # Collect all annotations
    # key: (eip_num, item_name, spec, change_type) to dedup
    annotations = {}
    for item_name, item in items.items():
        spec = item.get("spec", "")
        for fork_name, fork_data in item.get("forks", {}).items():
            code = fork_data.get("code", "")
            if not code:
                continue
            for match in pattern.finditer(code):
                change_type = match.group(1).lower()  # "new" or "modified"
                inner = match.group(2)                 # e.g. "Electra:EIP7002:EIP7251" or "EIP7928"
                parts = inner.split(":")
                # Determine fork: first part if it's not an EIP ref, else use fork_name
                if parts[0].startswith("EIP"):
                    anno_fork = fork_name  # no named fork, use the fork key
                else:
                    anno_fork = parts[0].lower()
                # Extract all EIP numbers
                for eip_match in eip_pattern.finditer(inner):
                    eip_num = eip_match.group(1)
                    key = (eip_num, item_name, spec, change_type)
                    if key not in annotations:
                        annotations[key] = {
                            "name": item_name,
                            "kind": item.get("kind", ""),
                            "spec": spec,
                            "change": change_type,
                            "fork": anno_fork,
                        }

    # Group by EIP
    from collections import defaultdict
    eip_groups = defaultdict(list)
    for key, anno in annotations.items():
        eip_groups[key[0]].append(anno)

    # Build the index
    # Determine fork ordering for finding "earliest fork"
    all_fork_order = []
    for fo in fork_orders.values():
        for f in fo:
            if f not in all_fork_order:
                all_fork_order.append(f)

    eip_index = {}
    for eip_num, annos in sorted(eip_groups.items(), key=lambda x: int(x[0])):
        # Find earliest fork
        forks_seen = set(a["fork"] for a in annos)
        earliest = None
        for f in all_fork_order:
            if f in forks_seen:
                earliest = f
                break
        if earliest is None:
            earliest = sorted(forks_seen)[0]

        # Dedup items and build summary
        items_list = []
        seen = set()
        for a in annos:
            item_key = (a["name"], a["spec"], a["change"])
            if item_key not in seen:
                seen.add(item_key)
                items_list.append({
                    "name": a["name"],
                    "kind": a["kind"],
                    "spec": a["spec"],
                    "change": a["change"],
                })

        # Sort: types first, then functions, alphabetical within
        kind_order = {"class": 0, "def": 1}
        items_list.sort(key=lambda x: (kind_order.get(x["kind"], 2), x["name"]))

        new_count = sum(1 for i in items_list if i["change"] == "new")
        mod_count = sum(1 for i in items_list if i["change"] == "modified")
        specs_touched = sorted(set(i["spec"] for i in items_list))

        entry = {
            "number": int(eip_num),
            "fork": earliest,
            "items": items_list,
            "summary": {
                "new": new_count,
                "modified": mod_count,
                "total": len(items_list),
                "specs": specs_touched,
            },
        }

        # Enrich with EIPs repo metadata
        if eips_dir:
            meta = parse_eip_frontmatter(eips_dir, eip_num)
            if meta:
                entry["title"] = meta.get("title", "")
                entry["authors"] = meta.get("author", "")
                entry["status"] = meta.get("status", "")
                entry["category"] = meta.get("category", "")
                entry["created"] = meta.get("created", "")
                requires = meta.get("requires", "")
                if requires:
                    entry["requires"] = requires

        entry["url"] = f"https://eips.ethereum.org/EIPS/eip-{eip_num}"

        eip_index[eip_num] = entry

    return eip_index


def build_catalog(indexes_dir, output_path, include_prs=False, eips_dir=None):
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
            "references": data.get("_references", {}),
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

    # Build EIP index from code annotations
    fork_orders = {}
    for spec_name, data in all_spec_data.items():
        fork_orders[spec_name] = data.get("_meta", {}).get("fork_order", [])
    eip_index = build_eip_index(catalog["items"], fork_orders, eips_dir=eips_dir)
    if eip_index:
        catalog["eip_index"] = eip_index
        print(f"EIP index: {len(eip_index)} EIPs referenced in specs", file=sys.stderr)

    # Update spec metadata with item counts
    for spec_name in catalog["specs"]:
        items_in_spec = sum(1 for item in catalog["items"].values() if spec_name in item["specs"])
        catalog["_meta"]["specs"][spec_name] = {
            "items": items_in_spec,
            "endpoints": len(catalog["specs"][spec_name]["endpoints"]),
            "constants": len(catalog["specs"][spec_name]["constants"]),
            "type_aliases": len(catalog["specs"][spec_name]["type_aliases"]),
        }

    # Merge PR overlays (if requested)
    if include_prs:
        pr_dir = indexes_dir / "pr"
        if pr_dir.exists():
            pr_overlays = {}
            for spec_dir in sorted(pr_dir.iterdir()):
                if not spec_dir.is_dir():
                    continue
                spec_name = spec_dir.name
                pr_overlays[spec_name] = {}
                for pr_file in sorted(spec_dir.glob("pr-*.json")):
                    with open(pr_file) as f:
                        overlay = json.load(f)
                    pr_num = str(overlay.get("number", pr_file.stem.replace("pr-", "")))
                    pr_overlays[spec_name][pr_num] = {
                        k: overlay[k] for k in overlay if k != "_meta"
                    }
                if not pr_overlays[spec_name]:
                    del pr_overlays[spec_name]

            if pr_overlays:
                catalog["pr_overlays"] = pr_overlays
                total_prs = sum(len(v) for v in pr_overlays.values())
                print(f"PR overlays: {total_prs} PRs across {len(pr_overlays)} specs",
                      file=sys.stderr)

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
    parser.add_argument("--include-prs", action="store_true",
                        help="Include PR shadow overlays in catalog")
    parser.add_argument("--eips-dir", default=None,
                        help="Path to cloned ethereum/EIPs repo for metadata")
    args = parser.parse_args()
    build_catalog(args.indexes_dir, args.output, include_prs=args.include_prs, eips_dir=args.eips_dir)
