import { beforeEach, describe, expect, it } from "vitest";

import { setTransport } from "../ipc/transport";
import { FakeTransport, apiError } from "../test/fakeTransport";
import { api } from "./client";
import { friendly, isApiError, isUnreachable } from "./errors";
import { chunksLabel, heartbeatAge, humanize, searchKind, statusColor } from "./format";
import { auditActor, eventSummary, nodeHasOllama, nodeOffersClaude, nodeRuntimesLabel, routeFor, type NodeInfo } from "./types";

describe("errors", () => {
  it("renders the envelope with field details, like ApiException.friendly", () => {
    const e = { kind: "api", status: 422, code: "validation_error", message: "Invalid input", details: [{ field: "email", message: "required" }] };
    expect(friendly(e)).toBe("Invalid input (email: required)");
    expect(isApiError(e, 422)).toBe(true);
    expect(isApiError(e, 401)).toBe(false);
  });

  it("names the controller that could not be reached", () => {
    const e = { kind: "unreachable", base_url: "http://10.0.0.5:8000", message: "the connection failed" };
    expect(friendly(e)).toBe("Could not reach controller at http://10.0.0.5:8000");
    expect(isUnreachable(e)).toBe(true);
  });

  it("falls back for unknown errors", () => {
    expect(friendly(new Error("boom"))).toBe("boom");
    expect(friendly(42)).toBe("Request failed");
    expect(friendly({ kind: "insecure", message: "use https" })).toBe("use https");
  });
});

describe("format", () => {
  it("maps statuses to one color each", () => {
    expect(statusColor("online")).toBe("var(--success)");
    expect(statusColor("embedded")).toBe("var(--success)");
    expect(statusColor("failed")).toBe("var(--error)");
    expect(statusColor("skipped")).toBe("var(--text-2)");
    expect(statusColor("pending_approval")).toBe("var(--warning)");
  });

  it("formats heartbeat ages", () => {
    const now = Date.parse("2026-10-20T12:00:00Z");
    expect(heartbeatAge(null, now)).toBe("—");
    expect(heartbeatAge("2026-10-20T11:59:30Z", now)).toBe("30s ago");
    expect(heartbeatAge("2026-10-20T11:15:00Z", now)).toBe("45m ago");
    expect(heartbeatAge("2026-10-20T07:00:00Z", now)).toBe("5h ago");
    expect(heartbeatAge("2026-10-17T12:00:00Z", now)).toBe("3d ago");
  });

  it("words API identifiers and embedders", () => {
    expect(humanize("pending_approval")).toBe("pending approval");
    expect(searchKind("hashing")).toBe("keyword search");
    expect(searchKind("fastembed")).toBe("semantic search");
    expect(searchKind("other")).toBe("other search");
    expect(chunksLabel(1)).toBe("1 chunk");
    expect(chunksLabel(3)).toBe("3 chunks");
  });
});

describe("model helpers", () => {
  const node = (profile: Record<string, unknown>): NodeInfo => ({ id: "n", name: "n", status: "online", hardware_profile: profile });

  it("reads runtimes and the Claude capability from the hardware profile", () => {
    const n = node({ runtimes: [{ name: "ollama", models: ["a", "b"] }], extra: { agent_capabilities: { claude_subscription: true } } });
    expect(nodeHasOllama(n)).toBe(true);
    expect(nodeOffersClaude(n)).toBe(true);
    expect(nodeRuntimesLabel(n)).toBe("ollama (2 models), Claude Code (owner's plan)");
    expect(nodeRuntimesLabel(node({}))).toBe("none detected");
  });

  it("names audit actors, falling back for older controllers", () => {
    expect(auditActor({ created_at: "", action: "x", actor_email: "a@b" })).toBe("a@b");
    expect(auditActor({ created_at: "", action: "x", actor_api_key_name: "ci" })).toBe("ci (API key)");
    expect(auditActor({ created_at: "", action: "x", actor_user_id: "u" })).toBe("user");
    expect(auditActor({ created_at: "", action: "x", actor_api_key_id: "k" })).toBe("api key");
    expect(auditActor({ created_at: "", action: "x" })).toBe("—");
  });

  it("summarizes events", () => {
    expect(eventSummary({ type: "node.connected", ts: "", data: { name: "gpu" } })).toBe("Node gpu connected");
    expect(eventSummary({ type: "workflow.paused", ts: "", data: { step: "gate" } })).toBe("Workflow paused at gate");
    expect(eventSummary({ type: "alert.created", ts: "", data: { message: "Node down" } })).toBe("Node down");
    expect(eventSummary({ type: "x.y", ts: "", data: {} })).toBe("x.y");
  });

  it("finds a route by purpose and scope", () => {
    const routing = { purposes: ["default"], personal: [{ purpose: "default", scope: "personal", chain: [] }], deployment: [] };
    expect(routeFor(routing, "default", "personal")).toBeDefined();
    expect(routeFor(routing, "default", "deployment")).toBeUndefined();
  });
});

describe("api client requests", () => {
  let fake: FakeTransport;
  beforeEach(() => {
    fake = new FakeTransport();
    setTransport(fake);
  });

  it("submits a node task without the default provider or empty fields", async () => {
    fake.route("POST /api/v1/tasks", { id: "t" });
    await api.submitTask({ prompt: "hi", provider: "ollama", type: null, knowledgeQuery: null });
    const call = fake.apiCall("POST", "/api/v1/tasks")[0]!;
    expect(call.body).toEqual({ prompt: "hi" });
    expect(call.timeoutMs).toBe(7 * 60_000);
  });

  it("keeps a legacy provider, but drops it on the LLM layer", async () => {
    fake.route("POST /api/v1/tasks", { id: "t" });
    await api.submitTask({ prompt: "a", provider: "openrouter", model: "m" });
    await api.submitTask({ prompt: "b", provider: "openrouter", route: "default" });
    const [legacy, layered] = fake.apiCall("POST", "/api/v1/tasks");
    expect(legacy!.body).toEqual({ prompt: "a", provider: "openrouter", model: "m" });
    expect(layered!.body).toEqual({ prompt: "b", route: "default" });
  });

  it("marks credential-bearing calls secret", async () => {
    fake.route("POST /api/v1/llm/accounts", { id: "a" });
    fake.route("PUT /api/v1/admin/providers/:p/credential", null);
    await api.connectLlmAccount({ provider: "openai", label: "Work", apiKey: "sk-1" });
    await api.connectLlmAccount({ provider: "ollama", label: "Local", baseUrl: "http://10.0.0.2:11434" });
    await api.saveLegacyProviderKey("openai", "sk-2", "session");
    const [withKey, keyless] = fake.apiCall("POST", "/api/v1/llm/accounts");
    expect(withKey!.secret).toBe(true);
    expect(withKey!.body).toMatchObject({ api_key: "sk-1", scope: "personal" });
    expect(keyless!.secret).toBe(false);
    expect(keyless!.body).not.toHaveProperty("api_key");
    expect(fake.apiCall("PUT", "/api/v1/admin/providers/openai/credential")[0]!.secret).toBe(true);
  });

  it("gives model pulls a long timeout and encodes path parts", async () => {
    fake.route("POST /api/v1/nodes/:id/models", { models: ["llama3.2:1b"] });
    expect(await api.installNodeModel("a/b", "llama3.2:1b")).toEqual(["llama3.2:1b"]);
    const call = fake.apiCalls[0]!;
    expect(call.path).toBe("/api/v1/nodes/a%2Fb/models");
    expect(call.timeoutMs).toBe(16 * 60_000);
  });

  it("passes query parameters separately", async () => {
    fake.route("GET /api/v1/admin/audit-logs", []);
    fake.route("DELETE /api/v1/llm/routing/:p", null);
    await api.listAuditLogs(100);
    await api.deleteLlmRoute("coding", "deployment");
    expect(fake.apiCalls[0]!.query).toEqual([["limit", "100"]]);
    expect(fake.apiCalls[1]!.query).toEqual([["scope", "deployment"]]);
  });

  it("returns the controller error object as thrown", async () => {
    fake.route("DELETE /api/v1/workflows/:id", () => {
      throw apiError(409, "Approve or reject the paused run first");
    });
    await expect(api.deleteWorkflow("w")).rejects.toMatchObject({ kind: "api", status: 409 });
  });
});
