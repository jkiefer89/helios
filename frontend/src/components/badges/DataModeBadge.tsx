import type { ProvenancePayload } from "../../api/types";
import { ResearchGate } from "../states/ResearchGate";

export function modeTone(mode?: string) {
  if (mode === "real") return "positive";
  if (mode === "mixed") return "warning";
  if (mode === "invalid_for_research") return "negative";
  return "neutral";
}

export function DataModeBadge({ mode, label, title }: { mode?: string; label?: string; title?: string }) {
  const text = label || "Data status unavailable";
  return <span className={`badge tone-${modeTone(mode)}`} title={title || text}>{text}</span>;
}

export function DataQualityBanner({
  payload,
  compact = false,
  actionLabel,
  onAction,
}: {
  payload: ProvenancePayload;
  compact?: boolean;
  actionLabel?: string;
  onAction?: () => void;
}) {
  const blocked = payload.eligible_for_real_research !== true;
  const defaultAction = blocked
    ? () => window.dispatchEvent(new Event("helios:reveal-data-intake"))
    : undefined;
  return (
    <ResearchGate
      payload={payload}
      compact={compact}
      actionLabel={blocked ? actionLabel || "Resolve data gate" : undefined}
      onAction={blocked ? onAction || defaultAction : undefined}
    />
  );
}

export function SourcePill({ source }: { source?: string }) {
  const excluded = source === "sample" || source === "simulated" || source === "demo";
  const label = excluded ? "ineligible" : source || "unavailable";
  return <span className={`source-pill source-${sourceTone(excluded ? "excluded" : source)}`}>{label}</span>;
}

function sourceTone(source?: string) {
  if (source === "live" || source === "upload" || source === "model" || source === "excluded") return source;
  return "unknown";
}
