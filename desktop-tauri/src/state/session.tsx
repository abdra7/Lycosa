// Session state (ADR-015):
// saved controller profiles, the active one, and who is signed in there.
// Tokens never reach this side; Rust holds them in the OS keychain.

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { friendly } from "../api/errors";
import type { Principal } from "../api/types";
import { transport } from "../ipc/transport";

export interface ProfileView {
  id: string;
  name: string;
  baseUrl: string;
  kind: "remote" | "local";
  signedIn: boolean;
}

export interface SessionSnapshot {
  profiles: ProfileView[];
  activeId: string | null;
  keychainPersistent: boolean;
}

export type SessionStatus =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "ready"; session: SessionSnapshot; principal: Principal | null; unreachable: boolean };

interface Restored {
  session: SessionSnapshot;
  principal: Principal | null;
  unreachable: boolean;
}

export interface SessionApi {
  status: SessionStatus;
  /** Re-validate the active profile's token (startup, after a 401). */
  restore: () => Promise<void>;
  addProfileAndLogin: (p: { name: string; baseUrl: string; email: string; password: string }) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  switchProfile: (id: string) => Promise<void>;
  removeProfile: (id: string) => Promise<void>;
  /** One-click sign-in to this computer's controller. */
  signInLocal: () => Promise<void>;
}

const SessionContext = createContext<SessionApi | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SessionStatus>({ phase: "loading" });
  const queries = useQueryClient();

  const restore = useCallback(async () => {
    try {
      const restored = await transport().invoke<Restored>("session_restore");
      setStatus({ phase: "ready", ...restored });
    } catch (e) {
      setStatus({ phase: "error", message: friendly(e) });
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    transport()
      .invoke<Restored>("session_restore")
      .then((restored) => !cancelled && setStatus({ phase: "ready", ...restored }))
      .catch((e) => !cancelled && setStatus({ phase: "error", message: friendly(e) }));
    return () => {
      cancelled = true;
    };
  }, []);

  const signedIn = useCallback(
    async (principal: Principal) => {
      const session = await transport().invoke<SessionSnapshot>("session_state");
      queries.clear();
      setStatus({ phase: "ready", session, principal, unreachable: false });
    },
    [queries],
  );

  const api = useMemo<SessionApi>(
    () => ({
      status,
      restore,
      addProfileAndLogin: async ({ name, baseUrl, email, password }) => {
        const principal = await transport().invoke<Principal>("session_add_profile", { name, baseUrl, email, password });
        await signedIn(principal);
      },
      login: async (email, password) => {
        const principal = await transport().invoke<Principal>("session_login", { email, password });
        await signedIn(principal);
      },
      logout: async () => {
        await transport().invoke("session_logout");
        queries.clear();
        await restore();
      },
      switchProfile: async (id) => {
        await transport().invoke("session_switch", { id });
        queries.clear();
        setStatus({ phase: "loading" });
        await restore();
      },
      removeProfile: async (id) => {
        await transport().invoke("session_remove_profile", { id });
        queries.clear();
        await restore();
      },
      signInLocal: async () => {
        const principal = await transport().invoke<Principal>("standalone_sign_in");
        await signedIn(principal);
      },
    }),
    [status, restore, signedIn, queries],
  );

  return <SessionContext.Provider value={api}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionApi {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession outside SessionProvider");
  return value;
}

export function activeProfile(status: SessionStatus): ProfileView | null {
  if (status.phase !== "ready") return null;
  const { profiles, activeId } = status.session;
  return profiles.find((p) => p.id === activeId) ?? profiles[0] ?? null;
}

/** The signed-in principal; screens inside the shell can rely on it. */
export function usePrincipal(): Principal {
  const { status } = useSession();
  if (status.phase !== "ready" || !status.principal) throw new Error("not signed in");
  return status.principal;
}

export function useIsAdmin(): boolean {
  return usePrincipal().role === "admin";
}
