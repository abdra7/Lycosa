import { useState } from "react";

import { friendly } from "../../api/errors";
import { Button, Dialog, TextField } from "../../components/ui";
import { useSession } from "../../state/session";

/** A URL like http://host:8000 (the Rust side normalizes and re-checks). */
export function validControllerUrl(value: string): boolean {
  try {
    const url = new URL(value.trim());
    return (url.protocol === "http:" || url.protocol === "https:") && url.hostname.length > 0;
  } catch {
    return false;
  }
}

/** Controller URL + credentials: probe, sign in, then save the profile. */
export function ConnectionForm({ adding = false, onDone }: { adding?: boolean; onDone?: () => void }) {
  const session = useSession();
  // first run suggests the local default; adding another controller starts
  // empty so it cannot silently reuse the current controller's address
  const [url, setUrl] = useState(adding ? "" : "http://localhost:8000");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<{ url?: string; email?: string; password?: string }>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const connect = async () => {
    const next = {
      url: validControllerUrl(url) ? undefined : "Enter a valid URL like http://host:8000",
      email: email.trim() ? undefined : "Email is required",
      password: password ? undefined : "Password is required",
    };
    setErrors(next);
    if (next.url || next.email || next.password) return;
    setBusy(true);
    setError(null);
    try {
      await session.addProfileAndLogin({ name: name.trim(), baseUrl: url.trim(), email: email.trim(), password });
      onDone?.();
    } catch (e) {
      setError(friendly(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="col"
      style={{ gap: 10 }}
      onSubmit={(e) => {
        e.preventDefault();
        void connect();
      }}
    >
      <TextField label="Controller URL" value={url} onChange={setUrl} placeholder="http://controller-host:8000" error={errors.url} autoFocus={adding} />
      <TextField label="Profile name (optional)" value={name} onChange={setName} placeholder="Home lab" />
      <TextField label="Email" value={email} onChange={setEmail} error={errors.email} autoComplete="username" />
      <TextField label="Password" type="password" value={password} onChange={setPassword} error={errors.password} autoComplete="current-password" />
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <Button type="submit" busy={busy}>
        Connect
      </Button>
    </form>
  );
}

export function AddControllerDialog({ onClose }: { onClose: () => void }) {
  return (
    <Dialog title="Add a controller" onClose={onClose} width={460}>
      <div style={{ paddingBottom: 12 }}>
        <ConnectionForm adding onDone={onClose} />
      </div>
    </Dialog>
  );
}
