// The live event stream for the active profile (ADR-016): Rust holds the
// WebSocket (and its five-second reconnect); this hook follows it.

import { useEffect, useRef, useState } from "react";

import type { LycosaEvent } from "../api/types";
import { transport } from "../ipc/transport";

type Outgoing = { type: "status"; connected: boolean } | { type: "event"; event: LycosaEvent };

export function useLiveEvents(onEvent: (event: LycosaEvent) => void): { connected: boolean; last: LycosaEvent | null } {
  const [connected, setConnected] = useState(false);
  const [last, setLast] = useState<LycosaEvent | null>(null);
  const handler = useRef(onEvent);
  useEffect(() => {
    handler.current = onEvent;
  });
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    let cancelled = false;
    void transport()
      .listen<Outgoing>("lycosa://events", (message) => {
        if (message.type === "status") {
          setConnected(message.connected);
        } else {
          setLast(message.event);
          handler.current(message.event);
        }
      })
      .then((fn) => {
        if (cancelled) fn();
        else unlisten = fn;
      });
    void transport().invoke("events_start").catch(() => {});
    return () => {
      cancelled = true;
      unlisten?.();
      void transport().invoke("events_stop").catch(() => {});
    };
  }, []);
  return { connected, last };
}
