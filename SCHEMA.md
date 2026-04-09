# Ethereum Specs Index — Unified Schema

## Purpose

A single intermediate JSON format that captures the data model and API surface
of every Ethereum spec repo. Designed to be:

1. **Agent-readable** — structured enough that an LLM can answer questions about
   the protocol without reading raw markdown or YAML
2. **UI-renderable** — a single explorer frontend can consume any spec index
   that conforms to this schema
3. **Cross-linkable** — types referenced across spec boundaries carry enough
   metadata to resolve to their source

## Spec Sources

Each spec repo produces one index file. The schema is the same across all.

| Source Repo | Source Format | Content |
|---|---|---|
| `consensus-specs` | Markdown + Python blocks | CL state machine: types, functions, constants |
| `builder-specs` | Markdown + Python blocks + OpenAPI | Builder API: types, endpoints |
| `relay-specs` | Markdown + Python blocks + OpenAPI | Relay API: types, endpoints |
| `beacon-APIs` | OpenAPI YAML | Beacon node REST API: endpoints, types |
| `execution-specs` | Python source | EL state machine: types, functions, opcodes |
| `execution-apis` | OpenRPC YAML | EL JSON-RPC API: methods, schemas |
| `remote-signing-api` | OpenAPI YAML | Remote signer API: endpoints, types |

## Top-Level Structure

```json
{
  "_meta": {
    "schema_version": "1.0.0",
    "source": "ethereum/builder-specs",
    "source_format": "markdown+openapi",
    "branch": "main",
    "commit": "abc123...",
    "generated_at": "2026-04-09T12:00:00Z",
    "fork_order": ["bellatrix", "capella", "deneb", "electra", "fulu"],
    "features": [],
    "files_processed": 12,
    "extractors": ["markdown", "openapi"]
  },

  "items": { ... },
  "constants": { ... },
  "type_aliases": { ... },
  "endpoints": { ... },

  "domains": { ... },
  "fork_summary": { ... },

  "_references": { ... },
  "_field_index": { ... },
  "_eip_index": { ... },
  "_type_map": { ... }
}
```

## Items

An "item" is a named definition tracked across forks: a container/class,
a function, an enum, or a dataclass.

```json
{
  "items": {
    "BuilderBid": {
      "name": "BuilderBid",
      "kind": "class",
      "domain": "bidding",
      "introduced": "bellatrix",
      "modified_in": ["capella", "deneb", "electra"],
      "forks": {
        "bellatrix": {
          "fork": "bellatrix",
          "file": "specs/bellatrix/builder.md",
          "line_number": 42,
          "kind": "class",
          "is_new": true,
          "is_modified": false,
          "code": "class BuilderBid(Container):\n    header: ExecutionPayloadHeader\n    value: uint256\n    pubkey: BLSPubkey",
          "github_url": "https://github.com/ethereum/builder-specs/blob/main/specs/bellatrix/builder.md#builderbid",
          "section_path": ["Containers", "Fork Versioned", "Bellatrix"],
          "fields": [
            {"name": "header", "type": "ExecutionPayloadHeader", "comment": ""},
            {"name": "value", "type": "uint256", "comment": ""},
            {"name": "pubkey", "type": "BLSPubkey", "comment": ""}
          ],
          "references": ["ExecutionPayloadHeader", "BLSPubkey"],
          "inline_comments": [],
          "eips": [],
          "prose": ""
        },
        "electra": {
          "...": "same structure, with is_modified: true, new fields, etc."
        }
      }
    }
  }
}
```

### Item Kinds

| Kind | Description | Has fields | Has params | Has return_type |
|---|---|---|---|---|
| `class` | SSZ container | yes | no | no |
| `dataclass` | Python dataclass | yes | no | no |
| `def` | Function/helper | no | yes | yes |
| `enum` | Enumeration | yes (variants) | no | no |

### Item Fields (enriched)

For containers, each fork definition includes a `fields` array:

```json
{
  "name": "execution_requests",
  "type": "ExecutionRequests",
  "comment": "[New in Electra]",
  "is_new_in_fork": true,
  "eip": "7685"
}
```

### Function Signatures (enriched)

For functions, each fork definition includes:

```json
{
  "params": [
    {"name": "state", "type": "BeaconState"},
    {"name": "block", "type": "SignedBeaconBlock"}
  ],
  "return_type": "None",
  "docstring": "Process a signed beacon block."
}
```

## Constants

```json
{
  "constants": {
    "DOMAIN_APPLICATION_BUILDER": [
      {
        "name": "DOMAIN_APPLICATION_BUILDER",
        "value": "DomainType('0x00000001')",
        "description": "",
        "section": "Domain types",
        "category": "constant",
        "fork": "bellatrix",
        "file": "specs/bellatrix/builder.md",
        "line_number": 18,
        "github_url": "..."
      }
    ]
  }
}
```

## Type Aliases

```json
{
  "type_aliases": {
    "Slot": [
      {
        "name": "Slot",
        "ssz_equivalent": "uint64",
        "description": "a slot number",
        "fork": "bellatrix",
        "file": "specs/bellatrix/relay.md",
        "line_number": 10,
        "github_url": "..."
      }
    ]
  }
}
```

## Endpoints

New in the unified schema. Captures API routes from OpenAPI/OpenRPC specs.

```json
{
  "endpoints": {
    "GET /eth/v1/builder/header/{slot}/{parent_hash}/{pubkey}": {
      "method": "GET",
      "path": "/eth/v1/builder/header/{slot}/{parent_hash}/{pubkey}",
      "operation_id": "getHeader",
      "summary": "Get an execution payload header.",
      "description": "Requests a builder node to produce a valid execution payload header...",
      "tags": ["Builder"],
      "parameters": [
        {
          "name": "slot",
          "in": "path",
          "required": true,
          "type": "Uint64",
          "description": "The slot for which the block should be proposed."
        },
        {
          "name": "parent_hash",
          "in": "path",
          "required": true,
          "type": "Root",
          "description": "Hash of execution layer block the proposer will build on."
        },
        {
          "name": "pubkey",
          "in": "path",
          "required": true,
          "type": "Pubkey",
          "description": "The validator's BLS public key."
        },
        {
          "name": "Date-Milliseconds",
          "in": "header",
          "required": false,
          "type": "integer",
          "description": "Unix timestamp in milliseconds..."
        }
      ],
      "request_body": null,
      "responses": {
        "200": {
          "description": "Success response.",
          "content_types": ["application/json", "application/octet-stream"],
          "schema_ref": "SignedBuilderBid",
          "fork_versioned": true,
          "fork_variants": {
            "bellatrix": "Bellatrix.SignedBuilderBid",
            "capella": "Capella.SignedBuilderBid",
            "deneb": "Deneb.SignedBuilderBid",
            "electra": "Electra.SignedBuilderBid",
            "fulu": "Fulu.SignedBuilderBid"
          }
        },
        "204": {
          "description": "No header is available."
        },
        "400": {
          "description": "Error response.",
          "schema_ref": "ErrorMessage"
        },
        "406": {
          "description": "Not Acceptable — requested content type not supported."
        }
      },
      "content_negotiation": {
        "request_types": ["application/json"],
        "response_types": ["application/json", "application/octet-stream"],
        "ssz_support": true,
        "notes": "Prefer SSZ via Accept: application/octet-stream;q=1.0,application/json;q=0.9"
      },
      "examples": {
        "bellatrix": {
          "ref": "Bellatrix.SignedBuilderBid",
          "source": "examples/bellatrix/signed_builder_bid.json"
        }
      },
      "errors": {
        "400": {
          "schema_ref": "ErrorMessage",
          "example": {"code": 400, "message": "Unknown hash: missing parent hash"}
        },
        "406": {
          "description": "Requested content type not supported by this endpoint."
        },
        "500": {
          "schema_ref": "ErrorMessage"
        }
      },
      "source_file": "apis/builder/header.yaml",
      "github_url": "..."
    }
  }
}
```

### Endpoint Fields

| Field | Description |
|---|---|
| `method` | HTTP method (GET, POST, etc.) or "rpc" for JSON-RPC |
| `path` | URL path template, or RPC method name for execution-apis |
| `operation_id` | Unique operation identifier |
| `parameters` | Path, query, header params with types |
| `request_body` | POST/PUT body schema reference, null if none |
| `responses` | Status code -> response description + schema |
| `fork_versioned` | Whether the response varies by fork |
| `fork_variants` | If fork_versioned, maps fork -> specific type |
| `content_types` | Supported content types (JSON, SSZ, etc.) |

## Cross-Reference Indexes

### `_references` — Reverse dependency graph

```json
{
  "_references": {
    "ExecutionPayloadHeader": ["BuilderBid", "BlindedBeaconBlockBody", "..."],
    "BLSPubkey": ["BuilderBid", "ValidatorRegistrationV1", "..."]
  }
}
```

### `_field_index` — Field-level tracking

```json
{
  "_field_index": {
    "BuilderBid.header": {
      "container": "BuilderBid",
      "field": "header",
      "type": "ExecutionPayloadHeader",
      "forks": {
        "bellatrix": {"type": "ExecutionPayloadHeader", "comment": ""},
        "electra": {"type": "ExecutionPayloadHeader", "comment": ""}
      }
    }
  }
}
```

### `_eip_index` — What each EIP touches

```json
{
  "_eip_index": {
    "7685": {
      "items": [
        {"item": "BuilderBid", "fork": "electra", "change": "modified"},
        {"item": "ExecutionRequests", "fork": "electra", "change": "new"}
      ],
      "count": 2
    }
  }
}
```

### `_type_map` — Cross-spec type resolution (NEW)

Maps type names to their canonical source. When `BuilderBid` references
`ExecutionPayloadHeader`, this tells you it lives in `consensus-specs`.

```json
{
  "_type_map": {
    "ExecutionPayloadHeader": {
      "source": "ethereum/consensus-specs",
      "introduced": "bellatrix",
      "kind": "class"
    },
    "BuilderBid": {
      "source": "ethereum/builder-specs",
      "introduced": "bellatrix",
      "kind": "class"
    },
    "BidTrace": {
      "source": "flashbots/relay-specs",
      "introduced": "bellatrix",
      "kind": "class"
    }
  }
}
```

This enables the explorer to render cross-spec links: clicking
`ExecutionPayloadHeader` inside a builder-specs item can navigate
to its definition in the consensus-specs index.

## Domains

Functional groupings for organizing the UI sidebar.

```json
{
  "domains": {
    "bidding": {
      "classes": ["BuilderBid", "SignedBuilderBid"],
      "functions": ["process_registration"],
      "other": []
    },
    "registration": {
      "classes": ["ValidatorRegistrationV1", "SignedValidatorRegistrationV1"],
      "functions": ["is_eligible_for_registration", "verify_registration_signature"],
      "other": []
    }
  }
}
```

## Fork Summary

```json
{
  "fork_summary": {
    "electra": {
      "new": ["ExecutionRequests"],
      "modified": ["BuilderBid", "BlindedBeaconBlockBody"],
      "total_definitions": 3
    }
  }
}
```

## Source Traceability

Every item, constant, type alias, and endpoint carries a  field
that links back to the exact line in the source repo. This serves two purposes:

1. **Verification** — agents and humans can click through to see the original
   context, surrounding prose, and related definitions
2. **Trust** — the schema is a derived artifact. The source is always one click away.

For items extracted from Python source (execution-specs), the 
points to the function or class definition line. For OpenAPI endpoints, it
points to the YAML operation definition. The  field preserves the full
path relative to the repo root (e.g., ),
which maps directly to the Python import path.

## Design Principles

1. **Each spec repo produces its own index file.** No monolithic extraction.
   Cross-spec linking happens at query time via `_type_map`.

2. **Items and endpoints are separate top-level collections.** An item is a
   type/function/constant. An endpoint is an API route. They reference each
   other by name.

3. **Fork-first organization.** Every definition is keyed by fork. This is
   the natural structure for diff-based specs and enables "what changed in
   fork X" queries trivially.

4. **Enrichment is a separate pass.** Raw extraction produces items with code.
   Enrichment adds fields, signatures, references, EIP tags. This mirrors the
   existing beacon-specs-explorer pipeline.

5. **Source format is abstracted away.** Whether the source is markdown+Python,
   real Python source, or OpenAPI YAML, the output schema is identical. The
   extractor is the only thing that differs.

## File Naming Convention

```
{repo-slug}_index.json

consensus-specs_index.json
builder-specs_index.json
relay-specs_index.json
beacon-apis_index.json
execution-specs_index.json
execution-apis_index.json
remote-signing-api_index.json
```

## Agent Usage Patterns

```python
# What types does BuilderBid reference?
data["items"]["BuilderBid"]["forks"]["electra"]["references"]

# What changed in Electra?
data["fork_summary"]["electra"]

# What endpoints return a SignedBuilderBid?
[ep for ep in data["endpoints"].values()
 if any(r.get("schema_ref") == "SignedBuilderBid"
        for r in ep["responses"].values())]

# What EIPs affect the builder spec?
data["_eip_index"]

# Where does ExecutionPayloadHeader come from?
data["_type_map"]["ExecutionPayloadHeader"]["source"]

# What are the fields of BuilderBid at Electra?
data["items"]["BuilderBid"]["forks"]["electra"]["fields"]

# GET /eth/v1/builder/header params?
data["endpoints"]["GET /eth/v1/builder/header/{slot}/{parent_hash}/{pubkey}"]["parameters"]
```
