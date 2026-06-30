import { useState } from "react";
import type { DataStatusResponse, ModelSummary, TickerSummary } from "../../api/types";
import { SourcePill } from "../badges/DataModeBadge";
import { Panel, StatTile } from "../cards/Panel";
import { EmptyState } from "../empty-states/EmptyState";
import { fmtNumber, fmtTimestamp } from "../../utils/format";

const templates = [
  { label: "Close-only price CSV", body: "Date,Close\n2026-01-02,100.25\n2026-01-05,101.40" },
  { label: "OHLCV price CSV", body: "Date,Open,High,Low,Close,Volume\n2026-01-02,100,102,99,101,1250000" },
  { label: "Model holdings CSV", body: "Ticker,Weight\nAAPL,30\nMSFT,25\nSPY,45" },
];

export function RealDataCenter({
  dataStatus,
  tickers,
  models,
  onRefreshData,
  onOpenInstrument,
  compact = false,
}: {
  dataStatus: DataStatusResponse | null;
  tickers: TickerSummary[];
  models: ModelSummary[];
  onRefreshData: (symbol?: string, all?: boolean) => Promise<void>;
  onOpenInstrument: (symbol: string) => void;
  compact?: boolean;
}) {
  const [pendingRefresh, setPendingRefresh] = useState("");
  const [copied, setCopied] = useState("");
  const liveCount = tickers.filter((ticker) => ticker.source === "live").length;
  const realRows = tickers.filter((ticker) => ticker.source === "live" || ticker.source === "upload");
  const missingTickers = dataStatus?.missing_data.missing_tickers || [];
  const autoLive = dataStatus?.auto_live;
  const encryption = dataStatus?.database.encryption;
  const refreshAll = async () => {
    setPendingRefresh("all");
    try {
      await onRefreshData(undefined, true);
    } finally {
      setPendingRefresh("");
    }
  };
  const refreshSymbol = async (symbol: string) => {
    setPendingRefresh(symbol);
    try {
      await onRefreshData(symbol, false);
    } finally {
      setPendingRefresh("");
    }
  };
  const copyTemplate = async (label: string, body: string) => {
    try {
      await navigator.clipboard.writeText(body);
      setCopied(label);
    } catch {
      setCopied("Select the template text to copy");
    }
  };
  return (
    <div className={`real-data-center ${compact ? "compact" : ""}`}>
      <section className="dashboard-grid three">
        <Panel title="Persistence" meta={databaseLabel(dataStatus)}>
          <div className="metric-grid persistence-metrics">
            <StatTile label="Eligible histories" value={fmtNumber(dataStatus?.real_instrument_count, 0)} tone={dataStatus?.real_instrument_count ? "positive" : "warning"} />
            <StatTile label="Persisted models" value={fmtNumber(dataStatus?.persisted_model_count, 0)} />
            <StatTile label="Last refresh" value={fmtTimestamp(dataStatus?.last_refresh?.attempted_at)} />
            <StatTile label="Live refresh set" value={fmtNumber(liveCount, 0)} />
            <StatTile label="At-rest storage" value={encryption?.enabled ? "Encrypted" : "Plaintext"} tone={encryption?.enabled ? "positive" : "warning"} />
          </div>
          {encryption?.enabled && <p className="muted">Local persisted payloads are encrypted; lookup keys remain visible for database operation.</p>}
          {dataStatus?.warnings?.length ? (
            <div className="warning-list compact-list">{dataStatus.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div>
          ) : (
            <p className="muted">SQLite is available and real-data gates are intact.</p>
          )}
        </Panel>
        <Panel title="Refresh Control" meta={liveCount ? `${liveCount} live symbols` : "no live symbols"}>
          <div className="refresh-control">
            <p>Refresh only updates symbols already imported as live data. Samples and uploads are never converted into live evidence by refresh.</p>
            <button type="button" onClick={refreshAll} disabled={!liveCount || pendingRefresh !== ""}>
              {pendingRefresh === "all" ? "Refreshing..." : "Refresh all live"}
            </button>
            {dataStatus?.refresh_log?.length ? (
              <ul className="refresh-log">
                {dataStatus.refresh_log.slice(0, 3).map((row) => (
                  <li key={`${row.symbol}-${row.attempted_at}`}>
                    <strong>{row.symbol}</strong><span>{row.status} · {row.rows_added} rows · {fmtTimestamp(row.attempted_at)}</span>
                  </li>
                ))}
              </ul>
            ) : <span className="muted">No refresh attempts recorded yet.</span>}
          </div>
        </Panel>
        <Panel title="Automatic Live Refresh" meta={autoLive?.enabled ? `${autoLive.symbols.length} symbols` : "off"}>
          {autoLive?.enabled ? (
            <div className="auto-live-status">
              <div className="status-line">
                <b>{autoLive.running ? "Running" : autoLive.live_available ? "Configured" : "Provider unavailable"}</b>
                <span>Every {formatInterval(autoLive.interval_seconds)} · {autoLive.period} history</span>
              </div>
              <div className="symbol-strip">
                {autoLive.symbols.slice(0, 12).map((symbol) => <span key={symbol}>{symbol}</span>)}
                {autoLive.symbols.length > 12 && <span>+{autoLive.symbols.length - 12}</span>}
              </div>
              <div className="refresh-log">
                <span>Last run: {fmtTimestamp(autoLive.last_run)}</span>
                {autoLive.last_result && <span>{autoLive.last_result.refreshed} refreshed · {autoLive.last_result.failed} failed</span>}
              </div>
            </div>
          ) : (
            <p className="muted">Set HELIOS_AUTO_LIVE_SYMBOLS to keep a live universe refreshed automatically.</p>
          )}
        </Panel>
        <Panel title="Missing Coverage" meta={`${missingTickers.length} tickers`}>
          {missingTickers.length ? (
            <div className="missing-ticker-list">
              {missingTickers.map((ticker) => <span key={ticker}>{ticker}</span>)}
            </div>
          ) : (
            <p className="muted">No imported model is currently missing real price history.</p>
          )}
          {models.length > 0 && (
            <div className="model-coverage-list">
              {models.slice(0, 4).map((model) => (
                <span key={model.id}>
                  <b>{model.name}</b>
                  <small>{model.real_coverage_count || 0}/{model.n_holdings} holdings · {model.coverage_state || "pending"}</small>
                </span>
              ))}
            </div>
          )}
        </Panel>
      </section>

      {!compact && (
        <>
          <Panel title="Persisted Instruments" meta={`${realRows.length} live/upload histories`}>
            {realRows.length === 0 ? (
              <EmptyState title="No persisted real histories" body="Fetch a live ticker or upload a price CSV to create durable local research data." />
            ) : (
              <div className="terminal-table real-data-table" tabIndex={0} aria-label="Persisted real data instruments">
                <div className="terminal-table__head">
                  <span>Symbol</span><span>Source</span><span>Rows</span><span>Date range</span><span>Last refresh</span><span>Action</span>
                </div>
                {realRows.map((ticker) => (
                  <div className="table-row" key={ticker.symbol}>
                    <span><strong>{ticker.symbol}</strong><small>{ticker.name}</small></span>
                    <SourcePill source={ticker.source} />
                    <span>{fmtNumber(ticker.row_count, 0)}</span>
                    <span>{formatDateRange(ticker)}</span>
                    <span>{fmtTimestamp(ticker.last_refresh?.attempted_at)}</span>
                    <span className="row-actions">
                      <button type="button" onClick={() => onOpenInstrument(ticker.symbol)}>Open</button>
                      <button
                        type="button"
                        onClick={() => refreshSymbol(ticker.symbol)}
                        disabled={ticker.source !== "live" || pendingRefresh !== ""}
                      >
                        {pendingRefresh === ticker.symbol ? "Refreshing" : "Refresh"}
                      </button>
                    </span>
                  </div>
                ))}
              </div>
            )}
          </Panel>
          <Panel title="Import Templates" meta={copied || "copyable examples"}>
            <div className="template-grid">
              {templates.map((template) => (
                <div className="template-card" key={template.label}>
                  <div><strong>{template.label}</strong><button type="button" onClick={() => copyTemplate(template.label, template.body)}>Copy</button></div>
                  <pre>{template.body}</pre>
                </div>
              ))}
            </div>
          </Panel>
        </>
      )}
    </div>
  );
}

function databaseLabel(dataStatus: DataStatusResponse | null) {
  if (!dataStatus) return "loading";
  if (dataStatus.database.available) return "SQLite ready";
  if (!dataStatus.database.configured) return "disabled";
  return "warning";
}

function formatDateRange(ticker: TickerSummary) {
  if (!ticker.first_date && !ticker.last_date) return "—";
  if (ticker.first_date === ticker.last_date) return ticker.last_date || "—";
  return `${ticker.first_date || "?"} to ${ticker.last_date || "?"}`;
}

function formatInterval(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  return `${Math.round(seconds / 3600)} hr`;
}
