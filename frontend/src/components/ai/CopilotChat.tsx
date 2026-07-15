import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../../api/client";
import type { AIStatusResponse, CloudTransferDisclosure } from "../../api/types";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

interface CopilotChatProps {
  contextLabel: string;
  /** Sanitized-enough context sent with every turn (server sanitizes again). */
  payload: Record<string, unknown> | null;
}

/**
 * Multi-turn research dialogue with the AI copilot over the current analysis.
 * The operator can argue with the copilot; the copilot argues back from the
 * engine's numbers. Conversation state lives in the component (per analysis).
 */
export function CopilotChat({ contextLabel, payload }: CopilotChatProps) {
  const [status, setStatus] = useState<AIStatusResponse | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [pendingTransfer, setPendingTransfer] = useState<{
    messages: ChatMessage[];
    payload: Record<string, unknown>;
    disclosure: CloudTransferDisclosure;
  } | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    void api.aiStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  // New analysis target -> new conversation.
  useEffect(() => {
    setMessages([]);
    setError("");
    setPendingTransfer(null);
  }, [contextLabel]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, loading]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || loading || !payload) return;
    const next: ChatMessage[] = [...messages, { role: "user", content: text }];
    setMessages(next);
    setInput("");
    setLoading(true);
    setError("");
    try {
      const res = await api.aiChat(next, payload);
      setMessages([...next, { role: "assistant", content: res.reply }]);
    } catch (err) {
      if (err instanceof ApiError && err.code === "cloud_ai_confirmation_required") {
        const disclosure = err.details.cloud_transfer as CloudTransferDisclosure | undefined;
        if (disclosure?.disclosure_hash) {
          setPendingTransfer({ messages: next, payload, disclosure });
          setError("");
        } else {
          setError("Cloud transfer confirmation metadata was incomplete.");
          setMessages(messages);
          setInput(text);
        }
      } else {
        setError(err instanceof Error ? err.message : "Copilot request failed.");
        setMessages(messages); // roll back the optimistic user turn
        setInput(text);
      }
    } finally {
      setLoading(false);
    }
  }, [input, loading, messages, payload]);

  const confirmTransfer = useCallback(async () => {
    if (!pendingTransfer) return;
    setLoading(true);
    setError("");
    try {
      const res = await api.aiChat(
        pendingTransfer.messages,
        pendingTransfer.payload,
        { confirmed: true, disclosure_hash: pendingTransfer.disclosure.disclosure_hash },
      );
      setMessages([...pendingTransfer.messages, { role: "assistant", content: res.reply }]);
      setPendingTransfer(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Copilot request failed.");
    } finally {
      setLoading(false);
    }
  }, [pendingTransfer]);

  const unavailable = status ? !status.available : false;

  return (
    <div className="copilot-chat">
      <div className="copilot-chat__meta">
        <span>Dialogue over {contextLabel} — engine numbers are authoritative; the copilot argues, you decide.</span>
        <span>{status?.model || "model n/a"}</span>
      </div>
      {status?.privacy_warning && <div className="notice warning">{status.privacy_warning}</div>}
      <div className="copilot-chat__scroll" ref={scrollRef}>
        {messages.length === 0 && (
          <p className="copilot-chat__empty">
            Ask for a forecast read, challenge a rating, or red-team the evidence.
            Example: “The engine says {contextLabel} is a HOLD — argue the other side.”
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`copilot-msg copilot-msg--${m.role}`}>
            <span className="copilot-msg__who">{m.role === "user" ? "You" : "Copilot"}</span>
            <div className="copilot-msg__body">{m.content}</div>
          </div>
        ))}
        {loading && <div className="copilot-msg copilot-msg--assistant"><span className="copilot-msg__who">Copilot</span><div className="copilot-msg__body copilot-msg__body--pending">Working…</div></div>}
      </div>
      {pendingTransfer && (
        <div className="ai-cloud-confirmation" role="alert">
          <strong>Confirm sanitized cloud dialogue</strong>
          <span>
            Local DLP redacted {pendingTransfer.disclosure.redaction_count} sensitive value(s).
            The sanitized conversation and Helios context will leave this machine only after confirmation.
          </span>
          <small>
            {pendingTransfer.disclosure.provider} / {pendingTransfer.disclosure.model} / {pendingTransfer.disclosure.task}
          </small>
          {pendingTransfer.disclosure.redacted_fields.length > 0 && (
            <small>Redacted fields: {pendingTransfer.disclosure.redacted_fields.join(", ")}</small>
          )}
          <small>Transfer fingerprint: {pendingTransfer.disclosure.disclosure_hash.slice(0, 12)}</small>
          <div>
            <button type="button" disabled={loading} onClick={() => void confirmTransfer()}>Confirm and send</button>
            <button
              type="button"
              disabled={loading}
              onClick={() => {
                const last = pendingTransfer.messages[pendingTransfer.messages.length - 1];
                setInput(last?.role === "user" ? last.content : "");
                setMessages(messages.slice(0, -1));
                setPendingTransfer(null);
              }}
            >Cancel</button>
          </div>
        </div>
      )}
      {error && <div className="notice danger" role="alert">{error}</div>}
      <form
        className="copilot-chat__input"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <textarea
          value={input}
          rows={2}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send();
            }
          }}
          placeholder={unavailable ? (status?.reason || "AI provider unavailable.") : "Argue with the copilot… (Enter to send, Shift+Enter for newline)"}
          disabled={unavailable || loading || !payload}
        />
        <button type="submit" disabled={unavailable || loading || !input.trim() || !payload}>
          {loading ? "…" : "Send"}
        </button>
      </form>
    </div>
  );
}
