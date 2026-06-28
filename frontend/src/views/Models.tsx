import type { KeyboardEvent } from "react";
import type { ModelSummary } from "../api/types";
import { Panel } from "../components/cards/Panel";
import { EmptyState } from "../components/empty-states/EmptyState";

export function Models({
  models,
  onOpenModel,
  onOpenClinic,
}: {
  models: ModelSummary[];
  onOpenModel: (id: string) => void;
  onOpenClinic: (id: string) => void;
}) {
  return (
    <div className="view-stack">
      <header className="view-head">
        <div>
          <div className="section-label">Models</div>
          <h1>Client model workspace</h1>
          <p>Model-level research stays blocked until every analyzed holding has live or uploaded price history.</p>
        </div>
      </header>
      <Panel title="Imported Models" meta={`${models.length} loaded`}>
        {models.length === 0 ? (
          <EmptyState title="No models imported" body="Upload a model CSV or Excel file to unlock model diagnostics." />
        ) : (
          <div className="terminal-table models-table" tabIndex={0} aria-label="Scrollable imported models table" onKeyDown={scrollTableByKey}>
            <div className="terminal-table__head">
              <span>Name</span><span>Mandate</span><span>Coverage</span><span>Missing</span><span>Actions</span>
            </div>
            {models.map((model) => (
              <div className="table-row" key={model.id}>
                <span><strong>{model.name}</strong><small>{model.id}</small></span>
                <span>{model.mandate_label}</span>
                <span>{model.real_coverage_count || 0}/{model.n_holdings}</span>
                <span>{model.missing_tickers?.slice(0, 3).join(", ") || "None"}</span>
                <span className="row-actions">
                  <button type="button" onClick={() => onOpenModel(model.id)}>Analysis</button>
                  <button type="button" onClick={() => onOpenClinic(model.id)}>Clinic</button>
                </span>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}

function scrollTableByKey(event: KeyboardEvent<HTMLDivElement>) {
  const table = event.currentTarget;
  const pageStep = Math.max(160, table.clientHeight - 56);
  if (event.key === "ArrowRight") {
    event.preventDefault();
    table.scrollLeft += 80;
    return;
  }
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    table.scrollLeft -= 80;
    return;
  }
  if (event.key === "PageDown") {
    event.preventDefault();
    table.scrollTop += pageStep;
  }
  if (event.key === "PageUp") {
    event.preventDefault();
    table.scrollTop -= pageStep;
  }
}
