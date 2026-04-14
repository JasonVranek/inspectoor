#!/usr/bin/env python3
"""
OpenAPI spec extractor. Parses OpenAPI YAML files and produces endpoint
definitions conforming to the unified schema.

Handles $ref resolution for endpoint files that live in separate YAML files.

Usage:
    python3 extract_openapi.py --profile builder-specs --repo-dir /path/to/repo
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    print("pyyaml required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

from profiles import SpecProfile, get_profile


def load_yaml(path: Path) -> Optional[dict]:
    """Load a YAML file, returning None if not found."""
    if not path.exists():
        return None
    with open(path) as f:
        return yaml.safe_load(f)


def resolve_ref_path(ref: str) -> str:
    """Extract the file path portion of a $ref."""
    if "#" in ref:
        return ref.split("#")[0]
    return ref


def extract_schema_name(ref: str) -> str:
    """Extract a human-readable type name from a $ref string.

    e.g. "../../builder-oapi.yaml#/components/schemas/Bellatrix.SignedBuilderBid"
    -> "Bellatrix.SignedBuilderBid"
    """
    if "#" in ref:
        path = ref.split("#")[1]
        return path.split("/")[-1]
    return ref.split("/")[-1]


def extract_type_from_schema(schema: dict) -> str:
    """Extract a human-readable type string from a schema object."""
    if "$ref" in schema:
        return extract_schema_name(schema["$ref"])
    if "type" in schema:
        t = schema["type"]
        if t == "array" and "items" in schema:
            inner = extract_type_from_schema(schema["items"])
            return f"List[{inner}]"
        return t
    if "anyOf" in schema:
        types = [extract_type_from_schema(s) for s in schema["anyOf"]]
        return " | ".join(types)
    if "oneOf" in schema:
        types = [extract_type_from_schema(s) for s in schema["oneOf"]]
        return " | ".join(types)
    return "unknown"


def extract_fork_variants(schema: dict) -> dict:
    """Extract fork-versioned variants from anyOf/oneOf schemas.

    Returns {fork_name: type_name} or empty dict if not fork-versioned.
    """
    variants = {}
    for key in ("anyOf", "oneOf"):
        if key in schema:
            for item in schema[key]:
                if "$ref" in item:
                    name = extract_schema_name(item["$ref"])
                    # Pattern: "Bellatrix.SignedBuilderBid" -> fork="bellatrix"
                    parts = name.split(".")
                    if len(parts) >= 2:
                        fork = parts[0].lower()
                        variants[fork] = name
    return variants


def extract_content_negotiation(operation: dict) -> dict:
    """Extract content negotiation details from an operation."""
    request_types = []
    response_types = []
    ssz_support = False

    # Request content types
    if "requestBody" in operation:
        rb = operation["requestBody"]
        if "content" in rb:
            request_types = list(rb["content"].keys())

    # Response content types (from 200 response)
    responses = operation.get("responses", {})
    for code, resp in responses.items():
        if code.startswith("2") and "content" in resp:
            response_types = list(resp["content"].keys())
            break

    if "application/octet-stream" in response_types or "application/octet-stream" in request_types:
        ssz_support = True

    # Check description for SSZ notes
    desc = operation.get("description", "")
    if "octet-stream" in desc or "SSZ" in desc:
        ssz_support = True

    result = {
        "request_types": request_types or ["application/json"],
        "response_types": response_types or ["application/json"],
        "ssz_support": ssz_support,
    }

    # Add notes about content negotiation if present in description
    if ssz_support:
        result["notes"] = "Supports SSZ via Accept: application/octet-stream"

    return result


def extract_examples(operation: dict, responses: dict) -> dict:
    """Extract example values from operation responses."""
    examples = {}
    for code, resp in responses.items():
        if not code.startswith("2"):
            continue
        if "content" not in resp:
            continue
        for ct, media in resp["content"].items():
            if "examples" in media:
                for name, ex in media["examples"].items():
                    if "$ref" in ex:
                        examples[name] = {"ref": extract_schema_name(ex["$ref"])}
                    elif "value" in ex:
                        examples[name] = {"value": ex["value"]}
            elif "example" in media:
                examples["default"] = {"value": media["example"]}
    return examples


def extract_errors(responses: dict) -> dict:
    """Extract error response details."""
    errors = {}
    for code, resp in responses.items():
        if code.startswith("2"):
            continue
        error = {"description": resp.get("description", "")}
        if "content" in resp:
            for ct, media in resp["content"].items():
                if "schema" in media:
                    if "$ref" in media["schema"]:
                        error["schema_ref"] = extract_schema_name(media["schema"]["$ref"])
                if "examples" in media:
                    for name, ex in media["examples"].items():
                        if "value" in ex:
                            error["example"] = ex["value"]
                            break
        # Also handle $ref at the response level
        if "$ref" in resp:
            ref_name = extract_schema_name(resp["$ref"])
            error["ref"] = ref_name
        errors[code] = error
    return errors


def extract_parameters(params: list) -> list:
    """Extract parameter definitions."""
    result = []
    for p in params:
        param = {
            "name": p.get("name", ""),
            "in": p.get("in", ""),
            "required": p.get("required", False),
            "description": p.get("description", "").strip(),
        }
        if "schema" in p:
            if "$ref" in p["schema"]:
                param["type"] = extract_schema_name(p["schema"]["$ref"])
            else:
                param["type"] = p["schema"].get("type", "string")
                if "format" in p["schema"]:
                    param["format"] = p["schema"]["format"]
        result.append(param)
    return result


def extract_request_body(operation: dict) -> Optional[dict]:
    """Extract request body schema."""
    if "requestBody" not in operation:
        return None
    rb = operation["requestBody"]
    result = {
        "description": rb.get("description", ""),
        "required": rb.get("required", True),
        "content_types": [],
        "schemas": {},
    }
    if "content" in rb:
        for ct, media in rb["content"].items():
            result["content_types"].append(ct)
            if "schema" in media:
                schema = media["schema"]
                if "$ref" in schema:
                    result["schemas"][ct] = extract_schema_name(schema["$ref"])
                elif "anyOf" in schema or "oneOf" in schema:
                    variants = extract_fork_variants(schema)
                    if variants:
                        result["schemas"][ct] = variants
                    else:
                        result["schemas"][ct] = extract_type_from_schema(schema)
                else:
                    result["schemas"][ct] = extract_type_from_schema(schema)
    return result


def extract_endpoints(profile: SpecProfile, repo_dir: str, branch: str = "main") -> dict:
    """Extract all endpoint definitions from an OpenAPI spec."""
    if not profile.openapi_file:
        return {}

    repo_path = Path(repo_dir)
    oapi_path = repo_path / profile.openapi_file

    spec = load_yaml(oapi_path)
    if not spec:
        print(f"  Warning: OpenAPI file not found: {oapi_path}", file=sys.stderr)
        return {}

    print(f"  Parsing OpenAPI: {profile.openapi_file}", file=sys.stderr)

    endpoints = {}
    paths = spec.get("paths", {})

    for path, path_item in paths.items():
        # Resolve $ref if the path item references an external file
        if "$ref" in path_item:
            ref_file = resolve_ref_path(path_item["$ref"])
            ref_path = (repo_path / ref_file) if not ref_file.startswith("/") else Path(ref_file)
            # Resolve relative to the oapi file location
            if not ref_path.is_absolute():
                ref_path = oapi_path.parent / ref_file
            resolved = load_yaml(ref_path)
            if resolved:
                path_item = resolved
            else:
                print(f"  Warning: Could not resolve $ref {ref_file}", file=sys.stderr)
                continue

        for method_lower in ("get", "post", "put", "delete", "patch"):
            if method_lower not in path_item:
                continue

            operation = path_item[method_lower]
            method = method_lower.upper()
            key = f"{method} {path}"

            responses = operation.get("responses", {})

            # Detect fork-versioned responses
            fork_versioned = False
            fork_variants = {}
            for code, resp in responses.items():
                if not code.startswith("2"):
                    continue
                if "content" in resp:
                    for ct, media in resp["content"].items():
                        if "schema" in media:
                            schema = media["schema"]
                            # Check nested: schema.properties.data.anyOf
                            if "properties" in schema and "data" in schema["properties"]:
                                data_schema = schema["properties"]["data"]
                                fv = extract_fork_variants(data_schema)
                                if fv:
                                    fork_versioned = True
                                    fork_variants = fv
                            else:
                                fv = extract_fork_variants(schema)
                                if fv:
                                    fork_versioned = True
                                    fork_variants = fv

            # Determine the primary response schema
            schema_ref = None
            for code, resp in responses.items():
                if not code.startswith("2"):
                    continue
                if "content" in resp:
                    for ct, media in resp["content"].items():
                        if "schema" in media:
                            s = media["schema"]
                            if "$ref" in s:
                                schema_ref = extract_schema_name(s["$ref"])
                            elif "properties" in s and "data" in s["properties"]:
                                data = s["properties"]["data"]
                                if "$ref" in data:
                                    schema_ref = extract_schema_name(data["$ref"])
                                elif fork_variants:
                                    # Use the base name without fork prefix
                                    first_variant = list(fork_variants.values())[0]
                                    schema_ref = first_variant.split(".")[-1] if "." in first_variant else first_variant
                    break

            # Build the github_url for this endpoint's source file
            source_file = None
            if "$ref" in (paths.get(path) or {}):
                source_file = resolve_ref_path(paths[path]["$ref"])
            else:
                source_file = profile.openapi_file
            github_base = profile.github_web.replace("/specs", "").format(branch=branch)
            github_url = f"{github_base}/{source_file}" if source_file else ""

            endpoint = {
                "method": method,
                "path": path,
                "operation_id": operation.get("operationId", ""),
                "summary": operation.get("summary", "").strip(),
                "description": operation.get("description", "").strip(),
                "tags": operation.get("tags", []),
                "parameters": extract_parameters(operation.get("parameters", [])),
                "request_body": extract_request_body(operation),
                "responses": {},
                "fork_versioned": fork_versioned,
                "fork_variants": fork_variants,
                "content_negotiation": extract_content_negotiation(operation),
                "examples": extract_examples(operation, responses),
                "errors": extract_errors(responses),
                "source_file": source_file,
                "github_url": github_url,
            }

            if schema_ref:
                endpoint["schema_ref"] = schema_ref

            # Build simplified response map
            for code, resp in responses.items():
                resp_entry = {"description": resp.get("description", "")}
                if "content" in resp:
                    resp_entry["content_types"] = list(resp["content"].keys())
                    # Get schema ref for this response
                    for ct, media in resp["content"].items():
                        if "schema" in media:
                            if "$ref" in media["schema"]:
                                resp_entry["schema_ref"] = extract_schema_name(media["schema"]["$ref"])
                            break
                endpoint["responses"][code] = resp_entry

            endpoints[key] = endpoint

    print(f"  Extracted {len(endpoints)} endpoints", file=sys.stderr)
    return endpoints


# ── CLI ────────────────────────────────────────────────────────────────────

# Known fork names for suffix matching (order matters: longest first to avoid
# partial matches like "phase0" matching before "phase0altair" if that existed)
_KNOWN_FORKS = [
    "bellatrix", "capella", "electra", "deneb", "fulu",
    "altair", "phase0", "gloas", "heze",
]


def _infer_fork_from_name(name: str, default_fork: str = "phase0") -> tuple:
    """Infer fork from type name suffix. Returns (base_name, fork).

    Examples:
        BlockRequestFulu -> (BlockRequest, fulu)
        AggregateAndProofElectra -> (AggregateAndProof, electra)
        AttestationData -> (AttestationData, phase0)
    """
    name_lower = name.lower()
    for fork in _KNOWN_FORKS:
        if name_lower.endswith(fork):
            base = name[:len(name) - len(fork)]
            # Only strip if the base isn't empty and the suffix is capitalized
            if base and name[len(base):][0].isupper():
                return base.rstrip("_"), fork
    return name, default_fork


def _extract_flat_schemas(profile, repo_path, schema_path, branch: str) -> dict:
    """Extract schemas from a flat YAML file (no fork directories).

    Fork membership is inferred from type name suffixes. Types without a
    recognized fork suffix are assigned to the profile's first_fork.
    """
    print(f"  Extracting flat schemas from {schema_path.relative_to(repo_path)}...", file=sys.stderr)

    data = load_yaml(schema_path)
    if not data:
        return {}

    # Navigate to components.schemas (standard OpenAPI location)
    schemas = data
    if "components" in data and "schemas" in data["components"]:
        schemas = data["components"]["schemas"]

    items = {}
    default_fork = profile.first_fork or "phase0"
    rel_path = str(schema_path.relative_to(repo_path))
    github_base = profile.github_web.format(branch=branch)

    # Domain classification for remote-signing-api types
    domain_rules = {
        "signing": "signing", "sign": "signing",
        "block": "block-signing", "beacon": "block-signing",
        "attestation": "attestation", "attester": "attestation",
        "aggregate": "aggregation", "aggregation": "aggregation",
        "sync": "sync-committee", "contribution": "sync-committee",
        "deposit": "deposit", "voluntary": "voluntary-exit",
        "validator": "validator-registration", "registration": "validator-registration",
        "randao": "randao",
        "fork": "primitives", "checkpoint": "primitives", "eth1": "primitives",
        "proposer": "primitives", "indexed": "primitives",
    }

    for type_name, schema in schemas.items():
        if not isinstance(schema, dict):
            continue

        base_name, fork = _infer_fork_from_name(type_name, default_fork)
        github_url = f"{github_base}/{rel_path}"

        # Extract fields from properties (handles allOf composition)
        fields = []
        refs = set()
        _collect_fields_and_refs(schema, fields, refs)

        # Domain from type name
        domain = "other"
        name_lower = type_name.lower()
        for keyword, dom in domain_rules.items():
            if keyword in name_lower:
                domain = dom
                break

        fork_data = {
            "fork": fork,
            "file": rel_path,
            "line_number": 1,
            "kind": "class",
            "is_new": True,
            "is_modified": False,
            "code": "",
            "fields": fields,
            "github_url": github_url,
            "description": schema.get("description", ""),
            "section_path": [],
            "inline_comments": [],
            "references": sorted(refs) if refs else [],
        }

        items[type_name] = {
            "name": type_name,
            "kind": "class",
            "domain": domain,
            "introduced": fork,
            "modified_in": [],
            "forks": {fork: fork_data},
        }

    print(f"  Extracted {len(items)} schemas from flat file", file=sys.stderr)
    return items


def _collect_fields_and_refs(schema: dict, fields: list, refs: set):
    """Recursively collect fields and $ref references from a schema.

    Handles allOf/oneOf composition and nested $ref.
    """
    if "$ref" in schema:
        ref_name = extract_schema_name(schema["$ref"])
        if "." in ref_name:
            ref_name = ref_name.split(".")[-1]
        refs.add(ref_name)

    if "properties" in schema:
        for fname, fschema in schema["properties"].items():
            if not isinstance(fschema, dict):
                continue
            ftype = extract_type_from_schema(fschema)
            fields.append({
                "name": fname,
                "type": ftype,
                "description": fschema.get("description", ""),
            })
            # Collect nested refs
            if "$ref" in fschema:
                ref_name = extract_schema_name(fschema["$ref"])
                if "." in ref_name:
                    ref_name = ref_name.split(".")[-1]
                refs.add(ref_name)
            if "items" in fschema and isinstance(fschema["items"], dict) and "$ref" in fschema["items"]:
                ref_name = extract_schema_name(fschema["items"]["$ref"])
                if "." in ref_name:
                    ref_name = ref_name.split(".")[-1]
                refs.add(ref_name)

    # Recurse into allOf/oneOf
    for key in ("allOf", "oneOf"):
        if key in schema and isinstance(schema[key], list):
            for sub in schema[key]:
                if isinstance(sub, dict):
                    _collect_fields_and_refs(sub, fields, refs)



def extract_type_schemas(profile: SpecProfile, repo_dir: str, branch: str = "main") -> dict:
    """Extract type schemas from OpenAPI type directories (beacon-APIs style).

    Returns items dict compatible with the unified schema.
    """
    repo_path = Path(repo_dir)
    types_dir = repo_path / "types"

    # Flat schema file mode (e.g. remote-signing-api: all schemas in one file)
    if hasattr(profile, 'schema_file') and profile.schema_file:
        schema_path = repo_path / profile.schema_file
        if schema_path.exists():
            return _extract_flat_schemas(profile, repo_path, schema_path, branch)

    if not types_dir.is_dir():
        return {}

    print(f"  Extracting type schemas from types/...", file=sys.stderr)

    items = {}
    fork_dirs = sorted([
        d.name for d in types_dir.iterdir()
        if d.is_dir() and not d.name.startswith(("_", "."))
    ])

    for fork in fork_dirs:
        fork_dir = types_dir / fork
        for yaml_file in sorted(fork_dir.glob("*.yaml")):
            data = load_yaml(yaml_file)
            if not data or not isinstance(data, dict):
                continue

            for namespace, type_defs in data.items():
                if not isinstance(type_defs, dict):
                    continue
                for type_name, schema in type_defs.items():
                    if not isinstance(schema, dict):
                        continue

                    full_name = type_name
                    file_path = f"types/{fork}/{yaml_file.name}"
                    github_base = profile.github_web.format(branch=branch)
                    github_url = f"{github_base}/{file_path}"

                    # Extract fields from properties
                    fields = []
                    if "properties" in schema:
                        for fname, fschema in schema["properties"].items():
                            ftype = extract_type_from_schema(fschema) if isinstance(fschema, dict) else str(fschema)
                            fields.append({
                                "name": fname,
                                "type": ftype,
                                "description": fschema.get("description", "") if isinstance(fschema, dict) else "",
                            })

                    # Domain from file name
                    domain_map = {
                        "state": "beacon-state", "block": "block-processing",
                        "attestation": "block-processing", "validator": "validator",
                        "eth1": "deposit", "deposit": "deposit",
                        "light_client": "light-client", "execution_payload": "execution",
                        "execution_requests": "execution", "consolidation": "block-processing",
                        "block_contents": "block-processing",
                    }
                    domain = "other"
                    for key, val in domain_map.items():
                        if key in yaml_file.stem:
                            domain = val
                            break

                    fork_data = {
                        "fork": fork,
                        "file": file_path,
                        "line_number": 1,
                        "kind": "class",
                        "is_new": full_name not in items,
                        "is_modified": full_name in items,
                        "code": "",
                        "fields": fields,
                        "github_url": github_url,
                        "description": schema.get("description", ""),
                        "section_path": [namespace],
                        "inline_comments": [],
                        "references": [],
                    }

                    # Extract references from $ref
                    refs = set()
                    if "properties" in schema:
                        for fschema in schema["properties"].values():
                            if isinstance(fschema, dict):
                                if "$ref" in fschema:
                                    ref_name = extract_schema_name(fschema["$ref"])
                                    if "." in ref_name:
                                        ref_name = ref_name.split(".")[-1]
                                    refs.add(ref_name)
                                if "items" in fschema and isinstance(fschema["items"], dict) and "$ref" in fschema["items"]:
                                    ref_name = extract_schema_name(fschema["items"]["$ref"])
                                    if "." in ref_name:
                                        ref_name = ref_name.split(".")[-1]
                                    refs.add(ref_name)
                    if refs:
                        fork_data["references"] = sorted(refs)

                    if full_name not in items:
                        items[full_name] = {
                            "name": full_name,
                            "kind": "class",
                            "domain": domain,
                            "introduced": fork,
                            "modified_in": [],
                            "forks": {},
                        }
                    else:
                        items[full_name]["modified_in"].append(fork)

                    items[full_name]["forks"][fork] = fork_data

    print(f"  Extracted {len(items)} type schemas from {len(fork_dirs)} forks", file=sys.stderr)
    return items


def main():
    parser = argparse.ArgumentParser(description="Extract endpoints from OpenAPI specs")
    parser.add_argument("--profile", required=True, help="Spec profile name")
    parser.add_argument("--repo-dir", required=True, help="Path to local repo clone")
    parser.add_argument("--output", help="Output JSON path")
    parser.add_argument("--branch", default="main", help="Git branch for URLs")
    args = parser.parse_args()

    profile = get_profile(args.profile)
    output_path = args.output or f"./{profile.name}_endpoints.json"

    print(f"Extracting OpenAPI endpoints from {profile.repo}...", file=sys.stderr)
    endpoints = extract_endpoints(profile, args.repo_dir, args.branch)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(endpoints, f, indent=2)

    print(f"Written to {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
