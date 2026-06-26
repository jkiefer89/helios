import type { ProvenancePayload } from "../../api/types";
import { sourceSummary } from "../../utils/format";

export function modeTone(mode?: string) {
  if (mode === "real") return "positive";
  if (mode === "mixed") return "warning";
  if (mode === "invalid_for_research") return "negative";
  return "neutral";
}

export function DataModeBadge({ mode, label }: { mode?: string; label?: string }) {
  return <span className={`badge tone-${modeTone(mode)}`}>{label || "Data status unavailable"}</span>;
}

export function DataQualityBanner({ payload, compact = false }: { payload: ProvenancePayload; compact?: boolean }) {
  const provenance = payload.data_provenance || {};
  const summary = sourceSummary(provenance.source_counts, provenance.source_weight_pct);
  const label = payload.display_label || provenance.display_label || "Data Quality";
  const body = payload.reason || provenance.reason || payload.required_action || provenance.required_action ||
    "Source quality determines which research panels can unlock.";
  const eligible = payload.eligible_for_real_research ?? provenance.eligible_for_real_research;
  return (
    <section className={`quality-banner tone-${modeTone(payload.data_mode || provenance.data_mode)} ${compact ? "compact" : ""}`}>
      <div>
        <strong>{label}</strong>
        <p>{body}</p>
      </div>
      <div className="quality-banner__chips">
        {summary && <span>{summary}</span>}
        {typeof eligible === "boolean" && <span>{eligible ? "Eligible real research" : "Research locked"}</span>}
        {Array.isArray(provenance.missing_tickers) && provenance.missing_tickers.length > 0 && (
          <span>Missing {provenance.missing_tickers.join(", ")}</span>
        )}
      </div>
    </section>
  );
}

export function SourcePill({ source }: { source?: string }) {
  return <span className={`source-pill source-${source || "unknown"}`}>{source || "unavailable"}</span>;
}
