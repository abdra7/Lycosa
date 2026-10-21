import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { OPERATOR, STANDALONE_OFF, apiError, session, signedIn } from "./fakeTransport";
import { renderApp } from "./render";

const NODE = {
  id: "n1",
  name: "gpu-box",
  status: "online",
  role: null,
  recommended_role: "ai_compute",
  recommendation_confidence: 0.82,
  recommendation_rationale: ["has a GPU"],
  cpu_cores: 16,
  ram_gb: 64,
  storage_gb: 1000,
  os_name: "Windows 11",
  hardware_profile: { cpu_model: "Ryzen 9", gpus: [{ model: "RTX 4090", vram_gb: 24 }], runtimes: [{ name: "ollama", models: ["llama3.2:1b"] }] },
  last_heartbeat_at: new Date().toISOString(),
  metrics: { cpu_percent: 12.4, ram_percent: 40, disk_percent: 70, running_tasks: 1, gpus: [{ index: 0, name: "RTX 4090", utilization_percent: 55, memory_percent: 30, memory_used_mb: 7372, memory_total_mb: 24576, temperature_c: 61 }] },
  agent_url: "http://10.0.0.7:8010",
};

const go = async (section: string) => {
  await userEvent.setup().click(await screen.findByRole("button", { name: section }));
};

describe("nodes", () => {
  it("lists nodes and opens a node's details", async () => {
    const fake = signedIn()
      .route("GET /api/v1/nodes", [NODE])
      .route("GET /api/v1/nodes/:id", NODE)
      .route("GET /api/v1/nodes/:id/llm-recommendations", [
        { model: "llama3.1:8b", params_b: 8, use_case: "general", runnable: true, runs_on: "gpu", recommended: true, installed: false, reason: "fits in VRAM" },
      ]);
    renderApp(fake);
    const row = (await screen.findByText("gpu-box")).closest("tr")!;
    expect(within(row).getByText("ai_compute (82%)")).toBeInTheDocument();
    expect(within(row).getByText("12%")).toBeInTheDocument();
    await userEvent.setup().click(row);
    expect(await screen.findByText("Hardware profile")).toBeInTheDocument();
    expect(screen.getByText("Ryzen 9 (16 cores)")).toBeInTheDocument();
    expect(screen.getByText("RTX 4090 (24 GB)")).toBeInTheDocument();
    expect(screen.getByText("ollama (1 models)")).toBeInTheDocument();
    expect(screen.getByText("GPU 0: RTX 4090")).toBeInTheDocument();
    expect(await screen.findByText(/llama3.1:8b · general · runs on gpu · best fit/)).toBeInTheDocument();
  });

  it("sets a role and installs a model", async () => {
    const fake = signedIn()
      .route("GET /api/v1/nodes", [NODE])
      .route("GET /api/v1/nodes/:id", NODE)
      .route("PATCH /api/v1/nodes/:id", { ...NODE, role: "ai_compute" })
      .route("POST /api/v1/nodes/:id/models", { models: ["llama3.1:8b"] })
      .route("GET /api/v1/nodes/:id/llm-recommendations", [
        { model: "llama3.1:8b", params_b: 8, use_case: "general", runnable: true, runs_on: "gpu", recommended: true, installed: false, reason: "fits" },
      ]);
    renderApp(fake);
    const user = userEvent.setup();
    await user.click(await screen.findByText("gpu-box"));
    await user.click(await screen.findByRole("button", { name: "Save" }));
    await waitFor(() => expect(fake.apiCall("PATCH", "/api/v1/nodes/n1")[0]!.body).toEqual({ role: "ai_compute" }));
    expect(await screen.findByText("Role set to ai_compute")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Install" }));
    expect(await screen.findByText("llama3.1:8b installed on gpu-box")).toBeInTheDocument();
  });

  it("deletes a node after confirmation (admin only)", async () => {
    const fake = signedIn()
      .route("GET /api/v1/nodes", [NODE])
      .route("GET /api/v1/nodes/:id", NODE)
      .route("GET /api/v1/nodes/:id/llm-recommendations", [])
      .route("DELETE /api/v1/nodes/:id", null);
    renderApp(fake);
    const user = userEvent.setup();
    await user.click(await screen.findByText("gpu-box"));
    await user.click(await screen.findByRole("button", { name: "Delete node" }));
    expect(screen.getByRole("dialog", { name: "Delete gpu-box?" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("gpu-box deleted")).toBeInTheDocument();
    expect(fake.apiCall("DELETE", "/api/v1/nodes/n1")).toHaveLength(1);
  });

  it("operators get no add or delete controls", async () => {
    const fake = signedIn(OPERATOR).route("GET /api/v1/nodes", [NODE]);
    renderApp(fake);
    await screen.findByText("gpu-box");
    expect(screen.queryByRole("button", { name: "Add node" })).not.toBeInTheDocument();
  });

  it("scans the LAN and flags unregistered agents", async () => {
    const fake = signedIn()
      .route("GET /api/v1/nodes", [NODE])
      .on("lan_scan", [
        { name: "gpu-box", address: "10.0.0.7", port: 8010, version: "0.5.0" },
        { name: "laptop", address: "10.0.0.9", port: 8010, version: null },
      ]);
    renderApp(fake);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Scan" }));
    expect(await screen.findByText("registered — click for details & model setup")).toBeInTheDocument();
    expect(screen.getByText("not registered — click to add a node key")).toBeInTheDocument();
    expect(screen.getByText("10.0.0.7:8010 · v0.5.0")).toBeInTheDocument();
  });

  it("explains a failed scan", async () => {
    const fake = signedIn().on("lan_scan", () => {
      throw { kind: "internal", message: "LAN scan failed: no interfaces" };
    });
    renderApp(fake);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Scan" }));
    expect(await screen.findByText(/no interfaces — mDNS needs UDP 5353/)).toBeInTheDocument();
  });

  it("mints a node key once and shows the command with the LAN IP for a localhost controller", async () => {
    const local = [{ id: "p1", name: "Docker here", baseUrl: "http://localhost:8000", kind: "remote", signedIn: true }];
    const fake = signedIn()
      .on("session_restore", session({ profiles: local }))
      .on("lan_ipv4", "192.168.1.20")
      .route("POST /api/v1/admin/api-keys", { id: "k", name: "garage", api_key: "lyc_abcd1234_SECRET" });
    renderApp(fake);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add node" }));
    await user.type(screen.getByLabelText("Key name"), "garage");
    await user.click(screen.getByRole("button", { name: "Create key" }));
    expect(await screen.findByText("Copy this key now — it will not be shown again.")).toBeInTheDocument();
    expect(fake.apiCall("POST", "/api/v1/admin/api-keys")[0]!.body).toEqual({ name: "garage", role: "node" });
    expect(screen.getByText(/\$env:LYCOSA_CONTROLLER_URL = "http:\/\/192\.168\.1\.20:8000"/)).toBeInTheDocument();
    expect(screen.getByText(/Using this controller's LAN IP \(192\.168\.1\.20\)/)).toBeInTheDocument();
  });
});

describe("tasks", () => {
  it("preselects the default route and submits through it", async () => {
    const fake = signedIn()
      .route("GET /api/v1/llm/routing", { purposes: ["default"], personal: [{ purpose: "default", scope: "personal", chain: [{ account_id: "a1", model: "m" }] }], deployment: [] })
      .route("POST /api/v1/tasks", { id: "t1", type: "general", status: "succeeded", queued_at: "", payload: { prompt: "hi" }, result: { output: "Hello there" } });
    renderApp(fake);
    await go("Tasks");
    await waitFor(() => expect(screen.getByLabelText("Route")).toHaveValue("default"));
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Prompt"), "hi");
    await user.click(screen.getByRole("button", { name: "Run task" }));
    expect(await screen.findByText("Hello there")).toBeInTheDocument();
    expect(fake.apiCall("POST", "/api/v1/tasks")[0]!.body).toEqual({ prompt: "hi", route: "default" });
  });

  it("an explicit API account needs a model; a Claude plan defaults it", async () => {
    const accounts = [
      { id: "a1", provider: "openai", provider_name: "OpenAI", label: "Work", scope: "personal", mine: true, usable: true, manageable: true, auth_method: "api_key", base_url: "", is_local: false, status: "active", api_access: "available", credential_configured: true },
      { id: "c1", provider: "claude_subscription", provider_name: "Claude subscription", label: "My Claude", scope: "personal", mine: true, usable: true, manageable: true, auth_method: "node_sign_in", base_url: "", is_local: false, status: "active", api_access: "available", credential_configured: false, node_name: "desk" },
    ];
    const fake = signedIn().route("GET /api/v1/llm/accounts", accounts).route("POST /api/v1/tasks", { id: "t", type: "general", status: "succeeded", queued_at: "" });
    renderApp(fake);
    await go("Tasks");
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("Route"), "account:a1");
    await user.type(screen.getByLabelText("Prompt"), "hi");
    await user.click(screen.getByRole("button", { name: "Run task" }));
    expect(await screen.findByText("Enter the model to use with Work.")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Route"), "account:c1");
    expect(screen.getByText(/Runs Claude Code on desk/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run task" }));
    await waitFor(() => expect(fake.apiCall("POST", "/api/v1/tasks")[0]!.body).toEqual({ prompt: "hi", llm_account_id: "c1", model: "default" }));
  });

  it("keeps the task running message when the controller drops the connection", async () => {
    const fake = signedIn().route("POST /api/v1/tasks", () => {
      throw { kind: "unreachable", base_url: "http://10.0.0.5:8000", message: "the request timed out" };
    });
    renderApp(fake);
    await go("Tasks");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Prompt"), "long job");
    await user.click(screen.getByRole("button", { name: "Run task" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("the task may still be running; watch Recent below.");
  });

  it("lists recent tasks with their execution trace", async () => {
    const fake = signedIn().route("GET /api/v1/tasks", [
      { id: "t", type: "coding", status: "failed", queued_at: "", payload: { prompt: "fix it" }, error: "all nodes failed", executions: [{ node_id: "n1", attempt: 1, status: "failed", error: "timeout" }] },
    ]);
    renderApp(fake);
    await go("Tasks");
    expect(await screen.findByText("fix it")).toBeInTheDocument();
    expect(screen.getByText("failed · coding · 1 attempt(s)")).toBeInTheDocument();
    expect(screen.getByText("attempt 1: failed — timeout")).toBeInTheDocument();
  });

  it("phantom runs show once and cannot run without a configured model", async () => {
    const fake = signedIn().route("GET /api/v1/phantom/capabilities", { enabled: false, models: [] });
    renderApp(fake);
    await go("Tasks");
    await userEvent.setup().click(await screen.findByRole("button", { name: "Phantom task (no history)" }));
    expect(await screen.findByText("Ask the controller administrator to configure an isolated local model.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run Phantom" })).toBeDisabled();
  });
});

describe("workflows", () => {
  const WF = { id: "w1", name: "Plan and do", description: null, definition: { steps: [{ id: "plan", kind: "task" }, { id: "gate", kind: "approval" }] } };

  it("refuses invalid JSON before calling the controller", async () => {
    const fake = signedIn().route("GET /api/v1/workflows", []);
    renderApp(fake);
    await go("Workflows");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "New workflow" }));
    const definition = screen.getByLabelText("Definition (JSON)");
    await user.clear(definition);
    await user.type(definition, "{{not json");
    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText(/Definition is not valid JSON/)).toBeInTheDocument();
    expect(fake.apiCall("POST", "/api/v1/workflows")).toHaveLength(0);
  });

  it("shows the controller's reason when a delete is refused", async () => {
    const fake = signedIn()
      .route("GET /api/v1/workflows", [WF])
      .route("DELETE /api/v1/workflows/:id", () => {
        throw apiError(409, "A run is waiting for approval; approve or reject it first");
      });
    renderApp(fake);
    await go("Workflows");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Delete workflow" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("approve or reject it first");
  });

  it("views a definition", async () => {
    renderApp(signedIn().route("GET /api/v1/workflows", [WF]));
    await go("Workflows");
    await userEvent.setup().click(await screen.findByRole("button", { name: "View" }));
    const dialog = screen.getByRole("dialog", { name: "Plan and do" });
    expect(within(dialog).getByText("2. gate · approval")).toBeInTheDocument();
  });

  it("runs, then approves a paused run", async () => {
    const paused = { id: "r1", workflow_id: "w1", status: "paused", input: "ship it", current_step: "gate", step_runs: [{ step_id: "plan", kind: "task", status: "succeeded", attempt: 1, output: "the plan" }, { step_id: "gate", kind: "approval", status: "pending_approval", attempt: 1 }] };
    const fake = signedIn()
      .route("GET /api/v1/workflows", [WF])
      .route("POST /api/v1/workflows/:id/run", paused)
      .route("GET /api/v1/workflows/:id/runs/:run", paused)
      .route("POST /api/v1/workflows/:id/runs/:run/approve", { ...paused, status: "succeeded" });
    renderApp(fake);
    await go("Workflows");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Run" }));
    await user.type(screen.getByLabelText("Input"), "ship it");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Run" }));
    expect(await screen.findByText("Status: paused (at gate)")).toBeInTheDocument();
    expect(screen.getByText("task · succeeded")).toBeInTheDocument();
    fake.route("GET /api/v1/workflows/:id/runs/:run", { ...paused, status: "succeeded", current_step: null });
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(fake.apiCall("POST", "/api/v1/workflows/w1/runs/r1/approve")[0]!.body).toEqual({ approved: true }));
    expect(await screen.findByText("Status: succeeded")).toBeInTheDocument();
  });
});

describe("knowledge", () => {
  it("creates a collection, uploads through the picker and retrieves", async () => {
    const fake = signedIn()
      .route("GET /api/v1/knowledge/collections", [{ id: "c1", name: "docs", description: "Manuals", embedding_backend: "hashing" }])
      .route("POST /api/v1/knowledge/collections", { id: "c2", name: "new", embedding_backend: "hashing" })
      .route("GET /api/v1/knowledge/collections/:id/documents", [{ id: "d1", filename: "guide.pdf", status: "embedded", chunk_count: 4, size_bytes: 2048 }])
      .on("knowledge_upload", { id: "d2", filename: "notes.txt", status: "embedded", chunk_count: 1, size_bytes: 10 })
      .route("POST /api/v1/knowledge/retrieve", { chunks: [{ text: "Reset the router", source: "guide.pdf", collection: "docs", score: 0.91 }] });
    renderApp(fake);
    await go("Knowledge");
    expect(await screen.findByText("Manuals · keyword search")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByText("docs"));
    expect(await screen.findByText("4 chunks · 2.0 KB")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Upload document" }));
    expect(await screen.findByText("notes.txt: embedded as 1 chunk")).toBeInTheDocument();
    expect(fake.called("knowledge_upload")[0]!.args).toEqual({ collectionId: "c1" });
    await user.type(screen.getByLabelText("Query"), "router");
    await user.click(screen.getByRole("button", { name: "Retrieve" }));
    expect(await screen.findByText("Reset the router")).toBeInTheDocument();
    expect(fake.apiCall("POST", "/api/v1/knowledge/retrieve")[0]!.body).toEqual({ query: "router", collection: "docs", top_k: 5 });
    await user.click(screen.getByRole("button", { name: "New collection" }));
    await user.type(screen.getByLabelText("Name"), "new");
    await user.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(fake.apiCall("POST", "/api/v1/knowledge/collections")[0]!.body).toEqual({ name: "new" }));
  });
});

describe("providers", () => {
  const OPENAI = { id: "openai", display_name: "OpenAI", kind: "cloud", auth_methods: ["api_key"], credential_required: true, base_url_mode: "fixed", default_base_url: "https://api.openai.com/v1", capabilities: { streaming: true, vision: null, tool_calling: false }, docs_url: "" };
  const CLAUDE = { id: "claude_subscription", display_name: "Claude subscription", kind: "node", auth_methods: ["node_sign_in"], credential_required: false, base_url_mode: "fixed", default_base_url: "", capabilities: { streaming: true }, docs_url: "", notes: ["Runs Claude Code on your node"] };
  const ACCOUNT = { id: "a1", provider: "openai", provider_name: "OpenAI", label: "Work", scope: "personal", mine: true, usable: true, manageable: true, auth_method: "api_key", base_url: "https://api.openai.com/v1", is_local: false, status: "active", api_access: "unknown", credential_configured: true };

  it("lists providers with capability chips and tests an account", async () => {
    const fake = signedIn()
      .route("GET /api/v1/llm/providers", [OPENAI])
      .route("GET /api/v1/llm/accounts", [ACCOUNT])
      .route("POST /api/v1/llm/accounts/:id/test", { status: "unauthorized", api_access: "unavailable", detail: "key refused", subscription_note: "ChatGPT Plus is not API access" });
    renderApp(fake);
    await go("Providers");
    expect(await screen.findByText("✓ Streaming")).toBeInTheDocument();
    expect(screen.getByText("? Vision")).toBeInTheDocument();
    expect(screen.getByText("✗ Tools")).toBeInTheDocument();
    expect(screen.getByText(/Personal · API access: not tested yet/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Test" }));
    const dialog = await screen.findByRole("dialog", { name: "Test · Work" });
    expect(dialog).toHaveTextContent("API access is not available through this subscription.");
    expect(dialog).toHaveTextContent("ChatGPT Plus is not API access");
  });

  it("connects an account with a key sent as a secret", async () => {
    const fake = signedIn().route("GET /api/v1/llm/providers", [OPENAI]).route("POST /api/v1/llm/accounts", ACCOUNT);
    renderApp(fake);
    await go("Providers");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Connect" }));
    const dialog = screen.getByRole("dialog", { name: "Connect OpenAI" });
    await user.click(within(dialog).getByRole("button", { name: "Connect" }));
    expect(within(dialog).getByText("OpenAI needs an API key.")).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("API key"), "sk-test");
    await user.click(within(dialog).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(fake.apiCall("POST", "/api/v1/llm/accounts")).toHaveLength(1));
    const call = fake.apiCall("POST", "/api/v1/llm/accounts")[0]!;
    expect(call.secret).toBe(true);
    expect(call.body).toEqual({ provider: "openai", label: "Personal", scope: "personal", api_key: "sk-test" });
  });

  it("connects a Claude plan only through a ready node", async () => {
    const fake = signedIn()
      .route("GET /api/v1/llm/providers", [CLAUDE])
      .route("GET /api/v1/llm/claude-subscription/nodes", [
        { node_id: "n1", node_name: "desk", reachable: true, installed: true, signed_in: false, owner_matches: true, detail: "" },
      ])
      .route("POST /api/v1/llm/accounts", { ...ACCOUNT, id: "c1" });
    renderApp(fake);
    await go("Providers");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Connect" }));
    const dialog = await screen.findByRole("dialog", { name: "Connect your Claude plan" });
    expect(await within(dialog).findByText("Sign in on desk")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Connect" })).toBeDisabled();
    fake.route("GET /api/v1/llm/claude-subscription/nodes", [
      { node_id: "n1", node_name: "desk", reachable: true, installed: true, signed_in: true, owner_matches: true, plan: "pro", plan_usage: { five_hour: { utilization: 0.25 } }, version: "2.1.300" },
    ]);
    await user.click(within(dialog).getByRole("button", { name: "Re-check" }));
    expect(await within(dialog).findByText(/Signed in · Pro plan/)).toBeInTheDocument();
    expect(within(dialog).getByText(/5-hour: 25% used/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(fake.apiCall("POST", "/api/v1/llm/accounts")[0]!.body).toEqual({ provider: "claude_subscription", label: "My Claude", scope: "personal", node_id: "n1" }));
  });
});

describe("chat screen", () => {
  it("streams an answer into the conversation", async () => {
    const fake = signedIn();
    fake.streamHandler = async (_c, onEvent) => {
      onEvent({ event: "text", data: { text: "Hi there" } });
      onEvent({ event: "done", data: {} });
    };
    renderApp(fake);
    await go("Chat");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Message"), "hello{Enter}");
    expect(await screen.findByText("Hi there")).toBeInTheDocument();
    expect(screen.getByText("hello")).toBeInTheDocument();
  });
});

describe("admin", () => {
  it("requires the admin role", async () => {
    renderApp(signedIn(OPERATOR));
    await go("Admin");
    expect(await screen.findByText("This section requires the admin role.")).toBeInTheDocument();
  });

  it("lists keys and audit entries, and revokes a key", async () => {
    const fake = signedIn()
      .route("GET /api/v1/admin/api-keys", [{ id: "k1", name: "ci", key_prefix: "ab12cd34", node_id: null, last_used_at: null, revoked_at: null }])
      .route("GET /api/v1/admin/audit-logs", [{ created_at: "2026-10-20T10:00:00Z", action: "auth.login", actor_email: "admin@lycosa.local", ip_address: "127.0.0.1" }])
      .route("DELETE /api/v1/admin/api-keys/:id", null);
    renderApp(fake);
    await go("Admin");
    expect(await screen.findByText("ci (ab12cd34…)")).toBeInTheDocument();
    expect(await screen.findByText("auth.login")).toBeInTheDocument();
    expect(screen.getAllByText("admin@lycosa.local").length).toBeGreaterThan(0);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Revoke" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Revoke" }));
    expect(await screen.findByText("Revoked ci")).toBeInTheDocument();
  });

  it("never renders a credential call's error", async () => {
    const fake = signedIn().route("PUT /api/v1/admin/providers/:p/credential", () => {
      throw apiError(500, "sk-leaked-in-error");
    });
    renderApp(fake);
    await go("Admin");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Legacy provider keys" }));
    await user.type(screen.getByLabelText("API key"), "sk-1");
    await user.click(screen.getByRole("button", { name: "Save key" }));
    expect(await screen.findByText(/Operation failed\./)).toBeInTheDocument();
    expect(screen.queryByText(/sk-leaked/)).not.toBeInTheDocument();
  });
});

describe("this computer", () => {
  it("shows services, starts the controller and reads logs", async () => {
    const fake = signedIn()
      .on("standalone_enable", { ...STANDALONE_OFF, phase: "starting" })
      .on("standalone_logs", [{ ts: "2026-10-20 14:00:00", line: "Starting PostgreSQL" }]);
    renderApp(fake);
    await go("This computer");
    expect(await screen.findByRole("cell", { name: "PostgreSQL" })).toBeInTheDocument();
    expect(await screen.findByText("2026-10-20 14:00:00 Starting PostgreSQL")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Start" }));
    expect(fake.called("standalone_enable")).toHaveLength(1);
    act(() =>
      fake.emit("lycosa://standalone", {
        ...STANDALONE_OFF,
        phase: "error",
        message: "port 8000 is already in use on this computer",
      }),
    );
    expect(await screen.findByText("port 8000 is already in use on this computer")).toBeInTheDocument();
  });

  it("applies network-controller mode with a valid port", async () => {
    const fake = signedIn().on("standalone_set_network", { ...STANDALONE_OFF, exposeOnLan: true, lanPort: 8100 });
    renderApp(fake);
    await go("This computer");
    const user = userEvent.setup();
    await user.click(await screen.findByLabelText(/Let other devices on my network connect/));
    const port = screen.getByLabelText("Port");
    await user.clear(port);
    await user.type(port, "80");
    await user.click(screen.getByRole("button", { name: /Apply/ }));
    expect(await screen.findByText("Choose a port from 1024 to 65535")).toBeInTheDocument();
    await user.clear(port);
    await user.type(port, "8100");
    await user.click(screen.getByRole("button", { name: /Apply/ }));
    await waitFor(() => expect(fake.called("standalone_set_network")[0]!.args).toEqual({ exposeOnLan: true, lanPort: 8100 }));
  });
});
