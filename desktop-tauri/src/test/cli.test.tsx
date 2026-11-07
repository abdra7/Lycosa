import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DEFAULT_CLI_SETTINGS, sessionTokens, type CliSession, type CliSettings, type CliWorkspace } from "../api/cli";
import type { ApiCall } from "../ipc/transport";
import type { TaskInfo } from "../api/types";
import { apiError, signedIn } from "./fakeTransport";
import { renderApp } from "./render";

const workspace: CliWorkspace = { id: "w1", name: "Lycosa", created_at: "2026-11-06T10:00:00Z" };
const makeSession = (id = "s1", name = "Session 1"): CliSession => ({ id, workspace_id: "w1", name, archived: false, node_id: null, settings: { ...DEFAULT_CLI_SETTINGS }, tasks: [], created_at: "2026-11-06T10:00:00Z" });
const answer = (id: string, prompt: string): TaskInfo => ({ id, type: "general", status: "succeeded", queued_at: "2026-11-06T10:00:00Z", payload: { prompt }, result: { output: `Answer: ${prompt}`, usage: { total_tokens: 124 } } });

function setupCli(initial = [makeSession()], initialWorkspaces = [workspace]) {
  let sessions = initial;
  let workspaces = initialWorkspaces;
  const fake = signedIn()
    .route("GET /api/v1/cli/workspaces", () => structuredClone(workspaces))
    .route("POST /api/v1/cli/workspaces", (call: ApiCall) => {
      const { name } = call.body as { name: string };
      if (workspaces.some((w) => w.name.toLowerCase() === name.toLowerCase())) throw apiError(409, "A workspace with this name already exists");
      const next = { ...workspace, id: `w${workspaces.length + 1}`, name }; workspaces = [...workspaces, next]; return next;
    })
    .route("GET /api/v1/cli/workspaces/:id/sessions", (_call: ApiCall, [id]: string[]) => structuredClone(sessions.filter((s) => s.workspace_id === id)))
    .route("POST /api/v1/cli/workspaces/:id/sessions", (call: ApiCall, [id]: string[]) => {
      const body = call.body as { name: string; settings: CliSettings };
      const next = { ...makeSession(`s${sessions.length + 1}`, body.name), workspace_id: id!, settings: body.settings }; sessions = [...sessions, next]; return next;
    })
    .route("PATCH /api/v1/cli/sessions/:id", (call: ApiCall, [id]: string[]) => {
      const next = { ...sessions.find((s) => s.id === id)!, ...call.body as Partial<CliSession> };
      sessions = sessions.map((s) => s.id === id ? next : s); return structuredClone(next);
    })
    .route("DELETE /api/v1/cli/sessions/:id", (_call: ApiCall, [id]: string[]) => { sessions = sessions.filter((s) => s.id !== id); return null; })
    .route("POST /api/v1/cli/sessions/:id/tasks", (call: ApiCall, [id]: string[]) => {
      const task = answer(`t${Date.now()}`, (call.body as { prompt: string }).prompt);
      sessions = sessions.map((s) => s.id === id ? { ...s, tasks: [...s.tasks, task] } : s); return task;
    });
  return { fake, sessions: () => sessions };
}

const openCli = async () => userEvent.setup().click(await screen.findByRole("button", { name: "CLI" }));
const menuAction = async (name: string | RegExp) => {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Workspace and session actions" }));
  await user.click(screen.getByRole("menuitem", { name }));
};

describe("CLI sessions", () => {
  it("creates real workspaces and sessions, preselects the default route, and runs through the session API", async () => {
    const { fake } = setupCli([], []);
    fake.route("GET /api/v1/llm/routing", { purposes: ["default"], personal: [{ purpose: "default", scope: "personal", chain: [{ account_id: "a1", model: "m" }] }], deployment: [] });
    renderApp(fake); await openCli(); const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add workspace" }));
    await user.type(screen.getByLabelText("Workspace name"), "Product");
    await user.click(screen.getByRole("button", { name: "Create" }));
    await screen.findByText("Start your first session");
    await user.click(screen.getAllByRole("button", { name: "New session" })[0]!);
    await user.type(screen.getByLabelText("Session name"), "Login");
    await user.click(screen.getByRole("button", { name: "Create" }));
    const command = await screen.findByLabelText("Command for Login");
    expect(fake.apiCall("POST", "/api/v1/cli/workspaces/w1/sessions")[0]!.body).toMatchObject({ name: "Login", settings: { route: "default" } });
    await user.type(command, "Find the login component"); await user.click(screen.getByRole("button", { name: "Send to Login" }));
    expect(await screen.findByText("Answer: Find the login component")).toBeInTheDocument();
    expect(fake.apiCall("POST", "/api/v1/cli/sessions/s1/tasks")[0]!.body).toEqual({ prompt: "Find the login component" });
    expect(screen.getByText("Tokens 124")).toBeInTheDocument();
    expect(screen.getByLabelText("Node for Login")).toBeDisabled();
  });

  it("persists node choice, renders unavailable nodes and restores submitted instructions after rejection", async () => {
    const { fake } = setupCli([{ ...makeSession(), node_id: "gone" }]);
    fake.route("GET /api/v1/nodes", [{ id: "n1", name: "Desk", status: "online" }, { id: "n2", name: "Offline", status: "offline" }]);
    renderApp(fake); await openCli(); const user = userEvent.setup();
    const picker = await screen.findByLabelText("Node for Session 1");
    expect(picker).toHaveValue("gone"); expect(screen.getByRole("option", { name: "Unavailable node" })).toBeInTheDocument();
    await user.selectOptions(picker, "n1");
    await waitFor(() => expect(fake.apiCall("PATCH", "/api/v1/cli/sessions/s1")[0]!.body).toEqual({ node_id: "n1" }));
    fake.route("POST /api/v1/cli/sessions/:id/tasks", () => { throw apiError(422, "Selected node is unavailable"); });
    await user.type(screen.getByLabelText("Command for Session 1"), "Keep my instruction");
    await user.click(screen.getByRole("button", { name: "Send to Session 1" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Selected node is unavailable");
    expect(screen.getByLabelText("Command for Session 1")).toHaveValue("Keep my instruction");
  });

  it("supports independent concurrent sessions and disables edits only on the running card", async () => {
    const { fake } = setupCli([makeSession(), makeSession("s2", "Session 2")]);
    let finish!: (task: TaskInfo) => void;
    fake.route("POST /api/v1/cli/sessions/s1/tasks", () => new Promise<TaskInfo>((resolve) => { finish = resolve; }));
    renderApp(fake); await openCli(); const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Command for Session 1"), "Slow job");
    await user.click(screen.getByRole("button", { name: "Send to Session 1" }));
    expect(screen.getByLabelText("Command for Session 1")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Settings for Session 1" })).toBeDisabled();
    expect(screen.getByLabelText("Command for Session 2")).toBeEnabled();
    await user.type(screen.getByLabelText("Command for Session 2"), "Quick job");
    await user.click(screen.getByRole("button", { name: "Send to Session 2" }));
    expect(await screen.findByText("Answer: Quick job")).toBeInTheDocument();
    await act(async () => finish(answer("t1", "Slow job")));
    await waitFor(() => expect(screen.getByLabelText("Command for Session 1")).toBeEnabled());
  });

  it("archives multiple sessions, restores them, and confirms deletion without affecting task history", async () => {
    const { fake, sessions } = setupCli([makeSession(), makeSession("s2", "Session 2")]);
    renderApp(fake); await openCli(); await screen.findByLabelText("Command for Session 1"); const user = userEvent.setup();
    await menuAction("Archive sessions…");
    await user.click(screen.getByRole("checkbox", { name: "Session 1" })); await user.click(screen.getByRole("checkbox", { name: "Session 2" }));
    await user.click(screen.getByRole("button", { name: "Archive selected" }));
    await screen.findByText("Start your first session"); expect(sessions().every((s) => s.archived)).toBe(true);
    await menuAction("View archived sessions (2)");
    expect(await screen.findByRole("heading", { name: "Archived Sessions (2)" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Command for Session 1")).not.toBeInTheDocument();
    await menuAction("Restore sessions…"); await user.click(screen.getByRole("checkbox", { name: "Session 1" }));
    await user.click(screen.getByRole("button", { name: "Restore selected" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await menuAction("View active sessions");
    await user.click(await screen.findByRole("button", { name: "Delete Session 1" }));
    await user.click(screen.getByRole("button", { name: "Cancel" })); expect(fake.apiCall("DELETE", "/api/v1/cli/sessions/s1")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Delete Session 1" }));
    await user.click(screen.getByRole("button", { name: "Delete session" }));
    await waitFor(() => expect(fake.apiCall("DELETE", "/api/v1/cli/sessions/s1")).toHaveLength(1));
  });

  it("saves account/model and knowledge settings, rejecting an API account with no model", async () => {
    const accounts = [
      { id: "a1", provider: "openai", provider_name: "OpenAI", label: "Work", usable: true, auth_method: "api_key", status: "active" },
      { id: "c1", provider: "claude_subscription", provider_name: "Claude subscription", label: "My Claude", usable: true, auth_method: "node_sign_in", status: "active", node_name: "desk" },
    ];
    const { fake } = setupCli([{ ...makeSession(), node_id: "n1" }]); fake.route("GET /api/v1/llm/accounts", accounts);
    renderApp(fake); await openCli(); const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Settings for Session 1" }));
    await user.selectOptions(screen.getByLabelText("Route"), "account:a1");
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter the model to use with Work.");
    await user.selectOptions(screen.getByLabelText("Route"), "account:c1");
    expect(screen.getByText(/Runs Claude Code on desk/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Knowledge query (optional)"), "auth");
    await user.type(screen.getByLabelText("Knowledge collection (optional)"), "docs");
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    await waitFor(() => expect(fake.apiCall("PATCH", "/api/v1/cli/sessions/s1")[0]!.body).toMatchObject({ node_id: null, settings: { llm_account_id: "c1", model: "default", knowledge_query: "auth", knowledge_collection: "docs" } }));
  });

  it("keeps a timeout instruction visible while the controller may still be running it", async () => {
    const { fake } = setupCli(); fake.route("POST /api/v1/cli/sessions/:id/tasks", () => { throw { kind: "unreachable", base_url: "http://controller", message: "timeout" }; });
    renderApp(fake); await openCli(); const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Command for Session 1"), "Long job"); await user.click(screen.getByRole("button", { name: "Send to Session 1" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("the task may still be running");
    expect(screen.getByText("› Long job")).toBeInTheDocument();
  });

  it("reconciles a timed-out submission with its newly polled task, even after an identical earlier instruction", async () => {
    const initial = { ...makeSession(), tasks: [answer("old", "Repeat")] };
    const { fake } = setupCli([initial]);
    fake.route("POST /api/v1/cli/sessions/:id/tasks", () => { throw { kind: "unreachable", base_url: "http://controller", message: "timeout" }; });
    const { client } = renderApp(fake); await openCli(); const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Command for Session 1"), "Repeat"); await user.click(screen.getByRole("button", { name: "Send to Session 1" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("the task may still be running");
    expect(screen.getAllByText("› Repeat")).toHaveLength(2);
    fake.route("GET /api/v1/cli/workspaces/:id/sessions", [{ ...initial, tasks: [...initial.tasks, answer("recovered", "Repeat")] }]);
    await act(async () => { await client.invalidateQueries({ queryKey: ["cli"] }); });
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getAllByText("› Repeat")).toHaveLength(2);
    expect(screen.getAllByText("Answer: Repeat")).toHaveLength(2);
  });

  it("switches between server-backed workspaces and rejects duplicate workspace names", async () => {
    const { fake } = setupCli([makeSession(), { ...makeSession("s2", "Research session"), workspace_id: "w2" }], [workspace, { ...workspace, id: "w2", name: "Research" }]);
    renderApp(fake); await openCli(); const user = userEvent.setup(); await screen.findByLabelText("Command for Session 1");
    await user.click(screen.getByRole("button", { name: "Workspace: Lycosa" }));
    await user.click(screen.getByRole("menuitem", { name: "Research" }));
    expect(await screen.findByLabelText("Command for Research session")).toBeInTheDocument();
    expect(screen.queryByLabelText("Command for Session 1")).not.toBeInTheDocument();
    await menuAction("Add workspace");
    await user.type(screen.getByLabelText("Workspace name"), "research"); await user.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already exists");
  });

  it("preserves task history with execution trace and Phantom entry", async () => {
    const { fake } = setupCli(); fake.route("GET /api/v1/tasks", [{ id: "t", type: "coding", status: "failed", queued_at: "", payload: { prompt: "fix it" }, error: "all nodes failed", executions: [{ node_id: "n1", attempt: 1, status: "failed", error: "timeout" }] }]);
    fake.route("GET /api/v1/phantom/capabilities", { enabled: false, models: [] });
    renderApp(fake); await openCli(); await screen.findByLabelText("Command for Session 1");
    await menuAction("Task history");
    expect(await screen.findByText("failed · coding · 1 attempt(s)")).toBeInTheDocument();
    expect(screen.getByText("attempt 1: failed — timeout")).toBeInTheDocument();
    await userEvent.setup().click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));
    await menuAction("Phantom task (no history)");
    expect(await screen.findByText("Ask the controller administrator to configure an isolated local model.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run Phantom" })).toBeDisabled();
  });

  it("shows a recoverable backend-load error and does not substitute demo sessions", async () => {
    const { fake } = setupCli(); fake.route("GET /api/v1/cli/workspaces", () => { throw apiError(503, "Controller upgrading"); });
    renderApp(fake); await openCli(); expect(await screen.findByRole("alert")).toHaveTextContent("Controller upgrading");
    expect(screen.queryByLabelText("Command for Session 1")).not.toBeInTheDocument();
    fake.route("GET /api/v1/cli/workspaces", [workspace]); await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByLabelText("Command for Session 1")).toBeInTheDocument();
  });

  it("uses reported token usage instead of a fabricated model-size badge", () => {
    expect(sessionTokens(makeSession())).toBeNull();
    expect(sessionTokens({ ...makeSession(), tasks: [answer("t1", "one"), answer("t2", "two")] })).toBe(248);
  });
});
