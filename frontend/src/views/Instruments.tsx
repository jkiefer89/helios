import type { KeyboardEvent } from "react";
import type { DataStatusResponse, ModelSummary, TickerSummary } from "../api/types";
import { SourcePill } from "../components/badges/DataModeBadge";
import { Panel } from "../components/cards/Panel";
import { RealDataCenter } from "../components/data/RealDataCenter";
import { EmptyState } from "../components/empty-states/EmptyState";
import { fmtMoney, fmtPct } from "../utils/format";

export function Instruments({
  tickers,
  models,
  dataStatus,
  onOpenInstrument,
  onRefreshData,
}: {
  tickers: TickerSummary[];
  models: ModelSummary[];
  dataStatus: DataStatusResponse | null;
  onOpenInstrument: (symbol: string) => void;
  onRefreshData: (symbol?: string, all?: boolean) => Promise<void>;
}) {
  return (
    <div className="view-stack">
      <header className="view-head">
        <div>
          <div className="section-label">Instruments</div>
          <h1>Available price histories</h1>
          <p>Only live or uploaded instruments can unlock real research rankings. Sample histories remain demo-only.</p>
        </div>
      </header>
      <RealDataCenter
        dataStatus={dataStatus}
        tickers={tickers}
        models={models}
        onRefreshData={onRefreshData}
        onOpenInstrument={onOpenInstrument}
      />
      <Panel title="Instrument Universe" meta={`${tickers.length} loaded`}>
        {tickers.length === 0 ? (
          <EmptyState title="No instruments loaded" body="Upload a price CSV or fetch a live ticker to begin." />
        ) : (
          <div className="terminal-table instruments-table" tabIndex={0} aria-label="Scrollable instrument universe table" onKeyDown={scrollTableByKey}>
            <div className="terminal-table__head">
              <span>Symbol</span><span>Name</span><span>Source</span><span>Rows</span><span>Last</span><span>Action</span>
            </div>
            {tickers.map((ticker) => (
              <button type="button" key={ticker.symbol} onClick={() => onOpenInstrument(ticker.symbol)}>
                <span><strong>{ticker.symbol}</strong></span>
                <span>{ticker.name}</span>
                <SourcePill source={ticker.source} />
                <span>{ticker.row_count ?? "—"}</span>
                <span>{fmtMoney(ticker.last_price)}</span>
                <span className="table-action">Open · <i className={(ticker.change_pct || 0) >= 0 ? "up" : "down"}>{fmtPct(ticker.change_pct)}</i></span>
              </button>
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
