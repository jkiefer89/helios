import { useEffect, useRef, useState, type FormEvent } from "react";
import type { DataStatusResponse, MandateSummary, TickerSummary } from "../../api/types";
import { TerminalSelect } from "../forms/TerminalSelect";
import { RequestStatus, toRequestFailure, type RequestFailureState } from "../states/RequestStatus";

export interface DataIntakeProps {
  mandates: MandateSummary[];
  tickers: TickerSummary[];
  dataStatus: DataStatusResponse | null;
  liveAvailable: boolean;
  onUploadPrice: (file: File, symbol: string) => Promise<void>;
  onUploadModel: (file: File, name: string, mandate: string, context: string) => Promise<void>;
  onFetchLive: (symbol: string) => Promise<void>;
  onRefreshData: (symbol?: string, all?: boolean) => Promise<void>;
  fullPage?: boolean;
}

export function DataIntakePanel(props: DataIntakeProps) {
  const [formNotice, setFormNotice] = useState("");
  const [failure, setFailure] = useState<RequestFailureState | null>(null);
  const [pendingForm, setPendingForm] = useState<"" | "live" | "price" | "model" | "refresh">("");
  const lastAction = useRef<{
    kind: Exclude<typeof pendingForm, "">;
    action: () => Promise<void>;
    success: string;
  } | null>(null);
  const [modelMandate, setModelMandate] = useState(props.mandates[0]?.key || "");
  const mandateOptions = props.mandates.map((mandate) => ({ value: mandate.key, label: mandate.label }));
  useEffect(() => {
    if (modelMandate || !props.mandates[0]) return;
    setModelMandate(props.mandates[0].key);
  }, [modelMandate, props.mandates]);

  const run = async (kind: Exclude<typeof pendingForm, "">, action: () => Promise<void>, success: string) => {
    lastAction.current = { kind, action, success };
    setFormNotice("");
    setFailure(null);
    setPendingForm(kind);
    try {
      await action();
      setFormNotice(success);
    } catch (error) {
      setFailure(toRequestFailure(error, `${kind} operation failed.`));
      throw error;
    } finally {
      setPendingForm("");
    }
  };

  const retry = () => {
    const previous = lastAction.current;
    if (!previous) return;
    void run(previous.kind, previous.action, previous.success).catch(() => undefined);
  };

  const priceForm = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const file = (form.elements.namedItem("priceFile") as HTMLInputElement).files?.[0];
    const symbol = (form.elements.namedItem("priceSymbol") as HTMLInputElement).value;
    if (!file) return setFormNotice("Choose a price CSV before uploading price history.");
    try {
      await run("price", () => props.onUploadPrice(file, symbol), "Price history uploaded and provenance checks refreshed.");
      form.reset();
    } catch { /* Error is rendered locally. */ }
  };

  const modelForm = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const file = (form.elements.namedItem("modelFile") as HTMLInputElement).files?.[0];
    const name = (form.elements.namedItem("modelName") as HTMLInputElement).value;
    const mandate = modelMandate || props.mandates[0]?.key || "";
    const context = (form.elements.namedItem("modelContext") as HTMLTextAreaElement).value;
    if (!file) return setFormNotice("Choose a model CSV or spreadsheet before importing a model.");
    try {
      await run("model", () => props.onUploadModel(file, name, mandate, context), "Model imported. Review holding coverage and governance before analysis.");
      form.reset();
    } catch { /* Error is rendered locally. */ }
  };

  const liveForm = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const symbol = (form.elements.namedItem("liveSymbol") as HTMLInputElement).value.trim();
    if (!symbol) return setFormNotice("Enter a ticker symbol before fetching live data.");
    try {
      await run("live", () => props.onFetchLive(symbol), "Live history fetched and persisted. Review readiness below.");
      form.reset();
    } catch { /* Error is rendered locally. */ }
  };

  const liveCount = props.tickers.filter((ticker) => ticker.source === "live").length;
  const realDataReady = liveCount > 0 || Boolean(props.dataStatus?.data_mode_summary?.eligible_for_real_research);
  const title = liveCount > 0 ? "Live refresh active" : realDataReady ? "Real data active" : "Real data setup";
  const body = liveCount > 0
    ? `${liveCount} live ${liveCount === 1 ? "history is" : "histories are"} persisted. Refreshes preserve provenance and retain the last good result on provider failure.`
    : realDataReady
      ? "Uploaded histories are available for real research. Uploaded files remain fixed; only live-fetched symbols are refreshed."
      : "Fetch live history or upload eligible price data. Helios does not generate research rows until evidence passes provenance checks.";

  return (
    <section className={`side-section onboarding data-intake-panel ${props.fullPage ? "data-intake-panel--full" : ""}`}>
      <div className="data-intake-panel__head"><div><h2>{title}</h2><p>{body}</p></div><span>{liveCount} live</span></div>
      <RequestStatus failure={failure} stale={failure?.staleResultPreserved} onRetry={retry} />
      {formNotice && <div className="form-feedback" role="status">{formNotice}</div>}
      <div className={props.fullPage ? "data-intake-grid" : ""}>
        <form onSubmit={liveForm}>
          <label htmlFor={props.fullPage ? "setup-live-symbol" : "shell-live-symbol"}>Fetch live ticker</label>
          <div className="inline-form">
            <input id={props.fullPage ? "setup-live-symbol" : "shell-live-symbol"} name="liveSymbol" placeholder="e.g. SPY" aria-label="Live ticker symbol" disabled={!props.liveAvailable || pendingForm !== ""} />
            <button type="submit" disabled={!props.liveAvailable || pendingForm !== ""}>{pendingForm === "live" ? "Fetching..." : "Fetch"}</button>
          </div>
          {!props.liveAvailable && <small className="form-hint">No configured live provider is available. Check provider configuration or upload price history.</small>}
          <button className="side-secondary-action" type="button" onClick={() => void run("refresh", () => props.onRefreshData(undefined, true), "Live universe refreshed. Review per-symbol diagnostics below.").catch(() => undefined)} disabled={!liveCount || pendingForm !== ""}>
            {pendingForm === "refresh" ? "Refreshing live data..." : `Refresh live data (${liveCount})`}
          </button>
        </form>
        <form onSubmit={priceForm}>
          <label htmlFor={props.fullPage ? "setup-price-file" : "shell-price-file"}>Upload price CSV</label>
          <input id={props.fullPage ? "setup-price-file" : "shell-price-file"} name="priceFile" type="file" accept=".csv" aria-label="Price CSV" disabled={pendingForm !== ""} />
          <input name="priceSymbol" placeholder="Series symbol, e.g. MYFUND" aria-label="Uploaded series symbol" disabled={pendingForm !== ""} />
          <button type="submit" disabled={pendingForm !== ""}>{pendingForm === "price" ? "Uploading..." : "Upload price history"}</button>
        </form>
        <form onSubmit={modelForm}>
          <label htmlFor={props.fullPage ? "setup-model-file" : "shell-model-file"}>Upload model CSV/Excel</label>
          <input id={props.fullPage ? "setup-model-file" : "shell-model-file"} name="modelFile" type="file" accept=".xlsx,.xlsm,.csv,.tsv" aria-label="Model file" disabled={pendingForm !== ""} />
          <input name="modelName" placeholder="Model name" aria-label="Model name" disabled={pendingForm !== ""} />
          <TerminalSelect name="modelMandate" ariaLabel="Model mandate" value={modelMandate} options={mandateOptions} onChange={setModelMandate} disabled={pendingForm !== ""} placeholder="Select mandate" />
          <textarea name="modelContext" rows={2} placeholder="Mandate context (optional)" aria-label="Model context" disabled={pendingForm !== ""} />
          <button type="submit" disabled={pendingForm !== ""}>{pendingForm === "model" ? "Uploading..." : "Upload model"}</button>
        </form>
      </div>
    </section>
  );
}
