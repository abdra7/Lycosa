import { useEffect, useRef, useState } from "react";

import { friendly } from "../../api/errors";
import { BrandHero } from "../../components/brand";
import { Banner, Button, Card, LinearProgress } from "../../components/ui";
import { transport } from "../../ipc/transport";
import { useSession } from "../../state/session";
import { useStandalone } from "../../state/standalone";
import { ConnectionForm } from "./ConnectionForm";

/** First run: run the controller on this computer, or connect to one. */
export function SetupScreen() {
  const { status, info } = useStandalone();
  const available = info?.standaloneAvailable ?? status?.phase !== "unavailable";
  return (
    <div className="auth-page">
      <div className="auth-card" style={{ maxWidth: 520 }}>
        <BrandHero subtitle="AI operations for your local fabric" />
        <div className="stack-12">
          {available && status && status.phase !== "unavailable" && <RunHereCard />}
          <Card title="Connect to a controller" icon="link">
            <p className="muted small" style={{ marginBottom: 12 }}>
              Use a Lycosa controller that already runs on your network (for example the Docker install).
            </p>
            <ConnectionForm />
          </Card>
          {status?.phase === "unavailable" && (
            <Banner>This build has no bundled controller, so it can only connect to one: {status.message}</Banner>
          )}
        </div>
      </div>
    </div>
  );
}

function RunHereCard() {
  const { status } = useStandalone();
  const session = useSession();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const signingIn = useRef(false);

  // once the services are up, sign in with the generated admin account
  useEffect(() => {
    if (!busy || status?.phase !== "running" || signingIn.current) return;
    signingIn.current = true;
    session
      .signInLocal()
      .catch((e) => {
        setError(friendly(e));
        setBusy(false);
      })
      .finally(() => {
        signingIn.current = false;
      });
  }, [busy, status?.phase, session]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await transport().invoke("standalone_enable");
    } catch (e) {
      setError(friendly(e));
      setBusy(false);
    }
  };

  const starting = busy && status?.phase !== "error";
  return (
    <Card title="Run Lycosa on this computer" icon="computer">
      <p className="muted small" style={{ marginBottom: 12 }}>
        The app starts its own database (PostgreSQL), vector store (Qdrant) and the Lycosa API here, and signs you in.
        Nothing else to install. Data stays in your user profile, reachable only from this computer until you turn on
        network mode.
      </p>
      {starting && (
        <div className="stack-8" style={{ marginBottom: 12 }}>
          <LinearProgress />
          <p className="small">{status?.stage ?? "Starting…"}</p>
        </div>
      )}
      {(error || status?.phase === "error") && (
        <p className="error-text" role="alert" style={{ marginBottom: 12 }}>
          {error ?? status?.message}
        </p>
      )}
      <Button icon="play" busy={starting} onClick={() => void start()}>
        Start on this computer
      </Button>
    </Card>
  );
}
