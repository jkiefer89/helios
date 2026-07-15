import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { AIStatusResponse } from "../../api/types";

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
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    void api.aiStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  // New analysis target -> new conversation.
  useEffect(() => {
    setMessages([]);
    setError("");
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
      setError(err instanceof Error ? err.message : "Copilot request failed.");
      setMessages(messages); // roll back the optimistic user turn
      setInput(text);
    } finally {
      setLoading(false);
    }
  }, [input, loading, messages, payload]);

  const unavailable = status ? !status.available : false;

  return (
    <div className="copilot-chat">
      <div className="copilot-chat__meta">
        <span>Dialogue over {contextLabel} — engine numbers are authoritative; the copilot argues, you decide.</span>
        <span>{status?.model || "model n/a"}</span>
      </div>
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
