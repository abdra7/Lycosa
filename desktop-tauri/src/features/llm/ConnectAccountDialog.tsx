import { useEffect, useRef, useState } from "react";

import { api } from "../../api/client";
import { friendly } from "../../api/errors";
import { providerIsLocal, providerSupportsOAuth, providerTakesBaseUrl, type LlmProvider, type OpenRouterSignIn } from "../../api/types";
import { Button, Dialog, IconButton, LinearProgress, TextField, useCopy } from "../../components/ui";
import { transport } from "../../ipc/transport";

/** Connect a provider account: an API key (or none, for local runtimes),
 * or OpenRouter's official PKCE sign-in. The key is sent once, encrypted
 * by the controller, and never shown again. */
export function ConnectAccountDialog({
  provider: p,
  isAdmin,
  onClose,
}: {
  provider: LlmProvider;
  isAdmin: boolean;
  onClose: (added: boolean) => void;
}) {
  const copy = useCopy();
  const [label, setLabel] = useState("Personal");
  const [baseUrl, setBaseUrl] = useState(p.default_base_url);
  const [key, setKey] = useState("");
  const [code, setCode] = useState("");
  const [scope, setScope] = useState("personal");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [signIn, setSignIn] = useState<OpenRouterSignIn | null>(null);
  const listener = useRef<string | null>(null);
  const choices = (p.official_base_urls ?? []).filter((u) => !u.includes("{"));

  // never leave a loopback listener behind
  useEffect(
    () => () => {
      if (listener.current) void transport().invoke("oauth_loopback_cancel", { id: listener.current }).catch(() => {});
    },
    [],
  );

  const connect = async () => {
    const trimmed = key.trim();
    if (p.credential_required && !trimmed) {
      setError(`${p.display_name} needs an API key.`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.connectLlmAccount({
        provider: p.id,
        label: label.trim(),
        scope,
        baseUrl: providerTakesBaseUrl(p) ? baseUrl.trim() : null,
        apiKey: trimmed || null,
      });
      setKey("");
      onClose(true);
    } catch (e) {
      setError(friendly(e));
    } finally {
      setBusy(false);
    }
  };

  const complete = async (flow: string, authCode: string) => {
    setBusy(true);
    try {
      await api.completeOpenRouterSignIn(flow, authCode, label.trim(), scope);
      onClose(true);
    } catch (e) {
      setError(friendly(e));
    } finally {
      setBusy(false);
    }
  };

  /** The controller keeps the PKCE verifier; the browser hands the code to a
   * one-shot 127.0.0.1 listener in Rust (or the user pastes it). */
  const startSignIn = async () => {
    setBusy(true);
    setError(null);
    let id: string | null = null;
    try {
      const started = await transport().invoke<{ id: string; callbackUrl: string }>("oauth_loopback_start");
      id = started.id;
      listener.current = id;
      const flow = await api.startOpenRouterSignIn(started.callbackUrl);
      setSignIn(flow);
      const opened = await transport().invoke<boolean>("open_sign_in_page", { url: flow.authorization_url }).catch(() => false);
      if (!opened) setError("Open the sign-in link below in your browser.");
      const received = await transport().invoke<string | null>("oauth_loopback_wait", { id });
      listener.current = null;
      if (received) await complete(flow.flow, received);
    } catch (e) {
      if (id) void transport().invoke("oauth_loopback_cancel", { id }).catch(() => {});
      listener.current = null;
      setError(friendly(e) || "Sign-in could not start on this device.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={`Connect ${p.display_name}`}
      onClose={() => onClose(false)}
      width={580}
      actions={
        <>
          <Button variant="text" disabled={busy} onClick={() => onClose(false)}>
            Cancel
          </Button>
          <Button disabled={busy} onClick={() => void connect()}>
            Connect
          </Button>
        </>
      }
    >
      <div className="stack-12">
        {p.subscription_note && <p>{p.subscription_note}</p>}
        {(p.notes ?? []).map((n) => (
          <p key={n} className="small muted">
            {n}
          </p>
        ))}
        <TextField label="Account label" value={label} onChange={setLabel} placeholder="Personal, Work…" disabled={busy} />
        {isAdmin && (
          <label className="field">
            <span className="small muted">Who can use it</span>
            <select className="select" value={scope} disabled={busy} onChange={(e) => setScope(e.target.value)}>
              <option value="personal">Only me (personal)</option>
              <option value="deployment">Every operator (shared deployment account)</option>
            </select>
          </label>
        )}
        {providerTakesBaseUrl(p) &&
          (p.base_url_mode === "official_choice" && choices.length > 0 ? (
            <label className="field">
              <span className="small muted">Official endpoint (region)</span>
              <select className="select" value={choices.includes(baseUrl) ? baseUrl : choices[0]} disabled={busy} onChange={(e) => setBaseUrl(e.target.value)}>
                {choices.map((u) => (
                  <option key={u} value={u}>
                    {u}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <TextField
              label="Endpoint URL"
              value={baseUrl}
              onChange={setBaseUrl}
              disabled={busy}
              help={providerIsLocal(p) ? "The controller connects to this address; it must be on a network your administrator allowed." : undefined}
            />
          ))}
        <TextField
          label={p.credential_required ? "API key" : "API key / token (optional)"}
          type="password"
          value={key}
          onChange={setKey}
          disabled={busy}
          autoComplete="off"
          help="Encrypted by the controller; never shown again."
        />
        {providerSupportsOAuth(p) && (
          <div className="stack-8">
            <Button variant="outlined" icon="link" disabled={busy} onClick={() => void startSignIn()}>
              Sign in with OpenRouter instead
            </Button>
            {signIn && (
              <>
                <p className="small muted">Waiting for OpenRouter… If the browser did not open, copy the link. If no code arrives, paste it below.</p>
                <div className="row">
                  <span className="small muted grow" style={{ wordBreak: "break-all", userSelect: "text" }}>
                    {signIn.authorization_url}
                  </span>
                  <IconButton icon="copy" label="Copy link" onClick={() => void copy(signIn.authorization_url)} />
                </div>
                <div className="row" style={{ alignItems: "flex-end" }}>
                  <div className="grow">
                    <TextField label="Authorization code" value={code} onChange={setCode} />
                  </div>
                  <Button variant="text" disabled={!code.trim()} onClick={() => void complete(signIn.flow, code.trim())}>
                    Use code
                  </Button>
                </div>
              </>
            )}
          </div>
        )}
        {busy && <LinearProgress />}
        {error && <p className="error-text">{error}</p>}
      </div>
    </Dialog>
  );
}
