import { useCallback, useEffect, useState } from "react";

import { api } from "../../api/client";
import { friendly } from "../../api/errors";
import {
  CAPABILITY_LABELS,
  accountIsShared,
  routeFor,
  type Capabilities,
  type LlmAccount,
  type LlmAnswer,
  type LlmModel,
  type LlmRouting,
} from "../../api/types";
import { Button, Chip, Dialog, IconButton, LinearProgress, TextField } from "../../components/ui";

/** ✓ supported, ✗ not supported, ? unknown (never guessed). */
export function CapabilityChips({ capabilities }: { capabilities: Capabilities }) {
  return (
    <div className="row wrap" style={{ gap: 4 }}>
      {Object.entries(CAPABILITY_LABELS).map(([key, label]) => {
        const value = capabilities[key];
        const mark = value === true ? "✓" : value === false ? "✗" : "?";
        const tip = value === true ? `${label}: supported` : value === false ? `${label}: not supported` : `${label}: unknown (provider does not say)`;
        return (
          <Chip key={key} small title={tip}>
            {mark} {label}
          </Chip>
        );
      })}
    </div>
  );
}

/** The models one account reports, with capabilities; set one as your
 * default (it goes first in your personal default route). */
export function ModelsDialog({ account, onClose }: { account: LlmAccount; onClose: () => void }) {
  const [models, setModels] = useState<LlmModel[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);

  const fetchModels = useCallback(
    (refresh: boolean) =>
      api
        .listLlmAccountModels(account.id, refresh)
        .then(setModels)
        .catch((e) => setMessage(friendly(e)))
        .finally(() => setBusy(false)),
    [account.id],
  );

  const load = (refresh = false) => {
    setBusy(true);
    void fetchModels(refresh);
  };

  useEffect(() => {
    void fetchModels(false);
  }, [fetchModels]);

  const makeDefault = async (m: LlmModel) => {
    try {
      const routing = await api.getLlmRouting();
      const current = routeFor(routing, "default", "personal")?.chain ?? [];
      const chain = [
        { account_id: m.account_id, model: m.id },
        ...current.filter((e) => !(e.account_id === m.account_id && e.model === m.id)),
      ].slice(0, 5);
      await api.setLlmRoute("default", chain);
      setMessage(`${m.ref} is now your default model.`);
    } catch (e) {
      setMessage(friendly(e));
    }
  };

  return (
    <Dialog
      title={`Models · ${account.label}`}
      onClose={onClose}
      width={680}
      actions={
        <>
          <Button variant="text" disabled={busy} onClick={() => load(true)}>
            Refresh
          </Button>
          <Button variant="text" onClick={onClose}>
            Close
          </Button>
        </>
      }
    >
      <div className="stack-8" style={{ minHeight: 300 }}>
        {busy && <LinearProgress />}
        {message && <p>{message}</p>}
        {models &&
          (models.length === 0 ? (
            <p className="muted">The provider reported no models.</p>
          ) : (
            models.map((m) => (
              <div key={m.ref} className="row top" style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
                <div className="grow stack-8">
                  <div>{m.display_name ?? m.id}</div>
                  <div className="small muted">{[m.ref, m.context_window ? `${m.context_window} token context` : null].filter(Boolean).join(" · ")}</div>
                  <CapabilityChips capabilities={m.capabilities ?? {}} />
                </div>
                <Button variant="text" onClick={() => void makeDefault(m)}>
                  Set as default
                </Button>
              </div>
            ))
          ))}
      </div>
    </Dialog>
  );
}

const PURPOSE_HELP: Record<string, string> = {
  default: "Default model; later entries are fallbacks.",
  coding: 'Coding tasks (route "auto" with type coding).',
  reasoning: "Reasoning-heavy work.",
  vision: 'Image input (route "auto" with type vision).',
  cheap: "Lower-cost model for bulk work.",
  private: "Local runtimes only; prompts never leave your network.",
  offline: "Local runtimes only, for working without internet.",
};

interface Row {
  accountId: string;
  model: string;
}

/** Edit the route (primary + up to four fallbacks) for one purpose. */
export function RoutingDialog({
  accounts,
  routing,
  isAdmin,
  onClose,
}: {
  accounts: LlmAccount[];
  routing: LlmRouting;
  isAdmin: boolean;
  onClose: (changed: boolean) => void;
}) {
  const [purpose, setPurpose] = useState("default");
  const [scope, setScope] = useState("personal");
  const rowsFor = (p: string, s: string): Row[] => {
    const chain = routeFor(routing, p, s)?.chain ?? [];
    return chain.length ? chain.map((e) => ({ accountId: e.account_id, model: e.model })) : [{ accountId: "", model: "" }];
  };
  const [rows, setRows] = useState<Row[]>(() => rowsFor("default", "personal"));
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const eligible = accounts.filter((a) => {
    if (!a.usable) return false;
    if (scope === "deployment" && !accountIsShared(a)) return false;
    if ((purpose === "private" || purpose === "offline") && !a.is_local) return false;
    return true;
  });

  const save = async () => {
    const chain = rows.filter((r) => r.accountId && r.model.trim()).map((r) => ({ account_id: r.accountId, model: r.model.trim() }));
    if (chain.length === 0) {
      setMessage("Add at least one account and model.");
      return;
    }
    setBusy(true);
    try {
      await api.setLlmRoute(purpose, chain, scope);
      onClose(true);
    } catch (e) {
      setMessage(friendly(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await api.deleteLlmRoute(purpose, scope);
      onClose(true);
    } catch (e) {
      setMessage(friendly(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title="Routing & fallback"
      onClose={() => onClose(false)}
      width={660}
      actions={
        <>
          {routeFor(routing, purpose, scope) && (
            <Button variant="text" disabled={busy} onClick={() => void remove()}>
              Remove route
            </Button>
          )}
          <Button variant="text" disabled={busy} onClick={() => onClose(false)}>
            Cancel
          </Button>
          <Button busy={busy} onClick={() => void save()}>
            Save route
          </Button>
        </>
      }
    >
      <div className="stack-8">
        <div className="row">
          <label className="field grow">
            <span className="small muted">Purpose</span>
            <select
              className="select"
              value={purpose}
              onChange={(e) => {
                setPurpose(e.target.value);
                setRows(rowsFor(e.target.value, scope));
              }}
            >
              {routing.purposes.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
          {isAdmin && (
            <label className="field grow">
              <span className="small muted">Scope</span>
              <select
                className="select"
                value={scope}
                onChange={(e) => {
                  setScope(e.target.value);
                  setRows(rowsFor(purpose, e.target.value));
                }}
              >
                <option value="personal">My route</option>
                <option value="deployment">Deployment default</option>
              </select>
            </label>
          )}
        </div>
        <p className="small muted">{PURPOSE_HELP[purpose] ?? ""}</p>
        {rows.map((row, i) => (
          <div key={i} className="row">
            <span style={{ width: 80 }}>{i === 0 ? "Primary" : `Fallback ${i}`}</span>
            <select
              className="select"
              style={{ flex: 3 }}
              aria-label={`Account ${i + 1}`}
              value={eligible.some((a) => a.id === row.accountId) ? row.accountId : ""}
              onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, accountId: e.target.value } : r)))}
            >
              <option value="" disabled>
                Account
              </option>
              {eligible.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.provider_name} · {a.label}
                </option>
              ))}
            </select>
            <input
              className="input"
              style={{ flex: 2 }}
              aria-label={`Model ${i + 1}`}
              placeholder="Model id"
              value={row.model}
              onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, model: e.target.value } : r)))}
            />
            <IconButton icon="close" label="Remove entry" disabled={rows.length === 1} onClick={() => setRows(rows.filter((_, j) => j !== i))} />
          </div>
        ))}
        <Button variant="text" icon="add" disabled={rows.length >= 5} onClick={() => setRows([...rows, { accountId: "", model: "" }])}>
          Add fallback
        </Button>
        {eligible.length === 0 && <p className="muted">No eligible accounts for this purpose/scope yet.</p>}
        {busy && <LinearProgress />}
        {message && <p className="error-text">{message}</p>}
      </div>
    </Dialog>
  );
}

/** Send one prompt through a route (or one account) and show who answered. */
export function PromptTestDialog({ account, onClose }: { account?: LlmAccount; onClose: () => void }) {
  const [prompt, setPrompt] = useState("Reply with one short sentence.");
  const [model, setModel] = useState("");
  const [purpose, setPurpose] = useState("default");
  const [answer, setAnswer] = useState<LlmAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const send = async () => {
    setBusy(true);
    setError(null);
    setAnswer(null);
    try {
      setAnswer(
        await api.llmTestPrompt({
          prompt,
          accountId: account?.id ?? null,
          model: account ? model.trim() : null,
          purpose: account ? null : purpose,
        }),
      );
    } catch (e) {
      setError(friendly(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={account ? `Test ${account.label}` : "Test a route"}
      onClose={onClose}
      width={580}
      actions={
        <>
          <Button variant="text" onClick={onClose}>
            Close
          </Button>
          <Button busy={busy} onClick={() => void send()}>
            Send
          </Button>
        </>
      }
    >
      <div className="stack-8">
        {account ? (
          <TextField label="Model id" value={model} onChange={setModel} />
        ) : (
          <label className="field">
            <span className="small muted">Route</span>
            <select className="select" value={purpose} onChange={(e) => setPurpose(e.target.value)}>
              {["default", "coding", "reasoning", "vision", "cheap", "private", "offline"].map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
        )}
        <TextField label="Prompt" value={prompt} onChange={setPrompt} multiline rows={3} />
        <p className="small muted">This sends a real request; the provider may charge for it.</p>
        {busy && <LinearProgress />}
        {error && <p className="error-text">{error}</p>}
        {answer && (
          <>
            <p className="pre-wrap" style={{ userSelect: "text" }}>
              {answer.content}
            </p>
            <p className="small" style={{ color: answer.fallback_index > 0 ? "var(--warning)" : "var(--text-2)" }}>
              {[
                `${answer.provider}:${answer.model}`,
                answer.fallback_index > 0 ? `fallback #${answer.fallback_index}` : null,
                `${answer.latency_ms} ms`,
                answer.usage?.total_tokens != null ? `${answer.usage.total_tokens} tokens` : null,
                answer.estimated_cost != null ? `cost ${answer.estimated_cost}` : "cost unknown",
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </>
        )}
      </div>
    </Dialog>
  );
}
