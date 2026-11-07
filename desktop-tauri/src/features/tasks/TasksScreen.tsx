import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { cliApi, DEFAULT_CLI_SETTINGS, sessionIsRunning, sessionTokens, type CliSession } from "../../api/cli";
import { friendly, isUnreachable } from "../../api/errors";
import { statusColor } from "../../api/format";
import { useLlmRouting, useNodes } from "../../api/queries";
import { routeFor, taskOutput, taskPrompt, type NodeInfo, type TaskInfo } from "../../api/types";
import { Icon } from "../../components/icons";
import { Button, IconButton, Spinner, StatusDot, useOutsideClick } from "../../components/ui";
import { activeProfile, usePrincipal, useSession } from "../../state/session";
import { PhantomDialog } from "./PhantomDialog";
import { SessionSettings } from "./SessionSettings";
import { ArchiveDialog, DeleteDialog, HistoryDialog, NameDialog } from "./SessionDialogs";
import "../../styles/cli.css";

type Modal = "workspace" | "session" | "archive" | "history" | "phantom" | { settings: CliSession } | { delete: CliSession } | null;

export function TasksScreen() {
  const auth = useSession();
  const principal = usePrincipal();
  const profile = activeProfile(auth.status);
  const scope = `${profile?.id}:${principal.type}:${principal.id}`;
  return <CliWorkspaceScreen key={scope} scope={scope} />;
}

function CliWorkspaceScreen({ scope }: { scope: string }) {
  const queries = useQueryClient();
  const workspacesKey = ["cli", scope, "workspaces"];
  const workspaces = useQuery({ queryKey: workspacesKey, queryFn: cliApi.workspaces });
  const nodes = useNodes();
  const routing = useLlmRouting();
  const [workspaceId, setWorkspaceId] = useState(() => {
    try { return localStorage.getItem(`lycosa-cli-workspace:${scope}`); } catch { return null; }
  });
  const workspace = workspaces.data?.find((w) => w.id === workspaceId) ?? workspaces.data?.[0];
  const sessionsKey = ["cli", scope, "sessions", workspace?.id];
  const sessions = useQuery({ queryKey: sessionsKey, queryFn: () => cliApi.sessions(workspace!.id), enabled: !!workspace, refetchInterval: 2_000 });
  const [archived, setArchived] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [menu, setMenu] = useState<"workspace" | "actions" | null>(null);
  const menuRef = useOutsideClick<HTMLDivElement>(() => setMenu(null));
  const [modal, setModal] = useState<Modal>(null);
  const [error, setError] = useState<string | null>(null);
  const visible = (sessions.data ?? []).filter((s) => s.archived === archived);
  const selected = visible.find((s) => s.id === selectedId) ?? visible[0];
  const selectedPending = useIsMutating({ mutationKey: ["cli", scope, "send", selected?.id] }) > 0;
  const chooseWorkspace = (id: string) => {
    setWorkspaceId(id); setSelectedId(null); setArchived(false); setMenu(null); setError(null);
    try { localStorage.setItem(`lycosa-cli-workspace:${scope}`, id); } catch { /* Selection is optional. */ }
  };
  const open = (next: Modal) => { setModal(next); setMenu(null); setError(null); };
  const refresh = () => queries.invalidateQueries({ queryKey: sessionsKey });
  const updateSession = async (s: CliSession, changes: Parameters<typeof cliApi.updateSession>[1]) => {
    const updated = await cliApi.updateSession(s.id, changes);
    queries.setQueryData<CliSession[]>(sessionsKey, (old) => old?.map((item) => item.id === updated.id ? updated : item));
    await refresh();
  };
  const onMenuKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault(); setMenu(null);
      menuRef.current?.querySelector<HTMLButtonElement>(`[data-menu-trigger="${menu}"]`)?.focus();
    }
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      const items = [...(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menu"] button:not(:disabled)') ?? [])];
      if (!items.length) return;
      event.preventDefault();
      const index = items.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[next]?.focus();
    }
  };

  return <div className="cli-screen">
    <header className="cli-header">
      <div className="cli-heading"><Icon name="grid" /><h1>{archived ? "Archived" : "Active"} Sessions ({visible.length})</h1><span className="cli-badge">Concurrent view</span></div>
      <div className="cli-controls" ref={menuRef} onKeyDown={onMenuKey}>
        <div className="cli-menu-wrap">
          <button type="button" className="cli-workspace-button" aria-label={`Workspace: ${workspace?.name ?? "Choose workspace"}`} data-menu-trigger="workspace" aria-haspopup="menu" aria-expanded={menu === "workspace"} aria-controls="cli-workspaces" onClick={() => setMenu(menu === "workspace" ? null : "workspace")}>
            <StatusDot color={workspaces.isError ? "var(--error)" : "var(--success)"} /><span>Workspace:</span><strong>{workspace?.name ?? "Choose workspace"}</strong><Icon name="chevron" size={14} />
          </button>
          {menu === "workspace" && <div className="menu cli-menu" role="menu" id="cli-workspaces">
            <div className="cli-menu-label">Change workspace</div>
            {workspaces.data?.map((w) => <button key={w.id} role="menuitem" className={w.id === workspace?.id ? "selected" : ""} onClick={() => chooseWorkspace(w.id)}><Icon name="folder" size={16} />{w.name}</button>)}
            <hr /><button role="menuitem" onClick={() => open("workspace")}><Icon name="add" size={16} />Add workspace</button>
          </div>}
        </div>
        <div className="cli-menu-wrap">
          <IconButton icon="chevron" label="Workspace and session actions" data-menu-trigger="actions" aria-haspopup="menu" aria-expanded={menu === "actions"} aria-controls="cli-actions" onClick={() => setMenu(menu === "actions" ? null : "actions")} />
          {menu === "actions" && <div className="menu cli-menu" role="menu" id="cli-actions">
            <div className="cli-menu-label">Workspace</div>
            <button role="menuitem" onClick={() => open("workspace")}><Icon name="folder" size={16} />Add workspace</button>
            <button role="menuitem" onClick={() => setMenu("workspace")}><Icon name="swap" size={16} />Change workspace</button><hr />
            <div className="cli-menu-label">Sessions</div>
            <button role="menuitem" disabled={!workspace} onClick={() => open("session")}><Icon name="add" size={16} />New session</button>
            <button role="menuitem" disabled={!visible.length} onClick={() => open("archive")}><Icon name="archive" size={16} />{archived ? "Restore sessions…" : "Archive sessions…"}</button>
            <button role="menuitem" onClick={() => { setArchived(!archived); setSelectedId(null); setMenu(null); }}><Icon name="archive" size={16} />{archived ? "View active sessions" : `View archived sessions (${sessions.data?.filter((s) => s.archived).length ?? 0})`}</button>
            <button role="menuitem" disabled={!selected || sessionIsRunning(selected) || selectedPending} className="error-text" onClick={() => selected && open({ delete: selected })}><Icon name="delete" size={16} />Delete selected session</button><hr />
            <button role="menuitem" onClick={() => open("history")}><Icon name="list" size={16} />Task history</button>
            <button role="menuitem" onClick={() => open("phantom")}><Icon name="privacy" size={16} />Phantom task (no history)</button>
          </div>}
        </div>
      </div>
    </header>
    <div className="cli-content">
      <div className="cli-viewbar"><span>{archived ? "Archived sessions · restore a session to continue" : "Your workspace, side by side · select a session by its title"}</span><Button variant="text" icon="add" disabled={!workspace} onClick={() => open("session")}>New session</Button></div>
      {(error || workspaces.isError || sessions.isError) && <div className="banner error cli-error" role="alert"><span className="grow">{error ?? `Could not load ${workspaces.isError ? "workspaces" : "sessions"}: ${friendly(workspaces.error ?? sessions.error)}`}</span><Button variant="text" onClick={() => { setError(null); void workspaces.refetch(); if (workspace) void sessions.refetch(); }}>Retry</Button></div>}
      {workspaces.isPending || (workspace && sessions.isPending) ? <div className="cli-empty"><Spinner /><p>Loading your workspace…</p></div> : !workspaces.isError && !workspace ? <div className="cli-empty"><Icon name="terminal" size={38} /><h2>A clear space for your next idea.</h2><p>Create a workspace, then open sessions side by side.</p><Button icon="folder" onClick={() => open("workspace")}>Add workspace</Button></div> : workspace && !sessions.isError && !visible.length ? <div className="cli-empty"><Icon name={archived ? "archive" : "terminal"} size={38} /><h2>{archived ? "No archived sessions" : "Start your first session"}</h2><p>{archived ? "Sessions you archive in this workspace will appear here." : "Each session keeps its own conversation and execution settings."}</p><Button onClick={() => archived ? setArchived(false) : open("session")}>{archived ? "Back to active sessions" : "New session"}</Button></div> : <div className="cli-grid" style={{ "--cli-columns": Math.min(visible.length || 1, visible.length === 4 ? 2 : 3) } as CSSProperties}>
        {visible.map((s) => <SessionCard key={s.id} session={s} workspaceName={workspace?.name ?? ""} scope={scope} sessionsKey={sessionsKey} nodes={nodes.data ?? []} nodesError={nodes.isError} selected={s.id === selected?.id} onSelect={() => setSelectedId(s.id)} onSettings={() => open({ settings: s })} onDelete={() => open({ delete: s })} onNode={async (nodeId) => { try { await updateSession(s, { node_id: nodeId }); } catch (e) { setError(friendly(e)); } }} />)}
      </div>}
    </div>
    {modal === "workspace" && <NameDialog kind="Workspace" onClose={() => setModal(null)} onSave={async (name) => { const w = await cliApi.createWorkspace(name); await queries.invalidateQueries({ queryKey: workspacesKey }); chooseWorkspace(w.id); setModal(null); }} />}
    {modal === "session" && workspace && <NameDialog kind="Session" onClose={() => setModal(null)} onSave={async (name) => {
      const currentRouting = routing.isSuccess ? routing.data : (await routing.refetch({ throwOnError: true })).data;
      const hasDefault = [routeFor(currentRouting, "default", "personal"), routeFor(currentRouting, "default", "deployment")].some((r) => r && r.chain.length > 0);
      const s = await cliApi.createSession(workspace.id, name, { ...DEFAULT_CLI_SETTINGS, route: hasDefault ? "default" : null });
      await refresh(); setArchived(false); setSelectedId(s.id); setModal(null);
    }} />}
    {modal === "archive" && <ArchiveDialog sessions={visible} archived={archived} scope={scope} onClose={() => setModal(null)} onSave={async (ids) => { const results = await Promise.allSettled(ids.map((id) => cliApi.updateSession(id, { archived: !archived }))); await refresh(); const failures = results.filter((r): r is PromiseRejectedResult => r.status === "rejected"); if (failures.length) throw new Error(`${failures.length} session(s) could not be ${archived ? "restored" : "archived"}: ${friendly(failures[0]!.reason)}`); setModal(null); }} />}
    {modal && typeof modal === "object" && "settings" in modal && <SessionSettings session={modal.settings} onClose={() => setModal(null)} onSave={async (changes) => { await updateSession(modal.settings, changes); setModal(null); }} />}
    {modal && typeof modal === "object" && "delete" in modal && <DeleteDialog session={modal.delete} onClose={() => setModal(null)} onDelete={async () => { await cliApi.deleteSession(modal.delete.id); await refresh(); setModal(null); }} />}
    {modal === "history" && <HistoryDialog onClose={() => setModal(null)} />}
    {modal === "phantom" && <PhantomDialog onClose={() => setModal(null)} />}
  </div>;
}

function SessionCard({ session, workspaceName, scope, sessionsKey, nodes, nodesError, selected, onSelect, onSettings, onDelete, onNode }: {
  session: CliSession; workspaceName: string; scope: string; sessionsKey: (string | undefined)[]; nodes: NodeInfo[]; nodesError: boolean; selected: boolean;
  onSelect: () => void; onSettings: () => void; onDelete: () => void; onNode: (node: string | null) => Promise<void>;
}) {
  const queries = useQueryClient();
  const [draft, setDraft] = useState("");
  const [submitted, setSubmitted] = useState<{ prompt: string; previousTaskIds: string[] } | null>(null);
  const [nodeBusy, setNodeBusy] = useState(false);
  const transcript = useRef<HTMLDivElement>(null);
  const mutationKey = ["cli", scope, "send", session.id];
  const pending = useIsMutating({ mutationKey }) > 0;
  const send = useMutation({
    mutationKey,
    mutationFn: (prompt: string) => cliApi.send(session.id, prompt),
    onSuccess: (task) => {
      queries.setQueryData<CliSession[]>(sessionsKey, (old) => old?.map((s) => s.id === session.id ? { ...s, tasks: [...s.tasks.filter((t) => t.id !== task.id), task] } : s));
      setSubmitted(null);
    },
    onError: (error, prompt) => {
      // A timeout can still leave a real task running. Keep that instruction
      // visible; for rejected requests, return it to the composer for editing.
      if (!isUnreachable(error)) { setDraft(prompt); setSubmitted(null); }
    },
    onSettled: () => { void queries.invalidateQueries({ queryKey: sessionsKey }); void queries.invalidateQueries({ queryKey: ["tasks"] }); },
  });
  const running = pending || sessionIsRunning(session);
  const viaLlm = !!session.settings.route || !!session.settings.llm_account_id;
  const controllerProvider = !["ollama", "anthropic"].includes(session.settings.provider);
  const tokens = sessionTokens(session);
  const lastTask = session.tasks[session.tasks.length - 1];
  const node = nodes.find((n) => n.id === session.node_id);
  // A timed-out POST may have completed on the controller. Reconcile only a
  // newly discovered task, never an earlier turn containing the same prompt.
  const discoveredTask = submitted && session.tasks.find((task) => !submitted.previousTaskIds.includes(task.id) && taskPrompt(task) === submitted.prompt);
  useEffect(() => { if (transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight; }, [session.tasks.length, lastTask?.status, submitted]);
  const submit = () => {
    if (!draft.trim() || running || session.archived || nodeBusy) return;
    const prompt = draft.trim(); setDraft(""); setSubmitted({ prompt, previousTaskIds: session.tasks.map((task) => task.id) }); onSelect(); send.mutate(prompt);
  };
  return <section className={`cli-card ${selected ? "selected" : ""}`} aria-label={session.name}>
    <header className="cli-card-header">
      <div className="cli-card-title"><button className="cli-session-select" aria-pressed={selected} title={`Select ${session.name}`} onClick={onSelect}>{session.name}</button><span className="cli-token" title={tokens === null ? "The provider has not reported token usage" : "Reported token usage across this session"}>Tokens {tokens === null ? "—" : tokens.toLocaleString()}</span></div>
      <div className="cli-card-actions"><div className="cli-node-picker"><span className="cli-node-label"><StatusDot color={node?.status === "online" ? "var(--success)" : session.node_id ? "var(--warning)" : "var(--text-2)"} />Node</span><select aria-label={`Node for ${session.name}`} title={viaLlm || controllerProvider ? "The selected route or provider determines execution. Change it in session settings." : "Run on this node, or let Lycosa choose"} value={session.node_id ?? ""} disabled={running || session.archived || viaLlm || controllerProvider || nodeBusy || nodesError} onChange={async (e) => { setNodeBusy(true); try { await onNode(e.target.value || null); } finally { setNodeBusy(false); } }}><option value="">{viaLlm ? "From route / account" : controllerProvider ? "Controller" : nodesError ? "Nodes unavailable" : "Auto-select"}</option>{session.node_id && !node && <option value={session.node_id}>Unavailable node</option>}{nodes.map((n) => <option key={n.id} value={n.id} disabled={n.status !== "online"}>{n.name}{n.status !== "online" ? ` (${n.status})` : ""}</option>)}</select></div><IconButton icon="settings" label={`Settings for ${session.name}`} disabled={running || session.archived} onClick={onSettings} /><IconButton icon="close" label={`Delete ${session.name}`} disabled={running} onClick={onDelete} /></div>
    </header>
    <div className="cli-terminal" ref={transcript} role="log" aria-label={`Transcript for ${session.name}`} aria-live="polite">
      <p className="cli-terminal-label">LYCOSA / {workspaceName}</p>
      {!session.tasks.length && !submitted && <><p className="cli-welcome">A fresh session. A clear workspace.</p><p>Your conversation starts here. Send an instruction to your connected model.</p><p className="cli-ready">› Ready when you are.</p></>}
      {session.tasks.map((t) => <TaskTranscript key={t.id} task={t} />)}
      {submitted && !discoveredTask && <><p className="cli-prompt">› {submitted.prompt}</p>{pending && <p className="cli-task-status"><Spinner small /> Sending to controller…</p>}</>}
      {send.isError && !discoveredTask && <p className="error-text" role="alert">{friendly(send.error)}{isUnreachable(send.error) ? " — the task may still be running. This session will refresh when the controller reconnects." : ""}</p>}
    </div>
    {session.archived ? <div className="cli-composer cli-archive-note"><Icon name="archive" size={14} />Archived · restore this session to continue.</div> : <form className="cli-composer" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="cli-input-wrap"><span aria-hidden="true">›</span><input aria-label={`Command for ${session.name}`} placeholder={running ? "Session is running…" : "Ask a question or enter an instruction…"} value={draft} maxLength={10000} autoComplete="off" disabled={running || nodeBusy} onChange={(e) => setDraft(e.target.value)} /><button type="submit" aria-label={`Send to ${session.name}`} title={`Send to ${session.name}`} disabled={!draft.trim() || running || nodeBusy}>{running ? <Spinner small /> : <Icon name="send" size={15} />}</button></div>
    </form>}
  </section>;
}

export function TaskTranscript({ task }: { task: TaskInfo }) {
  const output = taskOutput(task);
  return <div className="cli-turn"><p className="cli-prompt">› {taskPrompt(task)}</p><p className="cli-task-status"><StatusDot color={statusColor(task.status)} />{task.status}{task.result?.["model"] ? ` · ${String(task.result["model"])}` : ""}</p>{output !== null && <p className="cli-output">{output}</p>}{task.error && <p className="error-text">{task.error}</p>}{(task.executions?.length ?? 0) > 0 && <details className="cli-trace"><summary>Execution trace · {task.executions!.length} attempt(s)</summary>{task.executions!.map((attempt) => <p key={`${attempt.node_id}:${attempt.attempt}`}>attempt {attempt.attempt}: {attempt.status}{attempt.error ? ` — ${attempt.error}` : ""}</p>)}</details>}</div>;
}
