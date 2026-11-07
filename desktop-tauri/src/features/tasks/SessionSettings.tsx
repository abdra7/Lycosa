import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { CliSession, CliSettings } from "../../api/cli";
import { friendly } from "../../api/errors";
import { useLlmAccounts } from "../../api/queries";
import { accountIsActive, accountIsNodeRun } from "../../api/types";
import { Button, Dialog, SelectField, TextField } from "../../components/ui";
import { PROVIDER_LABELS } from "./providerLabels";

const ROUTES = [
  { value: "node", label: "Selected provider / node" },
  ...["auto", "default", "coding", "reasoning", "vision", "cheap", "private", "offline"].map((r) => ({ value: r, label: `LLM route: ${r}` })),
];

export function SessionSettings({ session, onClose, onSave }: {
  session: CliSession;
  onClose: () => void;
  onSave: (changes: { name: string; settings: CliSettings; node_id: string | null }) => Promise<void>;
}) {
  const [name, setName] = useState(session.name);
  const [settings, setSettings] = useState(session.settings);
  const [route, setRoute] = useState(session.settings.llm_account_id ? `account:${session.settings.llm_account_id}` : session.settings.route ?? "node");
  const accountsQuery = useLlmAccounts();
  const catalog = useQuery({ queryKey: ["providers-catalog"], queryFn: api.providerCatalog, retry: false });
  const accounts = (accountsQuery.data ?? []).filter((a) => a.usable && accountIsActive(a));
  const account = accounts.find((a) => `account:${a.id}` === route);
  const providers = catalog.data?.length ? catalog.data.map((p) => String(p["name"])) : Object.keys(PROVIDER_LABELS);
  const update = <K extends keyof CliSettings>(key: K, value: CliSettings[K]) => setSettings((previous) => ({ ...previous, [key]: value }));
  const save = useMutation({ mutationFn: async () => {
    if (route.startsWith("account:") && !account) throw new Error("This account is unavailable. Choose another route or account.");
    if (account && !accountIsNodeRun(account) && !settings.model?.trim()) throw new Error(`Enter the model to use with ${account.label}.`);
    const next: CliSettings = {
      ...settings,
      provider: route === "node" ? settings.provider : "ollama",
      route: route === "node" || account ? null : route,
      llm_account_id: account?.id ?? null,
      model: settings.model?.trim() || (account && accountIsNodeRun(account) ? "default" : null),
      knowledge_query: settings.knowledge_query?.trim() || null,
      knowledge_collection: settings.knowledge_collection?.trim() || null,
    };
    await onSave({ name: name.trim(), settings: next, node_id: route === "node" && ["ollama", "anthropic"].includes(next.provider) ? session.node_id : null });
  } });

  return <Dialog title={`Settings · ${session.name}`} width={590} onClose={onClose} dismissable={!save.isPending} actions={<><Button variant="text" disabled={save.isPending} onClick={onClose}>Cancel</Button><Button busy={save.isPending} disabled={!name.trim()} onClick={() => save.mutate()}>Save settings</Button></>}>
    <form className="stack-12" onSubmit={(e) => { e.preventDefault(); if (name.trim() && !save.isPending) save.mutate(); }}>
      <TextField label="Session name" value={name} onChange={setName} maxLength={60} autoFocus />
      <SelectField label="Route" value={route} onChange={(value) => { setRoute(value); update("model", null); }} disabled={save.isPending} options={[
        ...ROUTES,
        ...(route.startsWith("account:") && !account ? [{ value: route, label: "Unavailable account — choose another" }] : []),
        ...accounts.map((a) => ({ value: `account:${a.id}`, label: `Account: ${a.provider_name} · ${a.label}` })),
      ]} />
      {accountsQuery.isError && <p className="error-text small">Could not load accounts: {friendly(accountsQuery.error)}</p>}
      <SelectField label="Provider" value={settings.provider} onChange={(provider) => setSettings((previous) => ({ ...previous, provider, model: provider === "openrouter" ? "nvidia/nemotron-3.5-lightning:free" : null }))} disabled={route !== "node" || save.isPending} options={providers.map((p) => ({ value: p, label: PROVIDER_LABELS[p] ?? p }))} />
      {account ? <p className="small muted">{accountIsNodeRun(account) ? `Runs Claude Code on ${account.node_name ?? "your node"} with your own Claude plan. Model: sonnet, opus, haiku, or blank for the plan default.` : "Runs on this account only. Enter the model to use."}</p> : route !== "node" ? <p className="small muted">The route picks the account and model configured under Providers.</p> : settings.provider !== "ollama" ? <p className="small muted">This external provider receives your prompt and retrieved context. Configure its key in Admin → Legacy provider keys.</p> : <p className="small muted">Your selected node runs the model. Auto-select lets Lycosa choose an available node.</p>}
      <div className="cli-settings-row"><SelectField label="Type" value={settings.type ?? "auto"} onChange={(value) => update("type", value === "auto" ? null : value)} options={["auto", "coding", "retrieval", "tool", "vision", "general"].map((value) => ({ value, label: value }))} /><TextField label="Model (optional)" value={settings.model ?? ""} onChange={(value) => update("model", value)} /></div>
      <TextField label="Knowledge query (optional)" value={settings.knowledge_query ?? ""} onChange={(value) => update("knowledge_query", value)} />
      <TextField label="Knowledge collection (optional)" value={settings.knowledge_collection ?? ""} onChange={(value) => update("knowledge_collection", value)} help="Leave blank to search all accessible collections." />
      {save.isError && <p className="error-text" role="alert">{friendly(save.error)}</p>}
    </form>
  </Dialog>;
}
