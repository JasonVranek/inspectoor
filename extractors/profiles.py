"""
Spec repo profiles. Each profile captures the repo-specific configuration
that the generic markdown parser needs: fork detection, file lists, domain
classification, and GitHub URL templates.
"""

from dataclasses import dataclass, field
from typing import Optional
import re
from pathlib import Path


@dataclass
class SpecProfile:
    """Configuration for a specific spec repo."""

    # Identity
    name: str                          # e.g. "builder-specs"
    repo: str                          # e.g. "ethereum/builder-specs"
    source_format: str                 # e.g. "markdown+openapi"

    # GitHub URL templates
    github_raw: str                    # raw content URL with {branch} and path
    github_web: str                    # web URL with {branch} and path

    # Fork detection
    specs_subdir: str = "specs"        # directory containing fork subdirs
    fork_config_path: Optional[str] = None  # e.g. "configs/mainnet.yaml" for CL
    default_fork_order: list = field(default_factory=list)
    first_fork: str = ""               # the "genesis" fork (everything here is new)

    # File scanning
    spec_files: list = field(default_factory=list)  # files to scan in each fork dir
    features_subdir: Optional[str] = None  # e.g. "_features" for consensus-specs

    # Domain classification
    file_domain_rules: dict = field(default_factory=dict)  # file -> domain
    section_domain_rules: dict = field(default_factory=dict)  # section heading -> domain
    default_domain: str = "other"

    # Type alias table detection
    type_alias_sections: list = field(default_factory=list)  # section headings that contain type tables
    type_alias_file_filter: Optional[str] = None  # only look in files matching this

    # Constant table detection
    constant_parent_sections: list = field(default_factory=lambda: ["constants", "preset", "configuration"])

    # OpenAPI spec file (for combined markdown+openapi repos)
    openapi_file: Optional[str] = None


def detect_forks_from_config(repo_dir: str, config_path: str, specs_subdir: str = "specs") -> list:
    """Detect fork ordering from a mainnet.yaml config file (consensus-specs style).

    Reconciles config fork names with actual directory names. Handles the
    phase0 special case (no FORK_VERSION in config but directory exists).
    """
    full_path = Path(repo_dir) / config_path
    if not full_path.exists():
        return []

    fork_versions = {}
    for line in full_path.read_text().splitlines():
        m = re.match(r"([A-Z_]+)_FORK_VERSION:\s*(0x[0-9a-fA-F]+)", line.strip())
        if m:
            name = m.group(1).lower()
            version = int(m.group(2), 16)
            fork_versions[name] = version

    ordered_from_config = sorted(fork_versions.keys(), key=lambda k: fork_versions[k])

    # Detect actual directories
    specs_dir = Path(repo_dir) / specs_subdir
    all_spec_dirs = set()
    if specs_dir.is_dir():
        for d in specs_dir.iterdir():
            if d.is_dir() and not d.name.startswith(("_", ".")):
                all_spec_dirs.add(d.name)

    # Build fork order reconciled with actual directories
    fork_order = []

    # phase0 doesn't have a FORK_VERSION in config -- it's the genesis state
    if "phase0" in all_spec_dirs:
        fork_order.append("phase0")

    for fork in ordered_from_config:
        if fork in all_spec_dirs and fork not in fork_order:
            fork_order.append(fork)

    # Append any remaining dirs not in config (custom/experimental forks)
    for d in sorted(all_spec_dirs):
        if d not in fork_order:
            fork_order.append(d)

    return fork_order


def detect_forks_from_dirs(repo_dir: str, specs_subdir: str = "specs") -> list:
    """Detect forks by listing directories under specs/."""
    specs_dir = Path(repo_dir) / specs_subdir
    if not specs_dir.is_dir():
        return []
    return sorted([
        d.name for d in specs_dir.iterdir()
        if d.is_dir() and not d.name.startswith(("_", "."))
    ])


def detect_features(repo_dir: str, specs_subdir: str = "specs") -> list:
    """Detect feature branches under specs/_features/."""
    features_dir = Path(repo_dir) / specs_subdir / "_features"
    if not features_dir.is_dir():
        return []
    return sorted([d.name for d in features_dir.iterdir() if d.is_dir()])


# ── Profile Definitions ──────────────────────────────────────────────────

CONSENSUS_SPECS = SpecProfile(
    name="consensus-specs",
    repo="ethereum/consensus-specs",
    source_format="markdown",
    github_raw="https://raw.githubusercontent.com/ethereum/consensus-specs/{branch}/specs",
    github_web="https://github.com/ethereum/consensus-specs/blob/{branch}/specs",
    specs_subdir="specs",
    fork_config_path="configs/mainnet.yaml",
    default_fork_order=[
        "phase0", "altair", "bellatrix", "capella",
        "deneb", "electra", "fulu", "gloas", "heze",
    ],
    first_fork="phase0",  # directory name, not config name
    spec_files=[
        "beacon-chain.md",
        "fork-choice.md",
        "validator.md",
        "p2p-interface.md",
        "fork.md",
        "deposit-contract.md",
        "weak-subjectivity.md",
        "bls.md",
        "polynomial-commitments.md",
        "das-core.md",
        "polynomial-commitments-sampling.md",
        "builder.md",
        "inclusion-list.md",
        "light-client/sync-protocol.md",
        "light-client/light-client.md",
        "light-client/full-node.md",
        "light-client/p2p-interface.md",
        "light-client/fork.md",
    ],
    features_subdir="_features",
    file_domain_rules={
        "fork-choice.md": "fork-choice",
        "validator.md": "validator",
        "p2p-interface.md": "networking",
        "fork.md": "fork-logic",
        "deposit-contract.md": "deposit",
        "weak-subjectivity.md": "weak-subjectivity",
        "bls.md": "cryptography",
        "polynomial-commitments.md": "cryptography",
        "das-core.md": "data-availability",
        "polynomial-commitments-sampling.md": "data-availability",
        "builder.md": "builder",
        "inclusion-list.md": "inclusion-list",
        "light-client/sync-protocol.md": "light-client",
        "light-client/light-client.md": "light-client",
        "light-client/full-node.md": "light-client",
        "light-client/p2p-interface.md": "light-client",
        "light-client/fork.md": "light-client",
    },
    section_domain_rules={
        "containers": "beacon-state",
        "beacon state": "beacon-state",
        "beacon blocks": "block-processing",
        "beacon operations": "block-processing",
        "signed envelopes": "block-processing",
        "dataclasses": "beacon-state",
        "types": "custom-types",
        "constants": "constants",
        "preset": "constants",
        "configuration": "constants",
        "math": "helpers",
        "crypto": "cryptography",
        "predicates": "helpers",
        "misc": "helpers",
        "beacon state accessors": "helpers",
        "beacon state mutators": "helpers",
        "epoch processing": "epoch-processing",
        "block processing": "block-processing",
        "justification and finalization": "epoch-processing",
        "rewards and penalties": "epoch-processing",
        "slashings": "epoch-processing",
        "sync committee": "epoch-processing",
        "sync aggregate processing": "block-processing",
        "inactivity scores": "epoch-processing",
        "genesis": "genesis",
        "beacon chain state transition function": "block-processing",
        "execution engine": "block-processing",
        "execution proof": "block-processing",
        "execution payload processing": "block-processing",
        "execution": "block-processing",
        "withdrawals": "block-processing",
        "withdrawals processing": "block-processing",
        "pending deposits processing": "block-processing",
        "modified containers": "beacon-state",
        "new containers": "beacon-state",
        "modified dataclasses": "beacon-state",
        "new dataclasses": "beacon-state",
    },
    type_alias_sections=["types", "custom types"],
    type_alias_file_filter="beacon-chain",
)


BUILDER_SPECS = SpecProfile(
    name="builder-specs",
    repo="ethereum/builder-specs",
    source_format="markdown+openapi",
    github_raw="https://raw.githubusercontent.com/ethereum/builder-specs/{branch}/specs",
    github_web="https://github.com/ethereum/builder-specs/blob/{branch}/specs",
    specs_subdir="specs",
    fork_config_path=None,  # no config file, detect from dirs
    default_fork_order=["bellatrix", "capella", "deneb", "electra", "fulu"],
    first_fork="bellatrix",
    spec_files=["builder.md"],
    features_subdir=None,
    file_domain_rules={},
    section_domain_rules={
        "containers": "containers",
        "independently versioned": "registration",
        "fork versioned": "containers",
        "extended containers": "containers",
        "new containers": "containers",
        "modified containers": "containers",
        "signing": "signing",
        "validator registration processing": "registration",
        "validator registration": "registration",
        "building": "building",
        "bidding": "bidding",
        "relaying": "relaying",
        "block scoring": "scoring",
        "revealing the executionpayload": "revealing",
        "blinded block processing": "blinded-blocks",
        "endpoints": "endpoints",
        "constants": "constants",
        "domain types": "constants",
        "time parameters": "constants",
        "custom types": "custom-types",
    },
    type_alias_sections=["custom types"],
    type_alias_file_filter=None,
    openapi_file="builder-oapi.yaml",
)


RELAY_SPECS = SpecProfile(
    name="relay-specs",
    repo="flashbots/relay-specs",
    source_format="markdown+openapi",
    github_raw="https://raw.githubusercontent.com/flashbots/relay-specs/{branch}/specs",
    github_web="https://github.com/flashbots/relay-specs/blob/{branch}/specs",
    specs_subdir="specs",
    fork_config_path=None,
    default_fork_order=["bellatrix", "capella", "deneb", "electra", "fulu"],
    first_fork="bellatrix",
    spec_files=["builder.md"],
    features_subdir=None,
    file_domain_rules={},
    section_domain_rules={
        "containers": "containers",
        "custom types": "custom-types",
        "constants": "constants",
    },
    type_alias_sections=["custom types"],
    type_alias_file_filter=None,
    openapi_file="relay-oapi.yaml",
)


# Registry
PROFILES = {
    "consensus-specs": CONSENSUS_SPECS,
    "builder-specs": BUILDER_SPECS,
    "relay-specs": RELAY_SPECS,
}


def get_profile(name: str) -> SpecProfile:
    """Get a profile by name."""
    if name not in PROFILES:
        available = ", ".join(PROFILES.keys())
        raise ValueError(f"Unknown profile: {name}. Available: {available}")
    return PROFILES[name]
