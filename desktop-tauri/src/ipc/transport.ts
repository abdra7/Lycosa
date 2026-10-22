// The one seam between the UI and the Rust side (ADR-037). Production uses
// Tauri's IPC; tests install a fake with `setTransport`.

import { Channel, invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

export interface ApiCall {
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  query?: [string, string][];
  body?: unknown;
  timeoutMs?: number;
  /** The body carries a credential: HTTPS or a loopback controller only. */
  secret?: boolean;
}

export interface SseEvent {
  event: string;
  data: Record<string, unknown>;
}

export interface Transport {
  invoke<T>(command: string, args?: Record<string, unknown>): Promise<T>;
  listen<T>(event: string, handler: (payload: T) => void): Promise<() => void>;
  /** POST a streaming request; events arrive through `onEvent` until the
   * returned promise settles. */
  stream(call: ApiCall, streamId: string, onEvent: (event: SseEvent) => void): Promise<void>;
}

export const tauriTransport: Transport = {
  invoke: (command, args) => invoke(command, args),
  listen: async (event, handler) => {
    const unlisten = await listen(event, (e) => handler(e.payload as never));
    return unlisten;
  },
  stream: (call, streamId, onEvent) => {
    const channel = new Channel<SseEvent>();
    channel.onmessage = onEvent;
    return invoke("api_stream", { call, streamId, onEvent: channel });
  },
};

let current: Transport = tauriTransport;

export function transport(): Transport {
  return current;
}

export function setTransport(next: Transport): void {
  current = next;
}
