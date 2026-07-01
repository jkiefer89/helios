import { useEffect, useMemo, useRef, useState, type FocusEvent, type FormEvent, type ReactNode } from "react";
import type { DataMode, DataStatusResponse, MandateSummary, ModelSummary, TickerSummary } from "../../api/types";
import { DataModeBadge, SourcePill } from "../badges/DataModeBadge";
import { TerminalSelect } from "../forms/TerminalSelect";
import { fmtMoney, fmtPct } from "../../utils/format";

export type ViewId = "command" | "instruments" | "models" | "opportunities" | "strategy" | "clinic" | "risk" | "reports" | "journal" | "data-quality" | "analysis";

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
  onRefreshData: (symbol?: string, all?: boolean) => Promise<void>;
  liveAvailable: boolean;
  notice?: string;
  dataStatus: DataStatusResponse | null;
  children: ReactNode;
}

const views: Array<{ id: ViewId; label: string }> = [
  { id: "command", label: "Command Center" },
  { id: "instruments", label: "Instruments" },
  { id: "models", label: "Models" },
  { id: "opportunities", label: "Opportunity Radar" },
  { id: "strategy", label: "Strategy Lab" },
  { id: "clinic", label: "Portfolio Clinic" },
  { id: "risk", label: "Risk Analytics" },
  { id: "reports", label: "Reports" },
  { id: "journal", label: "Signal Journal" },
  { id: "data-quality", label: "Data Quality" },
  { id: "analysis", label: "Analysis" },
];

function compactDataModeLabel(mode: DataMode | undefined, label: string) {
  if (mode === "demo") return "Demo Mode";
  if (mode === "real") return "Live Data";
  if (mode === "mixed") return "Mixed Data";
  if (mode === "invalid_for_research") return "Research Locked";
  return label.length > 18 ? "Data Status" : label;
}

export function AppShell(props: ShellProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [advisorOpen, setAdvisorOpen] = useState(false);
  const searchShellRef = useRef<HTMLDivElement | null>(null);
  const advisorShellRef = useRef<HTMLDivElement | null>(null);
  const activeViewLabel = views.find((view) => view.id === props.activeView)?.label || "Workspace";
  const selectedContext = props.selectedModel
    ? props.models.find((model) => model.id === props.selectedModel)?.name || props.selectedModel
    : props.selectedInstrument
      ? props.tickers.find((ticker) => ticker.symbol === props.selectedInstrument)?.symbol || props.selectedInstrument
      : "No active selection";
  const dataModeFullLabel = props.dataMode?.label || "Data status pending";
  const dataModeShortLabel = compactDataModeLabel(props.dataMode?.mode, dataModeFullLabel);
  const searchResults = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    const viewResults = views.map((view) => ({
      key: `view-${view.id}`,
      label: view.label,
      meta: "Workspace",
      action: () => props.onViewChange(view.id),
    }));
    const instrumentResults = props.tickers.map((ticker) => ({
      key: `instrument-${ticker.symbol}`,
      label: `${ticker.symbol} · ${ticker.name}`,
      meta: `Instrument · ${ticker.source || "source pending"}`,
      action: () => props.onSelectInstrument(ticker.symbol),
    }));
    const modelResults = props.models.map((model) => ({
      key: `model-${model.id}`,
      label: model.name,
      meta: `Model · ${model.mandate_label} · ${model.n_holdings} holdings`,
      action: () => props.onSelectModel(model.id),
    }));
    const all = [...viewResults, ...instrumentResults, ...modelResults];
    if (!query) return all.slice(0, 7);
    return all
      .filter((item) => `${item.label} ${item.meta}`.toLowerCase().includes(query))
      .slice(0, 8);
  }, [props.models, props.onSelectInstrument, props.onSelectModel, props.onViewChange, props.tickers, searchQuery]);
  const runSearchResult = (result: { action: () => void }) => {
    result.action();
    setSearchOpen(false);
    setSearchQuery("");
  };
  const submitSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const first = searchResults[0];
    if (first) runSearchResult(first);
  };
  const runAdvisorAction = (view: ViewId) => {
    props.onViewChange(view);
    setAdvisorOpen(false);
  };
  const closeSearchOnBlur = (event: FocusEvent<HTMLDivElement>) => {
    const nextTarget = event.relatedTarget;
    if (nextTarget && searchShellRef.current?.contains(nextTarget as Node)) return;
    setSearchOpen(false);
  };
  const closeAdvisorOnBlur = (event: FocusEvent<HTMLDivElement>) => {
    const nextTarget = event.relatedTarget;
    if (nextTarget && advisorShellRef.current?.contains(nextTarget as Node)) return;
    setAdvisorOpen(false);
  };
  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand" type="button" onClick={() => props.onViewChange("command")} aria-label="Open Command Center">
          <span className="brand-mark" aria-hidden="true" />
          <span><b>Helios Pro</b><small>Advisor-grade research terminal</small></span>
        </button>
        <nav className="primary-nav" aria-label="Primary workspace">
          {views.map((view) => (
            <button
              key={view.id}
              className={props.activeView === view.id ? "active" : ""}
              type="button"
              onClick={() => props.onViewChange(view.id)}
            >
              <ShellIcon id={view.id} />
              <span>{view.label}</span>
            </button>
          ))}
        </nav>
        <div className="topbar__tools">
          <DataModeBadge mode={props.dataMode?.mode} label={dataModeShortLabel} title={dataModeFullLabel} />
          <div className="search-shell" ref={searchShellRef} onBlur={closeSearchOnBlur}>
            <form className="terminal-search" onSubmit={submitSearch}>
              <span aria-hidden="true">/</span>
              <input
                aria-label="Search instruments, models, and reports"
                placeholder="Search instruments, models, reports..."
                value={searchQuery}
                onChange={(event) => {
                  setSearchQuery(event.currentTarget.value);
                  setSearchOpen(true);
                }}
                onFocus={() => setSearchOpen(true)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    setSearchOpen(false);
                    return;
                  }
                  if (event.key !== "Enter") return;
                  event.preventDefault();
                  const first = searchResults[0];
                  if (first) runSearchResult(first);
                }}
              />
            </form>
            {searchOpen && (
              <div className="search-popover">
                {searchResults.length === 0 ? (
                  <span className="search-empty">No matching instruments, models, or views.</span>
                ) : searchResults.map((result) => (
                  <button type="button" key={result.key} onClick={() => runSearchResult(result)}>
                    <strong>{result.label}</strong>
                    <small>{result.meta}</small>
                  </button>
                ))}
              </div>
            )}
          </div>
          <button className="tool-icon" type="button" aria-label="Open reports" onClick={() => props.onViewChange("reports")}>!</button>
          <button className="tool-icon" type="button" aria-label="Open analysis" onClick={() => props.onViewChange("analysis")}>?</button>
          <button className="tool-icon" type="button" aria-label="Open instruments" onClick={() => props.onViewChange("instruments")}>*</button>
          <div className="advisor-shell" ref={advisorShellRef} onBlur={closeAdvisorOnBlur}>
            <button
              className="advisor-identity"
              type="button"
              aria-label="Advisor Console"
              aria-expanded={advisorOpen}
              aria-haspopup="menu"
              onClick={() => setAdvisorOpen((open) => !open)}
              onKeyDown={(event) => {
                if (event.key === "Escape") setAdvisorOpen(false);
              }}
            >
              <span className="advisor-avatar" aria-hidden="true">AC</span>
              <span className="advisor-label">Advisor Console<small>Local workspace</small></span>
            </button>
            {advisorOpen && (
              <div
                className="advisor-menu"
                role="menu"
                onKeyDown={(event) => {
                  if (event.key === "Escape") setAdvisorOpen(false);
                }}
              >
                <header>
                  <strong>Advisor Console</strong>
                  <small>Local Helios workspace</small>
                </header>
                <dl>
                  <div><dt>Data mode</dt><dd>{props.dataMode?.label || "Pending"}</dd></div>
                  <div><dt>SQLite</dt><dd>{props.dataStatus?.database.available ? "Ready" : "Warning"}</dd></div>
                  <div><dt>Real histories</dt><dd>{props.dataStatus?.real_instrument_count ?? 0}</dd></div>
                  <div><dt>Current view</dt><dd>{activeViewLabel}</dd></div>
                  <div><dt>Selection</dt><dd>{selectedContext}</dd></div>
                </dl>
                <div className="advisor-menu__actions">
                  <button type="button" role="menuitem" onClick={() => runAdvisorAction("command")}>Command Center</button>
                  <button type="button" role="menuitem" onClick={() => runAdvisorAction("instruments")}>Real Data Setup</button>
                  <button type="button" role="menuitem" onClick={() => runAdvisorAction("models")}>Client Models</button>
                  <button type="button" role="menuitem" onClick={() => runAdvisorAction("journal")}>Signal Journal</button>
                  <button type="button" role="menuitem" onClick={() => runAdvisorAction("risk")}>Risk Analytics</button>
                  <button type="button" role="menuitem" onClick={() => runAdvisorAction("data-quality")}>Data Quality</button>
                  <button type="button" role="menuitem" onClick={() => runAdvisorAction("reports")}>Reports & Disclosures</button>
                </div>
                <p>Authentication and data permissions are controlled by the Flask backend. Real research unlocks only after provenance checks pass.</p>
              </div>
            )}
          </div>
        </div>
      </header>
      <div className={`workspace ${props.activeView === "command" ? "workspace-command" : ""}`}>
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

function ShellIcon({ id }: { id: ViewId }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  return (
    <span className="nav-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24">
        {id === "command" && (
          <>
            <path {...common} d="M5 6h5v5H5zM14 6h5v5h-5zM5 15h5v3H5zM14 15h5v3h-5z" />
          </>
        )}
        {id === "instruments" && <path {...common} d="M7 17V7m5 10V4m5 13v-6M4 17h16" />}
        {id === "models" && <path {...common} d="M7 8a4 4 0 0 1 8 0v1h1a3 3 0 0 1 0 6h-2m-4 0H8a3 3 0 0 1-.6-5.9" />}
        {id === "opportunities" && <path {...common} d="M12 4v3m0 10v3M4 12h3m10 0h3m-5.5-4.5 2-2m-9 9-2 2m0-11 2 2m9 9-2-2" />}
        {id === "strategy" && <path {...common} d="M5 18 12 5l7 13M8.2 14h7.6M10 10h4" />}
        {id === "clinic" && <path {...common} d="M12 21s7-4.5 7-11V5l-7-3-7 3v5c0 6.5 7 11 7 11Z" />}
        {id === "risk" && <path {...common} d="M4 18h16M6 15l4-5 4 3 4-7M7 18V8m5 10v-5m5 5V6" />}
        {id === "reports" && <path {...common} d="M7 3h7l4 4v14H7zM14 3v5h5M10 12h6M10 16h6" />}
        {id === "journal" && <path {...common} d="M5 19V5m4 14V9m4 10v-7m4 7v-4M4 19h16" />}
        {id === "data-quality" && <path {...common} d="M5 19V5m0 14h14M9 15l2-3 3 2 4-7M8 7h.01M8 11h.01" />}
        {id === "analysis" && <path {...common} d="M5 17l4-4 3 3 7-8M5 21h14" />}
      </svg>
    </span>
  );
}

function ImportPanel(props: ShellProps) {
  const [formNotice, setFormNotice] = useState("");
  const [pendingForm, setPendingForm] = useState<"" | "live" | "price" | "model" | "refresh">("");
  const [modelMandate, setModelMandate] = useState(props.mandates[0]?.key || "");
  const mandateOptions = props.mandates.map((mandate) => ({ value: mandate.key, label: mandate.label }));
  useEffect(() => {
    if (modelMandate || !props.mandates[0]) return;
    setModelMandate(props.mandates[0].key);
  }, [modelMandate, props.mandates]);
  const clearNotice = () => setFormNotice("");
  const priceForm = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const file = (form.elements.namedItem("priceFile") as HTMLInputElement).files?.[0];
    const symbol = (form.elements.namedItem("priceSymbol") as HTMLInputElement).value;
    if (!file) {
      setFormNotice("Choose a price CSV before uploading price history.");
      return;
    }
    clearNotice();
    setPendingForm("price");
    try {
      await props.onUploadPrice(file, symbol);
      setFormNotice("Price history uploaded. Analysis will refresh when processing completes.");
      form.reset();
    } catch (error) {
      setFormNotice(error instanceof Error ? error.message : "Price upload failed.");
    } finally {
      setPendingForm("");
    }
  };
  const modelForm = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const file = (form.elements.namedItem("modelFile") as HTMLInputElement).files?.[0];
    const name = (form.elements.namedItem("modelName") as HTMLInputElement).value;
    const mandate = modelMandate || props.mandates[0]?.key || "";
    const context = (form.elements.namedItem("modelContext") as HTMLTextAreaElement).value;
    if (!file) {
      setFormNotice("Choose a model CSV or spreadsheet before importing a model.");
      return;
    }
    clearNotice();
    setPendingForm("model");
    try {
      await props.onUploadModel(file, name, mandate, context);
      setFormNotice("Model imported. Portfolio Clinic will refresh when processing completes.");
      form.reset();
    } catch (error) {
      setFormNotice(error instanceof Error ? error.message : "Model upload failed.");
    } finally {
      setPendingForm("");
    }
  };
  const liveForm = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const symbol = (form.elements.namedItem("liveSymbol") as HTMLInputElement).value.trim();
    if (!symbol) {
      setFormNotice("Enter a ticker symbol before fetching live data.");
      return;
    }
    clearNotice();
    setPendingForm("live");
    try {
      await props.onFetchLive(symbol);
      setFormNotice("Live data fetched. Analysis will refresh when processing completes.");
      form.reset();
    } catch (error) {
      setFormNotice(error instanceof Error ? error.message : "Live data fetch failed.");
    } finally {
      setPendingForm("");
    }
  };
  const refreshLiveData = async () => {
    clearNotice();
    setPendingForm("refresh");
    try {
      await props.onRefreshData(undefined, true);
      setFormNotice("Live refresh completed. Check Real Data Center for row counts and warnings.");
    } catch (error) {
      setFormNotice(error instanceof Error ? error.message : "Live refresh failed.");
    } finally {
      setPendingForm("");
    }
  };
  const liveCount = props.tickers.filter((ticker) => ticker.source === "live").length;
  const realDataReady = liveCount > 0 || Boolean(props.dataStatus?.data_mode_summary?.eligible_for_real_research);
  const onboardingCopy = realDataReady
    ? {
        title: "Live refresh active",
        body: `${liveCount} live ${liveCount === 1 ? "history is" : "histories are"} ready for real research. Automatic refresh keeps the local evidence set current while provenance checks remain visible.`,
      }
    : {
        title: "Real Data Onboarding",
        body: "Connect live or uploaded price history to unlock real research. Bundled sample histories are excluded from real research evidence.",
      };
  return (
    <section className="side-section onboarding">
      <h2>{onboardingCopy.title}</h2>
      <p>{onboardingCopy.body}</p>
      {formNotice && <div className="form-feedback" role="status">{formNotice}</div>}
      <form onSubmit={liveForm}>
        <label>Fetch live ticker</label>
        <div className="inline-form">
          <input name="liveSymbol" aria-label="Live ticker symbol" disabled={!props.liveAvailable || pendingForm !== ""} />
          <button type="submit" disabled={!props.liveAvailable || pendingForm !== ""}>{pendingForm === "live" ? "Fetching..." : "Fetch"}</button>
        </div>
        {!props.liveAvailable && <small className="form-hint">Live fetch is unavailable in this environment. Upload a price CSV to unlock real-data analysis.</small>}
      </form>
      <button
        className="side-secondary-action"
        type="button"
        onClick={refreshLiveData}
        disabled={!liveCount || pendingForm !== ""}
      >
        {pendingForm === "refresh" ? "Refreshing live data..." : `Refresh live data (${liveCount})`}
      </button>
      <form onSubmit={priceForm}>
        <label>Upload price CSV</label>
        <input name="priceFile" type="file" accept=".csv" aria-label="Price CSV" disabled={pendingForm !== ""} />
        <input name="priceSymbol" aria-label="Uploaded series symbol" disabled={pendingForm !== ""} />
        <button type="submit" disabled={pendingForm !== ""}>{pendingForm === "price" ? "Uploading..." : "Upload price history"}</button>
      </form>
      <form onSubmit={modelForm}>
        <label>Upload model CSV/Excel</label>
        <input name="modelFile" type="file" accept=".xlsx,.xlsm,.csv,.tsv" aria-label="Model file" disabled={pendingForm !== ""} />
        <input name="modelName" aria-label="Model name" disabled={pendingForm !== ""} />
        <TerminalSelect
          name="modelMandate"
          ariaLabel="Model mandate"
          value={modelMandate}
          options={mandateOptions}
          onChange={setModelMandate}
          disabled={pendingForm !== ""}
          placeholder="Select mandate"
        />
        <textarea name="modelContext" rows={2} aria-label="Model context" disabled={pendingForm !== ""} />
        <button type="submit" disabled={pendingForm !== ""}>{pendingForm === "model" ? "Uploading..." : "Upload model"}</button>
      </form>
    </section>
  );
}
