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

Useful stages are `highlight_start`, `highlight_complete`, `tunnel_stale`, `tunnel_healthy`, `viewer_url_resolved`, `check_file_info`, `get_file`, `frame_ready`, `host_ready_sent`, `document_loaded`, and `selection_sent`. A missing next stage identifies the boundary that failed.
