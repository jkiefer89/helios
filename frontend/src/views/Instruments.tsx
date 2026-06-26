import type { TickerSummary } from "../api/types";
import { SourcePill } from "../components/badges/DataModeBadge";
import { Panel } from "../components/cards/Panel";
import { EmptyState } from "../components/empty-states/EmptyState";
import { fmtMoney, fmtPct } from "../utils/format";

export function Instruments({
  tickers,
  onOpenInstrument,
}: {
  tickers: TickerSummary[];
  onOpenInstrument: (symbol: string) => void;
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
      <Panel title="Instrument Universe" meta={`${tickers.length} loaded`}>
        {tickers.length === 0 ? (
          <EmptyState title="No instruments loaded" body="Upload a price CSV or fetch a live ticker to begin." />
        ) : (
          <div className="terminal-table instruments-table">
            <div className="terminal-table__head">
              <span>Symbol</span><span>Name</span><span>Source</span><span>Last</span><span>1D</span><span>Action</span>
            </div>
            {tickers.map((ticker) => (
              <button type="button" key={ticker.symbol} onClick={() => onOpenInstrument(ticker.symbol)}>
                <span><strong>{ticker.symbol}</strong></span>
                <span>{ticker.name}</span>
                <SourcePill source={ticker.source} />
                <span>{fmtMoney(ticker.last_price)}</span>
                <span className={(ticker.change_pct || 0) >= 0 ? "up" : "down"}>{fmtPct(ticker.change_pct)}</span>
                <span>Open analysis</span>
              </button>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
