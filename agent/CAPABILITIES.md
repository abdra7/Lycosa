# Lycosa Agent: capabilities, MCP and grounded answers

Maintainer: **abdra7**. This is an incremental local implementation, not an autonomous system-access agent.

Grounded answers use labeled source passages. Explicit English abstentions are normalized
to a standard refusal with `citation_status=refused` and no citations; unsupported
elaboration is discarded. This conservative rule is not factual-entailment validation.
Live checks: `python agent/scripts/smoke_grounded_mcp.py --model llama3.2:1b`
and the same command with `--unsupported` test supported and absent facts respectively.

## What it can do

| Capability | Interface | Boundary |
|---|---|---|
| Device registration, hardware profiling, Usage reporting and LAN discovery | Existing agent service | Controller-managed node identity |
| Local inference and installed-model inventory | Existing `/execute`, `/models` | Existing agent-token authentication |
| Model download | Existing `/models/pull` | Explicit controller request; not exposed by the new MCP companion |
| Describe implemented capabilities | New `/capabilities` | Requires `X-Agent-Token`; capability metadata also included at registration |
| Answer a question from supplied evidence | New `/rag/answer` | Local runtime only; authenticated, bounded and citation-aware |
| Expose local tools to MCP hosts | Optional `lycosa-agent-mcp` process | Stdio only; no public listener or external MCP-server connections |

## Grounded-answer contract

POST `/rag/answer` on the agent with its existing `X-Agent-Token` and this body:

```json
{
  "model": "llama3.2:1b",
  "question": "What is the test code?",
  "evidence": [
    {"source": "synthetic-test.txt", "text": "The test code is LYCOSA-731.", "score": 0.9}
  ],
  "min_score": 0.5,
  "context_chars": 16000,
  "max_tokens": 512
}
```

The controller/caller retrieves documents first and supplies the evidence. The agent does **not** search arbitrary files, inspect the vault, access the controller database or obtain additional credentials. The existing desktop task route continues to use its previous prompt-injection-of-context path; it is not automatically migrated to `/rag/answer` by this increment.

- At most 20 input chunks, each at most 12,000 text characters. Evidence text budget is 256–32,000 characters, default 16,000; metadata/question/JSON overhead is separate and also schema-bounded. This is not a tokenizer-based context-window guarantee.
- Sort by caller-provided score, filter by `min_score`, remove exact duplicate text, then clip to budget. `truncated` reports clipping; supplied scores are not independently calibrated relevance scores.
- Empty/filtered-out evidence returns a fixed refusal without inference. Unknown installed-model names fail without downloading weights.
- Question/evidence are serialized separately from system instructions, which mark evidence as untrusted data. This mitigates but does not prove resistance to prompt injection.
- Results contain `sources` (S1 → source name), recognized `citations`, and `citation_status`: `no_evidence`, `refused`, `missing`, `invalid_ids` or `ids_valid`.
- `ids_valid` checks only that cited IDs exist. It does **not** prove that the cited passage supports the answer; unsupported answers remain possible. Invalid citations are not turned into fabricated source entries.
- Output budget: 1–4096 tokens. Model inventory deadline: 5 seconds; generation deadline: 90 seconds. HTTP and MCP each limit their own grounded-answer concurrency to 2; this is not a global GPU reservation or a limit on legacy `/execute`.
- HTTP saturation returns 429, generation timeout 504, other runtime failures a sanitized 502. Running Usage task counts are released on success, failure and cancellation.

## Connect an MCP host

Install from the repository root:

```console
python -m pip install -e "./agent[mcp]"
```

The optional dependency is the supported SDK v1 maintenance line (`mcp>=1.28,<2`), tested here with 1.29.1. It is not required to run the normal agent service.

Configure a trusted MCP host to launch the virtual environment's `lycosa-agent-mcp` executable, or its Python executable with arguments `-m lycosa_agent.mcp_server`. Example client configuration (adapt the wrapper to the host's format):

```json
{
  "mcpServers": {
    "lycosa-agent": {
      "command": "C:/Users/abdul/Lycosa_Project/.venv-v2/Scripts/python.exe",
      "args": ["-m", "lycosa_agent.mcp_server"],
      "env": {"LYCOSA_OLLAMA_URL": "http://127.0.0.1:11434"}
    }
  }
}
```

Tools:

- `agent_capabilities`: explicit supported/unsupported operations.
- `local_models`: list installed Ollama models, no download.
- `answer_from_context`: accepts a `request` object matching the grounded-answer contract above.

The host launches a **separate companion process** using the same local-runtime code; this is not a remote connection to a running registered agent, and it does not contribute to that service's Usage counter. Stdio trust comes from the launching user's process boundary; no HTTP port or new network authentication scheme is added. The host must approve tool use under its own policy. No client configuration is changed automatically.

## Explicitly not implemented

Shell execution, file browsing/writes, external MCP client connections, automatic tool planning/loops, web browsing, vector indexing on the agent, and autonomous multi-step execution. Existing controller workflows remain responsible for sequencing/approval. Cloud provider credentials and the previous OpenRouter integration are unchanged.

## Verification

Run `python -m pytest` from `agent/`. With the optional MCP SDK installed, the suite includes a real subprocess stdio handshake, tool discovery, calls, schema rejection and rejection of an unknown shell tool. Without that extra, MCP tests are skipped and must not be counted as a protocol pass.

Optional live local inference smoke test from the repository root:

```console
python agent/scripts/smoke_grounded_mcp.py --model llama3.2:1b
```

Uses synthetic evidence only, no provider API calls or downloads. It independently checks answer text and recognized citations. Restart an existing agent service to load its new HTTP endpoints and re-advertise its capabilities; merely editing source does not upgrade an already running process.

Reference: [official MCP Python SDK v1 documentation](https://github.com/modelcontextprotocol/python-sdk/blob/v1.x/README.md).
