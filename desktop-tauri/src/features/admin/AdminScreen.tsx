import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import { friendly } from "../../api/errors";
import { localTime } from "../../api/format";
import { useApiKeys, useAuditLogs } from "../../api/queries";
import { auditActor, type ApiKeyInfo } from "../../api/types";
import { Icon } from "../../components/icons";
import { Button, Card, Dialog, IconButton, LinearProgress, Spinner, useConfirm } from "../../components/ui";
import { usePrincipal } from "../../state/session";
import { useToast } from "../../state/toast";
import { PROVIDER_LABELS } from "../tasks/providerLabels";

export function AdminScreen() {
  const principal = usePrincipal();
  const queries = useQueryClient();
  const [legacy, setLegacy] = useState(false);
  if (principal.role !== "admin") {
    return <div className="fill-center muted">This section requires the admin role.</div>;
  }
  return (
    <div className="page col" style={{ height: "100%", gap: 12 }}>
      <div className="page-head" style={{ marginBottom: 0 }}>
        <h1>Admin</h1>
        <span className="spacer" />
        {/* not "Providers": that is the section for LLM accounts */}
        <Button variant="text" icon="key" onClick={() => setLegacy(true)}>
          Legacy provider keys
        </Button>
        <IconButton
          icon="refresh"
          label="Refresh"
          onClick={() => {
            void queries.invalidateQueries({ queryKey: ["audit-logs"] });
            void queries.invalidateQueries({ queryKey: ["api-keys"] });
          }}
        />
      </div>
      <div style={{ flex: 2, minHeight: 0, display: "flex" }}>
        <ApiKeysCard />
      </div>
      <div style={{ flex: 3, minHeight: 0, display: "flex" }}>
        <AuditCard />
      </div>
      {legacy && <LegacyKeysDialog onClose={() => setLegacy(false)} />}
    </div>
  );
}

function ApiKeysCard() {
  const keys = useApiKeys();
  const queries = useQueryClient();
  const toast = useToast();
  const { confirm, element } = useConfirm();

  const revoke = async (key: ApiKeyInfo) => {
    const ok = await confirm({
      title: `Revoke ${key.name}?`,
      message: "Anything using this key stops working at once; a node using it is cut off until it gets a new key. This cannot be undone.",
      confirmLabel: "Revoke",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.revokeApiKey(key.id);
      void queries.invalidateQueries({ queryKey: ["api-keys"] });
      void queries.invalidateQueries({ queryKey: ["audit-logs"] });
      toast.show(`Revoked ${key.name}`);
    } catch (e) {
      toast.show(friendly(e), "error");
    }
  };

  return (
    <Card title="API keys" className="grow col" >
      <div style={{ overflow: "auto", flex: 1 }}>
        {keys.isPending ? (
          <Spinner />
        ) : keys.isError ? (
          <p className="error-text">Failed: {friendly(keys.error)}</p>
        ) : keys.data.length === 0 ? (
          <p className="muted">No API keys.</p>
        ) : (
          keys.data.map((k) => (
            <div key={k.id} className="list-item">
              <span style={{ color: k.revoked_at ? "var(--error)" : undefined }}>
                <Icon name={k.revoked_at ? "block" : "key"} />
              </span>
              <div className="grow">
                <div>
                  {k.name} ({k.key_prefix}…)
                </div>
                <div className="small muted">
                  {[k.revoked_at ? "revoked" : null, k.node_id ? "bound to node" : null, k.last_used_at ? `last used ${localTime(k.last_used_at)}` : null]
                    .filter(Boolean)
                    .join(" · ")}
                </div>
              </div>
              {!k.revoked_at && (
                <Button variant="text" onClick={() => void revoke(k)}>
                  Revoke
                </Button>
              )}
            </div>
          ))
        )}
      </div>
      {element}
    </Card>
  );
}

function AuditCard() {
  const logs = useAuditLogs();
  return (
    <Card title="Audit log" className="grow col">
      <div style={{ overflow: "auto", flex: 1 }}>
        {logs.isPending ? (
          <Spinner />
        ) : logs.isError ? (
          <p className="error-text">Failed: {friendly(logs.error)}</p>
        ) : logs.data.length === 0 ? (
          <p className="muted">No audit entries.</p>
        ) : (
          <table className="table" style={{ minWidth: 720 }}>
            <thead>
              <tr>
                <th>Time</th>
                <th>Action</th>
                <th>Actor</th>
                <th>Resource</th>
                <th>IP</th>
              </tr>
            </thead>
            <tbody>
              {logs.data.map((entry, i) => (
                <tr key={`${entry.created_at}-${i}`}>
                  <td style={{ whiteSpace: "nowrap" }}>{localTime(entry.created_at)}</td>
                  <td>{entry.action}</td>
                  <td>{auditActor(entry)}</td>
                  <td>{entry.resource_type ?? "—"}</td>
                  <td>{entry.ip_address ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Card>
  );
}

/** Controller-wide keys of the older provider route (ADR-029). Exceptions
 * from credential-bearing calls are never rendered. */
function LegacyKeysDialog({ onClose }: { onClose: () => void }) {
  const catalog = useQuery({ queryKey: ["providers-catalog"], queryFn: api.providerCatalog, retry: false });
  const providers = catalog.data?.length
    ? catalog.data.filter((p) => p["execution_mode"] === "cloud").map((p) => String(p["name"]))
    : Object.keys(PROVIDER_LABELS).filter((p) => p !== "ollama");
  const [provider, setProvider] = useState("openrouter");
  const [storage, setStorage] = useState("session");
  const [key, setKey] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const act = async (action: "save" | "delete" | "status") => {
    setBusy(true);
    setMessage("");
    try {
      if (action === "save") {
        await api.saveLegacyProviderKey(provider, key, storage);
        setKey("");
      } else if (action === "delete") {
        await api.deleteLegacyProviderKey(provider, storage);
      }
      const list = await api.listLegacyProviders();
      const selected = list.find((p) => p["name"] === provider);
      setMessage(
        selected?.["credential_configured"] === true
          ? "Key configured (not yet validated). Use Tasks to test the model."
          : "No key configured.",
      );
    } catch {
      setMessage(
        "Operation failed. Check controller access, HTTPS/loopback URL, and storage support. Session mode requires one worker; vault mode requires a controller OS vault.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title="Legacy provider keys"
      onClose={onClose}
      width={600}
      actions={
        <>
          <Button variant="text" disabled={busy} onClick={() => void act("status")}>
            Check status
          </Button>
          <Button variant="text" disabled={busy} onClick={() => void act("delete")}>
            Remove selected key
          </Button>
          <Button disabled={busy} onClick={() => void act("save")}>
            Save key
          </Button>
          <Button variant="text" disabled={busy} onClick={onClose}>
            Close
          </Button>
        </>
      }
    >
      <div className="stack-12">
        <p>
          Controller-wide keys for the older provider route (Tasks → Route: "Provider above"). To connect accounts, pick
          models and set routing, use the Providers section instead.
        </p>
        <p className="small muted">
          Cloud providers require administrator-enabled model policies and credentials. New adapters run on the controller;
          Anthropic retains its trusted-agent route. Bedrock and Vertex AI may instead use configured workload identity.
          Phantom never uses cloud providers.
        </p>
        <label className="field">
          <span className="small muted">Provider</span>
          <select
            className="select"
            value={provider}
            disabled={busy}
            onChange={(e) => {
              setProvider(e.target.value);
              setStorage("session");
              setKey("");
              setMessage("");
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
          <span className="small muted">Storage</span>
          <select className="select" value={storage} disabled={busy} onChange={(e) => setStorage(e.target.value)}>
            <option value="session">Controller memory (until restart)</option>
            <option value="vault">Controller OS credential vault</option>
          </select>
        </label>
        <label className="field">
          <span className="small muted">API key</span>
          <input className="input" type="password" value={key} disabled={busy} autoComplete="off" onChange={(e) => setKey(e.target.value)} />
        </label>
        <p className="small muted">
          Saving authorizes permitted controller users to use this provider. Session storage is temporary and single-worker
          only. Removing a session key can reveal an existing vault key; Check status shows effective availability.
        </p>
        <p className="small muted">
          These are API keys, not subscriptions: Claude Pro / ChatGPT Plus sign-in does not work here. To use a Claude Pro
          or Max plan, connect it under Providers → Claude subscription.
        </p>
        {busy && <LinearProgress />}
        {message && <p>{message}</p>}
      </div>
    </Dialog>
  );
}
