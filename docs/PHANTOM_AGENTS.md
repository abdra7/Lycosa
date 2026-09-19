# Ephemeral Phantom Agents

Status: source implementation on `Phantom_Agent` at `af51866`; isolated CPU inference requires the deployment below. This is **application-level no-content-retention**, not a certified “Zero-Trace AI” standard or forensic memory-erasure guarantee.

## Contract

`POST /api/v1/phantom/tasks` accepts a user-session Bearer token and:

```json
{"provider":"phantom_local","model":"private_model","prompt":"Synthetic input","max_tokens":512,"temperature":0.2}
```

The response contains `output`, `persisted:false`, `cleanup:"confirmed"` and a privacy-boundary description. It has `Cache-Control: no-store`. There is no task ID, history entry, asynchronous retrieval URL or automatic retry.

Each request creates a new **local** CPU model runtime, with its own model/KV memory. It does not send the prompt to a long-lived Ollama server. The container has no network, a read-only root, a read-only model mount, non-root UID, dropped capabilities, no-new-privileges, bounded CPU/RAM/PIDs, disabled core dumps, no Docker log driver, and temporary scratch space. Input goes through stdin, not command arguments, an environment variable or a file. Only a bounded JSON result returns through stdout; native diagnostic output is discarded. The worker closes the model and exits. Docker removal is attempted on success, error, timeout and cancellation. A result is returned only after the daemon confirms the named container is absent. Failure to confirm cleanup returns a generic failure, not the output.

The path does **not** call the persistent task orchestrator, audit service, retrieval pipeline, workflow engine or event bus. It performs read-only session validation. Node API keys are rejected before their usual `last_used_at` write. Global rate-limit infrastructure can retain coarse IP/timing metadata. Existing login/session records remain intact.

Requests cannot select a cloud provider, request RAG ingestion, attach arbitrary options or enter a persistent workflow. The normal task/workflow contracts reject unknown `phantom`/`ephemeral` flags rather than silently persisting a supposedly private task.

## Deployment: local Linux Docker engine

The first version supports a controller running on a Linux host with Docker CLI access to a local Unix socket. Windows/macOS desktop clients can connect to that controller over HTTPS. The normal backend Docker image intentionally does not mount the host Docker socket or install Docker CLI automatically: access to that socket is a significant administrator privilege. Do not enable it casually in a shared deployment.

1. Build the trusted worker image from the repository root:

   ```bash
   docker build -t lycosa-phantom:local infra/phantom
   ```

2. Install a licensed, chat-compatible GGUF model beforehand. The worker never downloads models. The model file must be readable by UID 65534 and fit within the configured memory limit. The example paths below belong to the Docker host and must also be visible to the host controller.

3. Configure the controller's deployment environment (these are nonsecret settings):

   ```bash
   export PHANTOM_ENABLED=true
   export PHANTOM_MODELS='{"private_model":"/srv/lycosa-models/private-model.gguf"}'
   export PHANTOM_IMAGE=lycosa-phantom:local
   export PHANTOM_TIMEOUT_SECONDS=120
   export PHANTOM_MEMORY_MB=4096
   export PHANTOM_CPUS=2
   export PHANTOM_MAX_CONCURRENT=1
   ```

   Pin the worker image by digest for a reviewed deployment. Capacity limits are **per controller worker**; budget total host memory for `WORKERS × PHANTOM_MAX_CONCURRENT`. No global GPU scheduling/reservations are claimed; this worker is CPU-only.

4. Restart the host controller. Sign in, then use **Tasks → Phantom task (no history)**. The model menu comes from `GET /api/v1/phantom/capabilities`. If isolation is unavailable, the request fails closed: it never falls back to a regular task or cloud provider.

5. Use synthetic data first. Verify no new task, execution, retrieval or audit rows; verify no content in reverse-proxy/APM logs; test timeout and container deletion. Do not use real sensitive data until deployment-level acceptance passes.

## Client behavior

The separate dialog clears its input when submitting and holds the returned answer only in widget memory. It clears the displayed answer after 60 seconds, when explicitly cleared, on profile change, or when closed. It offers no copy/export control. Clearing a view is not proof of physical memory erasure. Closing the dialog does not guarantee immediate HTTP cancellation; the worker deadline still bounds execution. If the HTTP connection actually disconnects, the API cancels work and waits for cleanup. Results cannot be recovered later from Recent Tasks.

## Honest privacy boundaries

- HTTP/Python/Dart strings, OS memory, runtime allocators, browser/desktop screenshots, input methods and host backups are outside a container-removal guarantee. No claim of secure zeroization is made.
- The container prohibits swap for its memory through equal memory/swap limits. Controller and client swap, hibernation, crash collection and memory inspection require separate host controls. Protect or disable these according to the deployment threat model.
- Container metadata and daemon/security/access logs can show that execution occurred. They must not receive input/output content; operators must configure proxies, tracing and host logging accordingly. Rejecting query strings cannot retract a URL already logged upstream.
- Docker is not a defense against a malicious host administrator or kernel compromise. No TEE, remote attestation or cryptographic deletion proof is implemented.
- The immutable model weights remain on disk; they are not task-derived data. Inference does not train or modify them. Each runtime's task context dies with that runtime, subject to physical memory limitations above.
- A hard controller crash may leave an empty created container or an active worker. The worker has its own wall-clock alarm and `--rm`; inspect the `lycosa.phantom=true` label after a host incident. Do not delete other operators' active containers blindly. A deployment supervisor/recovery drill is still required.
- `cleanup:"confirmed"` means a successful Docker inventory check found no container with that generated name. It is not a memory-forensics result.

## Verification

Automated tests exercise API authorization, no task/audit writes, forbidden cloud/RAG input, non-echoing validation, body limits, bounded capacity, local-daemon restriction, cleanup failure, cancellation and timeout with a simulated Docker boundary. They do not replace building the image and running a real GGUF model. See `VALIDATION_PHANTOM_PROVIDERS.md` for this change's actual validation.

## ملخص عربي

الوكلاء المؤقتون ينفذون المهمة في نموذج محلي داخل حاوية مستقلة بلا شبكة، ولا يحفظ المسار الطلب أو النتيجة في جداول المهام أو التدقيق. يعاد الناتج بعد تأكيد إزالة الحاوية، ويختفي من الواجهة بعد دقيقة. يحتاج التفعيل إلى متحكم لينكس ومحرّك Docker محلي ونموذج GGUF مثبت مسبقاً. لا يستعمل هذا الوضع مزوّداً سحابياً ولا يسترجع معرفة من قاعدة المشروع.

هذا **تقليل للاحتفاظ بالمحتوى على مستوى التطبيق**، وليس ضماناً بمحو كل أثر من الذاكرة أو نظام التشغيل. نجاح اختبارات المحاكاة لا يثبت العزل الفعلي؛ يجب اختبار الحاوية والنموذج وإعدادات المضيف قبل استخدام بيانات حساسة.

Reference: [Docker runtime isolation controls](https://docs.docker.com/engine/containers/run/).
