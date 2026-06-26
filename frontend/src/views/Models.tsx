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
          <div className="terminal-table models-table">
            <div className="terminal-table__head">
              <span>Name</span><span>Mandate</span><span>Holdings</span><span>Top Holding</span><span>Actions</span>
            </div>
            {models.map((model) => (
              <div className="table-row" key={model.id}>
                <span><strong>{model.name}</strong><small>{model.id}</small></span>
                <span>{model.mandate_label}</span>
                <span>{model.n_holdings}</span>
                <span>{model.top || "—"}</span>
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
