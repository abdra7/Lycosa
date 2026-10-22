import { useEffect, useRef, useState } from "react";

import { api } from "../../api/client";
import { Button, Dialog, LinearProgress } from "../../components/ui";
import { activeProfile, useSession } from "../../state/session";

/** One isolated local model run, deliberately apart from task history,
 * provider choice and RAG. The answer is shown once and cleared after 60
 * seconds; there is no copy or export action for it. */
export function PhantomDialog({ onClose }: { onClose: () => void }) {
  const session = useSession();
  const profileId = activeProfile(session.status)?.id;
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [ready, setReady] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [output, setOutput] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const expiry = useRef<ReturnType<typeof setTimeout> | null>(null);
  const openedFor = useRef(profileId);

  useEffect(() => {
    api
      .phantomCapabilities()
      .then((info) => {
        const list = info.models ?? [];
        setModels(list);
        setModel(list[0] ?? "");
        const ok = info.enabled === true && list.length > 0;
        setReady(ok);
        if (!ok) setError("Ask the controller administrator to configure an isolated local model.");
      })
      .catch(() => setError("Phantom execution is unavailable."));
    return () => {
      if (expiry.current) clearTimeout(expiry.current);
    };
  }, []);

  // switching controllers closes the dialog
  useEffect(() => {
    if (profileId !== openedFor.current) onClose();
  }, [profileId, onClose]);

  const clear = () => {
    if (expiry.current) clearTimeout(expiry.current);
    setPrompt("");
    setOutput(null);
  };

  const run = async () => {
    if (!model || !prompt.trim()) return;
    if (expiry.current) clearTimeout(expiry.current);
    const text = prompt;
    setPrompt("");
    setBusy(true);
    setOutput(null);
    setError(null);
    try {
      const result = await api.submitPhantom(text, model);
      setOutput(result.output ?? "");
      expiry.current = setTimeout(clear, 60_000);
    } catch {
      setError(
        "Phantom did not return a confirmed result. No history lookup is available. Ask the administrator to check isolation and cleanup.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title="Ephemeral Phantom Agent"
      onClose={onClose}
      width={640}
      actions={
        <>
          <Button variant="text" onClick={clear}>
            Clear
          </Button>
          <Button disabled={!ready || busy} onClick={() => void run()}>
            Run Phantom
          </Button>
          <Button variant="text" onClick={onClose}>
            Close
          </Button>
        </>
      }
    >
      <div className="stack-12">
        <p>
          Runs one isolated local model without cloud access or task history. The result is shown once and cleared after
          60 seconds. This reduces retained content; it does not guarantee forensic erasure from RAM or your device.
        </p>
        <label className="field">
          <span className="small muted">Isolated model</span>
          <select className="select" value={model} disabled={busy} onChange={(e) => setModel(e.target.value)}>
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="small muted">Sensitive task input</span>
          <textarea
            className="textarea"
            rows={4}
            value={prompt}
            disabled={!ready || busy}
            spellCheck={false}
            autoComplete="off"
            onChange={(e) => setPrompt(e.target.value)}
          />
        </label>
        {busy && <LinearProgress />}
        {error && <p>{error}</p>}
        {output != null && <p className="pre-wrap">{output}</p>}
      </div>
    </Dialog>
  );
}
