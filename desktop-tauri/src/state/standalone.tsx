// The local controller's status, pushed by the Rust supervisor.

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { transport } from "../ipc/transport";

export type Phase = "unavailable" | "off" | "starting" | "running" | "stopping" | "error";
export type ServiceState = "stopped" | "starting" | "running" | "restarting" | "failed";

export interface ServiceStatus {
  name: "postgres" | "qdrant" | "backend" | "agent";
  label: string;
  state: ServiceState;
  pid: number | null;
  port: number | null;
  restarts: number;
  detail: string | null;
}

export interface StandaloneStatus {
  phase: Phase;
  stage: string | null;
  message: string | null;
  services: ServiceStatus[];
  apiUrl: string | null;
  lanPort: number;
  enabled: boolean;
  exposeOnLan: boolean;
  localNode: boolean;
  dataDir: string;
  runtime: { target: string; python: string; postgres: string; qdrant: string; backend: string } | null;
  adminEmail: string;
}

export interface AppInfo {
  version: string;
  os: string;
  standaloneAvailable: boolean;
}

interface StandaloneContextValue {
  status: StandaloneStatus | null;
  info: AppInfo | null;
}

const StandaloneContext = createContext<StandaloneContextValue>({ status: null, info: null });

export function StandaloneProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<StandaloneStatus | null>(null);
  const [info, setInfo] = useState<AppInfo | null>(null);
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    let cancelled = false;
    void transport()
      .listen<StandaloneStatus>("lycosa://standalone", (s) => setStatus(s))
      .then((fn) => {
        if (cancelled) fn();
        else unlisten = fn;
      });
    void transport()
      .invoke<StandaloneStatus>("standalone_status")
      .then((s) => !cancelled && setStatus((current) => current ?? s))
      .catch(() => {});
    void transport()
      .invoke<AppInfo>("app_info")
      .then((i) => !cancelled && setInfo(i))
      .catch(() => {});
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);
  return <StandaloneContext.Provider value={{ status, info }}>{children}</StandaloneContext.Provider>;
}

export const useStandalone = () => useContext(StandaloneContext);

export const SERVICE_STATE_COLOR: Record<ServiceState, string> = {
  stopped: "var(--text-2)",
  starting: "var(--warning)",
  restarting: "var(--warning)",
  running: "var(--success)",
  failed: "var(--error)",
};
