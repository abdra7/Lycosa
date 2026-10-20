import { useCallback, useEffect, useState } from "react";

import { api } from "../../api/client";
import { friendly } from "../../api/errors";
import { capitalize } from "../../api/format";
import { claudeNodeReady, planWindowLabel, type ClaudeNode, type LlmProvider } from "../../api/types";
import { Icon } from "../../components/icons";
import { Button, Dialog, IconButton, LinearProgress, Spinner, StatusDot, TextField, useCopy } from "../../components/ui";

export const CLAUDE_SIGN_IN_COMMAND = "claude auth login";

/** Connect your Claude plan through a node's own Claude Code (ADR-032).
 * Lycosa never signs in to Claude and never sees a Claude credential: you
 * sign in on the node with Anthropic's own flow, and the node runs Claude
 * Code for you only. */
export function ClaudeSubscriptionDialog({ provider, onClose }: { provider: LlmProvider; onClose: (added: boolean) => void }) {
  const [label, setLabel] = useState("My Claude");
  const [nodes, setNodes] = useState<ClaudeNode[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const node = nodes?.find((n) => n.node_id === selected) ?? null;

  /** Ask every capable node, live, whether Claude Code is ready for you. */
  const fetchNodes = useCallback(
    () =>
      api
        .listClaudeSubscriptionNodes()
        .then((list) => {
          setNodes(list);
          setSelected((current) =>
            list.some((n) => n.node_id === current) ? current : (list.find(claudeNodeReady) ?? list[0])?.node_id ?? null,
          );
        })
        .catch((e) => setError(friendly(e)))
        .finally(() => setLoading(false)),
    [],
  );

  const load = () => {
    setLoading(true);
    setError(null);
    void fetchNodes();
  };

  useEffect(() => {
    void fetchNodes();
  }, [fetchNodes]);

  const connect = async () => {
    if (!node) return;
    setBusy(true);
    setError(null);
    try {
      await api.connectLlmAccount({ provider: provider.id, label: label.trim(), nodeId: node.node_id });
      onClose(true);
    } catch (e) {
      setError(friendly(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title="Connect your Claude plan"
      onClose={() => onClose(false)}
      width={580}
      actions={
        <>
          <Button variant="text" disabled={busy} onClick={() => onClose(false)}>
            Cancel
          </Button>
          <Button busy={busy} disabled={!node || !claudeNodeReady(node)} onClick={() => void connect()}>
            Connect
          </Button>
        </>
      }
    >
      <div className="stack-12">
        <p>
          A node runs Claude Code, signed in with your own Claude plan. Lycosa never sees your Claude sign-in, and only you
          can use this connection. Calls count toward your plan's usage limits.
        </p>
        <div className="row">
          <h3>Nodes offering Claude</h3>
          <span className="spacer" />
          <Button variant="text" icon="refresh" disabled={loading} onClick={load}>
            Re-check
          </Button>
        </div>
        {loading && nodes === null ? (
          <div className="center">
            <Spinner />
          </div>
        ) : nodes && nodes.length === 0 ? (
          <EnableHelp />
        ) : (
          (nodes ?? []).map((n) => <NodeTile key={n.node_id} node={n} selected={n.node_id === selected} onSelect={() => setSelected(n.node_id)} />)
        )}
        {loading && nodes !== null && <LinearProgress />}
        {node && node.reachable && !node.signed_in && <SignInHelp nodeName={node.node_name} />}
        <TextField label="Name" value={label} onChange={setLabel} />
        <p className="small muted">Personal connection: it cannot be shared or used in deployment routes.</p>
        {error && <p className="error-text">{error}</p>}
      </div>
    </Dialog>
  );
}

function nodeState(n: ClaudeNode): [string, string] {
  if (!n.reachable) return ["var(--error)", n.detail || "Unreachable"];
  if (!n.installed) return ["var(--error)", "Claude Code is not installed"];
  if (!n.owner_matches) return ["var(--error)", "Serves another Lycosa user"];
  if (!n.signed_in) return ["var(--warning)", "Not signed in"];
  const plan = n.plan ? ` · ${capitalize(n.plan)} plan` : "";
  const how = n.auth_method === "api_key" ? " (API key)" : "";
  return ["var(--success)", `Signed in${plan}${how}`];
}

function NodeTile({ node, selected, onSelect }: { node: ClaudeNode; selected: boolean; onSelect: () => void }) {
  const [color, text] = nodeState(node);
  const usage = Object.entries(node.plan_usage ?? {})
    .filter(([, w]) => typeof w?.utilization === "number")
    .map(([name, w]) => `${planWindowLabel(name)}: ${Math.round((w.utilization as number) * 100)}% used`);
  return (
    <div
      className="list-item clickable"
      role="radio"
      aria-checked={selected}
      tabIndex={0}
      onClick={onSelect}
      style={{ border: `2px solid ${selected ? "var(--primary)" : "var(--border)"}` }}
    >
      <Icon name={selected ? "check" : "circle"} />
      <div className="grow">
        <div>{node.node_name}</div>
        <div className="small muted pre-wrap">
          {[text, node.version ? `Claude Code ${node.version}` : null, usage.length ? `Plan usage · ${usage.join(" · ")}` : null]
            .filter(Boolean)
            .join("\n")}
        </div>
      </div>
      <StatusDot color={color} />
    </div>
  );
}

function SignInHelp({ nodeName }: { nodeName: string }) {
  return (
    <div className="card surface-2 stack-8">
      <h3>Sign in on {nodeName}</h3>
      <p>
        Open a terminal on that machine and run the command below. Anthropic's own sign-in opens in the browser; Lycosa
        is not involved. Then press Re-check.
      </p>
      <CopyableCommand command={CLAUDE_SIGN_IN_COMMAND} />
    </div>
  );
}

function EnableHelp() {
  return (
    <div className="stack-8">
      <p>No node offers Claude yet. On the machine that should run it:</p>
      <p>1. Install Claude Code and sign in:</p>
      <CopyableCommand command={CLAUDE_SIGN_IN_COMMAND} />
      <p>2. Set these for its Lycosa agent, then restart the agent:</p>
      <CopyableCommand command={"LYCOSA_CLAUDE_SUBSCRIPTION=true\nLYCOSA_CLAUDE_SUBSCRIPTION_OWNER=<your Lycosa email>"} />
    </div>
  );
}

function CopyableCommand({ command }: { command: string }) {
  const copy = useCopy();
  return (
    <div className="row">
      <pre className="code mono grow" style={{ margin: 0 }}>
        {command}
      </pre>
      <IconButton icon="copy" label="Copy" onClick={() => void copy(command)} />
    </div>
  );
}
