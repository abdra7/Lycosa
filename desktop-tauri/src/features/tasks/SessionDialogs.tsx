import { useState, type ReactNode } from "react";
import { useMutation, useMutationState } from "@tanstack/react-query";
import { sessionIsRunning, type CliSession } from "../../api/cli";
import { friendly } from "../../api/errors";
import { statusColor } from "../../api/format";
import { useTasks } from "../../api/queries";
import { taskPrompt } from "../../api/types";
import { Button, Dialog, Spinner, StatusDot, TextField } from "../../components/ui";
import { TaskTranscript } from "./TasksScreen";

export function NameDialog({ kind, onClose, onSave }: { kind: "Workspace" | "Session"; onClose: () => void; onSave: (name: string) => Promise<void> }) {
  const [name, setName] = useState("");
  const action = useMutation({ mutationFn: () => onSave(name.trim()) });
  return <Dialog title={kind === "Workspace" ? "Add workspace" : "New session"} onClose={onClose} dismissable={!action.isPending} width={430} actions={<><Button variant="text" disabled={action.isPending} onClick={onClose}>Cancel</Button><Button busy={action.isPending} disabled={!name.trim()} onClick={() => action.mutate()}>Create</Button></>}><form onSubmit={(e) => { e.preventDefault(); if (name.trim() && !action.isPending) action.mutate(); }}><p className="muted small">{kind === "Workspace" ? "Group related sessions in a named workspace." : "Give this conversation a name. Its history is saved on your controller."}</p><div className="cli-dialog-field"><TextField label={`${kind} name`} value={name} onChange={setName} maxLength={60} autoFocus placeholder={kind === "Workspace" ? "e.g. my-project" : "e.g. Login component"} /></div>{action.isError && <p className="error-text" role="alert">{friendly(action.error)}</p>}</form></Dialog>;
}

export function ArchiveDialog({ sessions, archived, scope, onClose, onSave }: { sessions: CliSession[]; archived: boolean; scope: string; onClose: () => void; onSave: (ids: string[]) => Promise<void> }) {
  const [ids, setIds] = useState<string[]>([]);
  const action = useMutation({ mutationFn: () => onSave(ids) });
  const sending = useMutationState({ filters: { mutationKey: ["cli", scope, "send"], status: "pending" }, select: (mutation) => mutation.options.mutationKey?.[3] });
  return <Dialog title={archived ? "Restore sessions" : "Archive sessions"} onClose={onClose} dismissable={!action.isPending} width={460} actions={<><Button variant="text" disabled={action.isPending} onClick={onClose}>Cancel</Button><Button busy={action.isPending} disabled={!ids.length || ids.some((id) => sending.includes(id))} onClick={() => action.mutate()}>{archived ? "Restore selected" : "Archive selected"}</Button></>}><p className="small muted">Choose the sessions you want to {archived ? "restore" : "archive"}. Running sessions stay active.</p><div className="cli-session-options">{sessions.map((s) => <label className="cli-session-option" key={s.id}><input type="checkbox" checked={ids.includes(s.id)} disabled={sessionIsRunning(s) || sending.includes(s.id) || action.isPending} onChange={(e) => setIds(e.target.checked ? [...ids, s.id] : ids.filter((id) => id !== s.id))} /><span>{s.name}</span>{(sessionIsRunning(s) || sending.includes(s.id)) && <span className="small muted">Running</span>}</label>)}</div>{action.isError && <p className="error-text" role="alert">{friendly(action.error)}</p>}</Dialog>;
}

export function DeleteDialog({ session, onClose, onDelete }: { session: CliSession; onClose: () => void; onDelete: () => Promise<void> }) {
  const action = useMutation({ mutationFn: onDelete });
  return <Dialog title="Delete session?" onClose={onClose} dismissable={!action.isPending} width={430} actions={<><Button variant="text" onClick={onClose} disabled={action.isPending}>Cancel</Button><Button variant="danger" busy={action.isPending} onClick={() => action.mutate()}>Delete session</Button></>}><p>Delete “{session.name}” from this workspace? This cannot be undone. Executed tasks remain in task history.</p>{action.isError && <p className="error-text" role="alert">{friendly(action.error)}</p>}</Dialog>;
}

export function HistoryDialog({ onClose }: { onClose: () => void }) {
  const tasks = useTasks();
  let content: ReactNode;
  if (tasks.isPending) content = <Spinner />;
  else if (tasks.isError) content = <p className="error-text" role="alert">Failed to load tasks: {friendly(tasks.error)}</p>;
  else if (!tasks.data.length) content = <p className="muted">No tasks yet.</p>;
  else content = tasks.data.map((task) => <details className="expander" key={task.id}><summary><StatusDot color={statusColor(task.status)} /><div className="grow"><div>{taskPrompt(task)}</div><div className="small muted">{task.status} · {task.type} · {task.executions?.length ?? 0} attempt(s)</div></div></summary><div className="expander-body"><TaskTranscript task={task} /></div></details>);
  return <Dialog title="Task history" width={800} onClose={onClose} actions={<Button variant="text" onClick={onClose}>Close</Button>}><div className="stack-8 cli-history">{content}</div></Dialog>;
}
