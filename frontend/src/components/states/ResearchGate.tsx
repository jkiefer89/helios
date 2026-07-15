import type { ProvenancePayload, ResearchState } from "../../api/types";
import { sourceSummary } from "../../utils/format";

interface ResearchGateProps {
  payload: ProvenancePayload;
  state?: ResearchState;
  compact?: boolean;
  title?: string;
  actionLabel?: string;
  onAction?: () => void;
}

export function researchState(payload: ProvenancePayload, explicit?: ResearchState): ResearchState {
  if (explicit) return explicit;
  if (payload.eligible_for_real_research) return "ready";
  if (payload.data_mode === "mixed") return "mixed";
  const copy = `${payload.reason || ""} ${payload.required_action || ""}`.toLowerCase();
  if (copy.includes("stale") || copy.includes("refresh")) return "stale";
  const counts = payload.data_provenance?.source_counts || {};
  if (Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0) === 0) return "no_data";
  if (payload.data_mode === "invalid_for_research") return "invalid";
  return "blocked";
}

export function ResearchGate({ payload, state: explicitState, compact = false, title, actionLabel, onAction }: ResearchGateProps) {
  const provenance = payload.data_provenance || {};
  const state = researchState(payload, explicitState);
  const label = title || payload.display_label || provenance.display_label || stateLabel(state);
  const body = payload.reason || provenance.reason || payload.required_action || provenance.required_action || stateDescription(state);
  const sources = sourceSummary(provenance.source_counts, provenance.source_weight_pct);
  const missing = Array.isArray(provenance.missing_tickers) ? provenance.missing_tickers : [];
  return (
    <section
      className={`research-gate research-gate--${state} ${compact ? "compact" : ""}`}
      role={state === "ready" ? "status" : "alert"}
      aria-label={`${stateLabel(state)} research state`}
    >
      <span className="research-gate__state">{stateLabel(state)}</span>
      <div className="research-gate__copy">
        <strong>{label}</strong>
        <p>{body}</p>
      </div>
      <div className="research-gate__facts">
        {sources && <span>{sources}</span>}
        {missing.length > 0 && <span>Missing {missing.slice(0, 6).join(", ")}{missing.length > 6 ? ` +${missing.length - 6}` : ""}</span>}
      </div>
      {onAction && actionLabel && <button type="button" onClick={onAction}>{actionLabel}</button>}
    </section>
  );
}

function stateLabel(state: ResearchState): string {
  return {
    no_data: "No data",
    invalid: "Invalid",
    stale: "Stale",
    mixed: "Mixed",
    blocked: "Blocked",
    ready: "Ready",
  }[state];
}

function stateDescription(state: ResearchState): string {
  return {
    no_data: "Connect eligible live or uploaded histories before research can begin.",
    invalid: "The current evidence failed provenance or input validation checks.",
    stale: "Refresh the underlying histories before treating this output as current.",
    mixed: "Resolve mixed source quality before presenting unified model evidence.",
    blocked: "A required research or governance control has not passed.",
    ready: "Required provenance checks passed; research evidence is available.",
  }[state];
}
