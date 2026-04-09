#!/usr/bin/env python3
"""
Generic markdown spec extractor. Parses Python code blocks and tables from
Ethereum spec markdown files (consensus-specs, builder-specs, relay-specs).

Driven by a SpecProfile that captures repo-specific configuration.

Usage:
    python3 extract_markdown.py --profile builder-specs --repo-dir /path/to/repo
    python3 extract_markdown.py --profile consensus-specs --repo-dir /path/to/repo
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path

from profiles import (
    SpecProfile, get_profile,
    detect_forks_from_config, detect_forks_from_dirs, detect_features,
)


# ── Data structures ────────────────────────────────────────────────────────

@dataclass
class Definition:
    """A single definition of a type/function/constant at a specific fork."""
    fork: str
    file: str
    line_number: int
    kind: str                        # "class", "def", "type_alias", "constant", "dataclass"
    is_new: bool
    is_modified: bool
    code: str
    inline_comments: list = field(default_factory=list)
    section_path: list = field(default_factory=list)
    prose: str = ""
    github_url: str = ""

    def to_dict(self):
        d = asdict(self)
        if not d.get("prose"):
            d.pop("prose", None)
        return d


@dataclass
class SpecItem:
    """An item (type, function, constant) tracked across forks."""
    name: str
    kind: str
    domain: str
    introduced: str
    forks: dict = field(default_factory=dict)

    def modified_in(self) -> list:
        return [f for f, d in self.forks.items() if d.is_modified and f != self.introduced]

    def to_dict(self):
        return {
            "name": self.name,
            "kind": self.kind,
            "domain": self.domain,
            "introduced": self.introduced,
            "modified_in": self.modified_in(),
            "forks": {f: defn.to_dict() for f, defn in self.forks.items()},
        }


@dataclass
class ConstantEntry:
    """A constant/preset/config value at a specific fork."""
    name: str
    value: str
    description: str
    section: str
    category: str
    fork: str
    file: str
    line_number: int
    github_url: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class TypeAlias:
    """A custom type alias (from the Types table)."""
    name: str
    ssz_equivalent: str
    description: str
    fork: str
    file: str
    line_number: int
    github_url: str = ""

    def to_dict(self):
        return asdict(self)


# ── Helpers ────────────────────────────────────────────────────────────────

def name_to_anchor(name: str) -> str:
    """Convert a heading to a GitHub markdown anchor."""
    anchor = name.lower()
    anchor = anchor.replace("`", "")
    anchor = anchor.replace(" ", "-")
    anchor = re.sub(r"[^a-z0-9_\-]", "", anchor)
    return anchor


def make_github_url(profile: SpecProfile, fork: str, filepath: str, anchor: str, branch: str = "main") -> str:
    """Build a GitHub permalink for a definition."""
    base = profile.github_web.format(branch=branch)
    return f"{base}/{fork}/{filepath}#{anchor}"


def fetch_file_local(repo_dir: str, fork_dir: str, filepath: str, specs_subdir: str = "specs") -> Optional[str]:
    """Read a spec file from a local repo clone."""
    path = Path(repo_dir) / specs_subdir / fork_dir / filepath
    if path.exists():
        return path.read_text()
    return None


def classify_domain(profile: SpecProfile, filepath: str, section_path: list) -> str:
    """Determine functional domain from file and section context."""
    # Check file-level rules first
    if filepath in profile.file_domain_rules:
        return profile.file_domain_rules[filepath]

    # Walk section path from most specific to most general
    for heading in reversed(section_path):
        heading_lower = heading.lower().strip()
        # Strip backticks
        heading_lower = heading_lower.replace("`", "")
        # Strip "modified" / "new" prefixes for matching
        cleaned = re.sub(r"^(modified|new|extended)\s+", "", heading_lower)
        if cleaned in profile.section_domain_rules:
            return profile.section_domain_rules[cleaned]
        if heading_lower in profile.section_domain_rules:
            return profile.section_domain_rules[heading_lower]

    # Fallback heuristics
    for heading in section_path:
        hl = heading.lower()
        if "state transition" in hl:
            return "block-processing"
        if "genesis" in hl:
            return "genesis"

    return profile.default_domain


def detect_modification_status(heading_text: str, section_path: list, code: str, fork: str, first_fork: str) -> tuple:
    """Detect if an item is new or modified. Returns (is_new, is_modified)."""
    heading_lower = heading_text.lower()
    has_modified_heading = "modified" in heading_lower

    for s in section_path:
        sl = s.lower()
        if "new containers" in sl or "new dataclasses" in sl:
            return (True, False)
        if "modified containers" in sl or "modified dataclasses" in sl or "extended containers" in sl:
            return (False, True)

    new_pattern = re.compile(r"\[New in (\w+)\]", re.IGNORECASE)
    mod_pattern = re.compile(r"\[Modified in (\w+)\]", re.IGNORECASE)
    has_new = bool(new_pattern.search(code))
    has_modified = bool(mod_pattern.search(code))

    if has_modified_heading:
        return (False, True)

    # For the first fork, everything is "new"
    if fork == first_fork:
        return (True, False)

    if not has_new and not has_modified:
        return (True, False)

    return (has_new, has_modified)


def extract_inline_comments(code: str) -> list:
    """Extract [New in X] and [Modified in X] comments from code."""
    pattern = re.compile(r"#\s*\[(New|Modified) in (\w+)\]")
    return [m.group(0) for m in pattern.finditer(code)]


# ── Core Parser ────────────────────────────────────────────────────────────

def parse_spec_file(content: str, fork: str, filepath: str, profile: SpecProfile, branch: str = "main"):
    """Parse a single spec markdown file and extract all definitions.

    Yields (name, Definition) tuples.
    Also yields ("__constant__", ConstantEntry) for table-based constants.
    Also yields ("__type_alias__", TypeAlias) for custom type aliases.
    """
    lines = content.split("\n")
    total_lines = len(lines)

    section_stack = []  # [(level, heading_text)]

    i = 0
    while i < total_lines:
        line = lines[i]

        # Track heading hierarchy
        heading_match = re.match(r"^(#{2,5})\s+(.+)", line)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()

            while section_stack and section_stack[-1][0] >= level:
                section_stack.pop()
            section_stack.append((level, heading_text))

            if level in (2, 3, 4, 5):
                name_match = re.search(r"`([^`]+)`", heading_text)
                extract_name_from_code = not name_match

                if name_match or extract_name_from_code:
                    item_name = name_match.group(1) if name_match else None
                    heading_line = i + 1  # 1-indexed
                    section_path = [s[1] for s in section_stack]

                    # Capture prose between heading and first code block
                    prose_lines = []
                    j = i + 1
                    while j < total_lines:
                        pline = lines[j].strip()
                        if pline.startswith("```"):
                            break
                        if re.match(r"^#{2,5}\s", lines[j]):
                            break
                        if pline and not pline.startswith("|"):
                            prose_lines.append(pline)
                        elif not pline and prose_lines:
                            prose_lines.append("")
                        j += 1
                    while prose_lines and not prose_lines[-1]:
                        prose_lines.pop()
                    prose_lines = [
                        pl for pl in prose_lines
                        if not re.match(r"^\*?\[(?:New|Modified) in \w+:EIP\d+\]\*?$", pl)
                    ]
                    while prose_lines and not prose_lines[-1]:
                        prose_lines.pop()
                    item_prose = "\n".join(prose_lines) if prose_lines else ""

                    # Find code blocks under this heading
                    j = i + 1
                    found_blocks = []
                    stop_at_level = level
                    while j < total_lines:
                        if lines[j].strip().startswith("```python"):
                            code_start_line = j + 1
                            k = j + 1
                            code_lines = []
                            while k < total_lines and not lines[k].strip().startswith("```"):
                                code_lines.append(lines[k])
                                k += 1
                            found_blocks.append(("\n".join(code_lines), code_start_line))
                            j = k + 1
                            if name_match:
                                break
                        elif re.match(r"^#{2," + str(stop_at_level) + r"}\s", lines[j]):
                            break
                        elif re.match(r"^#{" + str(stop_at_level + 1) + r",}\s", lines[j]):
                            break
                        else:
                            j += 1

                    for code_block, code_start_line in found_blocks:
                        if not code_block.strip():
                            continue

                        code_stripped = code_block.strip()
                        if code_stripped.startswith("class "):
                            kind = "class"
                            if not item_name:
                                cm = re.match(r"class\s+(\w+)", code_stripped)
                                if cm:
                                    item_name = cm.group(1)
                        elif code_stripped.startswith("def "):
                            kind = "def"
                            if not item_name:
                                dm = re.match(r"def\s+(\w+)", code_stripped)
                                if dm:
                                    item_name = dm.group(1)
                        elif code_stripped.startswith("@dataclass"):
                            kind = "dataclass"
                            if not item_name:
                                dm = re.search(r"class\s+(\w+)", code_stripped)
                                if dm:
                                    item_name = dm.group(1)
                        else:
                            kind = "other"

                        if not item_name:
                            continue

                        effective_name = item_name
                        if extract_name_from_code and len(found_blocks) > 1:
                            if code_stripped.startswith("def "):
                                dm = re.match(r"def\s+(\w+)", code_stripped)
                                if dm:
                                    effective_name = dm.group(1)
                            elif code_stripped.startswith("class "):
                                cm = re.match(r"class\s+(\w+)", code_stripped)
                                if cm:
                                    effective_name = cm.group(1)

                        is_new, is_modified = detect_modification_status(
                            heading_text, section_path, code_block, fork, profile.first_fork
                        )
                        inline_comments = extract_inline_comments(code_block)
                        anchor = name_to_anchor(heading_text)

                        defn = Definition(
                            fork=fork,
                            file=filepath,
                            line_number=heading_line,
                            kind=kind,
                            is_new=is_new,
                            is_modified=is_modified,
                            code=code_stripped,
                            inline_comments=inline_comments,
                            section_path=section_path,
                            prose=item_prose,
                            github_url=make_github_url(profile, fork, filepath, anchor, branch),
                        )
                        yield (effective_name, defn)

                        if extract_name_from_code:
                            item_name = None

            # Check for type alias and constant tables
            if level in (2, 3):
                section_lower = heading_text.lower().strip()

                # Type alias tables
                is_type_section = any(
                    ta in section_lower for ta in profile.type_alias_sections
                )
                if is_type_section:
                    if profile.type_alias_file_filter is None or profile.type_alias_file_filter in filepath:
                        j = i + 1
                        while j < total_lines:
                            tline = lines[j].strip()
                            if tline.startswith("|") and "---" not in tline and "Name" not in tline:
                                cells = [c.strip().strip("`") for c in tline.split("|")[1:-1]]
                                if len(cells) >= 3:
                                    alias = TypeAlias(
                                        name=cells[0],
                                        ssz_equivalent=cells[1],
                                        description=cells[2],
                                        fork=fork,
                                        file=filepath,
                                        line_number=j + 1,
                                        github_url=make_github_url(profile, fork, filepath, name_to_anchor(heading_text), branch),
                                    )
                                    yield ("__type_alias__", alias)
                            elif tline and not tline.startswith("|") and not tline.startswith("*") and tline != "":
                                if re.match(r"^#{2,4}\s", tline):
                                    break
                            j += 1

                # Constant tables
                parent_sections = [s[1].lower() for s in section_stack]
                is_constant_section = any(
                    p in profile.constant_parent_sections
                    for p in parent_sections
                )

                if is_constant_section and level == 3:
                    category = "constant"
                    for ps in parent_sections:
                        if ps == "preset":
                            category = "preset"
                            break
                        elif ps == "configuration":
                            category = "configuration"
                            break
                        elif ps == "constants":
                            category = "constant"
                            break

                    j = i + 1
                    while j < total_lines:
                        tline = lines[j].strip()
                        if tline.startswith("|") and "---" not in tline and "Name" not in tline:
                            cells = [c.strip().strip("`") for c in tline.split("|")[1:-1]]
                            if len(cells) >= 2 and cells[0]:
                                desc = cells[2] if len(cells) >= 3 else ""
                                entry = ConstantEntry(
                                    name=cells[0],
                                    value=cells[1],
                                    description=desc,
                                    section=heading_text,
                                    category=category,
                                    fork=fork,
                                    file=filepath,
                                    line_number=j + 1,
                                    github_url=make_github_url(profile, fork, filepath, name_to_anchor(heading_text), branch),
                                )
                                yield ("__constant__", entry)
                        elif re.match(r"^#{2,3}\s", tline):
                            break
                        j += 1

        i += 1


# ── Extraction Pipeline ───────────────────────────────────────────────────

def detect_forks(profile: SpecProfile, repo_dir: str) -> tuple:
    """Detect fork order and features for a given profile and repo."""
    fork_order = []

    # Try config-based detection first
    if profile.fork_config_path:
        fork_order = detect_forks_from_config(repo_dir, profile.fork_config_path, profile.specs_subdir)

    # Fall back to directory listing
    if not fork_order:
        fork_order = detect_forks_from_dirs(repo_dir, profile.specs_subdir)

    # Fall back to defaults
    if not fork_order:
        fork_order = list(profile.default_fork_order)

    # Detect features
    features = []
    if profile.features_subdir:
        features = detect_features(repo_dir, profile.specs_subdir)

    return fork_order, features


def extract_all(profile: SpecProfile, repo_dir: str, branch: str = "main"):
    """Extract all definitions from all forks using the given profile.

    Returns:
        items, constants, type_aliases, files_processed, fork_order, features
    """
    fork_order, features = detect_forks(profile, repo_dir)

    print(f"  Detected forks: {fork_order}", file=sys.stderr)
    if features:
        print(f"  Detected features: {features}", file=sys.stderr)

    items = {}
    constants = {}
    type_aliases = {}
    files_processed = []

    all_dirs = [(f, f) for f in fork_order]
    if profile.features_subdir:
        all_dirs += [(f"_features/{feat}", feat) for feat in features]

    for dir_path, fork_label in all_dirs:
        for filepath in profile.spec_files:
            content = fetch_file_local(repo_dir, dir_path, filepath, profile.specs_subdir)
            if content is None:
                continue

            files_processed.append({
                "fork": fork_label,
                "dir": dir_path,
                "file": filepath,
                "lines": content.count("\n") + 1,
            })

            print(f"  Parsing {dir_path}/{filepath} ({content.count(chr(10))+1} lines)", file=sys.stderr)

            for marker, result in parse_spec_file(content, fork_label, filepath, profile, branch):
                if marker == "__constant__":
                    if result.name not in constants:
                        constants[result.name] = []
                    constants[result.name].append(result)

                elif marker == "__type_alias__":
                    if result.name not in type_aliases:
                        type_aliases[result.name] = []
                    type_aliases[result.name].append(result)

                else:
                    name = marker
                    defn = result
                    domain = classify_domain(profile, filepath, defn.section_path)

                    if name not in items:
                        items[name] = SpecItem(
                            name=name,
                            kind=defn.kind,
                            domain=domain,
                            introduced=fork_label,
                            forks={},
                        )

                    item = items[name]
                    item.forks[fork_label] = defn

                    if item.domain == "other" and domain != "other":
                        item.domain = domain

    return items, constants, type_aliases, files_processed, fork_order, features


def build_output(profile: SpecProfile, items, constants, type_aliases, files_processed, fork_order, features, branch="main"):
    """Build the final JSON output structure conforming to the unified schema."""

    # Build domain summary
    domain_items = {}
    for name, item in items.items():
        d = item.domain
        if d not in domain_items:
            domain_items[d] = {"classes": [], "functions": [], "other": []}
        bucket = "classes" if item.kind in ("class", "dataclass") else "functions" if item.kind == "def" else "other"
        domain_items[d][bucket].append(name)

    # Build fork summary
    fork_summary = {}
    for fork in fork_order + features:
        new_items = [n for n, it in items.items() if it.introduced == fork]
        modified_items = [n for n, it in items.items()
                         if fork in it.forks and fork != it.introduced]
        if new_items or modified_items:
            fork_summary[fork] = {
                "new": sorted(new_items),
                "modified": sorted(modified_items),
                "total_definitions": len(new_items) + len(modified_items),
            }

    # Build _type_map (types defined in this spec)
    type_map = {}
    for name, item in items.items():
        if item.kind in ("class", "dataclass"):
            type_map[name] = {
                "source": profile.repo,
                "introduced": item.introduced,
                "kind": item.kind,
            }

    output = {
        "_meta": {
            "schema_version": "1.0.0",
            "generated_by": f"extract_markdown.py",
            "source": profile.repo,
            "source_format": profile.source_format,
            "branch": branch,
            "fork_order": fork_order,
            "features": features,
            "files_processed": files_processed,
            "total_items": len(items),
            "total_constants": len(constants),
            "total_type_aliases": len(type_aliases),
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
        "items": {
            name: item.to_dict()
            for name, item in sorted(items.items())
        },
        "constants": {
            name: [c.to_dict() for c in entries]
            for name, entries in sorted(constants.items())
        },
        "type_aliases": {
            name: [a.to_dict() for a in entries]
            for name, entries in sorted(type_aliases.items())
        },
        "_type_map": type_map,
    }

    return output


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Extract spec definitions from markdown")
    parser.add_argument("--profile", required=True, help="Spec profile name (consensus-specs, builder-specs, relay-specs)")
    parser.add_argument("--repo-dir", required=True, help="Path to local repo clone")
    parser.add_argument("--output", help="Output JSON path (default: ./{profile}_index.json)")
    parser.add_argument("--branch", default="main", help="Git branch for GitHub URLs (default: main)")
    args = parser.parse_args()

    profile = get_profile(args.profile)
    output_path = args.output or f"./{profile.name}_index.json"

    print(f"Extracting from {profile.repo} ({profile.source_format})...", file=sys.stderr)
    print(f"Source: {args.repo_dir}", file=sys.stderr)

    items, constants, type_aliases, files_processed, fork_order, features = extract_all(
        profile=profile,
        repo_dir=args.repo_dir,
        branch=args.branch,
    )

    print(f"\nExtracted:", file=sys.stderr)
    print(f"  {len(items)} types/functions", file=sys.stderr)
    print(f"  {len(constants)} constants/presets/config values", file=sys.stderr)
    print(f"  {len(type_aliases)} custom type aliases", file=sys.stderr)
    print(f"  from {len(files_processed)} files", file=sys.stderr)

    output = build_output(profile, items, constants, type_aliases, files_processed, fork_order, features, args.branch)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nWritten to {output_path}", file=sys.stderr)

    # Print domain summary
    print("\n-- Domain summary --", file=sys.stderr)
    for domain, info in sorted(output["domains"].items()):
        total = len(info["classes"]) + len(info["functions"]) + len(info["other"])
        print(f"  {domain}: {len(info['classes'])} classes, {len(info['functions'])} functions ({total} total)", file=sys.stderr)

    # Print fork summary
    print("\n-- Fork summary --", file=sys.stderr)
    for fork in fork_order + features:
        if fork in output["fork_summary"]:
            fs = output["fork_summary"][fork]
            print(f"  {fork}: {len(fs['new'])} new, {len(fs['modified'])} modified", file=sys.stderr)


if __name__ == "__main__":
    main()
