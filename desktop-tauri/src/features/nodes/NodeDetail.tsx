import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import { friendly } from "../../api/errors";
import { heartbeatAge } from "../../api/format";
import { useNode, useRecommendations } from "../../api/queries";
import { NODE_ROLES, nodeHasOllama, nodeRuntimesLabel, type NodeInfo } from "../../api/types";
import { Icon } from "../../components/icons";
import { Bar, Button, Card, IconButton, Spinner, useConfirm } from "../../components/ui";
import { useNav } from "../../state/nav";
import { useIsAdmin } from "../../state/session";
import { useToast } from "../../state/toast";
import { StatusChip } from "./NodesScreen";

export function NodeDetail({ nodeId }: { nodeId: string }) {
  const nav = useNav();
  const node = useNode(nodeId);
  return (
    <div className="page">
      <div className="page-head">
        <IconButton icon="back" label="Back" onClick={nav.back} />
        <h1>{node.data?.name ?? "Node"}</h1>
      </div>
      {node.isPending ? (
        <div className="fill-center">
          <Spinner />
        </div>
      ) : node.isError ? (
        <p className="error-text">Failed to load node: {friendly(node.error)}</p>
      ) : (
        <div className="stack-12" style={{ maxWidth: 1200 }}>
          <IdentityCard node={node.data} />
          {/* two balanced columns on wide windows, one stack on narrow ones */}
          <div className="two-col">
            <div className="stack-12">
              <UsageCard node={node.data} />
              <ProfileCard node={node.data} />
            </div>
            <div className="stack-12">
              <RoleCard node={node.data} />
              <ModelsCard node={node.data} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function IdentityCard({ node }: { node: NodeInfo }) {
  const isAdmin = useIsAdmin();
  const nav = useNav();
  const toast = useToast();
  const queries = useQueryClient();
  const { confirm, element } = useConfirm();
  const [deleting, setDeleting] = useState(false);

  const remove = async () => {
    const ok = await confirm({
      title: `Delete ${node.name}?`,
      message:
        "This removes the node, its metrics, and its task history from the controller. The agent keeps running on the device, and its API key is unbound so it can register again later.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    setDeleting(true);
    try {
      await api.deleteNode(node.id);
      await queries.invalidateQueries({ queryKey: ["nodes"] });
      nav.back();
      toast.show(`${node.name} deleted`);
    } catch (e) {
      toast.show(friendly(e), "error");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <Card>
      <div className="row top" style={{ gap: 16 }}>
        <div className="center" style={{ width: 44, height: 44, borderRadius: 12, background: "var(--primary-light)" }}>
          <Icon name="nodes" size={24} />
        </div>
        <div className="grow">
          <h2>{node.name}</h2>
          <p className="small muted ellipsis">
            {node.id}
            {node.agent_url ? `  ·  ${node.agent_url}` : ""}
          </p>
        </div>
        <div className="col" style={{ alignItems: "flex-end", gap: 6 }}>
          <StatusChip status={node.status} />
          <span className="small muted">Usage updated {heartbeatAge(node.last_heartbeat_at)}</span>
        </div>
        {isAdmin && <IconButton icon="delete" label="Delete node" danger busy={deleting} onClick={() => void remove()} />}
      </div>
      {element}
    </Card>
  );
}

function num(metrics: Record<string, unknown> | null | undefined, key: string): number | null {
  const v = metrics?.[key];
  return typeof v === "number" ? v : null;
}

function UsageCard({ node }: { node: NodeInfo }) {
  const m = node.metrics;
  return (
    <Card title="Usage" icon="pulse">
      {!m ? (
        <p className="muted">No usage data received yet.</p>
      ) : (
        <div className="stack-8">
          <Bar label="CPU" percent={num(m, "cpu_percent")} />
          <Bar label="RAM" percent={num(m, "ram_percent")} />
          <Bar label="Disk" percent={num(m, "disk_percent")} />
          <dl className="kv">
            <dt>Running tasks</dt>
            <dd>{String(m["running_tasks"] ?? 0)}</dd>
          </dl>
          <GpuUsage metrics={m} />
        </div>
      )}
    </Card>
  );
}

/** Per-GPU usage from the latest heartbeat (Sprint 12), with the legacy
 * field aliases. */
export function GpuUsage({ metrics }: { metrics: Record<string, unknown> }) {
  const gpus = Array.isArray(metrics["gpus"]) ? (metrics["gpus"] as Record<string, unknown>[]) : [];
  if (gpus.length === 0) return <p>GPU: Not Available</p>;
  return (
    <div className="stack-8">
      {gpus.map((gpu, index) => {
        const used = gpu["memory_used_mb"];
        const total = gpu["memory_total_mb"];
        const temp = gpu["temperature_c"] ?? gpu["temp_c"];
        const util = (gpu["utilization_percent"] ?? gpu["util_percent"]) as number | undefined;
        return (
          <div key={index} className="stack-8">
            <p>
              GPU {String(gpu["index"] ?? index)}: {String(gpu["name"] ?? "Unknown GPU")}
            </p>
            <Bar label="GPU" percent={typeof util === "number" ? util : null} />
            <Bar label="VRAM" percent={typeof gpu["memory_percent"] === "number" ? (gpu["memory_percent"] as number) : null} />
            {typeof used === "number" && typeof total === "number" && (
              <p>
                {(used / 1024).toFixed(1)} / {(total / 1024).toFixed(1)} GB VRAM
              </p>
            )}
            {typeof temp === "number" && <p>GPU Temp: {temp}°C</p>}
          </div>
        );
      })}
    </div>
  );
}

function ProfileCard({ node }: { node: NodeInfo }) {
  const profile = node.hardware_profile ?? {};
  const gpus = Array.isArray(profile["gpus"]) ? (profile["gpus"] as Record<string, unknown>[]) : [];
  return (
    <Card title="Hardware profile" icon="memory">
      <dl className="kv">
        <dt>CPU</dt>
        <dd>
          {String(profile["cpu_model"] ?? "—")} ({node.cpu_cores ?? "—"} cores)
        </dd>
        <dt>RAM</dt>
        <dd>{node.ram_gb != null ? `${node.ram_gb} GB` : "—"}</dd>
        <dt>GPU</dt>
        <dd>{gpus.length === 0 ? "none" : gpus.map((g) => `${String(g["model"])} (${String(g["vram_gb"])} GB)`).join(", ")}</dd>
        <dt>Storage</dt>
        <dd>{node.storage_gb != null ? `${node.storage_gb} GB` : "—"}</dd>
        <dt>OS</dt>
        <dd>{node.os_name ?? "—"}</dd>
        <dt>Runtimes</dt>
        <dd>{nodeRuntimesLabel(node)}</dd>
      </dl>
    </Card>
  );
}

function RoleCard({ node }: { node: NodeInfo }) {
  const toast = useToast();
  const queries = useQueryClient();
  const [selected, setSelected] = useState<string>(node.role ?? node.recommended_role ?? "");
  const [saving, setSaving] = useState(false);
  const confidence = Math.round((node.recommendation_confidence ?? 0) * 100);

  const save = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      await api.patchNode(node.id, { role: selected });
      await queries.invalidateQueries({ queryKey: ["nodes", node.id] });
      toast.show(`Role set to ${selected}`);
    } catch (e) {
      toast.show(friendly(e), "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title="Role" icon="badge">
      <dl className="kv">
        <dt>Assigned</dt>
        <dd>{node.role ?? "not assigned"}</dd>
        <dt>Recommended</dt>
        <dd>{node.recommended_role ? `${node.recommended_role} (${confidence}% confidence)` : "—"}</dd>
      </dl>
      {!!node.recommendation_rationale?.length && (
        <div style={{ marginTop: 8 }}>
          <p className="small muted">Why:</p>
          <ul style={{ margin: "2px 0 0", paddingLeft: 22 }}>
            {node.recommendation_rationale.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="row" style={{ marginTop: 12, alignItems: "flex-end" }}>
        <label className="field grow">
          <span className="small muted">Set role</span>
          <select className="select" value={selected} onChange={(e) => setSelected(e.target.value)}>
            <option value="" disabled>
              choose a role
            </option>
            {NODE_ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <Button busy={saving} onClick={() => void save()}>
          Save
        </Button>
      </div>
    </Card>
  );
}

/** Hardware-fit model recommendations with one-click install: the node's
 * runtime pulls the weights (this can take many minutes). */
function ModelsCard({ node }: { node: NodeInfo }) {
  const recs = useRecommendations(node.id);
  const toast = useToast();
  const queries = useQueryClient();
  const [installing, setInstalling] = useState<string | null>(null);
  const online = node.status === "online";
  const ollama = nodeHasOllama(node);

  const install = async (model: string) => {
    setInstalling(model);
    try {
      await api.installNodeModel(node.id, model);
      toast.show(`${model} installed on ${node.name}`);
    } catch (e) {
      toast.show(friendly(e), "error");
    } finally {
      void queries.invalidateQueries({ queryKey: ["nodes", node.id] });
      setInstalling(null);
    }
  };

  return (
    <Card title="Recommended models" icon="sparkle">
      {recs.isPending ? (
        <Spinner />
      ) : recs.isError ? (
        <p className="error-text">Failed: {friendly(recs.error)}</p>
      ) : (
        <div className="stack-8">
          {!online ? (
            <p className="small muted">Node is offline — bring the agent online to install models.</p>
          ) : (
            !ollama && <p className="small muted">Ollama is not detected on this node. Install it from ollama.com and restart the agent to run local models.</p>
          )}
          {recs.data.map((rec) => (
            <div key={rec.model} className="row top">
              <span style={{ color: rec.installed ? "var(--success)" : rec.recommended ? "var(--warning)" : "var(--text-2)" }}>
                <Icon name={rec.installed ? "check" : rec.recommended ? "star" : rec.runnable ? "circle" : "block"} />
              </span>
              <div className="grow">
                <div>
                  {rec.model} · {rec.use_case}
                  {rec.runs_on ? ` · runs on ${rec.runs_on}` : ""}
                  {rec.recommended ? " · best fit" : ""}
                </div>
                <div className="small muted">{rec.reason}</div>
              </div>
              {rec.installed ? (
                <span className="small">installed</span>
              ) : (
                rec.runnable && (
                  <Button
                    variant="outlined"
                    busy={installing === rec.model}
                    disabled={!online || !ollama || installing !== null}
                    onClick={() => void install(rec.model)}
                  >
                    Install
                  </Button>
                )
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
