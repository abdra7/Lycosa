import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import { friendly } from "../../api/errors";
import { useWorkflows } from "../../api/queries";
import { workflowSteps, type WorkflowInfo } from "../../api/types";
import { Icon } from "../../components/icons";
import { Button, Dialog, IconButton, LinearProgress, Spinner, TextField, useConfirm } from "../../components/ui";
import { useNav } from "../../state/nav";
import { useToast } from "../../state/toast";

export const DEFINITION_TEMPLATE = `{
  "steps": [
    {"id": "plan", "kind": "task", "prompt": "Plan how to: {{input}}"},
    {"id": "gate", "kind": "approval", "message": "Review the plan"},
    {"id": "do", "kind": "task", "prompt": "Execute:\\n{{steps.plan.output}}"}
  ]
}`;

const OPENROUTER_TEMPLATE = JSON.stringify({
  steps: [
    { id: "plan", kind: "task", provider: "openrouter", model: "nvidia/nemotron-3.5-lightning:free", prompt: "Plan how to: {{input}}" },
    { id: "gate", kind: "approval", message: "Review before continuing" },
    { id: "answer", kind: "task", provider: "openrouter", model: "nvidia/nemotron-3.5-lightning:free", prompt: "Explain this plan: {{steps.plan.output}}" },
  ],
});

export function WorkflowsScreen() {
  const workflows = useWorkflows();
  const queries = useQueryClient();
  const toast = useToast();
  const { confirm, element } = useConfirm();
  const [creating, setCreating] = useState(false);
  const [viewing, setViewing] = useState<WorkflowInfo | null>(null);
  const [running, setRunning] = useState<WorkflowInfo | null>(null);

  const remove = async (w: WorkflowInfo) => {
    const ok = await confirm({
      title: "Delete workflow?",
      message: `"${w.name}" and its run history will be permanently deleted. Tasks its steps ran stay under Tasks. This cannot be undone.`,
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteWorkflow(w.id);
      toast.show(`Deleted ${w.name}`);
    } catch (e) {
      // a 409 explains that a run is still running or awaiting approval
      toast.show(friendly(e), "error");
    } finally {
      void queries.invalidateQueries({ queryKey: ["workflows"] });
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <h1>Workflows</h1>
        <span className="spacer" />
        <IconButton icon="refresh" label="Refresh" onClick={() => void queries.invalidateQueries({ queryKey: ["workflows"] })} />
        <Button icon="add" onClick={() => setCreating(true)}>
          New workflow
        </Button>
      </div>
      {workflows.isPending ? (
        <div className="center" style={{ padding: 24 }}>
          <Spinner />
        </div>
      ) : workflows.isError ? (
        <p className="error-text">Failed to load: {friendly(workflows.error)}</p>
      ) : workflows.data.length === 0 ? (
        <p className="muted center" style={{ padding: 24 }}>
          No workflows defined yet.
        </p>
      ) : (
        <div className="stack-8">
          {workflows.data.map((w) => (
            <div key={w.id} className="card row" style={{ padding: "10px 14px" }}>
              <Icon name="workflows" />
              <div className="grow">
                <div>{w.name}</div>
                <div className="small muted">{w.description ?? `${workflowSteps(w).length} steps`}</div>
              </div>
              <Button variant="text" icon="view" onClick={() => setViewing(w)}>
                View
              </Button>
              <Button variant="tonal" icon="play" onClick={() => setRunning(w)}>
                Run
              </Button>
              <IconButton icon="delete" label="Delete workflow" onClick={() => void remove(w)} />
            </div>
          ))}
        </div>
      )}
      {creating && <CreateWorkflowDialog onClose={() => setCreating(false)} />}
      {viewing && <DefinitionDialog workflow={viewing} onClose={() => setViewing(null)} />}
      {running && <RunInputDialog workflow={running} onClose={() => setRunning(null)} />}
      {element}
    </div>
  );
}

/** Read-only definition: the steps in order, then the stored JSON. */
function DefinitionDialog({ workflow, onClose }: { workflow: WorkflowInfo; onClose: () => void }) {
  const steps = workflowSteps(workflow);
  return (
    <Dialog
      title={workflow.name}
      onClose={onClose}
      width={680}
      actions={
        <Button variant="text" onClick={onClose}>
          Close
        </Button>
      }
    >
      <div className="stack-8">
        {workflow.description && <p>{workflow.description}</p>}
        <h3>{steps.length} steps</h3>
        {steps.map((s, i) => (
          <p key={i}>
            {i + 1}. {String(s["id"])} · {String(s["kind"])}
          </p>
        ))}
        <pre className="code mono">{JSON.stringify(workflow.definition, null, 2)}</pre>
      </div>
    </Dialog>
  );
}

function RunInputDialog({ workflow, onClose }: { workflow: WorkflowInfo; onClose: () => void }) {
  const nav = useNav();
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    if (!input.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.runWorkflow(workflow.id, input.trim());
      onClose();
      nav.push({ kind: "run", workflowId: r.workflow_id, runId: r.id, workflowName: workflow.name });
    } catch (e) {
      setError(friendly(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={`Run ${workflow.name}`}
      onClose={onClose}
      width={500}
      actions={
        <>
          <Button variant="text" onClick={onClose}>
            Cancel
          </Button>
          <Button busy={busy} onClick={() => void run()}>
            Run
          </Button>
        </>
      }
    >
      <div className="stack-12">
        <TextField label="Input" value={input} onChange={setInput} multiline rows={3} autoFocus />
        {busy && <LinearProgress />}
        {error && <p className="error-text">{error}</p>}
      </div>
    </Dialog>
  );
}

export function CreateWorkflowDialog({ onClose }: { onClose: () => void }) {
  const queries = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [definition, setDefinition] = useState(DEFINITION_TEMPLATE);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const create = async () => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(definition);
    } catch (e) {
      setError(`Definition is not valid JSON: ${(e as Error).message}`);
      return;
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      setError("Definition is not valid JSON: expected an object");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.createWorkflow(name.trim(), description.trim() || null, parsed as Record<string, unknown>);
      void queries.invalidateQueries({ queryKey: ["workflows"] });
      onClose();
    } catch (e) {
      setError(friendly(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title="New workflow"
      onClose={onClose}
      width={680}
      actions={
        <>
          <Button variant="text" onClick={onClose}>
            Cancel
          </Button>
          <Button busy={busy} onClick={() => void create()}>
            Create
          </Button>
        </>
      }
    >
      <div className="stack-8">
        <Button variant="text" icon="providers" disabled={busy} onClick={() => setDefinition(OPENROUTER_TEMPLATE)}>
          Use OpenRouter free template
        </Button>
        <TextField label="Name" value={name} onChange={setName} />
        <TextField label="Description (optional)" value={description} onChange={setDescription} />
        <TextField label="Definition (JSON)" value={definition} onChange={setDefinition} multiline rows={14} mono />
        {error && <p className="error-text">{error}</p>}
      </div>
    </Dialog>
  );
}
