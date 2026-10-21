// Controller API shapes, exactly as the JSON arrives (snake_case), plus the
// derived helpers the Flutter models had as getters.

export interface Principal {
  type: "user" | "api_key";
  id: string;
  role: string;
  email?: string | null;
  name?: string | null;
}

export const principalName = (p: Principal) => p.email ?? p.name ?? p.id;

export interface NodeInfo {
  id: string;
  name: string;
  status: string; // registered | online | offline
  role?: string | null;
  recommended_role?: string | null;
  recommendation_confidence?: number | null;
  recommendation_rationale?: string[] | null;
  cpu_cores?: number | null;
  ram_gb?: number | null;
  gpu_count?: number | null;
  gpu_vram_gb?: number | null;
  storage_gb?: number | null;
  os_name?: string | null;
  hardware_profile?: Record<string, unknown> | null;
  last_heartbeat_at?: string | null;
  metrics?: Record<string, unknown> | null;
  agent_url?: string | null;
}

export const NODE_ROLES = ["ai_compute", "hybrid", "knowledge", "tool", "vision", "storage"] as const;

function runtimes(node: NodeInfo): Record<string, unknown>[] {
  const list = node.hardware_profile?.["runtimes"];
  return Array.isArray(list) ? list.filter((r): r is Record<string, unknown> => !!r && typeof r === "object") : [];
}

/** A local model runtime (Ollama) answered when the agent registered. */
export const nodeHasOllama = (node: NodeInfo) => runtimes(node).some((r) => r["name"] === "ollama");

/** The agent runs its owner's Claude Code (ADR-032). */
export function nodeOffersClaude(node: NodeInfo): boolean {
  const extra = node.hardware_profile?.["extra"];
  const caps = extra && typeof extra === "object" ? (extra as Record<string, unknown>)["agent_capabilities"] : undefined;
  return !!caps && typeof caps === "object" && (caps as Record<string, unknown>)["claude_subscription"] === true;
}

export function nodeRuntimesLabel(node: NodeInfo): string {
  const parts = runtimes(node).map((r) => {
    const models = Array.isArray(r["models"]) ? (r["models"] as unknown[]).length : 0;
    return `${String(r["name"])}${models > 0 ? ` (${models} models)` : ""}`;
  });
  if (nodeOffersClaude(node)) parts.push("Claude Code (owner's plan)");
  return parts.length ? parts.join(", ") : "none detected";
}

export interface LlmRecommendation {
  model: string;
  params_b: number;
  use_case: string;
  runnable: boolean;
  runs_on?: string | null;
  recommended: boolean;
  installed: boolean;
  reason: string;
}

export interface MintedApiKey {
  id: string;
  name: string;
  api_key: string;
}

export interface TaskExecution {
  node_id: string;
  attempt: number;
  status: string;
  output?: string | null;
  error?: string | null;
}

export interface TaskInfo {
  id: string;
  type: string;
  status: string;
  payload?: Record<string, unknown> | null;
  result?: Record<string, unknown> | null;
  error?: string | null;
  node_id?: string | null;
  queued_at: string;
  finished_at?: string | null;
  executions?: TaskExecution[] | null;
}

export const taskPrompt = (t: TaskInfo) => (typeof t.payload?.["prompt"] === "string" ? (t.payload["prompt"] as string) : "");
export const taskOutput = (t: TaskInfo) =>
  typeof t.result?.["output"] === "string" ? (t.result["output"] as string) : null;

export interface WorkflowInfo {
  id: string;
  name: string;
  description?: string | null;
  definition: Record<string, unknown>;
}

export function workflowSteps(w: WorkflowInfo): Record<string, unknown>[] {
  const steps = w.definition?.["steps"];
  return Array.isArray(steps) ? steps.filter((s): s is Record<string, unknown> => !!s && typeof s === "object") : [];
}

export interface StepRun {
  step_id: string;
  kind: string;
  status: string;
  attempt: number;
  output?: string | null;
  error?: string | null;
}

export interface WorkflowRun {
  id: string;
  workflow_id: string;
  status: string; // running | paused | succeeded | failed
  input: string;
  current_step?: string | null;
  error?: string | null;
  step_runs?: StepRun[] | null;
}

export const runFinished = (r: WorkflowRun) => r.status === "succeeded" || r.status === "failed";

export interface CollectionInfo {
  id: string;
  name: string;
  description?: string | null;
  embedding_backend: string;
}

export interface DocumentInfo {
  id: string;
  filename: string;
  status: string; // uploaded | embedded | failed
  chunk_count: number;
  size_bytes: number;
  error?: string | null;
}

export interface RetrievedChunk {
  text: string;
  source: string;
  collection: string;
  score: number;
}

export interface AuditLogEntry {
  created_at: string;
  action: string;
  resource_type?: string | null;
  resource_id?: string | null;
  actor_user_id?: string | null;
  actor_api_key_id?: string | null;
  actor_email?: string | null;
  actor_api_key_name?: string | null;
  ip_address?: string | null;
}

/** ADR-033; older controllers send ids only. */
export function auditActor(e: AuditLogEntry): string {
  if (e.actor_email) return e.actor_email;
  if (e.actor_api_key_name) return `${e.actor_api_key_name} (API key)`;
  if (e.actor_user_id) return "user";
  if (e.actor_api_key_id) return "api key";
  return "—";
}

export interface ApiKeyInfo {
  id: string;
  name: string;
  key_prefix: string;
  node_id?: string | null;
  last_used_at?: string | null;
  revoked_at?: string | null;
}

// ---- Universal LLM layer (ADR-031) -----------------------------------------

export type Capabilities = Record<string, boolean | null>;

export const CAPABILITY_LABELS: Record<string, string> = {
  streaming: "Streaming",
  tool_calling: "Tools",
  parallel_tools: "Parallel tools",
  structured_output: "JSON schema",
  json_mode: "JSON mode",
  vision: "Vision",
  reasoning: "Reasoning",
};

export interface LlmProvider {
  id: string;
  display_name: string;
  kind: string; // cloud | aggregator | local | compatible | node
  auth_methods: string[];
  credential_required: boolean;
  base_url_mode: string; // fixed | official_choice | user
  default_base_url: string;
  official_base_urls?: string[] | null;
  capabilities?: Capabilities | null;
  docs_url: string;
  subscription_note?: string | null;
  notes?: string[] | null;
}

export const providerIsNodeRun = (p: LlmProvider) => p.kind === "node";
export const providerIsLocal = (p: LlmProvider) => p.kind === "local";
export const providerSupportsOAuth = (p: LlmProvider) => p.auth_methods.includes("oauth_pkce");
export const providerTakesBaseUrl = (p: LlmProvider) => p.base_url_mode !== "fixed" && !providerIsNodeRun(p);

export interface LlmAccount {
  id: string;
  provider: string;
  provider_name: string;
  label: string;
  scope: string; // personal | deployment
  mine: boolean;
  usable: boolean;
  manageable: boolean;
  auth_method: string;
  base_url: string;
  is_local: boolean;
  status: string; // active | disabled
  api_access: string; // available | unavailable | unknown
  credential_configured: boolean;
  last_test_status?: string | null;
  last_test_detail?: string | null;
  last_tested_at?: string | null;
  node_id?: string | null;
  node_name?: string | null;
}

export const accountIsNodeRun = (a: LlmAccount) => a.auth_method === "node_sign_in";
export const accountIsActive = (a: LlmAccount) => a.status === "active";
export const accountIsShared = (a: LlmAccount) => a.scope === "deployment";

export interface ClaudeNode {
  node_id: string;
  node_name: string;
  node_status?: string;
  reachable?: boolean;
  installed?: boolean;
  signed_in?: boolean;
  auth_method?: string;
  owner_matches?: boolean;
  detail?: string;
  version?: string | null;
  plan?: string | null;
  plan_usage?: Record<string, { utilization?: number | null; resets_at?: number | null }> | null;
}

export const claudeNodeReady = (n: ClaudeNode) => !!(n.reachable && n.installed && n.signed_in && n.owner_matches);

export function planWindowLabel(name: string): string {
  if (name === "five_hour") return "5-hour";
  if (name === "seven_day") return "7-day";
  return name.replaceAll("_", " ");
}

export interface LlmProbeResult {
  status: string; // ready | offline | unauthorized | error | unknown
  detail?: string;
  api_access: string;
  latency_ms?: number | null;
  models_available?: number | null;
  subscription_note?: string | null;
}

export interface LlmModel {
  ref: string;
  id: string;
  provider: string;
  account_id: string;
  account_label: string;
  capabilities?: Capabilities | null;
  display_name?: string | null;
  context_window?: number | null;
  max_output_tokens?: number | null;
}

export interface RouteEntry {
  account_id: string;
  model: string;
}

export interface LlmRoute {
  purpose: string;
  scope: string;
  chain: RouteEntry[];
}

export interface LlmRouting {
  purposes: string[];
  personal: LlmRoute[];
  deployment: LlmRoute[];
}

export function routeFor(routing: LlmRouting | null | undefined, purpose: string, scope: string): LlmRoute | undefined {
  if (!routing) return undefined;
  return (scope === "deployment" ? routing.deployment : routing.personal).find((r) => r.purpose === purpose);
}

export interface LlmAnswer {
  content: string;
  provider: string;
  model: string;
  finish_reason: string;
  latency_ms: number;
  fallback_index: number;
  usage?: { total_tokens?: number | null } | null;
  estimated_cost?: number | null;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface OpenRouterSignIn {
  authorization_url: string;
  flow: string;
}

export interface LycosaEvent {
  type: string;
  ts: string;
  data: Record<string, unknown>;
}

export function eventSummary(e: LycosaEvent): string {
  const d = e.data ?? {};
  const s = (k: string) => String(d[k] ?? "");
  switch (e.type) {
    case "node.connected":
      return `Node ${s("name")} connected`;
    case "node.disconnected":
      return `Node ${s("name")} disconnected`;
    case "node.metrics.updated":
      return `Metrics from ${s("name")}`;
    case "task.started":
      return `Task started (${s("type")})`;
    case "task.finished":
      return `Task ${s("status")}`;
    case "workflow.started":
      return `Workflow ${s("name")} started`;
    case "workflow.step.completed":
      return `Step ${s("step")} completed`;
    case "workflow.paused":
      return `Workflow paused at ${s("step")}`;
    case "workflow.finished":
      return `Workflow ${s("status")}`;
    case "alert.created":
      return typeof d["message"] === "string" ? (d["message"] as string) : "Alert";
    default:
      return e.type;
  }
}
