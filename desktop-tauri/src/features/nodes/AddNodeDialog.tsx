import { useEffect, useState } from "react";

import { api } from "../../api/client";
import { friendly } from "../../api/errors";
import type { MintedApiKey } from "../../api/types";
import { Icon } from "../../components/icons";
import { Banner, Button, CodeBlock, Dialog, TextField } from "../../components/ui";
import { transport } from "../../ipc/transport";
import { activeProfile, useSession } from "../../state/session";
import { useStandalone } from "../../state/standalone";

/** True when the URL's host is loopback: reachable only from this machine. */
export function isLoopbackHost(url: string): boolean {
  try {
    const host = new URL(url).hostname;
    return host === "localhost" || host === "127.0.0.1" || host === "[::1]";
  } catch {
    return false;
  }
}

/** A loopback controller URL rewritten to this machine's LAN IP, so agents
 * on other devices can reach it; unchanged otherwise. */
export function controllerUrlForOtherDevices(url: string, lanIp: string | null): string {
  if (!lanIp || !isLoopbackHost(url)) return url;
  const parsed = new URL(url);
  parsed.hostname = lanIp;
  return parsed.toString().replace(/\/+$/, "");
}

const INSTALL_POWERSHELL = "irm https://raw.githubusercontent.com/abdra7/Lycosa/main/scripts/install-agent.ps1 | iex";
const INSTALL_BASH = "curl -fsSL https://raw.githubusercontent.com/abdra7/Lycosa/main/scripts/install-agent.sh | bash";

export function agentCommands(baseUrl: string, apiKey: string, windows: boolean) {
  return {
    install: windows ? INSTALL_POWERSHELL : INSTALL_BASH,
    // PowerShell: one variable per line, no continuations; bash: one line
    run: windows
      ? `$env:LYCOSA_CONTROLLER_URL = "${baseUrl}"\n$env:LYCOSA_API_KEY = "${apiKey}"\nlycosa-agent run`
      : `LYCOSA_CONTROLLER_URL=${baseUrl} \\\nLYCOSA_API_KEY=${apiKey} \\\nlycosa-agent run`,
  };
}

/** Admin flow: mint a node API key and show the exact commands to run on
 * the new machine. The key is shown exactly once. */
export function AddNodeDialog({ onClose }: { onClose: () => void }) {
  const session = useSession();
  const { info, status: local } = useStandalone();
  const profile = activeProfile(session.status);
  const configured = profile?.baseUrl ?? "http://<controller>:8000";
  const windows = info?.os === "windows";
  const [name, setName] = useState("");
  const [minted, setMinted] = useState<MintedApiKey | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [lanIp, setLanIp] = useState<string | null>(null);
  const loopback = isLoopbackHost(configured);
  // the controller on this computer, reachable from other devices only in
  // network-controller mode, on its LAN port
  const isLocal = profile?.kind === "local";
  const localExposed = isLocal && local?.exposeOnLan;

  useEffect(() => {
    if (!loopback) return;
    void transport()
      .invoke<string | null>("lan_ipv4")
      .then(setLanIp)
      .catch(() => setLanIp(null));
  }, [loopback]);

  let baseUrl = controllerUrlForOtherDevices(configured, lanIp);
  if (localExposed && lanIp) baseUrl = `http://${lanIp}:${local!.lanPort}`;

  const mint = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setMinted(await api.createNodeApiKey(name.trim()));
    } catch (e) {
      setError(friendly(e));
    } finally {
      setBusy(false);
    }
  };

  const commands = minted ? agentCommands(baseUrl, minted.api_key, windows) : null;
  return (
    <Dialog
      title="Add a node"
      onClose={onClose}
      width={600}
      actions={
        <>
          <Button variant="text" onClick={onClose}>
            {minted ? "Done" : "Cancel"}
          </Button>
          {!minted && (
            <Button busy={busy} onClick={() => void mint()}>
              Create key
            </Button>
          )}
        </>
      }
    >
      {!minted ? (
        <div className="stack-12">
          <p>Mint a node API key. Install the agent on the machine, then run it with this key — the node registers itself.</p>
          {isLocal && !localExposed && (
            <Banner kind="warning">
              The controller on this computer only accepts connections from this computer. To add other devices, turn on
              network mode under This computer → Network first.
            </Banner>
          )}
          <TextField label="Key name" value={name} onChange={setName} placeholder="garage-workstation" onEnter={() => void mint()} autoFocus />
          {error && <p className="error-text">{error}</p>}
        </div>
      ) : (
        <div className="stack-8">
          <div className="row">
            <span style={{ color: "var(--warning)" }}>
              <Icon name="warning" />
            </span>
            <span>Copy this key now — it will not be shown again.</span>
          </div>
          <pre className="code mono" style={{ userSelect: "text" }}>
            {minted.api_key}
          </pre>
          <p className="small muted">{windows ? "On the new machine (Windows PowerShell):" : "On the new machine (macOS / Linux):"}</p>
          <p>1. Install the agent (once per machine):</p>
          <CodeBlock text={commands!.install} />
          <p>2. Register it and start:</p>
          <CodeBlock text={commands!.run} copyable />
          <UrlHint loopback={loopback && !localExposed} lanIp={lanIp} windows={windows} />
        </div>
      )}
    </Dialog>
  );
}

function UrlHint({ loopback, lanIp, windows }: { loopback: boolean; lanIp: string | null; windows: boolean }) {
  if (loopback && !lanIp) {
    return (
      <p className="small warning-text">
        This URL is localhost — it only works if the agent runs on THIS controller PC. For another device, replace it
        with this PC's LAN IP (run {windows ? "ipconfig" : "ip addr"} here).
      </p>
    );
  }
  if (loopback && lanIp) {
    return (
      <p className="small muted">
        Using this controller's LAN IP ({lanIp}) so other devices can reach it. Both devices must be on the same network.
        {windows ? " Paste each line separately." : ""}
      </p>
    );
  }
  return (
    <p className="small muted">
      {windows ? "Paste each line separately. " : ""}The agent device must be able to reach this URL on your network.
    </p>
  );
}
