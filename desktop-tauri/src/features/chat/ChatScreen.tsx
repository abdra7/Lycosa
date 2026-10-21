import { useEffect, useMemo, useRef, useState } from "react";

import { useAccountModels, useLlmAccounts } from "../../api/queries";
import { accountIsActive, accountIsNodeRun, type LlmAccount } from "../../api/types";
import { Icon } from "../../components/icons";
import { Button, IconButton, Spinner, useCopy } from "../../components/ui";
import { activeProfile, useSession } from "../../state/session";
import { chatStore, isReady, isSending, useChat, type ChatState, type ChatTurn } from "./chatStore";

/** Chat with any connected model, including a Claude plan through a node's
 * Claude Code (ADR-032). Answers stream in through Lycosa's LLM layer; the
 * conversation stays in this app session only. */
export function ChatScreen() {
  const session = useSession();
  const profileId = activeProfile(session.status)?.id ?? null;
  useEffect(() => chatStore.bindProfile(profileId), [profileId]);

  const chat = useChat();
  const accounts = useLlmAccounts();
  const usable = (accounts.data ?? []).filter((a) => a.usable && accountIsActive(a));
  const account = usable.find((a) => a.id === chat.accountId);
  const [input, setInput] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const sending = isSending(chat);

  // a sensible target once accounts load: your Claude plan if connected,
  // else your first usable account, else the default route
  useEffect(() => {
    if (!accounts.isSuccess || chat.targetChosen) return;
    if (chat.accountId && usable.some((a) => a.id === chat.accountId)) return;
    const preferred = usable.find(accountIsNodeRun) ?? usable[0];
    if (preferred) chatStore.selectTarget(preferred.id, null, true);
  }, [accounts.isSuccess, usable, chat.targetChosen, chat.accountId]);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat.turns]);

  const send = () => {
    if (!input.trim() || sending || !isReady(chat)) return;
    const text = input;
    setInput("");
    void chatStore.send(text);
  };

  return (
    <div className="page col" style={{ height: "100%", gap: 8 }}>
      <div className="page-head" style={{ marginBottom: 0 }}>
        <h1>Chat</h1>
        <span className="spacer" />
        <TargetPicker usable={usable} chat={chat} />
        <IconButton icon="add" label="New chat" disabled={chat.turns.length === 0} onClick={() => chatStore.clear()} />
      </div>
      <p className="small muted">
        {!account
          ? "Answers come from your default route (set it under Providers → Routing & fallback)."
          : accountIsNodeRun(account)
            ? `Runs Claude Code on ${account.node_name ?? "your node"} with your own Claude plan; usage counts toward its limits.`
            : `${account.provider_name} · ${account.label}`}
      </p>
      <div ref={scroller} className="card grow" style={{ overflow: "auto", minHeight: 200 }}>
        {chat.turns.length === 0 ? (
          <div className="fill-center col">
            <span style={{ color: "var(--primary)" }}>
              <Icon name="chat" size={40} />
            </span>
            <p className="muted">
              {usable.length > 0
                ? "Ask anything. Answers stream in from the model you pick."
                : 'Connect a model first: open Providers and connect an account. To use your Claude plan, connect "Claude subscription".'}
            </p>
          </div>
        ) : (
          chat.turns.map((turn, i) => <Bubble key={i} turn={turn} />)
        )}
      </div>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <textarea
          aria-label="Message"
          className="textarea grow"
          rows={2}
          placeholder="Message (Enter to send, Shift+Enter for a new line)"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        {sending ? (
          <Button variant="outlined" icon="stop" onClick={() => chatStore.stop()}>
            Stop
          </Button>
        ) : (
          <Button icon="send" disabled={!isReady(chat)} onClick={send}>
            Send
          </Button>
        )}
      </div>
    </div>
  );
}

function TargetPicker({ usable, chat }: { usable: LlmAccount[]; chat: ChatState }) {
  const accountId = usable.some((a) => a.id === chat.accountId) ? chat.accountId : null;
  const models = useAccountModels(accountId);
  const list = useMemo(() => models.data ?? [], [models.data]);
  const sending = isSending(chat);

  // an account's first model is preselected once its models load
  useEffect(() => {
    if (accountId && list.length > 0 && !list.some((m) => m.id === chat.model)) {
      chatStore.selectTarget(accountId, list[0]!.id, !chat.targetChosen);
    }
  }, [accountId, list, chat.model, chat.targetChosen]);

  return (
    <div className="row">
      <select
        className="select"
        aria-label="Chat target"
        style={{ width: "auto", maxWidth: 280 }}
        value={accountId ?? ""}
        disabled={sending}
        onChange={(e) => chatStore.selectTarget(e.target.value || null, null)}
      >
        <option value="">Default route</option>
        {usable.map((a) => (
          <option key={a.id} value={a.id}>
            {a.provider_name} · {a.label}
          </option>
        ))}
      </select>
      {accountId &&
        (models.isPending ? (
          <Spinner small />
        ) : (
          <select
            className="select"
            aria-label="Model"
            style={{ width: "auto", maxWidth: 240 }}
            value={list.some((m) => m.id === chat.model) ? chat.model! : ""}
            disabled={sending}
            onChange={(e) => chatStore.selectTarget(accountId, e.target.value)}
          >
            <option value="" disabled>
              {models.isError ? "Models unavailable" : "Model"}
            </option>
            {list.map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name ?? m.id}
              </option>
            ))}
          </select>
        ))}
    </div>
  );
}

function Bubble({ turn }: { turn: ChatTurn }) {
  const copy = useCopy();
  const mine = turn.role === "user";
  const meta = [turn.model, turn.totalTokens != null ? `${turn.totalTokens} tokens` : null].filter(Boolean).join(" · ");
  return (
    <div className={`bubble ${turn.error ? "failed" : mine ? "user" : "assistant"}`}>
      {(turn.text || turn.streaming) && <div>{turn.streaming ? `${turn.text}▍` : turn.text}</div>}
      {turn.error && (
        <div className="row">
          <Icon name="warning" size={16} />
          <span>{turn.error}</span>
        </div>
      )}
      {!mine && !turn.streaming && !turn.error && (
        <div className="row small muted">
          {meta && <span>{meta}</span>}
          <IconButton icon="copy" label="Copy" onClick={() => copy(turn.text)} />
        </div>
      )}
    </div>
  );
}
