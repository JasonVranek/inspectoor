#!/usr/bin/env python3
"""
Python AST-based extractor for execution-specs.

Parses real Python source (not markdown with embedded code blocks) and
produces items, constants, and type_aliases conforming to the unified schema.

Usage:
    python3 extract_python.py --profile execution-specs --repo-dir /path/to/execution-specs
"""

import ast
import json
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from profiles import SpecProfile


# ── Fork ordering ────────────────────────────────────────────────────────

def detect_fork_order(forks_dir: Path) -> list:
    """Detect fork order from FORK_CRITERIA in each fork's __init__.py.

    Returns forks sorted by (criteria_type, value):
      - ByBlockNumber(N) sorts first (type=0), ascending by N
      - ByTimestamp(N) sorts next (type=1), ascending by N
      - Unscheduled(order_index) sorts last (type=2), ascending by order_index
    """
    fork_sort_keys = {}
    for fork_dir in forks_dir.iterdir():
        if not fork_dir.is_dir() or fork_dir.name.startswith(("_", ".")):
            continue
        init_file = fork_dir / "__init__.py"
        if not init_file.exists():
            continue
        text = init_file.read_text()

        # Parse FORK_CRITERIA assignment
        m = re.search(r'FORK_CRITERIA.*=\s*(ByBlockNumber|ByTimestamp|Unscheduled)\((\d+)?\)', text)
        if m:
            kind = m.group(1)
            val = int(m.group(2)) if m.group(2) else 0
            if kind == "ByBlockNumber":
                fork_sort_keys[fork_dir.name] = (0, val)
            elif kind == "ByTimestamp":
                fork_sort_keys[fork_dir.name] = (1, val)
            else:  # Unscheduled
                fork_sort_keys[fork_dir.name] = (2, val)
        else:
            # Fallback: put at end
            fork_sort_keys[fork_dir.name] = (3, 0)

    return sorted(fork_sort_keys.keys(), key=lambda f: fork_sort_keys[f])


# ── EIP extraction ───────────────────────────────────────────────────────

def extract_eips_from_init(init_file: Path) -> list:
    """Extract EIP numbers from __init__.py docstring."""
    if not init_file.exists():
        return []
    text = init_file.read_text()
    return sorted(set(int(m) for m in re.findall(r'EIP[- ]?(\d+)', text)))


# ── Domain classification ────────────────────────────────────────────────

def classify_domain(rel_path: str) -> str:
    """Classify a file into a domain based on its path relative to fork dir.

    e.g. "blocks.py" -> "blocks", "vm/gas.py" -> "vm-gas",
         "vm/instructions/arithmetic.py" -> "vm-instructions"
    """
    p = Path(rel_path)
    stem = p.stem
    if stem == "__init__":
        # Use parent dir name
        parts = list(p.parent.parts)
        if not parts or parts == ["."]:
            return "fork-meta"
        return "-".join(parts)

    parts = list(p.parent.parts)
    parts = [x for x in parts if x != "."]

    if not parts:
        return stem
    # vm/instructions/arithmetic.py -> "vm-instructions"
    # vm/precompiled_contracts/ecrecover.py -> "vm-precompiles"
    if "precompiled_contracts" in parts:
        return "vm-precompiles"
    if "instructions" in parts:
        return "vm-instructions"
    return "-".join(parts + [stem]) if len(parts) == 1 else "-".join(parts)


# ── AST helpers ──────────────────────────────────────────────────────────

def get_docstring(node) -> str:
    """Extract docstring from a class or function node."""
    if (node.body and isinstance(node.body[0], ast.Expr) and
            isinstance(node.body[0].value, (ast.Constant, ast.Str))):
        val = node.body[0].value
        if isinstance(val, ast.Constant):
            return str(val.value).strip()
        return str(val.s).strip()  # Python 3.7 compat
    return ""


def annotation_to_str(node) -> str:
    """Convert an AST annotation node to a readable type string."""
    if node is None:
        return ""
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{annotation_to_str(node.value)}.{node.attr}"
    if isinstance(node, ast.Subscript):
        base = annotation_to_str(node.value)
        sl = node.slice
        if isinstance(sl, ast.Tuple):
            args = ", ".join(annotation_to_str(e) for e in sl.elts)
        else:
            args = annotation_to_str(sl)
        return f"{base}[{args}]"
    if isinstance(node, ast.Tuple):
        return ", ".join(annotation_to_str(e) for e in node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return f"{annotation_to_str(node.left)} | {annotation_to_str(node.right)}"
    if isinstance(node, ast.List):
        return "[" + ", ".join(annotation_to_str(e) for e in node.elts) + "]"
    return ast.dump(node)


def is_dataclass(node: ast.ClassDef) -> bool:
    """Check if a class has @dataclass or @slotted_freezable decorator."""
    for dec in node.decorator_list:
        name = ""
        if isinstance(dec, ast.Name):
            name = dec.id
        elif isinstance(dec, ast.Attribute):
            name = dec.attr
        if name in ("dataclass", "slotted_freezable"):
            return True
    return False


def is_constant_assign(node) -> bool:
    """Check if a module-level assignment looks like a constant.

    Matches UPPER_CASE names and annotated assignments with uppercase first char.
    Skips PascalCase-only names (those are type aliases).
    """
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        name = node.target.id
        # UPPER_CASE or UpperCase with underscores (e.g., GAS_LIMIT)
        return name.isupper() or ("_" in name and name[0].isupper())
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name):
            return target.id.isupper()
    return False


def is_type_alias(node) -> bool:
    """Check if a module-level assignment is a type alias.

    Patterns:
      Name = SomeType (simple Name on the right side)
      Name = SomeType[Arg] (subscript, e.g., List[int])
    Excludes UPPER_CASE constants and function calls.
    """
    # TypeAlias annotation: Name: TypeAlias = SomeType
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        ann = node.annotation
        if isinstance(ann, ast.Name) and ann.id == "TypeAlias":
            return True

    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id[0].isupper() and not target.id.isupper():
            val = node.value
            # Simple name: Address = Bytes20
            if isinstance(val, ast.Name) and val.id[0].isupper():
                return True
            # Attribute: foo.Bar
            if isinstance(val, ast.Attribute):
                return True
            # Subscript: List[SomeType]
            if isinstance(val, ast.Subscript):
                return True
    return False


def get_source_segment(source_lines: list, node) -> str:
    """Extract source code for an AST node."""
    start = node.lineno - 1  # 0-indexed
    end = node.end_lineno if hasattr(node, 'end_lineno') and node.end_lineno else start + 1
    return "\n".join(source_lines[start:end])


def get_preceding_docstring(source_lines: list, node) -> str:
    """Get the docstring comment block (triple-quoted string) after a constant assignment.

    In execution-specs, constants often have a docstring on the next line:
      GAS_TX_BASE = Uint(21000)
      \"\"\"
      Base cost of a transaction...
      \"\"\"
    """
    if not hasattr(node, 'end_lineno') or node.end_lineno is None:
        return ""
    # Look at lines after the assignment
    start = node.end_lineno  # 0-indexed would be end_lineno - 1, but we want the line AFTER
    remaining = "\n".join(source_lines[start:start + 10])
    m = re.match(r'\s*"""(.*?)"""', remaining, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.match(r"\s*'''(.*?)'''", remaining, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


# ── Main extraction ──────────────────────────────────────────────────────

def extract_file(file_path: Path, source_lines: list, tree: ast.Module,
                 fork_name: str, rel_path: str, domain: str,
                 github_web: str, branch: str, forks_prefix: str) -> dict:
    """Extract items, constants, and type_aliases from one parsed Python file.

    Returns {items: {name: fork_data}, constants: {name: data}, type_aliases: {name: data}}
    """
    items = {}
    constants = {}
    type_aliases = {}

    github_url_base = f"{github_web}/{forks_prefix}/{fork_name}/{rel_path}"

    for node in ast.iter_child_nodes(tree):
        # Dataclass / class extraction
        if isinstance(node, ast.ClassDef):
            name = node.id if hasattr(node, 'id') else node.name
            kind = "dataclass" if is_dataclass(node) else "class"
            docstring = get_docstring(node)
            code = get_source_segment(source_lines, node)
            line = node.lineno

            # Extract fields from annotations
            fields = []
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    field_name = item.target.id
                    field_type = annotation_to_str(item.annotation)
                    # Get field docstring (next expression if it's a string)
                    idx = node.body.index(item)
                    field_doc = ""
                    if idx + 1 < len(node.body):
                        next_node = node.body[idx + 1]
                        if (isinstance(next_node, ast.Expr) and
                                isinstance(next_node.value, ast.Constant) and
                                isinstance(next_node.value.value, str)):
                            field_doc = next_node.value.value.strip()
                    fields.append({
                        "name": field_name,
                        "type": field_type,
                        "comment": field_doc,
                    })

            # Extract EIPs from docstring
            eips = sorted(set(int(m) for m in re.findall(r'EIP[- ]?(\d+)', docstring)))

            # Extract references from code
            refs = extract_references_from_code(code)

            github_url = f"{github_url_base}#L{line}"

            items[name] = {
                "kind": kind,
                "code": code,
                "file": f"{forks_prefix}/{fork_name}/{rel_path}",
                "github_url": github_url,
                "fields": fields,
                "references": refs,
                "eips": eips,
                "prose": docstring,
                "domain": domain,
            }

        # Function extraction
        elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            name = node.name
            if name.startswith("_"):
                continue  # skip private helpers
            docstring = get_docstring(node)
            code = get_source_segment(source_lines, node)
            line = node.lineno

            # Extract params
            params = []
            for arg in node.args.args:
                if arg.arg == "self":
                    continue
                ptype = annotation_to_str(arg.annotation) if arg.annotation else ""
                params.append({"name": arg.arg, "type": ptype})

            return_type = annotation_to_str(node.returns) if node.returns else ""

            eips = sorted(set(int(m) for m in re.findall(r'EIP[- ]?(\d+)', docstring)))
            refs = extract_references_from_code(code)

            github_url = f"{github_url_base}#L{line}"

            items[name] = {
                "kind": "def",
                "code": code,
                "file": f"{forks_prefix}/{fork_name}/{rel_path}",
                "github_url": github_url,
                "params": params,
                "return_type": return_type,
                "references": refs,
                "eips": eips,
                "prose": docstring,
                "domain": domain,
            }

        # Type alias extraction (Name = SomeType or Name: TypeAlias = SomeType)
        elif is_type_alias(node):
            if isinstance(node, ast.AnnAssign):
                name = node.target.id
                val = annotation_to_str(node.value) if node.value else ""
            else:
                target = node.targets[0]
                name = target.id
                val = annotation_to_str(node.value)
            line = node.lineno
            github_url = f"{github_url_base}#L{line}"
            type_aliases[name] = {
                "ssz_equivalent": val,
                "description": get_preceding_docstring(source_lines, node),
                "file": f"{forks_prefix}/{fork_name}/{rel_path}",
                "github_url": github_url,
                "domain": domain,
            }

        # Constant extraction (UPPER_CASE = value)
        elif is_constant_assign(node):
            if isinstance(node, ast.AnnAssign):
                name = node.target.id
                ctype = annotation_to_str(node.annotation)
                line = node.lineno
                # Get value as source
                if node.value:
                    val_code = get_source_segment(source_lines, node)
                else:
                    val_code = ""
            elif isinstance(node, ast.Assign):
                target = node.targets[0]
                name = target.id
                ctype = ""
                line = node.lineno
                val_code = get_source_segment(source_lines, node)
            else:
                continue

            # Skip if it was already captured as a type alias
            if name in type_aliases:
                continue
            # Skip imports disguised as constants
            if name.startswith("__"):
                continue

            docstring = get_preceding_docstring(source_lines, node)
            github_url = f"{github_url_base}#L{line}"

            constants[name] = {
                "value": val_code.split("=", 1)[1].strip() if "=" in val_code else val_code,
                "type": ctype,
                "description": docstring,
                "section": domain,
                "category": "constant",
                "file": f"{forks_prefix}/{fork_name}/{rel_path}",
                "github_url": github_url,
            }

    return {"items": items, "constants": constants, "type_aliases": type_aliases}


def extract_references_from_code(code: str) -> list:
    """Extract type/function references from Python code.

    Strips docstrings first, then looks for capitalized identifiers (types)
    and function calls in the actual code.
    """
    # Strip triple-quoted strings (docstrings) to avoid picking up English words
    stripped = re.sub(r'""".*?"""', '', code, flags=re.DOTALL)
    stripped = re.sub(r"'''.*?'''", '', stripped, flags=re.DOTALL)

    # Common English words that happen to be capitalized (appear in comments/strings)
    SKIP_WORDS = {
        "True", "False", "None", "Optional", "List", "Tuple",
        "Dict", "Set", "Union", "Final", "Literal", "Type",
        "Callable", "Any", "ClassVar", "Sequence",
        "Mapping", "Iterator", "Iterable", "Protocol",
        "ABC", "Override",
        # Common English words that sneak through
        "The", "This", "If", "For", "When", "Each", "All", "Not",
        "Returns", "Parameters", "See", "Also", "New", "Modified",
        "Note", "However", "Since", "After", "Before", "Given",
        "Total", "Maximum", "Minimum", "Base", "Pre", "Post",
        "Output", "Input", "Data", "Value", "Index", "Length",
        "Arbitrary", "Running", "Introduced", "Constructed",
        "Difficulty", "Nonce", "Timestamp", "Address",
    }

    refs = set()
    for m in re.finditer(r'\b([A-Z][a-zA-Z0-9_]+)\b', stripped):
        name = m.group(1)
        if name in SKIP_WORDS:
            continue
        # Skip ALL_CAPS constants (we track those separately)
        if name.isupper() and "_" in name:
            continue
        refs.add(name)
    return sorted(refs)


# ── Top-level orchestrator ───────────────────────────────────────────────

def extract_all(profile: SpecProfile, repo_dir: str, branch: str = "main"):
    """Extract all items from execution-specs.

    Returns (items, constants, type_aliases, files_processed, fork_order, eip_map)
    where items is {name: {introduced, domain, kind, forks: {fork: data}}}
    """
    repo_path = Path(repo_dir)
    forks_dir = repo_path / profile.specs_subdir

    if not forks_dir.is_dir():
        raise FileNotFoundError(f"Forks directory not found: {forks_dir}")

    fork_order = detect_fork_order(forks_dir)
    print(f"  Fork order ({len(fork_order)}): {', '.join(fork_order)}", file=sys.stderr)

    # The path prefix for files relative to repo root
    forks_prefix = profile.specs_subdir

    # Collect items across all forks
    all_items = OrderedDict()       # name -> {introduced, domain, kind, forks: {fork: data}}
    all_constants = OrderedDict()   # name -> {introduced, forks: {fork: data}}
    all_type_aliases = OrderedDict()
    files_processed = []
    eip_map = {}  # fork -> [eip_numbers]

    # File exclusions (skip __init__.py for items -- we only read it for EIPs)
    skip_files = {"__init__.py"}

    for fork_name in fork_order:
        fork_dir = forks_dir / fork_name
        if not fork_dir.is_dir():
            continue

        # Extract EIPs from __init__.py
        fork_eips = extract_eips_from_init(fork_dir / "__init__.py")
        if fork_eips:
            eip_map[fork_name] = fork_eips

        # Walk all .py files in this fork
        py_files = sorted(fork_dir.rglob("*.py"))
        for py_file in py_files:
            rel_path = str(py_file.relative_to(fork_dir))
            if py_file.name in skip_files:
                continue

            # Skip utils/ -- too low-level, clutters the index
            if rel_path.startswith("utils/"):
                continue

            try:
                source = py_file.read_text()
                source_lines = source.splitlines()
                tree = ast.parse(source)
            except (SyntaxError, UnicodeDecodeError) as e:
                print(f"  WARN: Could not parse {py_file}: {e}", file=sys.stderr)
                continue

            domain = classify_domain(rel_path)
            result = extract_file(
                py_file, source_lines, tree,
                fork_name, rel_path, domain,
                profile.github_web.format(branch=branch),
                branch, forks_prefix,
            )

            files_processed.append(f"{forks_prefix}/{fork_name}/{rel_path}")

            # Merge items
            for name, fork_data in result["items"].items():
                if name not in all_items:
                    all_items[name] = {
                        "introduced": fork_name,
                        "domain": fork_data["domain"],
                        "kind": fork_data["kind"],
                        "forks": {},
                    }
                all_items[name]["forks"][fork_name] = fork_data

            # Merge constants
            for name, cdata in result["constants"].items():
                if name not in all_constants:
                    all_constants[name] = {
                        "introduced": fork_name,
                        "forks": {},
                    }
                all_constants[name]["forks"][fork_name] = cdata

            # Merge type aliases
            for name, tdata in result["type_aliases"].items():
                if name not in all_type_aliases:
                    all_type_aliases[name] = {
                        "introduced": fork_name,
                        "forks": {},
                    }
                all_type_aliases[name]["forks"][fork_name] = tdata

    return all_items, all_constants, all_type_aliases, files_processed, fork_order, eip_map


def build_output(profile: SpecProfile, items: dict, constants: dict,
                 type_aliases: dict, files_processed: list, fork_order: list,
                 eip_map: dict, branch: str) -> dict:
    """Build the unified schema output from extracted data."""

    # Mark is_new / is_modified per fork
    for name, item in items.items():
        prev_code = None
        for fork in fork_order:
            if fork not in item["forks"]:
                continue
            fdata = item["forks"][fork]
            if fork == item["introduced"]:
                fdata["is_new"] = True
                fdata["is_modified"] = False
            else:
                fdata["is_new"] = False
                fdata["is_modified"] = (fdata.get("code", "") != prev_code) if prev_code else False
            prev_code = fdata.get("code", "")

    # Build fork_summary
    fork_summary = {}
    for fork in fork_order:
        new_items = [n for n, it in items.items() if it["introduced"] == fork]
        modified_items = [n for n, it in items.items()
                         if fork in it["forks"] and it["introduced"] != fork
                         and it["forks"][fork].get("is_modified")]
        new_constants = [n for n, c in constants.items() if c["introduced"] == fork]
        eips = eip_map.get(fork, [])

        if new_items or modified_items or new_constants:
            fork_summary[fork] = {
                "new": sorted(new_items),
                "modified": sorted(modified_items),
                "new_constants": sorted(new_constants),
                "eips": eips,
                "total_definitions": len(new_items) + len(modified_items),
            }

    # Build domains
    domain_items = {}
    for name, item in items.items():
        d = item["domain"]
        if d not in domain_items:
            domain_items[d] = {"classes": [], "functions": [], "other": []}
        if item["kind"] in ("class", "dataclass"):
            domain_items[d]["classes"].append(name)
        elif item["kind"] == "def":
            domain_items[d]["functions"].append(name)
        else:
            domain_items[d]["other"].append(name)

    # Filter references to known items only (removes noise from primitive types,
    # variable names, etc.) and remove self-references
    known_names = set(items.keys())
    for name, item in items.items():
        for fork, fdata in item["forks"].items():
            raw_refs = fdata.get("references", [])
            fdata["references"] = sorted(
                r for r in raw_refs
                if r in known_names and r != name
            )

    # Build reverse references
    ref_index = {}
    for name, item in items.items():
        for fork_data in item["forks"].values():
            for ref in fork_data.get("references", []):
                if ref not in ref_index:
                    ref_index[ref] = set()
                ref_index[ref].add(name)

    # Build _type_map
    type_map = {}
    for name, item in items.items():
        if item["kind"] in ("class", "dataclass"):
            type_map[name] = {
                "source": profile.repo,
                "introduced": item["introduced"],
                "kind": item["kind"],
            }

    # Build _eip_index
    eip_index = {}
    for name, item in items.items():
        for fork, fdata in item["forks"].items():
            for eip in fdata.get("eips", []):
                eip_str = str(eip)
                if eip_str not in eip_index:
                    eip_index[eip_str] = {"items": []}
                change_type = "new" if fdata.get("is_new") else "modified"
                entry = {"item": name, "fork": fork, "change": change_type}
                # Deduplicate (same item+fork can appear from multiple references)
                if entry not in eip_index[eip_str]["items"]:
                    eip_index[eip_str]["items"].append(entry)

    # Build _field_index
    field_index = {}
    for name, item in items.items():
        if item["kind"] not in ("class", "dataclass"):
            continue
        for fork, fdata in item["forks"].items():
            for field in fdata.get("fields", []):
                key = f"{name}.{field['name']}"
                if key not in field_index:
                    field_index[key] = {
                        "type": field["type"],
                        "defined_at_fork": fork,
                    }

    # Flatten constants for output (pick latest fork's data, track all forks)
    constants_out = {}
    for name, cdata in constants.items():
        # Use the latest fork's data
        latest_fork = None
        for fork in fork_order:
            if fork in cdata["forks"]:
                latest_fork = fork
        if latest_fork:
            entry = dict(cdata["forks"][latest_fork])
            entry["introduced"] = cdata["introduced"]
            # Track which forks have this constant
            entry["present_in_forks"] = sorted(cdata["forks"].keys(),
                                                key=lambda f: fork_order.index(f) if f in fork_order else 999)
            constants_out[name] = entry

    # Flatten type aliases
    type_aliases_out = {}
    for name, tdata in type_aliases.items():
        latest_fork = None
        for fork in fork_order:
            if fork in tdata["forks"]:
                latest_fork = fork
        if latest_fork:
            entry = dict(tdata["forks"][latest_fork])
            entry["introduced"] = tdata["introduced"]
            type_aliases_out[name] = entry

    output = {
        "_meta": {
            "schema_version": "1.0.0",
            "generated_by": "extract_python.py",
            "source": profile.repo,
            "source_format": profile.source_format,
            "branch": branch,
            "fork_order": fork_order,
            "features": [],
            "files_processed": sorted(set(files_processed)),
            "total_items": len(items),
            "total_constants": len(constants_out),
            "total_type_aliases": len(type_aliases_out),
        },
        "items": {name: {
            "introduced": item["introduced"],
            "domain": item["domain"],
            "kind": item["kind"],
            "forks": {
                fork: {
                    k: v for k, v in fdata.items()
                    if k != "domain"  # domain is on the item, not per-fork
                }
                for fork, fdata in item["forks"].items()
            },
        } for name, item in sorted(items.items())},
        "constants": {name: entry for name, entry in sorted(constants_out.items())},
        "type_aliases": {name: entry for name, entry in sorted(type_aliases_out.items())},
        "endpoints": {},
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
        "_field_index": field_index,
        "_eip_index": eip_index,
        "_type_map": type_map,
    }

    return output


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extract execution-specs Python source")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--output-dir", default="./indexes")
    parser.add_argument("--branch", default="main")
    args = parser.parse_args()

    from profiles import get_profile
    profile = get_profile(args.profile)

    items, constants, type_aliases, files, fork_order, eip_map = extract_all(
        profile, args.repo_dir, args.branch
    )

    output = build_output(profile, items, constants, type_aliases, files, fork_order, eip_map, args.branch)

    os.makedirs(args.output_dir, exist_ok=True)
    output_path = os.path.join(args.output_dir, f"{profile.name}_index.json")
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nDone: {output_path}", file=sys.stderr)
    print(f"  Items: {output['_meta']['total_items']}", file=sys.stderr)
    print(f"  Constants: {output['_meta']['total_constants']}", file=sys.stderr)
    print(f"  Type aliases: {output['_meta']['total_type_aliases']}", file=sys.stderr)
    print(f"  Forks: {len(fork_order)}", file=sys.stderr)


if __name__ == "__main__":
    main()
