# Source-link debugging

Development builds display a copyable trace UUID at the top of the source-document dialog. The UUID connects browser, highlight generation, tunnel discovery, WOPI, iframe readiness, and selection events without storing source values, WOPI tokens, or tokenized URLs.

Inspect the most recent attempt:

```sh
./scripts/source-link-trace.sh latest
```

Inspect a copied UUID:

```sh
./scripts/source-link-trace.sh 00000000-0000-4000-8000-000000000000
```

Tracing defaults to enabled when `DEBUG=true`. Override it with `SOURCE_LINK_TRACE_ENABLED=true|false`; retention defaults to 30 days and can be changed with `SOURCE_LINK_TRACE_RETENTION_DAYS`.

`SOURCE_LINK_TRACE_SAMPLE_RATE` must be between `0` and `1`. The sampling
decision is derived from the trace UUID, so every event for one attempt is
kept or discarded together. It defaults to `1` in development, test, and
staging and `0.05` in production. Production trace events contain only the
existing sanitized fields and are retained for 30 days.

Useful stages are `highlight_start`, `highlight_complete`, `tunnel_stale`, `tunnel_healthy`, `viewer_url_resolved`, `check_file_info`, `get_file`, `iframe_loaded`, `frame_ready`, `host_ready_sent`, `document_loaded`, `selection_sent`, and the terminal `source_link_complete`. For a spreadsheet source selection, the required browser order is `iframe_loaded → frame_ready → host_ready_sent → document_loaded → selection_sent → source_link_complete`. A missing next stage identifies the boundary that failed.

## Collabora deployment contract

`docker-compose.stack.yml` in this repository is the only supported application
stack. The workspace-root and `collabora-setup` Compose files are legacy local
examples and must not be used for deployment.

Run the fast Compose contract with:

```sh
./scripts/check-collabora-compose-contract.py
```

Production uses `collabora/code:25.04.7.1.1` pinned to manifest digest
`sha256:b70d5ffb3c88ec365c3685cbd4ce56cd0d29122a1290e98e7f0e9932c2cd8246`.
The readiness endpoint requests both discovery and the real viewer shell and
returns `503` with code `COLLABORA_EMBEDDING_MISCONFIGURED` if the shell CSP
does not allow every configured `COLLABORA_FRAME_ANCESTORS` origin. The
response never includes those origins.

To upgrade, change the tag and manifest-list digest in a dedicated PR, run the
Compose contract and source-viewer smoke test, then deploy to staging before
production. `latest` is canary-only and must never be substituted by deployment
automation. To roll back, restore the preceding single `image:` line (tag and
digest), deploy the stack, and confirm `/ready` reports
`collabora.embedding_ready: true`.
