#!/usr/bin/env python3
"""
The Inspectoor -- Ethereum Specs Index builder.

Orchestrates extraction, enrichment, and optional example fetching
for one or more spec repos.

Usage:
    python3 build.py --profile builder-specs --repo-dir /path/to/builder-specs
    python3 build.py --profile consensus-specs --repo-dir /path/to/consensus-specs
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "extractors"))

from profiles import get_profile
from extract_markdown import extract_all, build_output
from extract_openapi import extract_endpoints, extract_type_schemas
from extract_python import extract_all as extract_python_all, build_output as build_python_output
from extract_openrpc import extract_all as extract_openrpc_all, build_output as build_openrpc_output
from enrich import enrich


def build(profile_name: str, repo_dir: str, branch: str = "main",
          output_dir: str = "./indexes", skip_enrich: bool = False) -> str:

    profile = get_profile(profile_name)
    output_path = os.path.join(output_dir, f"{profile.name}_index.json")

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Building index: {profile.name}", file=sys.stderr)
    print(f"  Source: {profile.repo}", file=sys.stderr)
    print(f"  Repo dir: {repo_dir}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    is_pure_openapi = profile.source_format == "openapi"
    is_python = profile.source_format == "python"
    is_openrpc = profile.source_format == "openrpc"

    if is_openrpc:
        # OpenRPC extraction (e.g., execution-apis)
        print("Step 1: Extracting OpenRPC methods and schemas...", file=sys.stderr)
        items, endpoints, eip_map = extract_openrpc_all(
            profile=profile, repo_dir=repo_dir, branch=branch,
        )

        print(f"\nStep 2: Building output structure...", file=sys.stderr)
        output = build_openrpc_output(
            profile, items, endpoints, eip_map, branch
        )

        print(f"\nStep 3: Skipping markdown enrichment (OpenRPC)", file=sys.stderr)

    elif is_python:
        # Python AST extraction (e.g., execution-specs)
        print("Step 1: Extracting Python source definitions...", file=sys.stderr)
        items, constants, type_aliases, files_processed, fork_order, eip_map = extract_python_all(
            profile=profile, repo_dir=repo_dir, branch=branch,
        )

        print(f"\nStep 2: Building output structure...", file=sys.stderr)
        output = build_python_output(
            profile, items, constants, type_aliases,
            files_processed, fork_order, eip_map, branch
        )

        print(f"\nStep 3: Skipping markdown enrichment (Python source)", file=sys.stderr)

    elif is_pure_openapi:
        # Pure OpenAPI profile (e.g., beacon-APIs): extract types from YAML schemas
        print("Step 1: Extracting type schemas from OpenAPI...", file=sys.stderr)
        type_schema_items = extract_type_schemas(profile, repo_dir, branch)

        # Detect fork order from the type schema items
        forks_seen = set()
        for item in type_schema_items.values():
            forks_seen.update(item["forks"].keys())
        fork_order = [f for f in profile.default_fork_order if f in forks_seen]

        # Build type_map
        type_map = {}
        for name, item in type_schema_items.items():
            type_map[name] = {
                "source": profile.repo,
                "introduced": item["introduced"],
                "kind": "class",
            }

        # Build domains
        domain_items = {}
        for name, item in type_schema_items.items():
            d = item["domain"]
            if d not in domain_items:
                domain_items[d] = {"classes": [], "functions": [], "other": []}
            domain_items[d]["classes"].append(name)

        # Build fork summary
        fork_summary = {}
        for fork in fork_order:
            new_items = [n for n, it in type_schema_items.items() if it["introduced"] == fork]
            modified_items = [n for n, it in type_schema_items.items()
                             if fork in it["forks"] and it["introduced"] != fork]
            if new_items or modified_items:
                fork_summary[fork] = {
                    "new": sorted(new_items),
                    "modified": sorted(modified_items),
                    "total_definitions": len(new_items) + len(modified_items),
                }

        # Build reverse references
        ref_index = {}
        for name, item in type_schema_items.items():
            for fork_data in item["forks"].values():
                for ref in fork_data.get("references", []):
                    if ref not in ref_index:
                        ref_index[ref] = set()
                    ref_index[ref].add(name)

        output = {
            "_meta": {
                "schema_version": "1.0.0",
                "generated_by": "extract_openapi.py",
                "source": profile.repo,
                "source_format": profile.source_format,
                "branch": branch,
                "fork_order": fork_order,
                "features": [],
                "files_processed": [],
                "total_items": len(type_schema_items),
                "total_constants": 0,
                "total_type_aliases": 0,
            },
            "domains": {
                domain: {
                    "classes": sorted(info["classes"]),
                    "functions": sorted(info["functions"]),
                    "other": sorted(info["other"]),
                }
                for domain, info in sorted(domain_items.items())
            },
            "fork_summary": fork_summary,
            "items": {name: item for name, item in sorted(type_schema_items.items())},
            "constants": {},
            "type_aliases": {},
            "_type_map": type_map,
            "_references": {k: sorted(v) for k, v in sorted(ref_index.items())},
        }

        print(f"\nStep 2: Extracted {len(type_schema_items)} types", file=sys.stderr)
        print("\nStep 3: Skipping markdown enrichment (pure OpenAPI)", file=sys.stderr)

    else:
        # Standard markdown+optional OpenAPI profile
        print("Step 1: Extracting markdown definitions...", file=sys.stderr)
        items, constants, type_aliases, files_processed, fork_order, features = extract_all(
            profile=profile, repo_dir=repo_dir, branch=branch,
        )

        print("\nStep 2: Building output structure...", file=sys.stderr)
        output = build_output(
            profile, items, constants, type_aliases,
            files_processed, fork_order, features, branch
        )

        if not skip_enrich:
            print("\nStep 3: Enriching...", file=sys.stderr)
            output = enrich(output)
        else:
            print("\nStep 3: Skipping enrichment", file=sys.stderr)

    # Step 4: Extract OpenAPI endpoints (skip for openrpc -- already has endpoints)
    if is_openrpc:
        print("\nStep 4: Endpoints already extracted (OpenRPC)", file=sys.stderr)
        output["_meta"]["total_endpoints"] = len(output.get("endpoints", {}))
    elif profile.openapi_file:
        print("\nStep 4: Extracting OpenAPI endpoints...", file=sys.stderr)
        endpoints = extract_endpoints(profile, repo_dir, branch)
        output["endpoints"] = endpoints
        output["_meta"]["total_endpoints"] = len(endpoints)
    else:
        print("\nStep 4: No OpenAPI file, skipping endpoint extraction", file=sys.stderr)
        output["endpoints"] = {}

    # Write output
    os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    # Summary
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"DONE: {profile.name}", file=sys.stderr)
    print(f"  Items: {output['_meta']['total_items']}", file=sys.stderr)
    print(f"  Constants: {output['_meta']['total_constants']}", file=sys.stderr)
    print(f"  Type aliases: {output['_meta']['total_type_aliases']}", file=sys.stderr)
    print(f"  Endpoints: {len(output.get('endpoints', {}))}", file=sys.stderr)
    if not is_pure_openapi and not skip_enrich:
        print(f"  References: {output['_meta'].get('total_references', 'N/A')}", file=sys.stderr)
        print(f"  EIPs: {output['_meta'].get('total_eips', 'N/A')}", file=sys.stderr)
    print(f"  Output: {output_path}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    return output_path


def main():
    parser = argparse.ArgumentParser(description="The Inspectoor -- build Ethereum spec indexes")
    parser.add_argument("--profile", required=True,
                        help="Spec profile (consensus-specs, builder-specs, relay-specs, beacon-apis)")
    parser.add_argument("--repo-dir", required=True, help="Path to local repo clone")
    parser.add_argument("--output-dir", default="./indexes", help="Output directory")
    parser.add_argument("--branch", default="main", help="Git branch for GitHub URLs")
    parser.add_argument("--skip-enrich", action="store_true", help="Skip enrichment pass")
    args = parser.parse_args()

    build(
        profile_name=args.profile,
        repo_dir=args.repo_dir,
        branch=args.branch,
        output_dir=args.output_dir,
        skip_enrich=args.skip_enrich,
    )


if __name__ == "__main__":
    main()
