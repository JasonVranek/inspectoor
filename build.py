#!/usr/bin/env python3
"""
Ethereum Specs Index builder. Orchestrates extraction, enrichment, and
optional example fetching for one or more spec repos.

Usage:
    python3 build.py --profile builder-specs --repo-dir /path/to/builder-specs
    python3 build.py --profile consensus-specs --repo-dir /path/to/consensus-specs
    python3 build.py --profile builder-specs --repo-dir /path/to/builder-specs --skip-enrich
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add extractors to path
sys.path.insert(0, str(Path(__file__).parent / "extractors"))

from profiles import get_profile
from extract_markdown import extract_all, build_output
from extract_openapi import extract_endpoints
from enrich import enrich


def build(profile_name: str, repo_dir: str, branch: str = "main",
          output_dir: str = "./indexes", skip_enrich: bool = False,
          skip_examples: bool = True) -> str:
    """Build a complete spec index for one repo.

    Returns the output file path.
    """
    profile = get_profile(profile_name)
    output_path = os.path.join(output_dir, f"{profile.name}_index.json")

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Building index: {profile.name}", file=sys.stderr)
    print(f"  Source: {profile.repo}", file=sys.stderr)
    print(f"  Repo dir: {repo_dir}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    # Step 1: Extract markdown definitions
    print("Step 1: Extracting markdown definitions...", file=sys.stderr)
    items, constants, type_aliases, files_processed, fork_order, features = extract_all(
        profile=profile,
        repo_dir=repo_dir,
        branch=branch,
    )

    # Step 2: Build base output
    print("\nStep 2: Building output structure...", file=sys.stderr)
    output = build_output(
        profile, items, constants, type_aliases,
        files_processed, fork_order, features, branch
    )

    # Step 3: Enrich (fields, signatures, references, EIPs)
    if not skip_enrich:
        print("\nStep 3: Enriching...", file=sys.stderr)
        output = enrich(output)
    else:
        print("\nStep 3: Skipping enrichment", file=sys.stderr)

    # Step 4: Extract OpenAPI endpoints
    if profile.openapi_file:
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
    if not skip_enrich:
        print(f"  References: {output['_meta'].get('total_references', 'N/A')}", file=sys.stderr)
        print(f"  EIPs: {output['_meta'].get('total_eips', 'N/A')}", file=sys.stderr)
    print(f"  Output: {output_path}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Build Ethereum spec indexes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 build.py --profile builder-specs --repo-dir ./repos/specs/builder-specs
  python3 build.py --profile consensus-specs --repo-dir ./repos/specs/consensus-specs
  python3 build.py --profile relay-specs --repo-dir ./repos/specs/relay-specs
        """
    )
    parser.add_argument("--profile", required=True,
                        help="Spec profile (consensus-specs, builder-specs, relay-specs)")
    parser.add_argument("--repo-dir", required=True,
                        help="Path to local repo clone")
    parser.add_argument("--output-dir", default="./indexes",
                        help="Output directory (default: ./indexes)")
    parser.add_argument("--branch", default="main",
                        help="Git branch for GitHub URLs (default: main)")
    parser.add_argument("--skip-enrich", action="store_true",
                        help="Skip enrichment pass")
    parser.add_argument("--skip-examples", action="store_true", default=True,
                        help="Skip fetching test fixture examples")
    args = parser.parse_args()

    build(
        profile_name=args.profile,
        repo_dir=args.repo_dir,
        branch=args.branch,
        output_dir=args.output_dir,
        skip_enrich=args.skip_enrich,
        skip_examples=args.skip_examples,
    )


if __name__ == "__main__":
    main()
