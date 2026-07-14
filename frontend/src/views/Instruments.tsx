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
          <p>Only live or uploaded instruments can unlock research rankings. Ineligible histories are excluded.</p>
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
          <EmptyState title="No instruments loaded" body="Upload a price CSV or fetch a live ticker to begin.">
            <button type="button" onClick={() => window.dispatchEvent(new Event("helios:reveal-data-intake"))}>Open data intake</button>
          </EmptyState>
        ) : (
          <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable instrument universe table" onKeyDown={scrollTableByKey}>
            <table className="terminal-data-table instruments-data-table">
            <thead><tr>
              <th scope="col">Symbol</th><th scope="col">Name</th><th scope="col">Source</th><th scope="col">Rows</th><th scope="col">Last</th><th scope="col">Action</th>
            </tr></thead>
            <tbody>{tickers.map((ticker) => (
              <tr key={ticker.symbol}>
                <td><strong>{ticker.symbol}</strong></td>
                <td>{ticker.name}</td>
                <td><SourcePill source={ticker.source} /></td>
                <td>{ticker.row_count ?? "—"}</td>
                <td>{fmtMoney(ticker.last_price)}</td>
                <td className="table-action"><button type="button" onClick={() => onOpenInstrument(ticker.symbol)}>Open</button> · <i className={(ticker.change_pct || 0) >= 0 ? "up" : "down"}>{fmtPct(ticker.change_pct)}</i></td>
              </tr>
            ))}</tbody>
            </table>
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
