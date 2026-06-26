import type { FormEvent, ReactNode } from "react";
import type { DataMode, MandateSummary, ModelSummary, TickerSummary } from "../../api/types";
import { DataModeBadge, SourcePill } from "../badges/DataModeBadge";
import { fmtMoney, fmtPct } from "../../utils/format";

export type ViewId = "command" | "instruments" | "models" | "opportunities" | "strategy" | "clinic" | "reports" | "analysis";

interface ShellProps {
  activeView: ViewId;
  onViewChange: (view: ViewId) => void;
  dataMode?: { mode?: DataMode; label?: string };
  tickers: TickerSummary[];
  models: ModelSummary[];
  mandates: MandateSummary[];
  selectedInstrument?: string;
  selectedModel?: string;
  onSelectInstrument: (symbol: string) => void;
  onSelectModel: (id: string) => void;
  onUploadPrice: (file: File, symbol: string) => Promise<void>;
  onUploadModel: (file: File, name: string, mandate: string, context: string) => Promise<void>;
  onFetchLive: (symbol: string) => Promise<void>;
  liveAvailable: boolean;
  notice?: string;
  children: ReactNode;
}

const views: Array<{ id: ViewId; label: string }> = [
  { id: "command", label: "Command Center" },
  { id: "instruments", label: "Instruments" },
  { id: "models", label: "Models" },
  { id: "opportunities", label: "Opportunity Radar" },
  { id: "strategy", label: "Strategy Lab" },
  { id: "clinic", label: "Portfolio Clinic" },
  { id: "reports", label: "Reports" },
  { id: "analysis", label: "Analysis" },
];

export function AppShell(props: ShellProps) {
  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand" type="button" onClick={() => props.onViewChange("command")} aria-label="Open Command Center">
          <span className="brand-mark" aria-hidden="true" />
          <span><b>Helios Pro</b><small>Advisor research terminal</small></span>
        </button>
        <nav className="primary-nav" aria-label="Primary workspace">
          {views.map((view) => (
            <button
              key={view.id}
              className={props.activeView === view.id ? "active" : ""}
              type="button"
              onClick={() => props.onViewChange(view.id)}
            >
              {view.label}
            </button>
          ))}
        </nav>
        <div className="topbar__status">
          <DataModeBadge mode={props.dataMode?.mode} label={props.dataMode?.label || "Data status pending"} />
          <span>Analysis only · no advice · no order execution</span>
        </div>
      </header>
      <div className="workspace">
        <aside className="sidebar">
          <ImportPanel {...props} />
          <section className="side-section">
            <h2>Client Models</h2>
            <div className="side-list">
              {props.models.length === 0 ? <p className="muted">No models imported.</p> : props.models.map((model) => (
                <button
                  type="button"
                  key={model.id}
                  className={props.selectedModel === model.id ? "active" : ""}
                  onClick={() => props.onSelectModel(model.id)}
                >
                  <span><b>{model.name}</b><small>{model.mandate_label} · {model.n_holdings} holdings</small></span>
                  <em>{model.top || ""}</em>
                </button>
              ))}
            </div>
          </section>
          <section className="side-section">
            <h2>Instruments</h2>
            <div className="side-list">
              {props.tickers.map((ticker) => (
                <button
                  type="button"
                  key={ticker.symbol}
                  className={props.selectedInstrument === ticker.symbol ? "active" : ""}
                  onClick={() => props.onSelectInstrument(ticker.symbol)}
                >
                  <span><b>{ticker.symbol}</b><small>{ticker.name}</small></span>
                  <em>{fmtMoney(ticker.last_price)} <i className={(ticker.change_pct || 0) >= 0 ? "up" : "down"}>{fmtPct(ticker.change_pct)}</i></em>
                  <SourcePill source={ticker.source} />
                </button>
              ))}
            </div>
          </section>
        </aside>
        <main className="content">
          {props.notice && <div className="notice">{props.notice}</div>}
          {props.children}
        </main>
      </div>
    </div>
  );
}

function ImportPanel(props: ShellProps) {
  const priceForm = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const file = (form.elements.namedItem("priceFile") as HTMLInputElement).files?.[0];
    const symbol = (form.elements.namedItem("priceSymbol") as HTMLInputElement).value;
    if (file) void props.onUploadPrice(file, symbol);
    form.reset();
  };
  const modelForm = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const file = (form.elements.namedItem("modelFile") as HTMLInputElement).files?.[0];
    const name = (form.elements.namedItem("modelName") as HTMLInputElement).value;
    const mandate = (form.elements.namedItem("modelMandate") as HTMLSelectElement).value;
    const context = (form.elements.namedItem("modelContext") as HTMLTextAreaElement).value;
    if (file) void props.onUploadModel(file, name, mandate, context);
    form.reset();
  };
  const liveForm = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const symbol = (form.elements.namedItem("liveSymbol") as HTMLInputElement).value.trim();
    if (symbol) void props.onFetchLive(symbol);
    form.reset();
  };
  return (
    <section className="side-section onboarding">
      <h2>Real Data Onboarding</h2>
      <p>Real research unlocks only from live or uploaded price history. Sample data remains demo-only.</p>
      <form onSubmit={liveForm}>
        <label>Fetch live ticker</label>
        <div className="inline-form">
          <input name="liveSymbol" aria-label="Live ticker symbol" disabled={!props.liveAvailable} />
          <button type="submit" disabled={!props.liveAvailable}>Fetch</button>
        </div>
      </form>
      <form onSubmit={priceForm}>
        <label>Upload price CSV</label>
        <input name="priceFile" type="file" accept=".csv" aria-label="Price CSV" />
        <input name="priceSymbol" aria-label="Uploaded series symbol" />
        <button type="submit">Upload price history</button>
      </form>
      <form onSubmit={modelForm}>
        <label>Upload model CSV/Excel</label>
        <input name="modelFile" type="file" accept=".xlsx,.xlsm,.csv,.tsv" aria-label="Model file" />
        <input name="modelName" aria-label="Model name" />
        <select name="modelMandate" aria-label="Model mandate">
          {props.mandates.map((mandate) => <option key={mandate.key} value={mandate.key}>{mandate.label}</option>)}
        </select>
        <textarea name="modelContext" rows={2} aria-label="Model context" />
        <button type="submit">Upload model</button>
      </form>
    </section>
  );
}
