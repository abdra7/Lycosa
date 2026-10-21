import { beforeEach, describe, expect, it } from "vitest";

import { setTransport } from "../../ipc/transport";
import { FakeTransport, apiError } from "../../test/fakeTransport";
import { ChatStore, answeredHistory, isReady, isSending, type ChatTurn } from "./chatStore";

const turn = (role: "user" | "assistant", text: string, error: string | null = null): ChatTurn => ({
  role,
  text,
  streaming: false,
  error,
  provider: null,
  model: null,
  totalTokens: null,
});

describe("chat store", () => {
  let fake: FakeTransport;
  let store: ChatStore;
  beforeEach(() => {
    fake = new FakeTransport();
    setTransport(fake);
    store = new ChatStore();
    store.bindProfile("p1");
  });

  it("streams an answer with its model and token count", async () => {
    fake.streamHandler = async (_call, onEvent) => {
      onEvent({ event: "start", data: { metadata: { provider: "anthropic", model: "sonnet" } } });
      onEvent({ event: "text", data: { text: "Hel" } });
      onEvent({ event: "text", data: { text: "lo" } });
      onEvent({ event: "usage", data: { usage: { total_tokens: 12 } } });
      onEvent({ event: "done", data: {} });
    };
    await store.send("  hi  ");
    const [q, a] = store.getState().turns;
    expect(q).toMatchObject({ role: "user", text: "hi" });
    expect(a).toMatchObject({ role: "assistant", text: "Hello", streaming: false, model: "sonnet", totalTokens: 12, error: null });
    const call = fake.called("api_stream")[0]!.args["call"] as { body: Record<string, unknown> };
    expect(call.body).toMatchObject({ messages: [{ role: "user", content: "hi" }], purpose: "default", stream: true });
  });

  it("sends an explicit account and model when both are chosen", async () => {
    store.selectTarget("acc-1", "opus");
    await store.send("x");
    const call = fake.called("api_stream")[0]!.args["call"] as { body: Record<string, unknown> };
    expect(call.body).toMatchObject({ account_id: "acc-1", model: "opus" });
    expect(call.body).not.toHaveProperty("purpose");
  });

  it("is not ready with an account but no model", () => {
    store.selectTarget("acc-1", null);
    expect(isReady(store.getState())).toBe(false);
  });

  it("shows a stream error event and an API failure on the answer", async () => {
    fake.streamHandler = async (_c, onEvent) => onEvent({ event: "error", data: { code: "usage_limit", message: "Plan limit reached" } });
    await store.send("a");
    expect(store.getState().turns[1]).toMatchObject({ error: "Plan limit reached", streaming: false });
    fake.streamHandler = async () => {
      throw apiError(502, "Upstream refused");
    };
    await store.send("b");
    expect(store.getState().turns[3]).toMatchObject({ error: "Upstream refused" });
  });

  it("stop cancels the stream", async () => {
    let release: () => void = () => {};
    fake.streamHandler = () => new Promise<void>((resolve) => (release = resolve));
    const sending = store.send("long");
    expect(isSending(store.getState())).toBe(true);
    store.stop();
    expect(isSending(store.getState())).toBe(false);
    expect(fake.called("api_stream_cancel")).toHaveLength(1);
    release();
    await sending;
  });

  it("keeps only answered exchanges as history", () => {
    const turns = [turn("user", "q1"), turn("assistant", "a1"), turn("user", "q2"), turn("assistant", "", "failed"), turn("user", "q3"), turn("assistant", "a3")];
    expect(answeredHistory(turns)).toEqual([
      { role: "user", content: "q1" },
      { role: "assistant", content: "a1" },
      { role: "user", content: "q3" },
      { role: "assistant", content: "a3" },
    ]);
  });

  it("resets when the controller profile changes", async () => {
    await store.send("x");
    store.bindProfile("p2");
    expect(store.getState().turns).toEqual([]);
  });
});
