# Extensible LLM provider adapters

Lycosa now has one normalized controller adapter backed by the pinned LiteLLM SDK, plus the existing native routes. “All providers” means an **extensible provider catalogue**, not a guarantee that every service, model, credential type or capability is operational. Every configured model requires its own live acceptance check and may incur charges.

## Routes

| Provider names | Execution |
|---|---|
| `ollama` | Existing registered local agent; unchanged |
| `anthropic` | Existing explicitly trusted HTTPS agent; unchanged |
| `openrouter` | Existing controller route with exact free-model and zero-price restriction; unchanged |
| `openai`, `gemini`, `azure`, `bedrock`, `vertex_ai`, `groq`, `mistral`, `cohere`, `deepseek`, `xai`, `together_ai`, `fireworks_ai`, `perplexity`, `cerebras`, `sambanova`, `nvidia_nim`, `huggingface`, `replicate`, `ai21`, `databricks`, `watsonx` | Opt-in controller adapter through LiteLLM |
| Administrator-defined alias | Same adapter, explicitly mapped to an SDK provider and model allowlist |
| `phantom_local` | Separate local-only Phantom API; rejected by normal task/workflow provider fields |

The SDK catalogue is broader than the built-in labels. An administrator can add an alias for another text-completion provider supported by the installed SDK, without editing the orchestrator. The desktop fetches aliases from the authenticated `/api/v1/providers` catalogue. This endpoint exposes policy metadata, not API keys, endpoint URLs or credential status.

## Install and configure

Install on the controller, not on every node:

```bash
cd backend
python -m pip install -e '.[providers]'
```

The default deployment omits this optional dependency. To build the standard backend image with adapters use the Docker build argument `LYCOSA_EXTRAS=providers`. Define `PROVIDER_PROFILES` as JSON in the controller environment. The following is a **template**; replace model placeholders with exact identifiers enabled for your account:

```json
{
  "openai": {
    "sdk_provider": "openai",
    "models": ["YOUR_APPROVED_OPENAI_MODEL"]
  },
  "gemini": {
    "sdk_provider": "gemini",
    "models": ["YOUR_APPROVED_GEMINI_MODEL"]
  },
  "azure": {
    "sdk_provider": "azure",
    "models": ["YOUR_DEPLOYMENT_NAME"],
    "api_base": "https://YOUR_RESOURCE.openai.azure.com",
    "api_version": "YOUR_SUPPORTED_API_VERSION"
  },
  "bedrock": {
    "sdk_provider": "bedrock",
    "models": ["YOUR_APPROVED_MODEL_OR_INFERENCE_PROFILE"],
    "auth": "workload_identity",
    "region": "us-east-1"
  },
  "vertex_ai": {
    "sdk_provider": "vertex_ai",
    "models": ["YOUR_APPROVED_VERTEX_MODEL"],
    "auth": "workload_identity",
    "project": "YOUR_PROJECT",
    "region": "us-central1"
  },
  "institution_gateway": {
    "sdk_provider": "openai",
    "models": ["YOUR_GATEWAY_MODEL_ALIAS"],
    "api_base": "https://YOUR_APPROVED_GATEWAY/v1"
  }
}
```

For example, on Bash set `export PROVIDER_PROFILES='{"openai":{"sdk_provider":"openai","models":["YOUR_APPROVED_OPENAI_MODEL"]}}'`, then restart the controller. For a custom Anthropic controller route, configure a new alias with `sdk_provider:"anthropic"`; the existing `anthropic` alias retains its trusted-node policy. The native `ollama`, `anthropic` and `openrouter` policies cannot be overridden by profiles.

Use **Admin → Providers** to save a key in the controller OS vault or explicitly in single-worker memory. Memory storage is now available for all API-key cloud adapters; it is never an automatic fallback. Keys are stored under the Lycosa **alias**, so two aliases can use different accounts. Workload identity is explicitly enabled only for Bedrock/Vertex AI and uses the SDK's host credential chain; configure IAM/ADC on the controller separately. Do not put access keys/service-account JSON in `PROVIDER_PROFILES` or task definitions. Providers requiring additional authentication beyond these supported paths need a reviewed extension.

An unknown/disabled model is rejected before a provider call. A valid provider name in the UI is not proof of configured access. `credential_configured` is only local key availability, not remote validation. Workload identity can work without a stored API key; its deployment policy is authoritative.

## Execution contract and limits

Normal `/api/v1/tasks` requests and task workflow steps accept a provider alias and explicit allowlisted model. New adapters execute on the controller, so no local inference node is required. Existing RAG retrieval ownership and workflow approvals remain unchanged.

- Only non-streaming text answers are supported here. No tool calls, agent loops, arbitrary headers, user-provided endpoints, SDK kwargs, cache configuration or fallback providers are accepted.
- `requires_privacy:true` blocks every external route, including custom aliases, before inference. Phantom cannot call this adapter at all.
- Model, temperature and token budget are passed through the normalized contract; unsupported parameter/model combinations fail rather than silently dropping constraints.
- No retries or provider fallbacks are configured. Callers must explicitly resubmit an ordinary failed task.
- Only completed text and allowlisted finite numeric token usage are retained. Truncated, filtered, tool-call and malformed results fail. Provider exception text/raw responses are not exposed by Lycosa. Known API-key strings are redacted from output.
- Normal cloud tasks **are persistent** and provider-side retention is outside Lycosa's control. Do not label these as Phantom or zero-retention.
- Admin endpoint configuration is credential-free HTTPS only. Endpoint ownership, allowed egress and provider SDK transport/redirect behavior still require deployment review. No arbitrary endpoint can be supplied in a task.
- ChatGPT Plus/Claude Pro subscriptions are not API credentials. API availability, rate limits and charges are account/model dependent.

## Acceptance

Tests cover every built-in SDK alias at the adapter boundary, privacy/model denial, metadata access, key storage, custom aliases, workflow parsing, structured errors and output filtering. Live provider access is not established by mocked calls. Use synthetic prompts and configured test accounts for individual smoke checks. Keep the existing free OpenRouter cap unless an administrator deliberately creates a separate approved alias for other models.

References: [LiteLLM provider catalogue](https://docs.litellm.ai/docs/providers), [normalized completion parameters](https://docs.litellm.ai/docs/completion/input).

## ملخص عربي

أُضيفت طبقة موحدة قابلة للتوسعة لمزوّدي النماذج، تشمل أسماء جاهزة لأوبن إيه آي وجيميني وأزور وبدروك وفيرتكس وغيرها. يجب تفعيل المزوّد ونماذجه المسموحة في إعدادات المتحكم وإضافة اعتماده المناسب. ويمكن إضافة أسماء أخرى يدعمها SDK دون تعديل منسّق المهام. لا يعني ظهور الاسم نجاح الاتصال الفعلي بكل خدمة.

المهام السحابية العادية تُحفظ في السجل، وتُرفض عند اشتراط الخصوصية. وضع Phantom منفصل ومحلي حصراً. لم تُستخدم مفاتيح حقيقية أو تُجرَ استدعاءات مدفوعة أثناء إعداد هذا التغيير.
