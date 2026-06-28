import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { AIResponse, AIResult, AIStatusResponse } from "../../api/types";
import { fmtTimestamp } from "../../utils/format";

export interface CopilotAction {
  id: string;
  label: string;
  run: (payload: Record<string, unknown>, regenerate?: boolean) => Promise<AIResponse>;
}

interface AICopilotPanelProps {
  title?: string;
  contextLabel: string;
  payload: Record<string, unknown> | null;
  dataMode?: string;
  actions: CopilotAction[];
}

export function AICopilotPanel({
  title = "AI Copilot",
  contextLabel,
  payload,
  dataMode,
  actions,
}: AICopilotPanelProps) {
  const [status, setStatus] = useState<AIStatusResponse | null>(null);
  const [result, setResult] = useState<AIResult | null>(null);
  const [activeAction, setActiveAction] = useState("");
  const [question, setQuestion] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await api.aiStatus());
    } catch (err) {
      setStatus(null);
      setError(err instanceof Error ? err.message : "AI status unavailable.");
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  const computedDataMode = useMemo(() => {
    const explicit = dataMode || readDataMode(payload);
    return (explicit || "").toLowerCase();
  }, [dataMode, payload]);

  const modeWarning = useMemo(() => {
    if (computedDataMode === "demo") return "Demo data only — not real market evidence.";
    if (computedDataMode === "blocked" || computedDataMode === "invalid_for_research") {
      return "Data quality blocked — upload/fetch real data before generating research narrative.";
    }
    return "";
  }, [computedDataMode]);

  const runAction = async (action: CopilotAction, regenerate = false) => {
    if (!payload) {
      setError("No Helios payload is available for AI review.");
      return;
    }
    if (status && !status.available) {
      setError(status.reason || "AI provider unavailable.");
      return;
    }
    setLoading(true);
    setActiveAction(action.id);
    setError("");
    try {
      const response = await action.run(payload, regenerate);
      setResult(response.result);
      setStatus(response.status);
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : "AI provider request failed.");
      await refreshStatus();
    } finally {
      setLoading(false);
      setActiveAction("");
    }
  };

  const askQuestion = async () => {
    if (!payload || !question.trim()) return;
    await runAction({
      id: "question",
      label: "Ask question",
      run: (body, regenerate) => api.aiQuestion(body, question.trim(), regenerate),
    }, true);
  };

  const unavailable = !payload || !status?.available;
  const statusTone = status?.available ? "ready" : status?.enabled ? "warning" : "disabled";

  return (
    <section className={`ai-copilot ai-copilot-${statusTone}`} aria-label={`${title} for ${contextLabel}`}>
      <header className="ai-copilot__head">
        <div>
          <div className="section-label">{title}</div>
          <h2>{contextLabel}</h2>
          <p>AI-generated narrative; calculations from Helios engine.</p>
        </div>
        <button type="button" onClick={() => void refreshStatus()} disabled={loading}>Refresh status</button>
      </header>

      <div className="ai-status-grid">
        <span><b>{providerLabel(status)}</b><small>Provider</small></span>
        <span><b>{status?.model || "not configured"}</b><small>Model</small></span>
        <span><b>{status?.available ? "Available" : "Unavailable"}</b><small>Status</small></span>
      </div>

      <div className="ai-state-message">
        <strong>{statusMessage(status)}</strong>
        {status?.reason && <span>{status.reason}</span>}
        {status?.privacy_warning && <span>{status.privacy_warning}</span>}
        {(status?.security_warnings || []).map((warning) => <span key={warning}>{warning}</span>)}
        {modeWarning && <span>{modeWarning}</span>}
      </div>

      <div className="ai-actions">
        {actions.map((action) => (
          <button
            type="button"
            key={action.id}
            disabled={unavailable || loading}
            onClick={() => void runAction(action)}
          >
            {loading && activeAction === action.id ? "Generating..." : action.label}
          </button>
        ))}
      </div>

      <form className="ai-question" onSubmit={(event) => { event.preventDefault(); void askQuestion(); }}>
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask a question about this Helios analysis..."
          disabled={unavailable || loading}
        />
        <button type="submit" disabled={unavailable || loading || !question.trim()}>
          {loading && activeAction === "question" ? "Asking..." : "Ask"}
        </button>
      </form>

      {error && <div className="notice danger">{error}</div>}
      {result && <AIResultView result={result} />}
    </section>
  );
}

export const opportunityCopilotActions: CopilotAction[] = [
  { id: "explain", label: "Explain this opportunity", run: api.aiOpportunityExplain },
  { id: "critique", label: "Red-team this opportunity", run: api.aiOpportunityCritique },
];

export const strategyCopilotActions: CopilotAction[] = [
  { id: "strategy", label: "Summarize strategy evidence", run: api.aiStrategySummary },
];

export const clinicCopilotActions: CopilotAction[] = [
  { id: "clinic", label: "Explain portfolio clinic recommendations", run: api.aiClinicSummary },
];

export const reportCopilotActions: CopilotAction[] = [
  { id: "report", label: "Generate advisor narrative", run: api.aiReport },
];

function AIResultView({ result }: { result: AIResult }) {
  return (
    <article className="ai-result">
      <header>
        <div>
          <strong>{result.needs_review ? "Narrative needs review" : "Generated narrative"}</strong>
          <span>{fmtTimestamp(result.generated_at)}{result.cached ? " · cached" : ""}</span>
        </div>
        <small>{result.provider}{result.model ? ` · ${result.model}` : ""}</small>
      </header>
      {result.summary && <p className="lead">{result.summary}</p>}
      <div className="ai-result-grid">
        <ResultList title="Key points" rows={result.key_points} />
        <ResultList title="Risks" rows={result.risks} />
        <ResultList title="What would invalidate" rows={result.what_would_invalidate} />
        <ResultList title="Missing information" rows={result.missing_information} />
      </div>
      {result.advisor_language && (
        <blockquote className="ai-advisor-language">{result.advisor_language}</blockquote>
      )}
      {result.data_quality_statement && <p className="muted">{result.data_quality_statement}</p>}
      <ResultList title="Compliance caveats" rows={result.compliance_caveats} />
      {(result.unsupported_numbers?.length || result.blocked_phrases?.length) ? (
        <div className="ai-review-flags">
          {result.unsupported_numbers?.length ? <span>Unsupported numbers: {result.unsupported_numbers.join(", ")}</span> : null}
          {result.blocked_phrases?.length ? <span>Blocked phrases: {result.blocked_phrases.join(", ")}</span> : null}
        </div>
      ) : null}
    </article>
  );
}

function ResultList({ title, rows }: { title: string; rows: string[] }) {
  if (!rows.length) return null;
  return (
    <div className="ai-result-list">
      <h3>{title}</h3>
      <ul>{rows.map((row) => <li key={row}>{row}</li>)}</ul>
    </div>
  );
}

function providerLabel(status: AIStatusResponse | null) {
  if (!status) return "Checking";
  if (!status.enabled) return "Disabled";
  if (status.provider === "anthropic") return "Claude";
  if (status.provider === "local") return "Local";
  if (status.provider === "openai") return "OpenAI";
  return status.provider || "None";
}

function statusMessage(status: AIStatusResponse | null) {
  if (!status) return "Checking AI Copilot status...";
  if (!status.enabled) return "AI Copilot is off. Helios analytics still work normally.";
  if (status.provider === "anthropic" && status.available) return "Claude provider configured.";
  if (status.provider === "anthropic") return "Claude provider unavailable.";
  if (status.provider === "local") return status.available ? "Local AI provider configured." : "Local AI is not configured or not running.";
  if (status.provider === "openai") return status.available ? "OpenAI provider configured." : "OpenAI provider unavailable.";
  return status.reason || "AI provider unavailable.";
}

function readDataMode(payload: Record<string, unknown> | null): string {
  if (!payload) return "";
  const direct = payload.data_mode;
  if (typeof direct === "string") return direct;
  const provenance = payload.data_provenance;
  if (provenance && typeof provenance === "object" && "data_mode" in provenance) {
    const value = (provenance as Record<string, unknown>).data_mode;
    return typeof value === "string" ? value : "";
  }
  return "";
}
