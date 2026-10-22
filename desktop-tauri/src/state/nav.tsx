// State-driven navigation inside the shell (no router, as in ADR-015): a
// selected section plus a stack of pushed views (node detail, a workflow
// run).

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

export type Section = "nodes" | "tasks" | "chat" | "workflows" | "knowledge" | "providers" | "admin" | "local";

export type View =
  | { kind: "node"; nodeId: string }
  | { kind: "run"; workflowId: string; runId: string; workflowName: string };

interface NavApi {
  section: Section;
  view: View | null;
  go: (section: Section) => void;
  push: (view: View) => void;
  back: () => void;
}

const NavContext = createContext<NavApi | null>(null);

export function NavProvider({ children, initial = "nodes" }: { children: ReactNode; initial?: Section }) {
  const [section, setSection] = useState<Section>(initial);
  const [stack, setStack] = useState<View[]>([]);
  const api = useMemo<NavApi>(
    () => ({
      section,
      view: stack[stack.length - 1] ?? null,
      go: (s) => {
        setStack([]);
        setSection(s);
      },
      push: (v) => setStack((st) => [...st, v]),
      back: () => setStack((st) => st.slice(0, -1)),
    }),
    [section, stack],
  );
  return <NavContext.Provider value={api}>{children}</NavContext.Provider>;
}

export function useNav(): NavApi {
  const value = useContext(NavContext);
  if (!value) throw new Error("useNav outside NavProvider");
  return value;
}
