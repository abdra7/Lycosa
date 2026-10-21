import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import { friendly, isUnreachable } from "../../api/errors";
import { statusColor } from "../../api/format";
import { useLlmAccounts, useLlmRouting, useTasks } from "../../api/queries";
import {
  accountIsActive,
  accountIsNodeRun,
  routeFor,
  taskOutput,
  taskPrompt,
  type LlmAccount,
  type TaskInfo,
} from "../../api/types";
import { Button, LinearProgress, OutputBlock, Spinner, StatusDot, TextField } from "../../components/ui";
import { PhantomDialog } from "./PhantomDialog";
import { PROVIDER_LABELS } from "./providerLabels";

const TASK_TYPES = ["auto", "coding", "retrieval", "tool", "vision", "general"];
const ACCOUNT_PREFIX = "account:";

/** Universal LLM layer routes (ADR-031); "node" keeps the provider choice. */
const LLM_ROUTES: [string, string][] = [
  ["node", "Provider above (node / legacy route)"],
  ["auto", "LLM route: auto (by task type)"],
  ["default", "LLM route: default model"],
  ["coding", "LLM route: coding"],
  ["reasoning", "LLM route: reasoning"],
  ["vision", "LLM route: vision"],
  ["cheap", "LLM route: cheap"],
  ["private", "LLM route: private (local only)"],
  ["offline", "LLM route: offline (local only)"],
];

const OPENROUTER_FREE_MODEL = "nvidia/nemotron-3.5-lightning:free";

export function TasksScreen() {
  const tasks = useTasks();
  const [phantom, setPhantom] = useState(false);
  return (
    <div className="page stack-12">
      <h1>Tasks</h1>
      <Button variant="outlined" icon="privacy" onClick={() => setPhantom(true)}>
        Phantom task (no history)
      </Button>
      <SubmitCard />
      <h2>Recent</h2>
      {tasks.isPending ? (
        <div className="center" style={{ padding: 24 }}>
          <Spinner />
        </div>
      ) : tasks.isError ? (
        <p className="error-text">Failed to load tasks: {friendly(tasks.error)}</p>
      ) : tasks.data.length === 0 ? (
        <p className="muted center" style={{ padding: 24 }}>
          No tasks yet.
        </p>
      ) : (
        <div className="stack-8">
          {tasks.data.map((t) => (
            <TaskTile key={t.id} task={t} />
          ))}
        </div>
      )}
      {phantom && <PhantomDialog onClose={() => setPhantom(false)} />}
    </div>
  );
}

function SubmitCard() {
  const queries = useQueryClient();
  const accountsQuery = useLlmAccounts();
  const routing = useLlmRouting();
  const catalog = useQuery({ queryKey: ["providers-catalog"], queryFn: api.providerCatalog, retry: false });
  const providers = catalog.data?.length ? catalog.data.map((p) => String(p["name"])) : Object.keys(PROVIDER_LABELS);

  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("");
  const [knowledgeQuery, setKnowledgeQuery] = useState("");
  const [knowledgeCollection, setKnowledgeCollection] = useState("");
  const [type, setType] = useState("auto");
  const [provider, setProvider] = useState("ollama");
  /** The route the user picked; null until they pick one. */
  const [chosenRoute, setChosenRoute] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TaskInfo | null>(null);

  const accounts = (accountsQuery.data ?? []).filter((a) => a.usable && accountIsActive(a));
  // a configured default model beats the node/Ollama route, which needs a
  // node with Ollama (ADR-031: personal, then deployment) — unless the user
  // already picked a route
  const hasDefault = [routeFor(routing.data, "default", "personal"), routeFor(routing.data, "default", "deployment")].some(
    (r) => r && r.chain.length > 0,
  );
  let route = chosenRoute ?? (hasDefault ? "default" : "node");
  const account: LlmAccount | undefined = route.startsWith(ACCOUNT_PREFIX)
    ? accounts.find((a) => `${ACCOUNT_PREFIX}${a.id}` === route)
    : undefined;
  // the chosen account went away: back to the node route
  if (route.startsWith(ACCOUNT_PREFIX) && accountsQuery.isSuccess && !account) route = "node";

  const submit = async () => {
    if (!prompt.trim()) return;
    let chosenModel: string | null = model.trim() || null;
    if (account && !chosenModel) {
      if (!accountIsNodeRun(account)) {
        setError(`Enter the model to use with ${account.label}.`);
        return;
      }
      chosenModel = "default"; // the plan's default model
    }
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const task = await api.submitTask({
        prompt: prompt.trim(),
        provider,
        route: route === "node" || account ? null : route,
        llmAccountId: account?.id ?? null,
        knowledgeCollection: knowledgeCollection.trim() || null,
        type: type === "auto" ? null : type,
        model: chosenModel,
        knowledgeQuery: knowledgeQuery.trim() || null,
      });
      setResult(task);
      void queries.invalidateQueries({ queryKey: ["tasks"] });
    } catch (e) {
      // the controller records the outcome even when the connection drops
      setError(isUnreachable(e) ? `${friendly(e)} — the task may still be running; watch Recent below.` : friendly(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card stack-12">
      <label className="field">
        <span className="small muted">Provider</span>
        <select
          className="select"
          value={provider}
          disabled={busy || route !== "node"}
          onChange={(e) => {
            setProvider(e.target.value);
            setModel(e.target.value === "openrouter" ? OPENROUTER_FREE_MODEL : "");
          }}
        >
          {providers.map((p) => (
            <option key={p} value={p}>
              {PROVIDER_LABELS[p] ?? p}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span className="small muted">Route</span>
        <select
          className="select"
          value={route}
          disabled={busy}
          onChange={(e) => {
            setChosenRoute(e.target.value);
            setModel("");
          }}
        >
          {LLM_ROUTES.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
          {accounts.map((a) => (
            <option key={a.id} value={`${ACCOUNT_PREFIX}${a.id}`}>
              Account: {a.provider_name} · {a.label}
            </option>
          ))}
        </select>
      </label>
      {account ? (
        <p className="small">
          {accountIsNodeRun(account)
            ? `Runs Claude Code on ${account.node_name ?? "your node"} with your own Claude plan. Model: sonnet, opus, haiku, or blank for the plan default.`
            : "Runs on this account only (no fallback). Enter the model to use."}
        </p>
      ) : (
        route !== "node" && (
          <p className="small">The route picks the account and model (with fallbacks) configured under Providers.</p>
        )
      )}
      {provider !== "ollama" && route === "node" && (
        <p className="small">
          External provider: prompt and retrieved context leave your machine. Configure the API key in Admin → Legacy
          provider keys.
        </p>
      )}
      <TextField label="Prompt" value={prompt} onChange={setPrompt} multiline rows={3} placeholder="What should the fabric do?" />
      <div className="row wrap" style={{ alignItems: "flex-end" }}>
        <label className="field" style={{ width: 160 }}>
          <span className="small muted">Type</span>
          <select className="select" value={type} onChange={(e) => setType(e.target.value)}>
            {TASK_TYPES.map((t) => (
              <option key={t} value={t}>
                type: {t}
              </option>
            ))}
          </select>
        </label>
        <div className="grow">
          <TextField label="Model (optional)" value={model} onChange={setModel} />
        </div>
        <div className="grow">
          <TextField label="Knowledge query (optional)" value={knowledgeQuery} onChange={setKnowledgeQuery} />
        </div>
        <Button icon="play" busy={busy} onClick={() => void submit()}>
          Run task
        </Button>
      </div>
      <TextField
        label="Knowledge collection (optional name; blank searches all collections)"
        value={knowledgeCollection}
        onChange={setKnowledgeCollection}
      />
      {busy && <LinearProgress />}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {result && <ResultPanel task={result} />}
    </section>
  );
}

function ResultPanel({ task }: { task: TaskInfo }) {
  const output = taskOutput(task);
  return (
    <div className="stack-8" style={{ border: `1px solid ${statusColor(task.status)}`, borderRadius: 12, padding: 12 }}>
      <div className="row">
        <StatusDot color={statusColor(task.status)} />
        <span>
          {task.status} · {task.type}
        </span>
      </div>
      <OutputBlock text={output ?? task.error ?? "(no output)"} isError={output == null && task.error != null} />
    </div>
  );
}

function TaskTile({ task }: { task: TaskInfo }) {
  const output = taskOutput(task);
  const executions = task.executions ?? [];
  return (
    <details className="expander">
      <summary>
        <StatusDot color={statusColor(task.status)} />
        <div className="grow" style={{ minWidth: 0 }}>
          <div className="ellipsis">{taskPrompt(task)}</div>
          <div className="small muted">
            {task.status} · {task.type}
            {executions.length > 0 ? ` · ${executions.length} attempt(s)` : ""}
          </div>
        </div>
      </summary>
      <div className="expander-body stack-8">
        {output != null && (
          <>
            <p className="small muted">Output</p>
            <OutputBlock text={output} />
          </>
        )}
        {task.error && (
          <>
            <p className="small muted">Error</p>
            <OutputBlock text={task.error} isError />
          </>
        )}
        {executions.length > 0 && (
          <div>
            <p className="small muted">Execution trace</p>
            {executions.map((x) => (
              <p key={`${x.node_id}-${x.attempt}`}>
                attempt {x.attempt}: {x.status}
                {x.error ? ` — ${x.error}` : ""}
              </p>
            ))}
          </div>
        )}
      </div>
    </details>
  );
}
