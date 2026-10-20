// The conversation (port of ChatController): kept outside the screen so it
// survives switching sections; reset when the active controller changes.

import { useSyncExternalStore } from "react";

import { api } from "../../api/client";
import { friendly } from "../../api/errors";
import type { ChatMessage } from "../../api/types";
import type { SseEvent } from "../../ipc/transport";

export interface ChatTurn {
  role: "user" | "assistant";
  text: string;
  streaming: boolean;
  error: string | null;
  provider: string | null;
  model: string | null;
  totalTokens: number | null;
}

export interface ChatState {
  turns: ChatTurn[];
  /** null = the default route (personal default, else the deployment one) */
  accountId: string | null;
  model: string | null;
  /** the user picked the target; automatic preselection stops */
  targetChosen: boolean;
  profileId: string | null;
}

export const isSending = (s: ChatState) => s.turns.length > 0 && s.turns[s.turns.length - 1]!.streaming;
/** An explicit account needs its model before a message can go out. */
export const isReady = (s: ChatState) => s.accountId === null || s.model !== null;

const EMPTY: ChatState = { turns: [], accountId: null, model: null, targetChosen: false, profileId: null };

/** Earlier exchanges that got an answer; a question whose answer failed is
 * left out so the model sees a clean user/assistant alternation. */
export function answeredHistory(turns: ChatTurn[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (let i = 0; i + 1 < turns.length; i += 2) {
    const q = turns[i]!;
    const a = turns[i + 1]!;
    if (q.role !== "user" || a.role !== "assistant") continue;
    if (a.error || !a.text) continue;
    out.push({ role: "user", content: q.text }, { role: "assistant", content: a.text });
  }
  return out;
}

export class ChatStore {
  private state: ChatState = EMPTY;
  private listeners = new Set<() => void>();
  private streamId: string | null = null;
  private seq = 0;

  getState = () => this.state;

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private set(next: Partial<ChatState>) {
    this.state = { ...this.state, ...next };
    this.listeners.forEach((l) => l());
  }

  /** A different controller profile starts a fresh conversation. */
  bindProfile(profileId: string | null) {
    if (this.state.profileId !== profileId) {
      this.cancel();
      this.state = { ...EMPTY, profileId };
      this.listeners.forEach((l) => l());
    }
  }

  /** `auto` marks a preselection that a later user choice overrides. */
  selectTarget(accountId: string | null, model: string | null, auto = false) {
    this.set({ accountId, model, targetChosen: auto ? this.state.targetChosen : true });
  }

  clear() {
    this.cancel();
    this.set({ turns: [] });
  }

  stop() {
    this.cancel();
    this.updateLast((t) => ({ ...t, streaming: false }));
  }

  private cancel() {
    if (this.streamId) {
      void api.cancelStream(this.streamId).catch(() => {});
      this.streamId = null;
    }
  }

  private updateLast(change: (t: ChatTurn) => ChatTurn) {
    const turns = this.state.turns;
    const last = turns[turns.length - 1];
    if (!last || last.role !== "assistant") return;
    this.set({ turns: [...turns.slice(0, -1), change(last)] });
  }

  async send(text: string) {
    const prompt = text.trim();
    if (!prompt || isSending(this.state) || !isReady(this.state)) return;
    const history = [...answeredHistory(this.state.turns), { role: "user" as const, content: prompt }];
    const blank = { error: null, provider: null, model: null, totalTokens: null };
    this.set({
      turns: [
        ...this.state.turns,
        { role: "user", text: prompt, streaming: false, ...blank },
        { role: "assistant", text: "", streaming: true, ...blank },
      ],
    });
    const explicit = this.state.accountId !== null && this.state.model !== null;
    const id = `chat-${Date.now()}-${++this.seq}`;
    this.streamId = id;
    try {
      await api.chatStream(
        {
          messages: history,
          accountId: explicit ? this.state.accountId : null,
          model: explicit ? this.state.model : null,
          purpose: explicit ? null : "default",
        },
        id,
        (event) => {
          if (this.streamId === id) this.onEvent(event);
        },
      );
      if (this.streamId === id) this.updateLast((t) => ({ ...t, streaming: false }));
    } catch (e) {
      if (this.streamId === id) this.updateLast((t) => ({ ...t, streaming: false, error: friendly(e) || "The chat stream broke off" }));
    } finally {
      if (this.streamId === id) this.streamId = null;
    }
  }

  private onEvent({ event, data }: SseEvent) {
    switch (event) {
      case "start": {
        const meta = (data["metadata"] ?? {}) as Record<string, unknown>;
        this.updateLast((t) => ({ ...t, provider: (meta["provider"] as string) ?? null, model: (meta["model"] as string) ?? null }));
        break;
      }
      case "text": {
        const chunk = typeof data["text"] === "string" ? (data["text"] as string) : "";
        if (chunk) this.updateLast((t) => ({ ...t, text: t.text + chunk }));
        break;
      }
      case "usage": {
        const usage = (data["usage"] ?? {}) as Record<string, unknown>;
        const total = typeof usage["total_tokens"] === "number" ? (usage["total_tokens"] as number) : null;
        this.updateLast((t) => ({ ...t, totalTokens: total }));
        break;
      }
      case "error":
        this.updateLast((t) => ({
          ...t,
          streaming: false,
          error: typeof data["message"] === "string" ? (data["message"] as string) : "The model call failed",
        }));
        break;
      case "done":
        this.updateLast((t) => ({ ...t, streaming: false }));
        break;
    }
  }
}

export const chatStore = new ChatStore();

export function useChat(store: ChatStore = chatStore): ChatState {
  return useSyncExternalStore(store.subscribe, store.getState);
}
