# Lycosa capability tests — RAG, Agent reasoning, Workflow (2026-07-12)

Run against the **live** controller (`http://localhost:8000`) after the v0.3.0
hardened rebuild. Authored test artifacts + real results.

> **Two caveats that shape the results.** (1) Lycosa is a LAN-first AI
> *orchestration platform* (Python/FastAPI + Flutter), not a Node.js/Firebase
> trucking app — the trucking scenario is used as a realistic test *domain* fed
> through Lycosa's generic RAG/agent/workflow engine. (2) The only online node
> (`DESKTOP-1NN35HM`) **advertises no LLM model**, so every test that needs the
> model to *generate* an answer cannot execute yet; those are authored and
> marked BLOCKED with the exact error and how to unblock.

---

## 1. RAG Knowledge Retrieval

Setup (done live): created collection `trucking-ops`, ingested
`driver_onboarding.md` (5 onboarding steps → embedded, 3 chunks, `hashing`
backend).

### Test 1 — Factual retrieval
**Query:** `What are the onboarding procedures for new truck drivers?`
**Result (live):** top chunk scores **0.289, 0.286, 0.227** — retrieval returns
the onboarding document's chunks.

### Test 2 — Hallucination / out-of-bounds refusal
**Query:** `What are the company's international shipping and customs policies?`
**Result (live):** top chunk scores **0.252, 0.198, 0.172** — no such content
exists in the collection.

### Finding
On the live **`hashing` (keyword) backend** the out-of-bounds top score (0.252)
is only marginally below the factual one (0.289), and both top-match the same
chunk — the keyword embedder does **not** cleanly separate in-bounds from
out-of-bounds. This is the exact weakness measured in the #3 embedding
benchmark (`docs/rag_embedding_benchmark.md`), where `fastembed` reached perfect
recall@3/MRR. The out-of-bounds **refusal** (ADR-019) is therefore two-layered:
(a) optional `RETRIEVAL_MIN_SCORE` filtering (default 0.0 here — filters
nothing), and (b) the LLM grounding instruction that refuses when the retrieved
context doesn't contain the answer. Layer (b) needs a model → see §2 blocker.
**Recommendation:** set `EMBEDDING_BACKEND=fastembed` and raise
`RETRIEVAL_MIN_SCORE` (~0.3) for reliable refusal on this data.

---

## 2. Agent Reasoning & Context Memory — AUTHORED, execution BLOCKED (no model)

Both need an online node with a loaded model. Live probe returned:
`node DESKTOP-1NN35HM: no models available`. Unblock: pull a model onto the
node (Ollama running + `POST /api/v1/nodes/{id}/models`, or the agent's
auto-pull), then POST these to `/api/v1/tasks`.

### 2a — Complex analysis prompt
```
Analyze the architecture of a fleet-dispatch system built on a Node.js backend
with a Firebase (Firestore + Realtime Database) data layer. Identify the
operational bottlenecks that would most affect truck drivers in the field —
e.g. real-time location write contention, Firestore read fan-out on the
dispatch feed, cold-start latency on cloud functions during shift peaks, and
offline sync conflicts when drivers lose connectivity. For each bottleneck,
state the driver-facing symptom and one concrete mitigation.
```
Submit as `{"prompt": "<above>"}`. (The agent analyzes whatever architecture the
prompt describes; Lycosa itself is Python/FastAPI, so this is external-system
analysis, not introspection.)

### 2b — Multi-turn memory (two-part)
> **Architecture note:** `POST /api/v1/tasks` is **single-shot / stateless** —
> there is no native conversation-memory feature. True multi-turn memory must be
> threaded manually (carry turn 1's output into turn 2's prompt) or modeled as a
> **workflow** using `{{ steps.<id>.output }}` template refs. Artifacts below;
> the workflow form is the memory-preserving one.

- **Turn 1:** `Act as a dispatcher. A shipper is moving 5 tons of cargo from
  Jeddah to Riyadh. Acknowledge the assignment and state the route.`
- **Turn 2:** `Now draft a short dispatch confirmation message for that shipment
  — do not restate the tonnage, origin, or destination; refer to "the shipment
  above".`
  Memory-preserving workflow version (2 task steps): step `dispatch` (Turn 1),
  then step `confirm` with prompt referencing `{{ steps.dispatch.output }}`.

---

## 3. Workflow Automation — 3-step gated workflow (RUN LIVE)

Workflow `dispatch-gate-test`: `retrieve(lookup) → approval(gate) → retrieve(confirm)`.

### Happy path
**Input:** `Ship 5 tons of cargo from Jeddah to Riyadh`
**Result (live):** start → `lookup=succeeded`, then **paused at the gate**
(`status=paused`, `gate=pending_approval`). After approve →
`lookup=succeeded, gate=succeeded, confirm=succeeded`, run **succeeded**. The
3-step gate (pause → human approval → resume) works end-to-end.

### Edge case / guardrail
**Input (missing weight):** `Ship cargo from Jeddah to Riyadh`
**Result (live):** rejecting the gate → run **failed**, `gate=failed`,
error `step 'gate' rejected` — the guardrail halts dispatch.

> **Note on "missing-weight validation":** Lycosa's gate is a *human approval*
> (or a `when` conditional on a prior step's output); it does **not** do
> structured field validation by itself. Automatic "reject if weight missing"
> needs an LLM **task** step to parse/validate, e.g. workflow `weight-validate-test`:
> step `validate` with prompt *"Extract origin, destination, weight from
> `{{ input }}`. If weight is missing reply exactly 'INVALID: missing weight'."*
> — created live, but its run **failed: no models available** (same blocker as §2).

---

## Live artifacts created (dev stack — safe to delete)
- collection `trucking-ops` + document `driver_onboarding.md`
- workflows `dispatch-gate-test`, `weight-validate-test` (+ their runs)

---

## Re-run with LLM installed (`llama3.2:1b` on DESKTOP-1NN35HM)

- **Grounded out-of-bounds refusal WORKS** ✅ — the international-shipping task
  returned exactly *"I cannot answer this based on the retrieved knowledge."*
- **Factual grounded answer was weak/hedged** — retrieval (hashing backend)
  surfaced Step 3 instead of the license/document step, so the model gave a
  hedged answer. Reinforces switching to `fastembed`.
- **Agent reasoning + trivial tasks now execute** (trivial task returned "PONG").

## PDF/DOCX RAG bug found → filed

Reproduced live: a text **PDF ingests and retrieves correctly** (exact sentence
at score 0.606). A **DOCX is silently corrupted** — a 2-paragraph docx produced
**50 garbage chunks** (binary ZIP decoded as UTF-8), and its real text is
unretrievable, yet `status: embedded`. Root cause: `loader.extract_text` has no
`.docx` branch → binary falls through to `data.decode("utf-8")`.
- **#28** (bug) — DOCX silently ingested as corrupt garbage; add python-docx +
  reject undecodable binary formats.
- **#29** (enhancement) — scanned/image-only PDFs have no text layer → no OCR.

## Bottom line
- **RAG retrieval:** works; on the default keyword backend, in/out-of-bounds
  separation is weak — switch to `fastembed` (+ `RETRIEVAL_MIN_SCORE`).
- **Workflow gating:** fully working live (approve → proceed, reject → halt).
- **Anything needing a generated answer** (agent reasoning, multi-turn, grounded
  refusal text, LLM field-validation): blocked until a model is loaded on an
  online node. Multi-turn memory is not native — model it as a workflow.
