import type { DataStatusResponse, MandateSummary, ModelSummary, TickerSummary } from "../api/types";
import { DataIntakePanel } from "../components/data/DataIntakePanel";
import { RealDataCenter } from "../components/data/RealDataCenter";

export function DataSetup(props: {
  tickers: TickerSummary[];
  models: ModelSummary[];
  mandates: MandateSummary[];
  dataStatus: DataStatusResponse | null;
  liveAvailable: boolean;
  onUploadPrice: (file: File, symbol: string) => Promise<void>;
  onUploadModel: (file: File, name: string, mandate: string, context: string) => Promise<void>;
  onFetchLive: (symbol: string) => Promise<void>;
  onRefreshData: (symbol?: string, all?: boolean) => Promise<void>;
  onOpenInstrument: (symbol: string) => void;
}) {
  return (
    <div className="view-stack data-setup-view">
      <header className="view-head">
        <div><div className="section-label">Data Setup</div><h1>Research data workspace</h1><p>Connect, persist, refresh, and diagnose the real price histories and model coverage that Helios research depends on.</p></div>
      </header>
      <DataIntakePanel {...props} fullPage />
      <RealDataCenter dataStatus={props.dataStatus} tickers={props.tickers} models={props.models} onRefreshData={props.onRefreshData} onOpenInstrument={props.onOpenInstrument} />
    </div>
  );
}
