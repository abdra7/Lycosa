import { useEffect, useState } from "react";

import { friendly } from "../../api/errors";
import { BrandHero } from "../../components/brand";
import { Banner, Button, Card, LinearProgress, TextField, useOutsideClick } from "../../components/ui";
import { Icon } from "../../components/icons";
import { transport } from "../../ipc/transport";
import { activeProfile, useSession } from "../../state/session";
import { useStandalone } from "../../state/standalone";
import { AddControllerDialog } from "../setup/ConnectionForm";

/** Sign-in for the active profile (token missing, expired or revoked). */
export function LoginScreen() {
  const session = useSession();
  const restoreSession = session.restore;
  const { status: local } = useStandalone();
  const profile = activeProfile(session.status);
  const ready = session.status.phase === "ready" ? session.status : null;
  const profiles = ready?.session.profiles ?? [];
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [switching, setSwitching] = useState(false);
  const menuRef = useOutsideClick<HTMLDivElement>(() => setSwitching(false));

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(friendly(e));
    } finally {
      setBusy(false);
    }
  };

  const isLocal = profile?.kind === "local";
  const localRunning = local?.phase === "running";
  const localStarting = local?.phase === "starting";

  // The app starts its local controller in the background at launch, so a
  // saved local session looks unreachable for its first seconds. Once the
  // controller is up, restore that session instead of asking again.
  const retryWhenUp = isLocal && localRunning && !!ready?.unreachable && !!profile?.signedIn;
  useEffect(() => {
    if (retryWhenUp) void restoreSession();
  }, [retryWhenUp, restoreSession]);

  return (
    <div className="auth-page">
      <div className="auth-card" style={{ maxWidth: 400 }}>
        <BrandHero />
        <Card>
          <div className="stack-12">
            <div>
              <h1 style={{ fontSize: 19 }}>Sign in</h1>
              {profile && (
                <div className="row" style={{ position: "relative" }} ref={menuRef}>
                  <span className="small muted grow ellipsis" title={profile.baseUrl}>
                    {profile.name} — {profile.baseUrl}
                  </span>
                  {profiles.length > 1 && (
                    <button className="icon-btn" title="Switch controller" aria-label="Switch controller" onClick={() => setSwitching((v) => !v)}>
                      <Icon name="swap" />
                    </button>
                  )}
                  {switching && (
                    <div className="menu" role="menu">
                      {profiles.map((p) => (
                        <button
                          key={p.id}
                          role="menuitem"
                          onClick={() => {
                            setSwitching(false);
                            void session.switchProfile(p.id);
                          }}
                        >
                          {p.name} — {p.baseUrl}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
            {ready?.unreachable && !isLocal && (
              <Banner kind="warning">
                <div className="row wrap">
                  <span className="grow">Could not reach {profile?.baseUrl}.</span>
                  <Button variant="text" onClick={() => void session.restore()}>
                    Retry
                  </Button>
                </div>
              </Banner>
            )}
            {isLocal && (
              <div className="stack-8">
                {localStarting && (
                  <>
                    <LinearProgress />
                    <p className="small">{local?.stage ?? "Starting the local controller…"}</p>
                  </>
                )}
                {local?.phase === "error" && <Banner kind="error">{local.message}</Banner>}
                {localRunning ? (
                  <Button icon="computer" busy={busy} onClick={() => void run(session.signInLocal)}>
                    Sign in to this computer
                  </Button>
                ) : (
                  !localStarting && (
                    <Button icon="play" busy={busy} onClick={() => void run(() => transport().invoke("standalone_enable"))}>
                      Start the local controller
                    </Button>
                  )
                )}
                <p className="small muted">Or sign in with another account:</p>
              </div>
            )}
            <form
              className="col"
              style={{ gap: 10 }}
              onSubmit={(e) => {
                e.preventDefault();
                void run(() => session.login(email.trim(), password));
              }}
            >
              <TextField label="Email" value={email} onChange={setEmail} autoComplete="username" />
              <TextField label="Password" type="password" value={password} onChange={setPassword} autoComplete="current-password" />
              {error && (
                <p className="error-text" role="alert">
                  {error}
                </p>
              )}
              <Button type="submit" variant={isLocal ? "outlined" : "filled"} busy={busy}>
                Sign in
              </Button>
            </form>
            <Button variant="text" disabled={busy} onClick={() => setAdding(true)}>
              Use a different controller
            </Button>
          </div>
        </Card>
      </div>
      {adding && <AddControllerDialog onClose={() => setAdding(false)} />}
    </div>
  );
}
