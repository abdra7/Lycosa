// A scripted stand-in for the Rust side: commands answer from handlers,
// `api_request` calls route by "METHOD /path" (":id" segments match
// anything), and tests can emit backend events.

import type { ApiCall, SseEvent, Transport } from "../ipc/transport";

type Handler = (args: Record<string, unknown>) => unknown;
type ApiHandler = (call: ApiCall, params: string[]) => unknown;

export class FakeTransport implements Transport {
  calls: { command: string; args: Record<string, unknown> }[] = [];
  apiCalls: ApiCall[] = [];
  private commands = new Map<string, Handler>();
  private routes: { method: string; pattern: RegExp; handler: ApiHandler }[] = [];
  private listeners = new Map<string, Set<(payload: unknown) => void>>();
  streamHandler: (call: ApiCall, onEvent: (e: SseEvent) => void) => Promise<void> = async () => {};

  on(command: string, handler: Handler | unknown): this {
    this.commands.set(command, typeof handler === "function" ? (handler as Handler) : () => handler);
    return this;
  }

  /** `route("GET /api/v1/nodes/:id", (call, [id]) => ...)` or a value. */
  route(spec: string, handler: ApiHandler | unknown): this {
    const [method, path] = spec.split(" ") as [string, string];
    const pattern = new RegExp(`^${path.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/:[a-z]+/gi, "([^/]+)")}$`);
    this.routes.unshift({ method, pattern, handler: typeof handler === "function" ? (handler as ApiHandler) : () => handler });
    return this;
  }

  async invoke<T>(command: string, args: Record<string, unknown> = {}): Promise<T> {
    this.calls.push({ command, args });
    if (command === "api_request") {
      const call = args["call"] as ApiCall;
      this.apiCalls.push(call);
      for (const r of this.routes) {
        const m = r.method === call.method ? r.pattern.exec(call.path) : null;
        if (m) return (await r.handler(call, m.slice(1))) as T;
      }
      throw { kind: "api", status: 404, code: "not_found", message: `no fake route for ${call.method} ${call.path}`, details: [] };
    }
    const handler = this.commands.get(command);
    if (!handler) return undefined as T;
    return (await handler(args)) as T;
  }

  async listen<T>(event: string, handler: (payload: T) => void): Promise<() => void> {
    const set = this.listeners.get(event) ?? new Set();
    set.add(handler as (payload: unknown) => void);
    this.listeners.set(event, set);
    return () => set.delete(handler as (payload: unknown) => void);
  }

  emit(event: string, payload: unknown): void {
    this.listeners.get(event)?.forEach((h) => h(payload));
  }

  stream(call: ApiCall, streamId: string, onEvent: (e: SseEvent) => void): Promise<void> {
    this.calls.push({ command: "api_stream", args: { call, streamId } });
    return this.streamHandler(call, onEvent);
  }

  called(command: string) {
    return this.calls.filter((c) => c.command === command);
  }

  apiCall(method: string, path: string) {
    return this.apiCalls.filter((c) => c.method === method && c.path === path);
  }
}

export const apiError = (status: number, message: string, code = "error") => ({ kind: "api", status, code, message, details: [] });

export const ADMIN = { type: "user", id: "u1", role: "admin", email: "admin@lycosa.local" };
export const OPERATOR = { type: "user", id: "u2", role: "operator", email: "op@lycosa.local" };

export function session(opts: { principal?: unknown; profiles?: unknown[]; activeId?: string | null; unreachable?: boolean } = {}) {
  const profiles = opts.profiles ?? [{ id: "p1", name: "Home lab", baseUrl: "http://10.0.0.5:8000", kind: "remote", signedIn: true }];
  return {
    session: { profiles, activeId: opts.activeId ?? (profiles[0] as { id: string } | undefined)?.id ?? null, keychainPersistent: true },
    principal: opts.principal === undefined ? ADMIN : opts.principal,
    unreachable: opts.unreachable ?? false,
  };
}

export const STANDALONE_OFF = {
  phase: "off",
  stage: null,
  message: null,
  services: [
    { name: "postgres", label: "PostgreSQL", state: "stopped", pid: null, port: null, restarts: 0, detail: null },
    { name: "qdrant", label: "Qdrant", state: "stopped", pid: null, port: null, restarts: 0, detail: null },
    { name: "backend", label: "Lycosa API", state: "stopped", pid: null, port: null, restarts: 0, detail: null },
    { name: "agent", label: "Local node", state: "stopped", pid: null, port: null, restarts: 0, detail: null },
  ],
  apiUrl: null,
  lanPort: 8000,
  enabled: false,
  exposeOnLan: false,
  localNode: false,
  dataDir: "C:\\Users\\me\\AppData\\Local\\com.lycosa.desktop",
  runtime: { target: "x86_64-pc-windows-msvc", python: "3.12.15", postgres: "16.15.0", qdrant: "1.18.2", backend: "0.5.0" },
  adminEmail: "admin@lycosa.local",
};

/** A transport for a signed-in admin on a remote controller. */
export function signedIn(principal: unknown = ADMIN): FakeTransport {
  return new FakeTransport()
    .on("session_restore", session({ principal }))
    .on("session_state", session({ principal }).session)
    .on("app_info", { version: "0.5.0", os: "windows", standaloneAvailable: true })
    .on("standalone_status", STANDALONE_OFF)
    .route("GET /api/v1/nodes", [])
    .route("GET /api/v1/tasks", [])
    .route("GET /api/v1/llm/accounts", [])
    .route("GET /api/v1/llm/providers", [])
    .route("GET /api/v1/llm/routing", { purposes: ["default"], personal: [], deployment: [] })
    .route("GET /api/v1/providers", []);
}
