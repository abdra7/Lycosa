import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { friendly } from "../../api/errors";
import { heartbeatAge, percent, statusColor } from "../../api/format";
import { useNodes } from "../../api/queries";
import type { NodeInfo } from "../../api/types";
import { Icon } from "../../components/icons";
import { Button, Chip, IconButton, Segmented, Spinner, StatusDot } from "../../components/ui";
import { transport } from "../../ipc/transport";
import { useNav } from "../../state/nav";
import { useIsAdmin } from "../../state/session";
import { AddNodeDialog } from "./AddNodeDialog";
import { NodesGraph } from "./NodesGraph";

export function StatusChip({ status }: { status: string }) {
  return (
    <Chip small>
      <StatusDot color={statusColor(status)} />
      {status}
    </Chip>
  );
}

export function NodesScreen() {
  const nodes = useNodes();
  const queries = useQueryClient();
  const isAdmin = useIsAdmin();
  const [view, setView] = useState<"list" | "graph">("list");
  const [adding, setAdding] = useState(false);
  const list = nodes.data ?? [];

  return (
    <div className="page col" style={{ height: "100%", gap: 12 }}>
      <div className="page-head" style={{ marginBottom: 0 }}>
        <h1>Nodes</h1>
        <Segmented
          value={view}
          onChange={setView}
          options={[
            { value: "list", label: "List", icon: "list" },
            { value: "graph", label: "Graph", icon: "hub" },
          ]}
        />
        <span className="spacer" />
        <IconButton icon="refresh" label="Refresh" onClick={() => void queries.invalidateQueries({ queryKey: ["nodes"] })} />
        {isAdmin && (
          <Button icon="add" onClick={() => setAdding(true)}>
            Add node
          </Button>
        )}
      </div>
      <div className="grow" style={{ minHeight: 0, overflow: "auto" }}>
        {nodes.isPending ? (
          <div className="fill-center">
            <Spinner />
          </div>
        ) : nodes.isError ? (
          <div className="fill-center error-text">Failed to load nodes: {friendly(nodes.error)}</div>
        ) : list.length === 0 ? (
          <div className="fill-center muted">No nodes yet. Add one and run lycosa-agent on the machine.</div>
        ) : view === "list" ? (
          <NodesTable nodes={list} />
        ) : (
          <NodesGraph nodes={list} />
        )}
      </div>
      <DiscoveryPanel registered={new Map(list.map((n) => [n.name, n.id]))} onAdd={() => setAdding(true)} />
      {adding && <AddNodeDialog onClose={() => setAdding(false)} />}
    </div>
  );
}

function NodesTable({ nodes }: { nodes: NodeInfo[] }) {
  const nav = useNav();
  return (
    <table className="table">
      <thead>
        <tr>
          <th>Name</th>
          <th>Status</th>
          <th>Role</th>
          <th>Recommended</th>
          <th>Usage</th>
          <th>CPU</th>
          <th>RAM</th>
        </tr>
      </thead>
      <tbody>
        {nodes.map((n) => (
          <tr
            key={n.id}
            className="clickable"
            tabIndex={0}
            onClick={() => nav.push({ kind: "node", nodeId: n.id })}
            onKeyDown={(e) => e.key === "Enter" && nav.push({ kind: "node", nodeId: n.id })}
          >
            <td>{n.name}</td>
            <td>
              <StatusChip status={n.status} />
            </td>
            <td>{n.role ?? "—"}</td>
            <td>{n.recommended_role ? `${n.recommended_role} (${Math.round((n.recommendation_confidence ?? 0) * 100)}%)` : "—"}</td>
            <td>{heartbeatAge(n.last_heartbeat_at)}</td>
            <td>{percent(n.metrics?.["cpu_percent"])}</td>
            <td>{percent(n.metrics?.["ram_percent"])}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

interface DiscoveredAgent {
  name: string;
  address: string;
  port: number;
  version: string | null;
}

/** "Discovered on LAN": an mDNS scan for machines running lycosa-agent
 * (ADR-018), only when the operator asks, so the app never sends multicast
 * traffic unprompted. */
export function DiscoveryPanel({ registered, onAdd }: { registered: Map<string, string>; onAdd: () => void }) {
  const nav = useNav();
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [found, setFound] = useState<DiscoveredAgent[] | null>(null);

  const scan = async () => {
    setScanning(true);
    setError(null);
    try {
      setFound(await transport().invoke<DiscoveredAgent[]>("lan_scan"));
    } catch (e) {
      setError(`${friendly(e)} — mDNS needs UDP 5353 allowed on this machine.`);
    } finally {
      setScanning(false);
    }
  };

  return (
    <section className="col" style={{ gap: 6 }}>
      <div className="row">
        <h2>Discovered on LAN</h2>
        <Button variant="outlined" icon="radar" busy={scanning} onClick={() => void scan()}>
          Scan
        </Button>
      </div>
      {error ? (
        <p className="error-text">{error}</p>
      ) : found === null ? (
        <p className="muted">Scan finds machines running lycosa-agent with discovery enabled (mDNS, UDP 5353).</p>
      ) : found.length === 0 ? (
        <p className="muted">
          No agents found. Check that lycosa-agent is running on the device and the firewall allows UDP 5353 (mDNS) plus
          TCP 8010 (agent exec API).
        </p>
      ) : (
        <div style={{ maxHeight: 160, overflow: "auto" }}>
          {found.map((agent) => {
            const nodeId = registered.get(agent.name);
            return (
              <div
                key={`${agent.name}@${agent.address}:${agent.port}`}
                className="list-item clickable"
                role="button"
                tabIndex={0}
                onClick={() => (nodeId ? nav.push({ kind: "node", nodeId }) : onAdd())}
              >
                <span style={{ color: nodeId ? "var(--success)" : "var(--warning)" }}>
                  <Icon name={nodeId ? "check" : "info"} />
                </span>
                <div className="grow">
                  <div>{agent.name}</div>
                  <div className="small muted">
                    {agent.address}:{agent.port}
                    {agent.version ? ` · v${agent.version}` : ""}
                  </div>
                </div>
                <span className="small muted">
                  {nodeId ? "registered — click for details & model setup" : "not registered — click to add a node key"}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
