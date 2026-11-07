import { transport, type ApiCall } from "../ipc/transport";
import type { TaskInfo } from "./types";

export interface CliSettings {
  provider: string;
  route: string | null;
  llm_account_id: string | null;
  model: string | null;
  type: string | null;
  knowledge_query: string | null;
  knowledge_collection: string | null;
}

export interface CliWorkspace { id: string; name: string; created_at: string }
export interface CliSession {
  id: string;
  workspace_id: string;
  name: string;
  archived: boolean;
  node_id: string | null;
  settings: CliSettings;
  tasks: TaskInfo[];
  created_at: string;
}

export const DEFAULT_CLI_SETTINGS: CliSettings = {
  provider: "ollama", route: null, llm_account_id: null, model: null,
  type: null, knowledge_query: null, knowledge_collection: null,
};

const enc = encodeURIComponent;
const request = <T>(method: ApiCall["method"], path: string, body?: unknown, timeoutMs?: number) =>
  transport().invoke<T>("api_request", { call: { method, path: `/api/v1/cli${path}`, body, timeoutMs } });

export const cliApi = {
  workspaces: () => request<CliWorkspace[]>("GET", "/workspaces"),
  createWorkspace: (name: string) => request<CliWorkspace>("POST", "/workspaces", { name }),
  sessions: (workspace: string) => request<CliSession[]>("GET", `/workspaces/${enc(workspace)}/sessions`),
  createSession: (workspace: string, name: string, settings: CliSettings) =>
    request<CliSession>("POST", `/workspaces/${enc(workspace)}/sessions`, { name, settings }),
  updateSession: (id: string, changes: Partial<Pick<CliSession, "name" | "archived" | "node_id" | "settings">>) =>
    request<CliSession>("PATCH", `/sessions/${enc(id)}`, changes),
  deleteSession: (id: string) => request<null>("DELETE", `/sessions/${enc(id)}`),
  send: (id: string, prompt: string) => request<TaskInfo>("POST", `/sessions/${enc(id)}/tasks`, { prompt }, 7 * 60_000),
};

export const taskIsRunning = (task: TaskInfo) => ["queued", "assigned", "running"].includes(task.status);
export const sessionIsRunning = (session: CliSession) => session.tasks.some(taskIsRunning);

/** Usage comes from the provider; model size is never presented as token usage. */
export function sessionTokens(session: CliSession): number | null {
  const totals = session.tasks.flatMap((task) => {
    const usage = task.result?.["usage"];
    const total = usage && typeof usage === "object" ? (usage as Record<string, unknown>)["total_tokens"] : undefined;
    return typeof total === "number" && Number.isFinite(total) ? [total] : [];
  });
  return totals.length ? totals.reduce((sum, n) => sum + n, 0) : null;
}
