import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { isApiError } from "../../api/errors";
import { eventSummary, principalName } from "../../api/types";
import { Brand } from "../../components/brand";
import { Icon } from "../../components/icons";
import { IconButton, StatusDot, useConfirm, useOutsideClick } from "../../components/ui";
import { useLiveEvents } from "../../state/events";
import { useNav, type Section } from "../../state/nav";
import { activeProfile, usePrincipal, useSession } from "../../state/session";
import { useStandalone } from "../../state/standalone";
import { useTheme } from "../../state/theme";
import { useToast } from "../../state/toast";
import { AdminScreen } from "../admin/AdminScreen";
import { ChatScreen } from "../chat/ChatScreen";
import { KnowledgeScreen } from "../knowledge/KnowledgeScreen";
import { ProvidersScreen } from "../llm/ProvidersScreen";
import { LocalControllerScreen } from "../local/LocalControllerScreen";
import { NodeDetail } from "../nodes/NodeDetail";
import { NodesScreen } from "../nodes/NodesScreen";
import { AddControllerDialog } from "../setup/ConnectionForm";
import { TasksScreen } from "../tasks/TasksScreen";
import { RunView } from "../workflows/RunView";
import { WorkflowsScreen } from "../workflows/WorkflowsScreen";

const SECTIONS: { id: Section; label: string; icon: string }[] = [
  { id: "nodes", label: "Nodes", icon: "nodes" },
  { id: "tasks", label: "Tasks", icon: "tasks" },
  { id: "chat", label: "Chat", icon: "chat" },
  { id: "workflows", label: "Workflows", icon: "workflows" },
  { id: "knowledge", label: "Knowledge", icon: "knowledge" },
  { id: "providers", label: "Providers", icon: "providers" },
  { id: "admin", label: "Admin", icon: "admin" },
];

/** Authenticated shell: nav rail, profile switcher, identity and sign-out,
 * plus the live event strip fed by the /events WebSocket (ADR-016). */
export function Shell() {
  const nav = useNav();
  const toast = useToast();
  const queries = useQueryClient();
  const { info } = useStandalone();
  useSessionExpiry();

  const { connected, last } = useLiveEvents((event) => {
    // push beats polling: refresh the node list right away
    if (event.type.startsWith("node.")) void queries.invalidateQueries({ queryKey: ["nodes"] });
    if (event.type === "alert.created") toast.show(eventSummary(event), "error");
  });

  const sections = info?.standaloneAvailable
    ? [...SECTIONS, { id: "local" as Section, label: "This computer", icon: "computer" }]
    : SECTIONS;

  return (
    <div className="shell">
      <Header />
      <nav className="nav" aria-label="Sections">
        {sections.map((s) => (
          <button
            key={s.id}
            className="nav-item"
            aria-current={nav.section === s.id ? "page" : undefined}
            onClick={() => nav.go(s.id)}
          >
            <span className="pill">
              <Icon name={s.icon} size={20} />
            </span>
            {s.label}
          </button>
        ))}
      </nav>
      <main className="main">
        <Content />
      </main>
      <footer className="statusbar">
        <StatusDot color={connected ? "var(--success)" : "var(--text-2)"} />
        <span>{connected ? "live" : "connecting…"}</span>
        <span className="grow ellipsis" style={{ marginLeft: 12 }}>
          {last ? eventSummary(last) : ""}
        </span>
        <LocalIndicator />
        <span>v{info?.version ?? ""}</span>
      </footer>
    </div>
  );
}

function Content() {
  const nav = useNav();
  if (nav.view?.kind === "node") return <NodeDetail nodeId={nav.view.nodeId} />;
  if (nav.view?.kind === "run") return <RunView {...nav.view} />;
  switch (nav.section) {
    case "nodes":
      return <NodesScreen />;
    case "tasks":
      return <TasksScreen />;
    case "chat":
      return <ChatScreen />;
    case "workflows":
      return <WorkflowsScreen />;
    case "knowledge":
      return <KnowledgeScreen />;
    case "providers":
      return <ProvidersScreen />;
    case "admin":
      return <AdminScreen />;
    case "local":
      return <LocalControllerScreen />;
  }
}

function LocalIndicator() {
  const { status } = useStandalone();
  if (!status || status.phase === "unavailable" || status.phase === "off") return null;
  const color = status.phase === "running" ? "var(--success)" : status.phase === "error" ? "var(--error)" : "var(--warning)";
  return (
    <span className="row" style={{ gap: 6, marginRight: 12 }} title="The controller on this computer">
      <StatusDot color={color} />
      this computer: {status.phase}
    </span>
  );
}

function Header() {
  const session = useSession();
  const principal = usePrincipal();
  const theme = useTheme();
  const profile = activeProfile(session.status);
  const profiles = session.status.phase === "ready" ? session.status.session.profiles : [];
  const keychain = session.status.phase === "ready" ? session.status.session.keychainPersistent : true;
  const [menu, setMenu] = useState(false);
  const [adding, setAdding] = useState(false);
  const ref = useOutsideClick<HTMLDivElement>(() => setMenu(false));
  const { confirm, element } = useConfirm();

  const remove = async () => {
    setMenu(false);
    if (!profile) return;
    const ok = await confirm({
      title: `Remove ${profile.name}?`,
      message:
        "The saved connection and its sign-in are removed from this app. The controller and its data are not touched.",
      confirmLabel: "Remove",
      danger: true,
    });
    if (ok) await session.removeProfile(profile.id);
  };

  return (
    <header className="shell-header">
      <Brand />
      <span className="spacer" />
      {!keychain && (
        <span className="chip small" title="No OS keychain is available, so sign-ins last until the app closes.">
          <Icon name="lock" size={14} /> sign-in not saved
        </span>
      )}
      {profiles.length > 1 && (
        <select
          className="select"
          style={{ width: "auto", maxWidth: 260 }}
          aria-label="Controller"
          value={profile?.id ?? ""}
          onChange={(e) => void session.switchProfile(e.target.value)}
        >
          {profiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      )}
      <div style={{ position: "relative" }} ref={ref}>
        <button className="chip" style={{ cursor: "pointer", maxWidth: 280 }} onClick={() => setMenu((v) => !v)} aria-haspopup="menu" title="Account and controllers">
          <Icon name="person" size={16} />
          <span className="ellipsis">
            {principalName(principal)} · {principal.role}
          </span>
        </button>
        {menu && (
          <div className="menu" role="menu">
            <button role="menuitem" onClick={() => (setMenu(false), setAdding(true))}>
              <Icon name="link" size={16} /> Add controller…
            </button>
            {profile && (
              <button role="menuitem" onClick={() => void remove()}>
                <Icon name="delete" size={16} /> Remove “{profile.name}”…
              </button>
            )}
          </div>
        )}
      </div>
      <IconButton
        icon={theme.mode === "dark" ? "sun" : "moon"}
        label={theme.mode === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        onClick={theme.toggle}
      />
      <IconButton icon="logout" label="Sign out" onClick={() => void session.logout()} />
      {adding && <AddControllerDialog onClose={() => setAdding(false)} />}
      {element}
    </header>
  );
}

/** A 401 anywhere means the session ended (expired or revoked token):
 * re-validate, which drops to the sign-in screen when it is gone. */
function useSessionExpiry() {
  const queries = useQueryClient();
  const session = useSession();
  useEffect(() => {
    const check = (error: unknown) => {
      if (isApiError(error, 401)) void session.restore();
    };
    const unsubQueries = queries.getQueryCache().subscribe((event) => {
      if (event.type === "updated" && event.action.type === "error") check(event.action.error);
    });
    const unsubMutations = queries.getMutationCache().subscribe((event) => {
      if (event.type === "updated" && event.action.type === "error") check(event.action.error);
    });
    return () => {
      unsubQueries();
      unsubMutations();
    };
  }, [queries, session]);
}
