#!/usr/bin/env bash
# Files the open findings from the v0.2.0 QA validation (Phases 4-6) as GitHub
# issues. Requires the GitHub CLI, authenticated against abdra7/Lycosa:
#   winget install GitHub.cli   (then restart shell)
#   gh auth login
# Then run:  bash scripts/file_v020_qa_issues.sh
#
# NOTE: the main Phase 4/5 defect (RAG hallucination / no uncertainty admission)
# is already FIXED in code (ADR-019) and is intentionally not filed here.
set -euo pipefail

gh issue create \
  --title "RAG: structure-aware loaders for CSV and JSON" \
  --label enhancement \
  --body "**Found by:** v0.2.0 Phase 4 & 5 (RAG & Knowledge) validation.

CSV and JSON documents ingest as plain UTF-8 text (loader parses PDF via pypdf,
everything else as text) — searchable but not structure-aware: no per-row /
per-field parsing, so field-level retrieval isn't possible.

**Proposed:** structured loaders (CSV -> row/record chunks with header context;
JSON -> path-addressed field chunks) behind the \`extract_text\` seam in
\`backend/app/services/knowledge/loader.py\`.
**Acceptance:** a CSV and a JSON file ingest into per-record chunks; a
field-targeted query retrieves the right record."

gh issue create \
  --title "RAG: benchmark semantic precision/recall on the fastembed backend" \
  --label tech-debt \
  --body "**Found by:** v0.2.0 Phase 4 & 5 validation.

The default \`hashing\` embedder is keyword-level; its scores aren't semantically
calibrated (observed live: an out-of-scope query scored *higher*, 0.40, than an
in-scope one, 0.23). Grounding is enforced regardless (ADR-019), but semantic
quality needs the \`fastembed\` backend.

**Proposed:** a labelled query set + precision@k / recall@k on both \`hashing\`
and \`fastembed\`; document the delta and recommend a default
\`RETRIEVAL_MIN_SCORE\` for fastembed.
**Acceptance:** reproducible benchmark script + results note; recommended
threshold."

gh issue create \
  --title "Controller: run multiple uvicorn/gunicorn workers for concurrency" \
  --label enhancement \
  --body "**Found by:** v0.2.0 Phase 6 concurrency stress test (throwaway stack).

The controller serves from a single uvicorn worker. Under 100-5000 concurrent
requests it stayed stable (no 5xx, flat ~106 MB memory, instant 13 ms recovery)
but throughput was ceiling-bound and latency grew linearly with concurrency as
requests queued behind the one worker (CPU never saturated).

**Proposed:** run N workers (\`uvicorn --workers\` / gunicorn+uvicorn workers),
configurable via env, in the compose command.
**Note/dependency:** the in-process rate limiter and event bus are per-process
(ADR-008/016) — multiple workers multiply the effective rate limit, which makes
the already-backlogged Redis-backed limiter relevant. Acceptance: worker count
is configurable; a multi-worker run shows higher throughput at equal latency."

gh issue create \
  --title "QA: proper distributed load-testing harness for capacity numbers" \
  --label tech-debt \
  --body "**Found by:** v0.2.0 Phase 6.

The Phase 6 sweep used a single-process async Python client on the same laptop
(WSL2) as the target, so the observed ~60 req/s ceiling reflects the client and
loopback, not the controller (which sat at ~14% CPU). Real capacity numbers need
a dedicated load tool (hey / wrk / k6) run from separate hardware, ideally
against a multi-worker controller (see the workers issue).

**Acceptance:** a documented k6/hey scenario + a capacity baseline captured off
the dev laptop."

gh issue create \
  --title "Security: rate-limit bypass via rotating X-API-Key header" \
  --label security \
  --body "**Found by:** v0.2.0 Phase 7 static security audit.

\`RateLimitMiddleware._client_key\` keys on the raw, *unvalidated* \`X-API-Key\`
header (first 16 chars) before authentication, falling back to client IP only
when no key header is present. An attacker can send a different arbitrary
\`X-API-Key\` value on every request to get a fresh bucket each time, bypassing
both per-key and per-IP limits — including on \`POST /api/v1/auth/login\`, which
defeats credential brute-force throttling.

**Failure scenario:** \`for i in \$(seq 1 100000); do curl -H \"X-API-Key: r\$i\"
-d '{bad creds}' .../auth/login; done\` is never rate-limited.

**Fix:** key on the validated principal after auth, or always fold client IP
into the key so a bogus rotating header can't escape the IP bucket; ignore
unrecognized key values for limiting. File: \`backend/app/core/ratelimit.py\`."

gh issue create \
  --title "Security: fail-fast on default/placeholder secrets in production" \
  --label security \
  --body "**Found by:** v0.2.0 Phase 7 static security audit.

The code defaults (\`jwt_secret='insecure-dev-only-...'\`) and the committed
\`.env.example\` placeholders (\`JWT_SECRET=change-me-...\`, \`POSTGRES_PASSWORD=
change-me\`, Grafana \`change-me\`) are usable as-is. A controller booted with the
placeholder \`JWT_SECRET\` lets anyone who knows it forge admin JWTs (HS256).
\`install.sh\` generates random secrets, but manual/dev stacks silently run
insecure.

**Fix:** when \`ENVIRONMENT=production\`, refuse to start if \`JWT_SECRET\` (or
DB/Grafana passwords) match a known-insecure default/placeholder. Cheap guard in
settings validation; keeps dev ergonomic while blocking insecure prod boots."

gh issue create \
  --title "Security: document RAG prompt-injection / poisoning trust boundary" \
  --label security \
  --body "**Found by:** v0.2.0 Phase 7 static security audit.

Retrieved document chunks are injected into LLM prompts, so a malicious upload
could carry prompt-injection payloads ('ignore previous instructions...'). The
ADR-019 grounding instruction reduces but does not eliminate this. Mitigating
factor: uploads require admin/operator (\`OperatorDep\`), so this needs an
authenticated privileged user — a limited trust boundary.

**Proposed (low priority):** document the boundary in SECURITY.md; if untrusted
uploads are ever enabled, add stronger context/instruction separation (e.g.
delimiter hardening, an instruction-hierarchy system prompt)."

gh issue create \
  --title "Observability: return 503 (not opaque 500) when a datastore is down" \
  --label enhancement \
  --body "**Found by:** v0.2.0 Phase 8 failure-recovery testing.

When Postgres or Qdrant is unreachable, DB-backed requests return a generic
\`500 {\"error\":{\"code\":\"internal_error\",\"message\":\"Internal server error\"}}\`
(ADR-007 catch-all). The controller stays up and recovers automatically once the
datastore returns, but operators get an opaque 500.

**Proposed (low priority):** map datastore connection errors to \`503 Service
Unavailable\` with an actionable message ('database/vector store unavailable'),
so monitoring and operators can distinguish an infra outage from a real bug.
Note: knowledge *ingestion* already does this well — it marks the document
\`failed\` with a clear 'is the qdrant service running?' message rather than a 500."

echo "Filed v0.2.0 QA issues (Phases 4-8)."
