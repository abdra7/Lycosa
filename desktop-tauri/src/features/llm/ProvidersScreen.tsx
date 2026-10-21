import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import { friendly } from "../../api/errors";
import { useLlmAccounts, useLlmProviders, useLlmRouting } from "../../api/queries";
import {
  accountIsActive,
  accountIsNodeRun,
  accountIsShared,
  providerIsNodeRun,
  providerTakesBaseUrl,
  routeFor,
  type LlmAccount,
  type LlmProbeResult,
  type LlmProvider,
  type LlmRouting,
  type RouteEntry,
} from "../../api/types";
import { Icon } from "../../components/icons";
import { Button, Card, Chip, Dialog, IconButton, Spinner, StatusDot, useConfirm } from "../../components/ui";
import { useIsAdmin } from "../../state/session";
import { useToast } from "../../state/toast";
import { CapabilityChips, ModelsDialog, PromptTestDialog, RoutingDialog } from "./LlmDialogs";
import { ClaudeSubscriptionDialog } from "./ClaudeSubscriptionDialog";
import { ConnectAccountDialog } from "./ConnectAccountDialog";

/** Shown when a connected account's key is refused: account and API access
 * are separate things (consumer subscriptions are not API access). */
export const API_ACCESS_UNAVAILABLE =
  "Account connected\n\nAPI access is not available through this subscription.\n\nConfigure the provider's API access separately.";

/** Providers: connect accounts, test them, browse models, set the default
 * model and fallback routes (ADR-031). */
export function ProvidersScreen() {
  const providers = useLlmProviders();
  const accounts = useLlmAccounts();
  const routing = useLlmRouting();
  const isAdmin = useIsAdmin();
  const queries = useQueryClient();
  const [dialog, setDialog] = useState<"test" | "routing" | null>(null);
  const refresh = () => void queries.invalidateQueries({ queryKey: ["llm"] });

  const error = providers.error ?? accounts.error;
  return (
    <div className="page stack-12">
      <div className="page-head" style={{ marginBottom: 0 }}>
        <h1>AI Providers</h1>
        <span className="spacer" />
        <Button variant="outlined" icon="science" onClick={() => setDialog("test")}>
          Test a route
        </Button>
        <Button variant="outlined" icon="route" disabled={!accounts.data || !routing.data} onClick={() => setDialog("routing")}>
          Routing &amp; fallback
        </Button>
        <IconButton icon="refresh" label="Refresh" onClick={refresh} />
      </div>
      <p className="small muted">
        Agents call models through Lycosa's LLM layer; they never see a provider or a key. Keys are encrypted on the
        controller.
      </p>
      <DefaultRouteCard routing={routing.data} accounts={accounts.data ?? []} />
      {error ? (
        <p className="error-text">Failed to load providers: {friendly(error)}</p>
      ) : !providers.data || !accounts.data ? (
        <div className="center" style={{ padding: 24 }}>
          <Spinner />
        </div>
      ) : (
        providers.data.map((p) => (
          <ProviderCard
            key={p.id}
            provider={p}
            accounts={accounts.data.filter((a) => a.provider === p.id)}
            isAdmin={isAdmin}
            onChanged={refresh}
            // Pro/Max plans have no API access; point to the plan card
            planHint={p.id === "anthropic" && providers.data.some((o) => o.id === "claude_subscription")}
          />
        ))
      )}
      {dialog === "test" && <PromptTestDialog onClose={() => setDialog(null)} />}
      {dialog === "routing" && accounts.data && routing.data && (
        <RoutingDialog
          accounts={accounts.data}
          routing={routing.data}
          isAdmin={isAdmin}
          onClose={(changed) => {
            setDialog(null);
            if (changed) refresh();
          }}
        />
      )}
    </div>
  );
}

function DefaultRouteCard({ routing, accounts }: { routing: LlmRouting | undefined; accounts: LlmAccount[] }) {
  const route = routeFor(routing, "default", "personal") ?? routeFor(routing, "default", "deployment");
  const describe = (e: RouteEntry) => {
    const a = accounts.find((x) => x.id === e.account_id);
    return a ? `${a.provider_name} · ${a.label} · ${e.model}` : `(removed account) · ${e.model}`;
  };
  const first = route?.chain[0];
  return (
    <div className="card row top">
      <Icon name="star" />
      <div className="grow">
        <div>{route && first ? `Default: ${describe(first)}` : "No default model yet"}</div>
        <div className="small muted">
          {route
            ? [
                route.scope === "deployment" ? "Deployment default" : "Your default",
                route.chain.length > 1 ? `fallbacks: ${route.chain.slice(1).map(describe).join(" → ")}` : null,
              ]
                .filter(Boolean)
                .join(" · ")
            : "Connect an account, then pick a model under Models or Routing & fallback."}
        </div>
      </div>
    </div>
  );
}

const KIND_LABEL: Record<string, string> = {
  local: "Local",
  aggregator: "Aggregator",
  compatible: "Compatible",
  node: "Your plan, on your node",
};

function ProviderCard({
  provider,
  accounts,
  isAdmin,
  onChanged,
  planHint,
}: {
  provider: LlmProvider;
  accounts: LlmAccount[];
  isAdmin: boolean;
  onChanged: () => void;
  planHint: boolean;
}) {
  const [connecting, setConnecting] = useState(false);
  return (
    <Card>
      <div className="row wrap">
        <h2>{provider.display_name}</h2>
        <Chip small>{KIND_LABEL[provider.kind] ?? "Cloud"}</Chip>
        <span className="spacer" />
        <Button variant="text" icon="link" onClick={() => setConnecting(true)}>
          {accounts.length === 0 ? "Connect" : "Add account"}
        </Button>
      </div>
      <div style={{ marginTop: 6 }}>
        <CapabilityChips capabilities={provider.capabilities ?? {}} />
      </div>
      {providerIsNodeRun(provider) && provider.notes?.[0] && <p className="small muted" style={{ marginTop: 4 }}>{provider.notes[0]}</p>}
      {planHint && (
        <p className="small muted row" style={{ marginTop: 4 }}>
          <Icon name="info" size={14} />
          Have Claude Pro or Max? Those plans include no API access. Use the Claude subscription card instead: it runs
          Claude Code on your own node with your plan.
        </p>
      )}
      <div style={{ marginTop: 6 }}>
        {accounts.length === 0 ? (
          <p className="small muted row">
            <span className="dot" style={{ border: "1.5px solid var(--text-2)", width: 12, height: 12 }} /> Not connected
          </p>
        ) : (
          accounts.map((a) => <AccountRow key={a.id} account={a} provider={provider} isAdmin={isAdmin} onChanged={onChanged} />)
        )}
      </div>
      {connecting &&
        (providerIsNodeRun(provider) ? (
          <ClaudeSubscriptionDialog
            provider={provider}
            onClose={(added) => {
              setConnecting(false);
              if (added) onChanged();
            }}
          />
        ) : (
          <ConnectAccountDialog
            provider={provider}
            isAdmin={isAdmin}
            onClose={(added) => {
              setConnecting(false);
              if (added) onChanged();
            }}
          />
        ))}
    </Card>
  );
}

function accessText(a: LlmAccount): string {
  if (accountIsNodeRun(a)) {
    return a.api_access === "available"
      ? "Claude plan: ready"
      : a.api_access === "unavailable"
        ? "Claude plan: needs attention on the node"
        : "Claude plan: not tested yet";
  }
  return a.api_access === "available"
    ? "API access: available"
    : a.api_access === "unavailable"
      ? "API access: unavailable"
      : "API access: not tested yet";
}

export function probeMessage(a: LlmAccount, r: LlmProbeResult): string {
  const detail = r.detail ?? "";
  switch (r.status) {
    case "ready":
      if (accountIsNodeRun(a)) return detail ? `${detail}.` : "Ready.";
      return `Connected. ${r.models_available ?? 0} model(s) available${r.latency_ms != null ? ` (${r.latency_ms} ms)` : ""}.`;
    case "unauthorized":
      if (accountIsNodeRun(a)) return detail;
      return [API_ACCESS_UNAVAILABLE, r.subscription_note, detail || null].filter(Boolean).join("\n\n");
    case "offline":
      return `Offline: ${detail}`;
    case "unknown":
      return detail;
    default:
      return `Error: ${detail}`;
  }
}

function AccountRow({ account: a, provider, isAdmin, onChanged }: { account: LlmAccount; provider: LlmProvider; isAdmin: boolean; onChanged: () => void }) {
  const toast = useToast();
  const { confirm, element } = useConfirm();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [models, setModels] = useState(false);
  const dot = !accountIsActive(a)
    ? "var(--text-2)"
    : a.api_access === "available"
      ? "var(--success)"
      : a.api_access === "unavailable"
        ? "var(--error)"
        : "var(--warning)";

  const test = async () => {
    setBusy(true);
    try {
      setResult(probeMessage(a, await api.testLlmAccount(a.id)));
      onChanged();
    } catch (e) {
      toast.show(friendly(e), "error");
    } finally {
      setBusy(false);
    }
  };

  const toggle = async () => {
    try {
      await api.updateLlmAccount(a.id, { status: accountIsActive(a) ? "disabled" : "active" });
      onChanged();
    } catch (e) {
      toast.show(friendly(e), "error");
    }
  };

  const disconnect = async () => {
    const ok = await confirm({
      title: `Disconnect ${a.label}?`,
      message: accountIsNodeRun(a)
        ? "The connection is removed from every route. Claude Code on the node stays signed in; run `claude auth logout` there to sign it out."
        : "The stored credential is deleted and the account is removed from every route. Revoke the key at the provider as well.",
      confirmLabel: "Disconnect",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.disconnectLlmAccount(a.id);
      onChanged();
    } catch (e) {
      toast.show(friendly(e), "error");
    }
  };

  const details = [
    accountIsShared(a) ? "Shared" : a.mine ? "Personal" : "Another user's",
    a.is_local ? "Local" : null,
    a.auth_method === "oauth_pkce" ? "Signed in" : null,
    accountIsNodeRun(a) ? `Runs on ${a.node_name ?? "a removed node"}` : null,
    !accountIsActive(a) ? "Disabled" : null,
    accessText(a),
  ].filter(Boolean);

  return (
    <div className="row top" style={{ padding: "6px 0" }}>
      <span style={{ paddingTop: 5 }}>
        <StatusDot color={dot} />
      </span>
      <div className="grow" style={{ minWidth: 0 }}>
        <div>{a.label}</div>
        <div className="small muted pre-wrap">
          {[details.join(" · "), a.last_test_detail || null, providerTakesBaseUrl(provider) ? a.base_url : null].filter(Boolean).join("\n")}
        </div>
      </div>
      <div className="row wrap" style={{ justifyContent: "flex-end" }}>
        {busy && <Spinner small />}
        {a.usable && (
          <>
            <Button variant="text" disabled={busy} onClick={() => void test()}>
              Test
            </Button>
            <Button variant="text" onClick={() => setModels(true)}>
              Models
            </Button>
          </>
        )}
        {a.manageable && (
          <Button variant="text" onClick={() => void toggle()}>
            {accountIsActive(a) ? "Disable" : "Enable"}
          </Button>
        )}
        {(a.manageable || isAdmin) && (
          <Button variant="text" onClick={() => void disconnect()}>
            Disconnect
          </Button>
        )}
      </div>
      {result !== null && (
        <Dialog
          title={`Test · ${a.label}`}
          onClose={() => setResult(null)}
          width={480}
          actions={
            <Button variant="text" onClick={() => setResult(null)}>
              Close
            </Button>
          }
        >
          <p className="pre-wrap">{result}</p>
        </Dialog>
      )}
      {models && <ModelsDialog account={a} onClose={() => setModels(false)} />}
      {element}
    </div>
  );
}
