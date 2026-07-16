#!/usr/bin/env bash
set -euo pipefail

selector="${1:-latest}"
container="${SOURCE_LINK_DB_CONTAINER:-supabase_db_shopify_supabase_backend}"

if [ "$selector" = "latest" ]; then
  where_clause="attempt_id = (select attempt_id from public.source_link_trace_events where attempt_id is not null order by created_at desc limit 1)"
elif [[ "$selector" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$ ]]; then
  where_clause="attempt_id = '$selector'::uuid"
else
  echo "Usage: $0 latest|TRACE_UUID" >&2
  exit 2
fi

docker exec "$container" psql -U postgres -d postgres -x -c \
  "select created_at, attempt_id, component, stage, status, source_file_id, highlight_file_id, request_id, correlation_id, duration_ms, details from public.source_link_trace_events where $where_clause order by created_at, id;"
