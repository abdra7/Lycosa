# OpenRouter free-model setup

Maintainer: **abdra7**. This engineering increment is not a tagged release; running services must be rebuilt or restarted to load source changes.

## Desktop use

1. Run the updated controller and rebuild/reopen the desktop app.
2. As admin, open **Admin > Providers**, choose **OpenRouter**, and paste your API key.
3. Select **Controller memory (until restart)** for an explicitly single-worker test controller, or **Controller OS credential vault** if that controller account has a working secure vault. Select **Save key**, then **Check status**. Configured does not mean remotely validated.
4. In **Tasks**, select **OpenRouter (Nemotron free)**. The model field fills with `nvidia/nemotron-3.5-lightning:free`. Enter a prompt and run it; inspect the persisted result in Recent.
5. For RAG, create a Knowledge collection and upload a document. In Tasks set **Knowledge query** and **Knowledge collection** to the exact collection name. Blank collection means all collections. External execution sends retrieved context to OpenRouter; use appropriate data and consent.
6. In **Workflows > New workflow**, **Use OpenRouter free template** fills the JSON editor with two tasks and an approval gate. This replaces the draft definition. Review before creating/running it.

## Execution and storage boundaries

- OpenRouter runs directly from the controller to the fixed verified HTTPS endpoint `https://openrouter.ai/api/v1/chat/completions`; no local execution node is required. Existing Ollama routing and the trusted HTTPS-agent Anthropic path remain separate.
- Only the exact model above is allowed. Requests set zero prompt/completion price caps, do not select a paid fallback model, and do not follow redirects. Free-model availability and quotas remain provider-controlled; failures are visible, not silently substituted.
- `requires_privacy=true` rejects external providers. A failed RAG retrieval cancels external execution rather than sending an ungrounded prompt.
- Credentials are controller-wide for authorized users. Do not enable a provider on an untrusted shared controller. Admin credential endpoints require admin authentication; deploy remote administration behind HTTPS. The desktop refuses key submission to plaintext remote URLs (loopback HTTP is permitted), and refuses redirects.
- Session storage is explicit, process-local and requires `WORKERS=1`. It is not a plaintext fallback from a failed vault. It disappears on restart; signing out of the desktop does not clear a controller-wide key. Remove it in Providers when no longer wanted.
- Vault storage uses the controller OS account, not the desktop OS account. A Linux container cannot automatically read the Windows host credential vault. Removing a session override may reveal an older vault key; check effective status afterward.
- Cloud results persist allowlisted numeric usage and routing details, not credentials or raw upstream responses. Free completion is not a latency/reliability guarantee. GPU reservations, streaming, tool execution and agent loops are outside this increment.
- OpenAI/Gemini direct adapters and subscription-login flows are not implemented. Anthropic API credentials can be managed here but its existing cloud-agent policy still needs deployment configuration; no live Anthropic test was performed.

## API examples (no credentials)

`POST /api/v1/tasks`:

```json
{
  "prompt": "What is the support code in the document?",
  "provider": "openrouter",
  "model": "nvidia/nemotron-3.5-lightning:free",
  "knowledge_query": "support code",
  "knowledge_collection": "my-test-collection",
  "max_tokens": 1024
}
```

Workflow task steps support `provider`, `model`, `requires_privacy`, `max_tokens`, `knowledge_query` and `knowledge_collection`. Retrieve steps retain their existing `collection` field. Start a workflow through `POST /api/v1/workflows/{id}/run`.

Admin key endpoints: `PUT /api/v1/admin/providers/{provider}/credential` with a `key` and explicit `storage` (`vault` or `session`); `DELETE` the same route with `?storage=session` or `?storage=vault`. Never include a key in a URL or persist it in task/workflow JSON.

## Testing

`backend/scripts/smoke_openrouter.py --execute` is an opt-in live smoke test intended to run inside the configured API container, with a key supplied through stdin, never command arguments. It uses only synthetic data, a scoped collection, and the exact free model; it preserves task/workflow history and removes its own collection. The retained QA workflow references that removed collection and is not rerunnable without fixture setup. This script is not the full desktop acceptance suite.

The local test deployment uses ignored `infra/openrouter-session.local.yml` to set one worker without modifying `.env`. Start it with both compose files. Using the base compose file alone restores its configured worker count and discards the session credential on recreation. Multi-worker production deployments should provision the OS vault instead.

Sources: [OpenRouter quickstart](https://openrouter.ai/docs/quickstart), [requested free model](https://openrouter.ai/nvidia/nemotron-3.5-lightning:free), [API contract](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion).
