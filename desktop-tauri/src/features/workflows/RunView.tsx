import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import { friendly } from "../../api/errors";
import { humanize } from "../../api/format";
import { useRun } from "../../api/queries";
import { runFinished, type StepRun, type WorkflowRun } from "../../api/types";
import { Icon } from "../../components/icons";
import { Button, Card, IconButton, Spinner } from "../../components/ui";
import { useNav } from "../../state/nav";
import { useToast } from "../../state/toast";

const RUN_LOOK: Record<string, [string, string]> = {
  succeeded: ["var(--success)", "check"],
  failed: ["var(--error)", "warning"],
  paused: ["var(--warning)", "pause"],
};

const STEP_LOOK: Record<string, [string, string]> = {
  succeeded: ["var(--success)", "check"],
  failed: ["var(--error)", "close"],
  skipped: ["var(--text-2)", "swap"],
  pending_approval: ["var(--warning)", "pause"],
};

/** One workflow run, polled every 3 s until it finishes, with the approval
 * bar while it waits. */
export function RunView({ workflowId, runId, workflowName }: { workflowId: string; runId: string; workflowName: string }) {
  const nav = useNav();
  const run = useRun(workflowId, runId);
  return (
    <div className="page">
      <div className="page-head">
        <IconButton icon="back" label="Back" onClick={nav.back} />
        <h1>{workflowName} — run</h1>
      </div>
      {run.isPending ? (
        <Spinner />
      ) : run.isError ? (
        <p className="error-text">Failed to load run: {friendly(run.error)}</p>
      ) : (
        <div className="stack-12" style={{ maxWidth: 1000 }}>
          <StatusBanner run={run.data} />
          <h2>Steps</h2>
          <div className="stack-8">
            {(run.data.step_runs ?? []).map((s) => (
              <StepTile key={`${s.step_id}-${s.attempt}`} step={s} />
            ))}
          </div>
          {run.data.status === "paused" && <ApprovalBar workflowId={workflowId} runId={runId} />}
        </div>
      )}
    </div>
  );
}

function StatusBanner({ run }: { run: WorkflowRun }) {
  const [color, icon] = RUN_LOOK[run.status] ?? ["var(--primary)", "refresh"];
  return (
    <Card>
      <div className="row top">
        <span style={{ color }}>
          <Icon name={icon} />
        </span>
        <div className="grow">
          <p>
            Status: {humanize(run.status)}
            {run.current_step && !runFinished(run) ? ` (at ${run.current_step})` : ""}
          </p>
          <p className="small muted pre-wrap">Input: {run.input}</p>
          {run.error && <p className="error-text pre-wrap">{run.error}</p>}
        </div>
      </div>
    </Card>
  );
}

function StepTile({ step }: { step: StepRun }) {
  const [color, icon] = STEP_LOOK[step.status] ?? ["var(--primary)", "refresh"];
  const subtitle = `${step.kind} · ${humanize(step.status)}${step.attempt > 1 ? ` · attempt ${step.attempt}` : ""}`;
  const head = (
    <>
      <span style={{ color }}>
        <Icon name={icon} />
      </span>
      <div className="grow">
        <div>{step.step_id}</div>
        <div className="small muted">{subtitle}</div>
      </div>
    </>
  );
  if (!step.output && !step.error) {
    return <div className="card row">{head}</div>;
  }
  return (
    <details className="expander">
      <summary>{head}</summary>
      <div className="expander-body">
        {step.output && <p className="pre-wrap" style={{ userSelect: "text" }}>{step.output}</p>}
        {step.error && <p className="pre-wrap error-text">{step.error}</p>}
      </div>
    </details>
  );
}

function ApprovalBar({ workflowId, runId }: { workflowId: string; runId: string }) {
  const queries = useQueryClient();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const resolve = async (approved: boolean) => {
    setBusy(true);
    try {
      // approving runs the next steps before the controller replies
      await api.approveRun(workflowId, runId, approved);
    } catch (e) {
      toast.show(friendly(e), "error");
    } finally {
      void queries.invalidateQueries({ queryKey: ["runs", workflowId, runId] });
      setBusy(false);
    }
  };
  return (
    <div className="card surface-2 row">
      <span style={{ color: "var(--warning)" }}>
        <Icon name="pause" />
      </span>
      <span className="grow">This run is paused waiting for your approval.</span>
      <Button variant="outlined" disabled={busy} onClick={() => void resolve(false)}>
        Reject
      </Button>
      <Button busy={busy} onClick={() => void resolve(true)}>
        Approve
      </Button>
    </div>
  );
}
