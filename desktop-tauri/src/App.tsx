import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { Button, Spinner } from "./components/ui";
import { LoginScreen } from "./features/auth/LoginScreen";
import { SetupScreen } from "./features/setup/SetupScreen";
import { Shell } from "./features/shell/Shell";
import { NavProvider } from "./state/nav";
import { SessionProvider, useSession } from "./state/session";
import { StandaloneProvider } from "./state/standalone";
import { ThemeProvider } from "./state/theme";
import { ToastProvider } from "./state/toast";

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      // the Flutter app never retried; a failed load shows its error at once
      queries: { retry: false, refetchOnWindowFocus: false, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

/** Providers around the session-driven root (ADR-015: setup → login → shell). */
export function Root({ client }: { client?: QueryClient }) {
  const [queries] = useState(() => client ?? makeQueryClient());
  return (
    <ThemeProvider>
      <ToastProvider>
        <QueryClientProvider client={queries}>
          <StandaloneProvider>
            <SessionProvider>
              <RootGate />
            </SessionProvider>
          </StandaloneProvider>
        </QueryClientProvider>
      </ToastProvider>
    </ThemeProvider>
  );
}

export function RootGate() {
  const { status, restore } = useSession();
  if (status.phase === "loading") {
    return (
      <div className="fill-center">
        <Spinner />
      </div>
    );
  }
  if (status.phase === "error") {
    return (
      <div className="fill-center col">
        <p>Something went wrong: {status.message}</p>
        <Button onClick={() => void restore()}>Retry</Button>
      </div>
    );
  }
  if (status.session.profiles.length === 0) return <SetupScreen />;
  if (!status.principal) return <LoginScreen />;
  return (
    <NavProvider>
      <Shell />
    </NavProvider>
  );
}
