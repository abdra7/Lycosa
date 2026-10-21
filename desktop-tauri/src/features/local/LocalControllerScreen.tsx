import { useEffect, useState } from "react";

import { friendly } from "../../api/errors";
import { Banner, Button, Card, Chip, LinearProgress, StatusDot, useConfirm } from "../../components/ui";
import { transport } from "../../ipc/transport";
import { activeProfile, useSession } from "../../state/session";
import { SERVICE_STATE_COLOR, useStandalone, type StandaloneStatus } from "../../state/standalone";
import { useToast } from "../../state/toast";

const LOG_SOURCES = [
  ["supervisor", "Supervisor"],
  ["backend", "Lycosa API"],
  ["postgres", "PostgreSQL"],
  ["postgres-setup", "Database tools"],
  ["qdrant", "Qdrant"],
  ["agent", "Local node"],
] as const;

/** "This computer": the controller the app runs itself (ADR-037). */
export function LocalControllerScreen() {
  const { status } = useStandalone();
  const session = useSession();
  const toast = useToast();
  const { confirm, element } = useConfirm();
  const [busy, setBusy] = useState<string | null>(null);
  const onLocal = activeProfile(session.status)?.kind === "local";

  if (!status) return null;
  if (status.phase === "unavailable") {
    return (
      <div className="page">
        <h1>This computer</h1>
        <Banner>This build has no bundled controller: {status.message}</Banner>
      </div>
    );
  }

  const run = async (label: string, command: string, args?: Record<string, unknown>) => {
    setBusy(label);
    try {
      await transport().invoke(command, args);
    } catch (e) {
      toast.show(friendly(e), "error");
    } finally {
      setBusy(null);
    }
  };

  const running = status.phase === "running";
  const working = status.phase === "starting" || status.phase === "stopping";

  const stop = async () => {
    if (onLocal) {
      const ok = await confirm({
        title: "Stop the local controller?",
        message: "You are signed in to it, so this app will lose its connection until you start it again. Its data is kept.",
        confirmLabel: "Stop",
      });
      if (!ok) return;
    }
    await run("stop", "standalone_disable");
  };

  return (
    <div className="page stack-12" style={{ maxWidth: 1100 }}>
      <div className="page-head" style={{ marginBottom: 0 }}>
        <h1>This computer</h1>
        <Chip>
          <StatusDot color={running ? "var(--success)" : status.phase === "error" ? "var(--error)" : working ? "var(--warning)" : "var(--text-2)"} />
          {status.phase}
        </Chip>
        <span className="spacer" />
        {running || status.phase === "error" ? (
          <>
            <Button variant="outlined" icon="refresh" busy={busy === "restart"} disabled={working} onClick={() => void run("restart", "standalone_restart")}>
              Restart
            </Button>
            <Button variant="outlined" icon="stop" busy={busy === "stop"} disabled={working} onClick={() => void stop()}>
              Stop
            </Button>
          </>
        ) : (
          <Button icon="play" busy={busy === "start" || working} onClick={() => void run("start", "standalone_enable")}>
            Start
          </Button>
        )}
      </div>
      <p className="small muted">
        The app runs Lycosa's controller here: PostgreSQL, Qdrant and the Lycosa API from its own bundled runtime. They start
        with the app while this is on, and stop cleanly when the app closes.
      </p>
      {working && (
        <div className="stack-8">
          <LinearProgress />
          <p className="small">{status.stage}</p>
        </div>
      )}
      {status.phase === "error" && status.message && <Banner kind="error">{status.message}</Banner>}
      {running && !onLocal && (
        <Banner>
          <div className="row wrap">
            <span className="grow">You are connected to another controller. Sign in here to manage this computer's controller.</span>
            <Button variant="text" onClick={() => void session.signInLocal().catch((e) => toast.show(friendly(e), "error"))}>
              Sign in to this computer
            </Button>
          </div>
        </Banner>
      )}
      <Card title="Services" icon="nodes">
        <table className="table">
          <thead>
            <tr>
              <th>Service</th>
              <th>State</th>
              <th>Port</th>
              <th>Restarts</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {status.services.map((s) => (
              <tr key={s.name}>
                <td>{s.label}</td>
                <td>
                  <span className="row">
                    <StatusDot color={SERVICE_STATE_COLOR[s.state]} />
                    {s.state}
                  </span>
                </td>
                <td>{s.port ?? "—"}</td>
                <td>{s.restarts}</td>
                <td className="small">{s.detail ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <div className="two-col">
        {/* a saved change re-mounts the card with the new values */}
        <NetworkCard key={`${status.exposeOnLan}-${status.lanPort}`} status={status} />
        <NodeCard status={status} />
      </div>
      <DataCard status={status} />
      <LogsCard />
      {element}
    </div>
  );
}

/** Network-controller mode: the API listens on every interface so agents
 * on other machines can register. Off = this computer only. */
function NetworkCard({ status }: { status: StandaloneStatus }) {
  const toast = useToast();
  const [expose, setExpose] = useState(status.exposeOnLan);
  const [port, setPort] = useState(String(status.lanPort));
  const [lanIp, setLanIp] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    void transport()
      .invoke<string | null>("lan_ipv4")
      .then(setLanIp)
      .catch(() => {});
  }, []);
  const dirty = expose !== status.exposeOnLan || Number(port) !== status.lanPort;
  const apply = async () => {
    const n = Number(port);
    if (!Number.isInteger(n) || n < 1024 || n > 65535) {
      toast.show("Choose a port from 1024 to 65535", "error");
      return;
    }
    setBusy(true);
    try {
      await transport().invoke("standalone_set_network", { exposeOnLan: expose, lanPort: n });
    } catch (e) {
      toast.show(friendly(e), "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Network" icon="link">
      <div className="stack-8">
        <label className="checkbox">
          <input type="checkbox" checked={expose} onChange={(e) => setExpose(e.target.checked)} />
          Let other devices on my network connect (network controller)
        </label>
        <p className="small muted">
          Off: the API answers only this computer, on a private port. On: it listens on every interface at the port below so
          agents and other desktop apps can connect; every request still needs a sign-in or an API key. Allow the port in your
          firewall, and only turn this on for networks you trust.
        </p>
        <label className="field" style={{ maxWidth: 180 }}>
          <span className="small muted">Port</span>
          <input className="input" inputMode="numeric" value={port} disabled={!expose} onChange={(e) => setPort(e.target.value.replace(/\D/g, ""))} />
        </label>
        {status.exposeOnLan && (
          <p className="small">
            Other devices use <span className="mono">http://{lanIp ?? "<this computer's IP>"}:{status.lanPort}</span>
          </p>
        )}
        <div>
          <Button variant="outlined" busy={busy} disabled={!dirty} onClick={() => void apply()}>
            Apply{status.phase === "running" ? " and restart" : ""}
          </Button>
        </div>
      </div>
    </Card>
  );
}

function NodeCard({ status }: { status: StandaloneStatus }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const toggle = async (enabled: boolean) => {
    setBusy(true);
    try {
      await transport().invoke("standalone_set_local_node", { enabled });
    } catch (e) {
      toast.show(friendly(e), "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Local node" icon="computer">
      <div className="stack-8">
        <label className="checkbox">
          <input type="checkbox" checked={status.localNode} disabled={busy} onChange={(e) => void toggle(e.target.checked)} />
          Also run a Lycosa node on this computer
        </label>
        <p className="small muted">
          The node (the Lycosa agent) registers with this controller using a node key the app mints for it, reports this
          computer's hardware, and runs tasks with a local runtime such as Ollama if one is installed. It listens on this
          computer only.
        </p>
        {busy && <LinearProgress />}
      </div>
    </Card>
  );
}

function DataCard({ status }: { status: StandaloneStatus }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const backup = async () => {
    setBusy(true);
    try {
      const path = await transport().invoke<string>("standalone_backup_now");
      toast.show(`Backed up to ${path}`);
    } catch (e) {
      toast.show(friendly(e), "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Data" icon="folder">
      <div className="stack-8">
        <p className="small">
          Everything lives in <span className="mono">{status.dataDir}</span>: the database, vectors, the generated secrets
          and the key that encrypts provider credentials. Updating or uninstalling the app leaves it in place.
        </p>
        <p className="small muted">
          Back it up as a whole, with the controller stopped, to keep provider credentials readable. "Back up now" writes a
          database dump (pg_dump) into its backups folder; the newest five are kept, and one is taken automatically before an
          upgrade migrates the database.
        </p>
        {status.runtime && (
          <p className="small muted">
            Bundled: Python {status.runtime.python} · PostgreSQL {status.runtime.postgres} · Qdrant {status.runtime.qdrant} ·
            Lycosa API {status.runtime.backend}
          </p>
        )}
        <div className="row">
          <Button variant="outlined" icon="folder" onClick={() => void transport().invoke("standalone_open_data_dir").catch((e) => toast.show(friendly(e), "error"))}>
            Open data folder
          </Button>
          <Button variant="outlined" icon="backup" busy={busy} disabled={status.phase !== "running"} onClick={() => void backup()}>
            Back up now
          </Button>
        </div>
        <p className="small muted">Sign-in for the local admin: {status.adminEmail} (the app signs you in; the password stays in the data folder).</p>
      </div>
    </Card>
  );
}

interface LogLine {
  ts: string;
  line: string;
}

function LogsCard() {
  const [source, setSource] = useState<string>("supervisor");
  const [lines, setLines] = useState<LogLine[]>([]);
  useEffect(() => {
    let alive = true;
    const load = () =>
      transport()
        .invoke<LogLine[]>("standalone_logs", { service: source, limit: 400 })
        .then((l) => alive && setLines(Array.isArray(l) ? l : []))
        .catch(() => {});
    void load();
    const timer = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [source]);
  return (
    <Card
      title="Logs"
      icon="list"
      actions={
        <select className="select" style={{ width: "auto" }} aria-label="Log source" value={source} onChange={(e) => setSource(e.target.value)}>
          {LOG_SOURCES.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      }
    >
      <div className="logview mono" data-testid="log-view">
        {lines.length === 0 ? <span className="muted">No lines yet.</span> : lines.map((l, i) => <div key={i}>{l.ts ? `${l.ts} ${l.line}` : l.line}</div>)}
      </div>
      <p className="small muted" style={{ marginTop: 6 }}>
        Secrets, tokens and keys are removed before any line is stored or shown.
      </p>
    </Card>
  );
}
