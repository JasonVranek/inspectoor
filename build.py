#!/usr/bin/env python3
"""
The Ethspectoor -- Ethereum Specs Index builder.

Orchestrates extraction, enrichment, and optional example fetching
for one or more spec repos. Auto-clones repos when not provided.

Usage:
    # Build everything from scratch (clones repos, builds, links, catalogs)
    python3 build.py --all

    # Build a single spec (auto-clones if needed)
    python3 build.py --profile builder-specs

    # Build with an existing local clone
    python3 build.py --profile consensus-specs --repo-dir /path/to/consensus-specs
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "extractors"))

from profiles import get_profile, PROFILES
from extract_markdown import extract_all, build_output
from extract_openapi import extract_endpoints, extract_type_schemas
from extract_python import extract_all as extract_python_all, build_output as build_python_output
from extract_openrpc import extract_all as extract_openrpc_all, build_output as build_openrpc_output
from enrich import enrich


def ensure_repo(profile_name: str, repos_dir: str = "./repos/specs") -> tuple[str, str]:
    """Clone or update a spec repo. Returns (repo_dir, branch)."""
    profile = get_profile(profile_name)
    clone_url = f"https://github.com/{profile.repo}.git"
    branch = profile.default_branch
    # Use the repo name from the GitHub path (preserves casing like beacon-APIs)
    repo_name = profile.repo.split("/")[-1]
    repo_dir = os.path.join(repos_dir, repo_name)

    if os.path.isdir(os.path.join(repo_dir, ".git")):
        print(f"  Updating {profile_name} ({branch})...", file=sys.stderr)
        try:
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=repo_dir, capture_output=True, check=True
            )
            subprocess.run(
                ["git", "checkout", branch],
                cwd=repo_dir, capture_output=True, check=True
            )
            subprocess.run(
                ["git", "pull", "--ff-only", "origin", branch],
                cwd=repo_dir, capture_output=True, check=True
            )
        except subprocess.CalledProcessError:
            print(f"    Warning: could not update {profile_name}", file=sys.stderr)
    else:
        print(f"  Cloning {profile_name} ({branch})...", file=sys.stderr)
        os.makedirs(os.path.dirname(repo_dir), exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", branch, clone_url, repo_dir],
            check=True
        )

    return repo_dir, branch


def ensure_eips_repo(repos_dir: str = "./repos/specs") -> str:
    """Clone or update ethereum/EIPs repo for metadata. Returns repo_dir."""
    repo_dir = os.path.join(repos_dir, "EIPs")
    if os.path.isdir(os.path.join(repo_dir, ".git")):
        print("  Updating EIPs repo...", file=sys.stderr)
        try:
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=repo_dir, capture_output=True, check=True
            )
            subprocess.run(
                ["git", "reset", "--hard", "origin/master"],
                cwd=repo_dir, capture_output=True, check=True
            )
        except subprocess.CalledProcessError:
            print("  Warning: EIPs repo update failed, using existing", file=sys.stderr)
    else:
        print("  Cloning ethereum/EIPs...", file=sys.stderr)
        os.makedirs(repos_dir, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1",
             "https://github.com/ethereum/EIPs.git", repo_dir],
            check=True
        )
    return repo_dir


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


def build_all(repos_dir: str = "./repos/specs", output_dir: str = "./indexes",
              skip_enrich: bool = False, include_prs: bool = False) -> None:
    """Build all specs: fetch repos, extract, link, and build catalog."""
    base_dir = Path(__file__).parent
    link_script = str(base_dir / "link.py")
    catalog_script = str(base_dir / "build_catalog.py")

    print("\n" + "=" * 60, file=sys.stderr)
    print("The Ethspectoor -- Full Build", file=sys.stderr)
    print(f"  Repos: {repos_dir}", file=sys.stderr)
    print(f"  Indexes: {output_dir}", file=sys.stderr)
    print("=" * 60 + "\n", file=sys.stderr)

    # Step 1: Clone/update all repos and build indexes
    for profile_name in PROFILES:
        repo_dir, branch = ensure_repo(profile_name, repos_dir)
        build(
            profile_name=profile_name,
            repo_dir=repo_dir,
            branch=branch,
            output_dir=output_dir,
            skip_enrich=skip_enrich,
        )

    # Step 1b: Clone/update EIPs repo for metadata
    eips_repo_dir = ensure_eips_repo(repos_dir)

    # Step 2: Cross-reference linking
    print("\n" + "=" * 60, file=sys.stderr)
    print("Cross-reference linking...", file=sys.stderr)
    print("=" * 60 + "\n", file=sys.stderr)
    subprocess.run(
        [sys.executable, link_script, "--indexes-dir", output_dir],
        check=True
    )

    # Step 3: Index PRs (if requested)
    if include_prs:
        pr_script = str(base_dir / "pr_index.py")
        github_token = os.environ.get("GITHUB_TOKEN", "")
        print("\n" + "=" * 60, file=sys.stderr)
        print("Indexing open PRs...", file=sys.stderr)
        print("=" * 60 + "\n", file=sys.stderr)
        for profile_name in PROFILES:
            profile = get_profile(profile_name)
            repo_name = profile.repo.split("/")[-1]
            repo_dir = os.path.join(repos_dir, repo_name)
            if not os.path.isdir(repo_dir):
                continue
            pr_args = [
                sys.executable, pr_script,
                "--spec", profile_name,
                "--repo-dir", repo_dir,
                "--indexes-dir", output_dir,
            ]
            if github_token:
                pr_args.extend(["--github-token", github_token])
            try:
                subprocess.run(pr_args, check=True)
            except subprocess.CalledProcessError:
                print(f"  Warning: PR indexing failed for {profile_name}", file=sys.stderr)

    # Step 4: Build catalog
    print("\n" + "=" * 60, file=sys.stderr)
    print("Building catalog...", file=sys.stderr)
    print("=" * 60 + "\n", file=sys.stderr)
    catalog_args = [
        sys.executable, catalog_script,
        "--indexes-dir", output_dir,
        "--output", "docs/catalog.json",
    ]
    if include_prs:
        catalog_args.append("--include-prs")
    if os.path.isdir(os.path.join(repos_dir, "EIPs")):
        catalog_args.extend(["--eips-dir", os.path.join(repos_dir, "EIPs")])
    subprocess.run(catalog_args, check=True)

    print("\n" + "=" * 60, file=sys.stderr)
    print("BUILD COMPLETE", file=sys.stderr)
    print(f"  Catalog: docs/catalog.json", file=sys.stderr)
    print(f"  Open docs/index.html to explore", file=sys.stderr)
    print("=" * 60 + "\n", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="The Ethspectoor -- build Ethereum spec indexes")
    parser.add_argument("--all", action="store_true",
                        help="Build all specs (clones repos if needed, links, builds catalog)")
    parser.add_argument("--profile",
                        help="Spec profile (consensus-specs, builder-specs, etc.)")
    parser.add_argument("--repo-dir",
                        help="Path to local repo clone (auto-clones if omitted)")
    parser.add_argument("--repos-dir", default="./repos/specs",
                        help="Base directory for auto-cloned repos (default: ./repos/specs)")
    parser.add_argument("--output-dir", default="./indexes", help="Output directory")
    parser.add_argument("--branch", help="Git branch (default: from profile)")
    parser.add_argument("--skip-enrich", action="store_true", help="Skip enrichment pass")
    parser.add_argument("--include-prs", action="store_true",
                        help="Include PR overlays in catalog (only with --all)")
    args = parser.parse_args()

    if args.all:
        build_all(
            repos_dir=args.repos_dir,
            output_dir=args.output_dir,
            skip_enrich=args.skip_enrich,
            include_prs=args.include_prs,
        )
    elif args.profile:
        if args.repo_dir:
            repo_dir = args.repo_dir
            branch = args.branch or get_profile(args.profile).default_branch
        else:
            repo_dir, branch = ensure_repo(args.profile, args.repos_dir)
            if args.branch:
                branch = args.branch
        build(
            profile_name=args.profile,
            repo_dir=repo_dir,
            branch=branch,
            output_dir=args.output_dir,
            skip_enrich=args.skip_enrich,
        )
    else:
        parser.error("Either --all or --profile is required")


if __name__ == "__main__":
    main()
