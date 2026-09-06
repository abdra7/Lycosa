# Sprint 12: Usage and runtime routing

Maintainer: **abdra7**.

## Included

- Per-GPU Usage and VRAM measurements through optional NVML, with an
  `nvidia-smi` fallback. Missing measurements are unavailable, not zero.
- Separate GPU and VRAM Usage bars in node details. Internal API telemetry
  field names remain unchanged for compatibility.
- Shared chat request/response contracts, Ollama chat, and legacy Ollama
  prompt execution.
- An opt-in Anthropic API adapter. Live cloud acceptance is deferred;
  automated provider tests use mocked requests.
- Routing filters for privacy, installed models, runtime health, resource
  requirements, trusted cloud nodes, and fresh Usage data, with explanations.

## Local execution

Install the agent and controller normally. Optional NVIDIA collection is
available with `pip install -e './agent[gpu]'`. Local Ollama execution does
not need a paid chat subscription.

Task requests retain `prompt`, `model`, and `options`. Optional fields include
`provider` (`ollama` by default), `requires_privacy`, `required_ram_mb`,
`required_vram_mb`, and `allow_cpu_fallback`. Memory units are MiB.

## Optional cloud configuration

On an explicitly trusted agent, set `LYCOSA_CLOUD_EXECUTION_ENABLED=true`.
Expose it through verified HTTPS. Configure these nonsecret controller
environment values as JSON:

- `CLOUD_ALLOWED_NODE_IDS`: approved node UUIDs.
- `CLOUD_NODE_ORIGINS`: exact node UUID-to-advertised-HTTPS-URL mapping.
- `CLOUD_MODELS`: explicitly allowed provider model identifiers.

Store the API key interactively under the controller's OS account:

```console
python -m app.services.provider_secrets set anthropic
python -m app.services.provider_secrets status anthropic
```

Never pass the key in shell arguments, task options, source files or chat.
Windows Credential Manager, macOS Keychain and Linux Secret Service are the
supported vault backends. A headless Linux container needs an intentionally
provisioned Secret Service; Windows host credentials are not automatically
available to the container. Cloud execution fails closed without a usable key.

The selected agent receives the key in memory during execution. Its OS
administrator can observe it; Python cannot guarantee memory zeroization.
Only trusted agents should be authorized. Disable secret-bearing header
tracing and crash dumps, and rotate keys if an agent is compromised.

Claude Pro does not include Anthropic API usage; API billing is separate.
Subscription-based login is not implemented in Lycosa.

## Limits and verification

Usage snapshots are not atomic memory reservations. VRAM is not summed across
cards, and the reported GPU index is advisory: Ollama controls placement.
Unknown model sizes need explicit requirements. CPU fallback needs explicit
permission and sufficient measured RAM. Tools, streaming and agent loops are
rejected in this increment. Additional providers and model cost/context
admission remain future work.

Automated API checks cover authentication, privacy, missing keys, upstream
errors, model validation and secret redaction without provider charges.
The local CPU path has passed a real Controller-to-Agent-to-Ollama task with
persisted output, and structured chat has also passed. Supported-GPU and
live-cloud acceptance remain outstanding; no new release is claimed here.

Developer smoke checks are in `backend/scripts/smoke_local.py` and
`backend/scripts/smoke_runtime_local.py`; they require a deliberately
configured local test deployment. Internal operational reports belong in
the project vault, not the public repository.
