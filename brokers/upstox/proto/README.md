# Upstox V3 Market-Data Feed — Vendored Protocol Artifacts

## Provenance

| Item | Value |
|---|---|
| Official schema source | https://assets.upstox.com/feed/market-data-feed/v3/MarketDataFeed.proto |
| Referenced by | https://upstox.com/developer/api-documentation/v3/get-market-data-feed/ |
| Retrieved | 2026-08-23 |
| SHA256 (`MarketDataFeed.proto`) | `570fe9ab5b31aca886f2f9af119bd3c6de9a53c84814de64ab71ed6fb923fa3f` |
| Modifications | **NONE** — vendored verbatim |

## Generated bindings

`MarketDataFeed_pb2.py` (natural filename for `MarketDataFeed.proto`) is
committed and imported directly at runtime.

**Provenance chain:**

```
official Upstox MarketDataFeed.proto   (vendored, SHA256 above)
      ->  standard grpc_tools.protoc   (PINNED version below)
      ->  committed MarketDataFeed_pb2.py
```

No custom translator is used or maintained. A former in-repo restricted-
grammar generator (`generate_pb2.py`) has been removed; the standard
protobuf compiler is the sole generation authority.

## Regeneration (development / CI only)

Exact command (run from the repository root so the generated module name
matches its import path):

```bash
python -m grpc_tools.protoc \
  -I. \
  --python_out=<OUTPUT_DIR>/brokers/upstox/proto \
  brokers/upstox/proto/MarketDataFeed.proto
```

Pinned standard tooling:

| Tool | Version |
|---|---|
| `grpcio-tools` | **1.80.0** |
| bundled protoc | the protoc shipped inside grpcio-tools 1.80.0 (`python -m grpc_tools.protoc --version` prints it) |
| generated Python protobuf version | **6.31.1** (emitted header: `# Protobuf Python Version: 6.31.1`) |

Why this pin: grpcio-tools 1.80.0 is the newest release on the
protobuf-6.x code-generation line (`protobuf>=6.31.1,<7.0.0`), matching
the runtime pin below. Later releases (1.81+) target protobuf 7.x and
would emit bindings incompatible with that runtime range.

The generated binding enforces its own runtime minimum at import time via
`_runtime_version.ValidateProtobufRuntimeVersion(Domain.PUBLIC, 6, 31, 1, ...)`;
the declared runtime requirement in `requirements.txt`
(`protobuf>=6.31.1,<7`) is derived from that check.

CI enforcement: `.github/workflows/upstox-proto-check.yml`
(`workflow_dispatch`, **check-only**) regenerates into a temp directory
and requires the result to be byte-identical to the committed binding.
On mismatch it fails and uploads the canonical generated file as an
artifact for manual adoption through the normal reviewed commit process.
The workflow has **no write permissions and never commits or pushes** to
the repository.

The repository copy is never regenerated in place during comparison.

## Runtime dependency policy

- Runtime requires **`protobuf>=6.31.1,<7` only** (`requirements.txt`),
  matching the generated binding's embedded runtime-version validation.
- `grpcio-tools` is a development/CI-only tool and is **NOT** a runtime
  dependency; it is never added to `requirements.txt`.
- There is **no runtime code generation** - the committed binding is
  imported directly.
- No custom proto parser/generator exists or is maintained.
- `websockets` remains deferred until Phase D3.

## Presence semantics

See the P-ZERO policy text in `brokers/upstox/feed_protocol.py`. In short:
V3 proto3 scalars have no presence — decoded default-zero equals unset on
the wire; MarketHub treats both as *not reported* for WS field maps, while
REST JSON keeps literal-zero semantics.
