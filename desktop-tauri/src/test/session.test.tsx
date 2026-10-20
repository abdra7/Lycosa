import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADMIN, FakeTransport, STANDALONE_OFF, apiError, session, signedIn } from "./fakeTransport";
import { renderApp } from "./render";

const firstRun = () =>
  new FakeTransport()
    .on("session_restore", { session: { profiles: [], activeId: null, keychainPersistent: true }, principal: null, unreachable: false })
    .on("app_info", { version: "0.5.0", os: "windows", standaloneAvailable: true })
    .on("standalone_status", STANDALONE_OFF);

describe("session-driven root (setup → login → shell)", () => {
  it("first run offers this computer or a controller", async () => {
    renderApp(firstRun());
    expect(await screen.findByText("Run Lycosa on this computer")).toBeInTheDocument();
    expect(screen.getByText("Connect to a controller")).toBeInTheDocument();
    expect(screen.getByLabelText("Controller URL")).toHaveValue("http://localhost:8000");
  });

  it("validates the connection form before calling the controller", async () => {
    const fake = firstRun();
    renderApp(fake);
    const user = userEvent.setup();
    await user.clear(await screen.findByLabelText("Controller URL"));
    await user.type(screen.getByLabelText("Controller URL"), "not-a-url");
    await user.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByText("Enter a valid URL like http://host:8000")).toBeInTheDocument();
    expect(screen.getByText("Email is required")).toBeInTheDocument();
    expect(fake.called("session_add_profile")).toHaveLength(0);
  });

  it("connects, signs in and opens the shell", async () => {
    const fake = firstRun()
      .on("session_add_profile", ADMIN)
      .on("session_state", session().session)
      .route("GET /api/v1/nodes", []);
    renderApp(fake);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "admin@lycosa.local");
    await user.type(screen.getByLabelText("Password"), "pw");
    await user.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByRole("heading", { name: "Nodes" })).toBeInTheDocument();
    expect(fake.called("session_add_profile")[0]!.args).toMatchObject({ baseUrl: "http://localhost:8000", email: "admin@lycosa.local", password: "pw" });
  });

  it("shows the controller's error on a failed connection", async () => {
    const fake = firstRun().on("session_add_profile", () => {
      throw apiError(401, "Invalid email or password");
    });
    renderApp(fake);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "a@b.c");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
  });

  it("runs the controller on this computer, then signs in automatically", async () => {
    const fake = firstRun()
      .on("standalone_enable", { ...STANDALONE_OFF, phase: "starting", stage: "Starting PostgreSQL" })
      .on("standalone_sign_in", ADMIN)
      .on("session_state", session({ profiles: [{ id: "l", name: "This computer", baseUrl: "http://127.0.0.1:18000", kind: "local", signedIn: true }] }).session)
      .route("GET /api/v1/nodes", []);
    renderApp(fake);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Start on this computer" }));
    act(() => fake.emit("lycosa://standalone", { ...STANDALONE_OFF, phase: "starting", stage: "Applying database migrations" }));
    expect(await screen.findByText("Applying database migrations")).toBeInTheDocument();
    act(() => fake.emit("lycosa://standalone", { ...STANDALONE_OFF, phase: "running", apiUrl: "http://127.0.0.1:18000" }));
    expect(await screen.findByRole("heading", { name: "Nodes" })).toBeInTheDocument();
    expect(fake.called("standalone_sign_in")).toHaveLength(1);
  });

  it("a saved profile without a session opens sign-in; signing in opens the shell", async () => {
    const fake = signedIn()
      .on("session_restore", session({ principal: null }))
      .on("session_login", ADMIN);
    renderApp(fake);
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText("Home lab — http://10.0.0.5:8000")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "admin@lycosa.local");
    await user.type(screen.getByLabelText("Password"), "pw");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Nodes" })).toBeInTheDocument();
  });

  it("offers a retry when the saved controller is unreachable", async () => {
    const fake = signedIn().on("session_restore", session({ principal: null, unreachable: true }));
    renderApp(fake);
    expect(await screen.findByText("Could not reach http://10.0.0.5:8000.")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));
    expect(fake.called("session_restore").length).toBeGreaterThanOrEqual(2);
  });

  it("switches between saved controllers from the sign-in screen", async () => {
    const profiles = [
      { id: "a", name: "Lab", baseUrl: "http://10.0.0.5:8000", kind: "remote", signedIn: false },
      { id: "b", name: "Office", baseUrl: "http://10.1.0.9:8000", kind: "remote", signedIn: false },
    ];
    const fake = signedIn().on("session_restore", session({ principal: null, profiles }));
    renderApp(fake);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Switch controller" }));
    await user.click(screen.getByRole("menuitem", { name: "Office — http://10.1.0.9:8000" }));
    await waitFor(() => expect(fake.called("session_switch")[0]!.args).toEqual({ id: "b" }));
  });

  it("after a relaunch, the saved local session comes back once the controller is up", async () => {
    const local = [{ id: "l", name: "This computer", baseUrl: "http://127.0.0.1:18000", kind: "local", signedIn: true }];
    const fake = signedIn()
      .on("session_restore", session({ principal: null, profiles: local, unreachable: true }))
      .on("standalone_status", { ...STANDALONE_OFF, phase: "starting", enabled: true, stage: "Starting PostgreSQL" });
    renderApp(fake);
    expect(await screen.findByText("Starting PostgreSQL")).toBeInTheDocument();
    fake.on("session_restore", session({ profiles: local }));
    act(() => fake.emit("lycosa://standalone", { ...STANDALONE_OFF, phase: "running", enabled: true, apiUrl: "http://127.0.0.1:18000" }));
    expect(await screen.findByRole("heading", { name: "Nodes" })).toBeInTheDocument();
    expect(fake.called("standalone_sign_in")).toHaveLength(0);
  });

  it("the local profile's sign-in uses the local controller", async () => {
    const local = [{ id: "l", name: "This computer", baseUrl: "http://127.0.0.1:18000", kind: "local", signedIn: false }];
    const fake = signedIn()
      .on("session_restore", session({ principal: null, profiles: local }))
      .on("standalone_status", { ...STANDALONE_OFF, phase: "running", apiUrl: "http://127.0.0.1:18000" })
      .on("standalone_sign_in", ADMIN);
    renderApp(fake);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Sign in to this computer" }));
    expect(await screen.findByRole("heading", { name: "Nodes" })).toBeInTheDocument();
  });
});

describe("shell", () => {
  it("shows identity, signs out and goes back to sign-in", async () => {
    const fake = signedIn().on("session_logout", null);
    renderApp(fake);
    expect(await screen.findByText(/admin@lycosa.local · admin/)).toBeInTheDocument();
    fake.on("session_restore", session({ principal: null }));
    await userEvent.setup().click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(fake.called("session_logout")).toHaveLength(1);
  });

  it("follows the live event stream and raises alerts", async () => {
    const fake = signedIn();
    renderApp(fake);
    await screen.findByRole("heading", { name: "Nodes" });
    await waitFor(() => expect(fake.called("events_start")).toHaveLength(1));
    act(() => fake.emit("lycosa://events", { type: "status", connected: true }));
    expect(await screen.findByText("live")).toBeInTheDocument();
    const before = fake.apiCall("GET", "/api/v1/nodes").length;
    act(() => fake.emit("lycosa://events", { type: "event", event: { type: "node.connected", ts: "", data: { name: "gpu-box" } } }));
    expect(await screen.findByText("Node gpu-box connected")).toBeInTheDocument();
    await waitFor(() => expect(fake.apiCall("GET", "/api/v1/nodes").length).toBeGreaterThan(before));
    act(() => fake.emit("lycosa://events", { type: "event", event: { type: "alert.created", ts: "", data: { message: "Node gpu-box offline" } } }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Node gpu-box offline");
  });

  it("a 401 from any screen re-validates the session", async () => {
    const fake = signedIn().route("GET /api/v1/nodes", () => {
      throw apiError(401, "Session expired");
    });
    renderApp(fake);
    fake.on("session_restore", session({ principal: null }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("toggles and remembers the theme", async () => {
    renderApp(signedIn());
    await userEvent.setup().click(await screen.findByRole("button", { name: "Switch to dark mode" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("lycosa_theme_mode")).toBe("dark");
    expect(screen.getByRole("button", { name: "Switch to light mode" })).toBeInTheDocument();
  });

  it("adds a controller from the account menu", async () => {
    const fake = signedIn().on("session_add_profile", ADMIN);
    renderApp(fake);
    const user = userEvent.setup();
    await user.click(await screen.findByTitle("Account and controllers"));
    await user.click(screen.getByRole("menuitem", { name: /Add controller/ }));
    expect(screen.getByRole("dialog", { name: "Add a controller" })).toBeInTheDocument();
    expect(screen.getByLabelText("Controller URL")).toHaveValue("");
  });
});
