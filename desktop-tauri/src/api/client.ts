// Typed client for the Lycosa controller API (ADR-015, ported to
// TypeScript in ADR-037). Each call goes through the Rust proxy, which adds
// the active profile's URL and token; per-call timeouts match the Flutter
// client's.

import { transport, type ApiCall, type SseEvent } from "../ipc/transport";
import type {
  ApiKeyInfo,
  AuditLogEntry,
  ChatMessage,
  ClaudeNode,
  CollectionInfo,
  DocumentInfo,
  LlmAccount,
  LlmAnswer,
  LlmModel,
  LlmProbeResult,
  LlmProvider,
  LlmRecommendation,
  LlmRoute,
  LlmRouting,
  MintedApiKey,
  NodeInfo,
  OpenRouterSignIn,
  Principal,
  RetrievedChunk,
  RouteEntry,
  TaskInfo,
  WorkflowInfo,
  WorkflowRun,
} from "./types";

const MINUTE = 60_000;

/** Drop undefined/null entries, as Dart's `'key': ?value` did. */
function compact(body: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(body).filter(([, v]) => v !== undefined && v !== null));
}

const enc = encodeURIComponent;

function request<T>(call: ApiCall): Promise<T> {
  return transport().invoke<T>("api_request", { call });
}

const get = <T>(path: string, extra: Partial<ApiCall> = {}) => request<T>({ method: "GET", path, ...extra });
const post = <T>(path: string, body?: unknown, extra: Partial<ApiCall> = {}) =>
  request<T>({ method: "POST", path, body, ...extra });
const put = <T>(path: string, body?: unknown, extra: Partial<ApiCall> = {}) =>
  request<T>({ method: "PUT", path, body, ...extra });
const patch = <T>(path: string, body?: unknown, extra: Partial<ApiCall> = {}) =>
  request<T>({ method: "PATCH", path, body, ...extra });
const del = <T = null>(path: string, extra: Partial<ApiCall> = {}) => request<T>({ method: "DELETE", path, ...extra });

export interface SubmitTask {
  prompt: string;
  type?: string | null;
  model?: string | null;
  knowledgeQuery?: string | null;
  knowledgeCollection?: string | null;
  provider?: string;
  /** Universal LLM layer route: auto | default | coding | ... */
  route?: string | null;
  llmAccountId?: string | null;
}

export const api = {
  me: () => get<Principal>("/api/v1/me"),

  listNodes: () => get<NodeInfo[]>("/api/v1/nodes"),
  getNode: (id: string) => get<NodeInfo>(`/api/v1/nodes/${enc(id)}`),
  patchNode: (id: string, changes: { role?: string; name?: string }) =>
    patch<NodeInfo>(`/api/v1/nodes/${enc(id)}`, compact(changes)),
  deleteNode: (id: string) => del(`/api/v1/nodes/${enc(id)}`),
  getLlmRecommendations: (id: string) => get<LlmRecommendation[]>(`/api/v1/nodes/${enc(id)}/llm-recommendations`),
  /** The node's runtime pulls multi-GB weights: a very generous timeout. */
  installNodeModel: async (id: string, model: string) => {
    const body = await post<{ models?: string[] }>(`/api/v1/nodes/${enc(id)}/models`, { model }, { timeoutMs: 16 * MINUTE });
    return body?.models ?? [];
  },
  /** Admin: mint a node-role API key; the full key appears only here. */
  createNodeApiKey: (name: string) => post<MintedApiKey>("/api/v1/admin/api-keys", { name, role: "node" }),

  /** Tasks run synchronously across up to three 120 s attempts (Ticket #102). */
  submitTask: (t: SubmitTask) => {
    const viaLlmLayer = !!t.route || !!t.llmAccountId;
    const provider = t.provider ?? "ollama";
    return post<TaskInfo>(
      "/api/v1/tasks",
      compact({
        prompt: t.prompt,
        type: t.type,
        model: t.model,
        knowledge_query: t.knowledgeQuery,
        knowledge_collection: t.knowledgeCollection,
        provider: provider !== "ollama" && !viaLlmLayer ? provider : undefined,
        route: t.route,
        llm_account_id: t.llmAccountId,
      }),
      { timeoutMs: 7 * MINUTE },
    );
  },
  listTasks: () => get<TaskInfo[]>("/api/v1/tasks"),
  providerCatalog: () => get<Record<string, unknown>[]>("/api/v1/providers"),
  phantomCapabilities: () => get<{ enabled?: boolean; models?: string[] }>("/api/v1/phantom/capabilities"),
  /** HTTPS or a loopback controller only, enforced again in Rust. */
  submitPhantom: (prompt: string, model: string) =>
    post<{ output?: string }>(
      "/api/v1/phantom/tasks",
      { prompt, model, provider: "phantom_local" },
      { timeoutMs: 11 * MINUTE, secret: true },
    ),

  listWorkflows: () => get<WorkflowInfo[]>("/api/v1/workflows"),
  createWorkflow: (name: string, description: string | null, definition: Record<string, unknown>) =>
    post<WorkflowInfo>("/api/v1/workflows", compact({ name, description, definition })),
  /** 409 while a run is running or waiting for approval (ADR-033). */
  deleteWorkflow: (id: string) => del(`/api/v1/workflows/${enc(id)}`),
  runWorkflow: (id: string, input: string) => post<WorkflowRun>(`/api/v1/workflows/${enc(id)}/run`, { input }, { timeoutMs: 7 * MINUTE }),
  getRun: (workflowId: string, runId: string) => get<WorkflowRun>(`/api/v1/workflows/${enc(workflowId)}/runs/${enc(runId)}`),
  approveRun: (workflowId: string, runId: string, approved: boolean) =>
    post<WorkflowRun>(`/api/v1/workflows/${enc(workflowId)}/runs/${enc(runId)}/approve`, { approved }, { timeoutMs: 7 * MINUTE }),

  listCollections: () => get<CollectionInfo[]>("/api/v1/knowledge/collections"),
  createCollection: (name: string, description: string | null) =>
    post<CollectionInfo>("/api/v1/knowledge/collections", compact({ name, description })),
  deleteCollection: (id: string) => del(`/api/v1/knowledge/collections/${enc(id)}`),
  listDocuments: (id: string) => get<DocumentInfo[]>(`/api/v1/knowledge/collections/${enc(id)}/documents`),
  /** Opens the OS file picker in Rust; null when the user cancelled. */
  uploadDocument: (collectionId: string) =>
    transport().invoke<DocumentInfo | null>("knowledge_upload", { collectionId }),
  retrieve: async (query: string, collection: string | null, topK = 5) => {
    const body = await post<{ chunks?: RetrievedChunk[] }>("/api/v1/knowledge/retrieve", compact({ query, collection, top_k: topK }));
    return body?.chunks ?? [];
  },

  listAuditLogs: (limit = 50) => get<AuditLogEntry[]>("/api/v1/admin/audit-logs", { query: [["limit", String(limit)]] }),
  listApiKeys: () => get<ApiKeyInfo[]>("/api/v1/admin/api-keys"),
  revokeApiKey: (id: string) => del(`/api/v1/admin/api-keys/${enc(id)}`),
  listLegacyProviders: () => get<Record<string, unknown>[]>("/api/v1/admin/providers"),
  saveLegacyProviderKey: (provider: string, key: string, storage: string) =>
    put(`/api/v1/admin/providers/${enc(provider)}/credential`, { key, storage }, { secret: true }),
  deleteLegacyProviderKey: (provider: string, storage: string) =>
    del(`/api/v1/admin/providers/${enc(provider)}/credential`, { query: [["storage", storage]] }),

  // ---- universal LLM layer (ADR-031) ----
  listLlmProviders: () => get<LlmProvider[]>("/api/v1/llm/providers"),
  listLlmAccounts: () => get<LlmAccount[]>("/api/v1/llm/accounts"),
  connectLlmAccount: (a: { provider: string; label: string; scope?: string; baseUrl?: string | null; apiKey?: string | null; nodeId?: string | null }) =>
    post<LlmAccount>(
      "/api/v1/llm/accounts",
      compact({ provider: a.provider, label: a.label, scope: a.scope ?? "personal", base_url: a.baseUrl, api_key: a.apiKey, node_id: a.nodeId }),
      { timeoutMs: MINUTE, secret: !!a.apiKey },
    ),
  updateLlmAccount: (id: string, changes: { label?: string; status?: string; baseUrl?: string; apiKey?: string }) =>
    patch<LlmAccount>(
      `/api/v1/llm/accounts/${enc(id)}`,
      compact({ label: changes.label, status: changes.status, base_url: changes.baseUrl, api_key: changes.apiKey }),
      { secret: !!changes.apiKey },
    ),
  disconnectLlmAccount: (id: string) => del(`/api/v1/llm/accounts/${enc(id)}`),
  testLlmAccount: (id: string, model?: string) =>
    post<LlmProbeResult>(`/api/v1/llm/accounts/${enc(id)}/test`, compact({ model }), { timeoutMs: 3 * MINUTE }),
  listLlmAccountModels: async (id: string, refresh = false) => {
    const body = await get<{ models: LlmModel[] }>(`/api/v1/llm/accounts/${enc(id)}/models`, {
      query: refresh ? [["refresh", "true"]] : [],
      timeoutMs: 2 * MINUTE,
    });
    return body?.models ?? [];
  },
  getLlmRouting: () => get<LlmRouting>("/api/v1/llm/routing"),
  setLlmRoute: (purpose: string, chain: RouteEntry[], scope = "personal") =>
    put<LlmRoute>(`/api/v1/llm/routing/${enc(purpose)}`, { scope, chain }),
  deleteLlmRoute: (purpose: string, scope = "personal") =>
    del(`/api/v1/llm/routing/${enc(purpose)}`, { query: [["scope", scope]] }),
  llmTestPrompt: (t: { prompt: string; accountId?: string | null; model?: string | null; purpose?: string | null }) =>
    post<LlmAnswer>("/api/v1/llm/test", compact({ prompt: t.prompt, account_id: t.accountId, model: t.model, purpose: t.purpose }), {
      timeoutMs: 7 * MINUTE,
    }),
  /** Each node is asked live whether Claude Code is ready for you (ADR-032). */
  listClaudeSubscriptionNodes: () => get<ClaudeNode[]>("/api/v1/llm/claude-subscription/nodes", { timeoutMs: 45_000 }),
  startOpenRouterSignIn: (callbackUrl: string | null) =>
    post<OpenRouterSignIn>("/api/v1/llm/oauth/openrouter/start", compact({ callback_url: callbackUrl })),
  completeOpenRouterSignIn: (flow: string, code: string, label: string, scope = "personal") =>
    post<LlmAccount>("/api/v1/llm/oauth/openrouter/complete", { flow, code, label, scope }, { timeoutMs: MINUTE, secret: true }),

  /** A streamed chat: normalized server-sent events ending with done or error. */
  chatStream: (
    t: { messages: ChatMessage[]; accountId?: string | null; model?: string | null; purpose?: string | null; system?: string | null },
    streamId: string,
    onEvent: (event: SseEvent) => void,
  ) =>
    transport().stream(
      {
        method: "POST",
        path: "/api/v1/llm/chat",
        body: compact({
          messages: t.messages,
          account_id: t.accountId,
          model: t.model,
          purpose: t.purpose,
          system: t.system,
          stream: true,
        }),
      },
      streamId,
      onEvent,
    ),
  cancelStream: (streamId: string) => transport().invoke<void>("api_stream_cancel", { streamId }),
};

export type Api = typeof api;
