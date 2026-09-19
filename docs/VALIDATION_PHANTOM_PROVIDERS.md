# Validation record: Phantom and provider adapters

## Local integration review — 2026-09-19

Source: Phantom_Agent at af51866; replayed onto cleaned main dd98e2a. No application-code conflicts or duplicate feature implementation were found. The smoke-script formatting overlap was already present. A pre-existing agent formatting issue and a new Phantom dialog lint issue were corrected.

- Backend: 364 passed, 2 optional SDK tests initially skipped, 13 warnings. Follow-up: all 64 focused Phantom/provider/workflow tests passed, including both SDK transport tests, after installing LiteLLM 1.101.0 and preparing its tokenizer cache. These 64 overlap the full suite and are not additional independent coverage.
- SDK environment: the Windows wheel required public tokenizer data. Set `CUSTOM_TIKTOKEN_CACHE_DIR` to the prepared cache for offline runs; the standard `TIKTOKEN_CACHE_DIR` is overridden by this SDK. Initial sandbox download failures were environmental, not adapter failures.
- Agent: 48 passed.
- Flutter 3.44.1 / Dart 3.12.1: analysis passed; all 79 tests passed. One non-fatal existing widget hit-test warning remains.
- Ruff lint and format checks passed across backend and agent; worker lint passed.
- No real provider calls, GGUF execution, native desktop visual acceptance or physical memory-erasure verification were performed.

## Historical team validation (retained for provenance)

The record below describes the original implementation environment. Its unpushed-patch instructions and unavailable-Flutter statement are historical, not current integration instructions.


Date: 19 September 2026. Source baseline: `e91df7e4b522e7a72befbb06d7178808173eae3e` from `abdra7/Lycosa`. Working branch: `feat/phantom-agents-provider-adapters`.

## Completed

- Full backend suite after the core changes and SDK transport tests: **365 passed**, 13 warnings, approximately 33 seconds.
- Final focused regression after the last request-scoped provider-log suppression change: **64 passed**, 3 warnings, approximately 5 seconds. This includes Phantom, provider boundary tests, real SDK/HTTP translation and workflow-definition validation. These overlap the full suite and must not be added together as independent coverage.
- Ruff checks on backend and the isolated worker: passed. Backend Ruff formatting check: 134 files compliant. One pre-existing indentation issue in `scripts/smoke_openrouter.py` was corrected to satisfy the existing format gate.
- `git diff --check`: passed.
- The installed pinned LiteLLM SDK recognizes every one of the 21 additional built-in provider identifiers.
- Real LiteLLM OpenAI and Gemini translation was exercised with intercepted HTTP, synthetic credentials and synthetic content. **No live provider calls, real credentials or billing were used.**
- Existing native Ollama/Anthropic/OpenRouter tests remain in the full suite. The old “OpenAI is unsupported” negative test now checks an actually unknown provider.

The initial environment lacked the SOCKS transport extra required by its network proxy. Installing `socksio` in the isolated test environment resolved that infrastructure issue; proxy settings were not bypassed. Existing Starlette deprecation and intentionally unreachable Qdrant test warnings remain.

## Important tested boundaries

Phantom success is checked against SQL statement instrumentation: no INSERT, UPDATE or DELETE during an authenticated execution, in addition to unchanged task/execution/audit counts. Validation denies cloud provider selection, knowledge queries, arbitrary options, streaming and extra fields before the execution boundary. Tests cover API-key rejection without last-used writes, bounded request size, cleanup verification failure, cancellation, timeout, malformed runtime output, per-worker admission, remote-daemon rejection and non-echoing errors.

Provider tests cover explicit provider/model allowlists, privacy denial without a call, per-alias keys, operator catalogue versus administrative credential access, workload identity configuration, finite token usage, error redaction, incomplete-answer rejection, workflow parsing and request-local suppression of credential-bearing SDK/HTTP logs.

## Not completed in this environment

- A real Docker daemon was not available. The worker image was not built or run with a real GGUF model. Containment flags and lifecycle are tested at a simulated Docker boundary; physical memory erasure is neither implemented nor claimed.
- Flutter/Dart were not installed. The new desktop UI and three new Phantom client tests were added but not run here. The repository's dashboard CI remains the analyze/test gate. Native visual acceptance, result expiry and profile-switch behavior must be verified on a supported desktop.
- No live acceptance against external provider accounts, cloud IAM or model-specific deployments. Account/model compatibility must be checked separately.
- No production deployment, host-hardening verification, memory-forensics test, GPU isolation or penetration test.
- GitHub push was not completed: the local Git transport had no usable write credential. The connected app did not expose a callable repository-write capability in this execution context. The delivered patch contains the reviewable changes; main was not modified remotely.

## Apply the delivered patch

Use a clean checkout of the baseline above, or review conflicts against your current branch. Do not discard local work to apply the patch.

```bash
git switch -c feat/phantom-agents-provider-adapters
git apply --check /path/to/Lycosa_Phantom_Providers.patch
git apply /path/to/Lycosa_Phantom_Providers.patch
cd backend
python -m pip install -e '.[dev,providers]'
pytest
```

Then follow [Phantom deployment](PHANTOM_AGENTS.md) and [provider setup](PROVIDER_ADAPTERS.md). Run `flutter analyze` and `flutter test` from `dashboard/` on a machine with the supported SDK. Configuration alone does not establish successful native or live-model acceptance.
