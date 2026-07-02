import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { EvidenceLabResponse, ModelSummary, TickerSummary } from "../api/types";
import { DataQualityBanner } from "../components/badges/DataModeBadge";
import { Panel, StatTile } from "../components/cards/Panel";
import { ChartSummary, LineChart, MiniBars } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import { TerminalSelect } from "../components/forms/TerminalSelect";
import { useViewFetch } from "../hooks/useViewFetch";
import { fmtNumber, fmtPct, titleCase } from "../utils/format";

type TargetKind = "model" | "instrument";

export function EvidenceLab({
  tickers,
  models,
  selectedInstrument,
  selectedModel,
  onSelectInstrument,
  onSelectModel,
}: {
  tickers: TickerSummary[];
  models: ModelSummary[];
  selectedInstrument?: string;
  selectedModel?: string;
  onSelectInstrument: (symbol: string) => void;
  onSelectModel: (id: string) => void;
}) {
  const defaultTarget = useMemo(() => {
    if (selectedModel) return `model:${selectedModel}`;
    if (selectedInstrument) return `instrument:${selectedInstrument}`;
    if (models[0]) return `model:${models[0].id}`;
    if (tickers[0]) return `instrument:${tickers[0].symbol}`;
    return "";
  }, [models, selectedInstrument, selectedModel, tickers]);
  const [target, setTarget] = useState(defaultTarget);
  const [horizon, setHorizon] = useState("21");
  const [trainWindow, setTrainWindow] = useState("252");
  const { payload, error, isLoading, load, isCurrentTarget } = useViewFetch<EvidenceLabResponse>({ failureMessage: "Evidence Lab failed." });

  const targetOptions = [
    ...models.map((model) => ({ value: `model:${model.id}`, label: `${model.name} · model` })),
    ...tickers.map((ticker) => ({ value: `instrument:${ticker.symbol}`, label: `${ticker.symbol} · ${ticker.name}` })),
  ];
  const selected = parseTarget(target);

  const run = useCallback((requestedTarget: string) => {
    const parsed = parseTarget(requestedTarget);
    if (!parsed) return;
    if (parsed.kind === "model") onSelectModel(parsed.id);
    if (parsed.kind === "instrument") onSelectInstrument(parsed.id);
    void load(requestedTarget, () => api.evidenceLab({
      kind: parsed.kind,
      id: parsed.id,
      horizon: parseBoundedInt(horizon, 21),
      trainWindow: parseBoundedInt(trainWindow, 252),
      step: 21,
    }));
  }, [horizon, load, onSelectInstrument, onSelectModel, trainWindow]);

  useEffect(() => {
    if (!defaultTarget || isCurrentTarget(defaultTarget)) return;
    setTarget(defaultTarget);
    run(defaultTarget);
  }, [defaultTarget, isCurrentTarget, run]);

  const labels = useMemo(() => payload?.windows.map((row) => row.signal_date) || [], [payload]);
  const chartSeries = useMemo(() => payload ? [
    { label: "Alpha", values: payload.windows.map((row) => row.alpha_pct), tone: "positive" },
    { label: "Forward result", values: payload.windows.map((row) => row.forward_result_pct), tone: "info" },
    { label: "Signal score", values: payload.windows.map((row) => row.signal_score), tone: "warning" },
  ] : [], [payload]);
  const decayRows = useMemo(() => payload?.decay.map((row) => ({
    label: `${row.horizon_days}d`,
    value: row.hit_rate_pct ?? 0,
    tone: (row.hit_rate_pct ?? 0) >= 50 ? "positive" : "warning",
  })) || [], [payload]);

  return (
    <div className="view-stack evidence-lab-view">
      <header className="view-head">
        <div>
          <div className="section-label">Evidence Lab</div>
          <h1>Walk-forward evidence</h1>
          <p>Rolling out-of-sample paper evidence for model and instrument signals: hit rate, alpha, false positives, regimes, decay, and confidence bands.</p>
        </div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); run(target); }}>
          <label>Target<TerminalSelect ariaLabel="Evidence Lab target" value={target} onChange={setTarget} options={targetOptions} disabled={targetOptions.length === 0} /></label>
          <label>Horizon<input value={horizon} onChange={(event) => setHorizon(event.currentTarget.value)} inputMode="numeric" /></label>
          <label>Train rows<input value={trainWindow} onChange={(event) => setTrainWindow(event.currentTarget.value)} inputMode="numeric" /></label>
          <button type="submit" disabled={!selected || isLoading}>{isLoading ? "Running..." : "Run evidence"}</button>
        </form>
      </header>

      {error && <div className="notice danger" role="alert">{error}</div>}
      {isLoading && <div className="loading" role="status">Running walk-forward evidence windows...</div>}
      {targetOptions.length === 0 && (
        <Panel title="Evidence Lab Gate" meta="target required">
          <EmptyState title="No models or instruments" body="Fetch live data or import a model before running walk-forward evidence." />
        </Panel>
      )}
      {payload && <DataQualityBanner payload={payload} />}
      {payload?.evidence_unavailable && (
        <Panel title="Walk-Forward Evidence Locked" meta="real data required">
          <EmptyState title={payload.display_label || "Evidence unavailable"} body={payload.required_action || payload.reason || "Real price history is required before running walk-forward evidence."} actions={payload.missing_tickers?.slice(0, 5) || []} />
        </Panel>
      )}
      {payload && !payload.evidence_unavailable && (
        <>
          <section className="dashboard-grid three">
            <Panel title="Hit Rate" meta={`${fmtNumber(payload.summary.measured_count, 0)} windows`}>
              <div className="metric-grid compact-metrics">
                <StatTile label="Paper hit rate" value={fmtPct(payload.summary.hit_rate_pct)} tone={toneForHit(payload.summary.hit_rate_pct)} />
                <StatTile label="Hit count" value={`${fmtNumber(payload.summary.hit_count, 0)} / ${fmtNumber(payload.summary.window_count, 0)}`} />
                <StatTile label="Avg score" value={fmtNumber(payload.summary.avg_score, 2)} />
                <StatTile label="Positive alpha" value={fmtPct(payload.summary.positive_alpha_rate_pct)} />
              </div>
            </Panel>
            <Panel title="Alpha vs Benchmark" meta={payload.benchmark.symbol}>
              <div className="metric-grid compact-metrics">
                <StatTile label="Avg alpha" value={fmtPct(payload.summary.avg_alpha_pct)} tone={toneForSigned(payload.summary.avg_alpha_pct)} />
                <StatTile label="Forward" value={fmtPct(payload.summary.avg_forward_result_pct)} tone={toneForSigned(payload.summary.avg_forward_result_pct)} />
                <StatTile label="Benchmark" value={fmtPct(payload.summary.avg_benchmark_result_pct)} />
                <StatTile label="Status" value={titleCase(payload.benchmark.status)} />
              </div>
            </Panel>
            <Panel title="False Positives" meta={payload.false_positives.basis}>
              <div className="metric-grid compact-metrics">
                <StatTile label="False positives" value={fmtNumber(payload.false_positives.count, 0)} tone={payload.false_positives.count ? "warning" : "positive"} />
                <StatTile label="FP rate" value={fmtPct(payload.false_positives.rate_pct)} tone={toneForFalsePositive(payload.false_positives.rate_pct)} />
                <StatTile label="Directional" value={fmtNumber(payload.false_positives.directional_signal_count, 0)} />
                <StatTile label="Horizon" value={`${fmtNumber(payload.parameters.horizon_days, 0)}d`} />
              </div>
            </Panel>
          </section>

          <Panel title="Rolling Evidence" meta={`${payload.summary.first_signal_date || "start"} to ${payload.summary.last_signal_date || "end"}`}>
            <ChartSummary items={[
              { label: "Mean alpha", value: fmtPct(payload.confidence_bands.alpha_pct.mean), tone: toneForSigned(payload.confidence_bands.alpha_pct.mean) },
              { label: "Alpha p05 / p95", value: `${fmtPct(payload.confidence_bands.alpha_pct.p05)} / ${fmtPct(payload.confidence_bands.alpha_pct.p95)}`, tone: "info" },
              { label: "Hit CI90", value: `${fmtPct(payload.confidence_bands.hit_rate_pct.ci90_low)} - ${fmtPct(payload.confidence_bands.hit_rate_pct.ci90_high)}`, tone: "warning" },
              { label: "Benchmark", value: `${payload.benchmark.symbol} ${payload.benchmark.status}`, tone: payload.benchmark.status === "available" ? "positive" : "warning" },
            ]} />
            <LineChart labels={labels} series={chartSeries} height={230} />
          </Panel>

          <ProspectiveValidationPanel payload={payload} />

          <section className="dashboard-grid">
            <Panel title="Signal Decay" meta="horizon comparison">
              {payload.decay.length === 0 ? (
                <EmptyState title="No decay rows" body="Signal decay appears when enough windows exist across the configured horizons." />
              ) : (
                <>
                  <MiniBars rows={decayRows} />
                  <div className="terminal-table evidence-decay-table" tabIndex={0} aria-label="Signal decay table">
                    <div className="terminal-table__head"><span>Horizon</span><span>Hit Rate</span><span>Alpha</span><span>FP Rate</span><span>IC</span></div>
                    {payload.decay.map((row) => (
                      <div className="table-row" key={row.horizon_days}>
                        <strong>{row.horizon_days}d</strong>
                        <span>{fmtPct(row.hit_rate_pct)}</span>
                        <span className={toneClass(row.avg_alpha_pct)}>{fmtPct(row.avg_alpha_pct)}</span>
                        <span>{fmtPct(row.false_positive_rate_pct)}</span>
                        <span>{fmtNumber(row.information_coefficient, 2)}</span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </Panel>

            <Panel title="Regime Sensitivity" meta="price-regime buckets">
              {payload.regime_sensitivity.length === 0 ? (
                <EmptyState title="No regime buckets" body="Regime sensitivity appears once walk-forward windows are available." />
              ) : (
                <div className="terminal-table evidence-regime-table" tabIndex={0} aria-label="Regime sensitivity table">
                  <div className="terminal-table__head"><span>Regime</span><span>Windows</span><span>Hit Rate</span><span>Alpha</span><span>FP Rate</span></div>
                  {payload.regime_sensitivity.map((row) => (
                    <div className="table-row" key={row.regime}>
                      <strong>{titleCase(row.regime)}</strong>
                      <span>{fmtNumber(row.count, 0)}</span>
                      <span>{fmtPct(row.hit_rate_pct)}</span>
                      <span className={toneClass(row.avg_alpha_pct)}>{fmtPct(row.avg_alpha_pct)}</span>
                      <span>{fmtPct(row.false_positive_rate_pct)}</span>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </section>

          <section className="dashboard-grid">
            <Panel title="Confidence Bands" meta="historical paper windows">
              <div className="terminal-table evidence-confidence-table" tabIndex={0} aria-label="Evidence confidence bands">
                <div className="terminal-table__head"><span>Metric</span><span>Mean</span><span>P05</span><span>P50</span><span>P95</span><span>CI90</span></div>
                <BandRow label="Forward" band={payload.confidence_bands.forward_result_pct} />
                <BandRow label="Alpha" band={payload.confidence_bands.alpha_pct} />
                <BandRow label="Hit rate" band={payload.confidence_bands.hit_rate_pct} isPct={false} />
              </div>
            </Panel>

            <Panel title="Methodology" meta="analysis-only">
              <dl className="report-dl">
                <div><dt>No Lookahead</dt><dd>{String(payload.methodology.no_lookahead ?? true)}</dd></div>
                <div><dt>Signal Basis</dt><dd>{String(payload.methodology.signal_basis || "Existing causal Helios historical signal.")}</dd></div>
                <div><dt>Alpha Basis</dt><dd>{String(payload.methodology.alpha_basis || "Forward return minus benchmark when available.")}</dd></div>
                <div><dt>Measurement</dt><dd>{String(payload.methodology.forward_measurement || "Forward paper result is measured after the signal date.")}</dd></div>
              </dl>
              {payload.warnings?.length ? <ul className="warning-list">{payload.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul> : null}
            </Panel>
          </section>

          <Panel title="Walk-Forward Windows" meta={`${payload.windows.length} measured`}>
            <div className="terminal-table evidence-window-table" tabIndex={0} aria-label="Walk-forward evidence windows">
              <div className="terminal-table__head"><span>Signal Date</span><span>Action</span><span>Score</span><span>Forward</span><span>Benchmark</span><span>Alpha</span><span>Regime</span><span>Hit</span></div>
              {payload.windows.slice(0, 80).map((row) => (
                <div className="table-row" key={`${row.signal_date}-${row.horizon_days}-${row.action_label}`}>
                  <span>{row.signal_date}<small>{row.input_rows} rows to {row.forward_end_date}</small></span>
                  <strong>{row.action_label}</strong>
                  <span>{fmtNumber(row.signal_score, 2)}</span>
                  <span className={toneClass(row.forward_result_pct)}>{fmtPct(row.forward_result_pct)}</span>
                  <span>{fmtPct(row.benchmark_result_pct)}</span>
                  <span className={toneClass(row.alpha_pct)}>{fmtPct(row.alpha_pct)}</span>
                  <span>{titleCase(row.regime)}</span>
                  <span className={row.paper_hit ? "tone-positive" : row.paper_hit === false ? "tone-negative" : ""}>{row.paper_hit == null ? "N/A" : row.paper_hit ? "Hit" : "Miss"}</span>
                </div>
              ))}
            </div>
          </Panel>
          <p className="report-disclaimer">{payload.disclaimer}</p>
        </>
      )}
    </div>
  );
}

function ProspectiveValidationPanel({ payload }: { payload: EvidenceLabResponse }) {
  const prospective = payload.prospective_validation;
  return (
    <Panel title="Prospective Validation" meta={`Signal Journal · ${titleCase(prospective.status)}`}>
      {prospective.total_count === 0 ? (
        <EmptyState title="No prospective journal yet" body={prospective.basis} />
      ) : (
        <>
          <div className="metric-grid compact-metrics">
            <StatTile label="Journal signals" value={fmtNumber(prospective.total_count, 0)} />
            <StatTile label="Measured" value={fmtNumber(prospective.measured_count, 0)} tone={prospective.measured_count ? "positive" : "warning"} />
            <StatTile label="Pending" value={fmtNumber(prospective.pending_count, 0)} tone={prospective.pending_count ? "warning" : "positive"} />
            <StatTile label="Hit rate" value={fmtPct(prospective.hit_rate_pct)} tone={toneForHit(prospective.hit_rate_pct)} />
            <StatTile label="Avg alpha" value={fmtPct(prospective.avg_alpha_pct)} tone={toneForSigned(prospective.avg_alpha_pct)} />
          </div>
          <p className="muted">{prospective.basis}</p>
          <div className="terminal-table evidence-prospective-table" tabIndex={0} aria-label="Prospective Signal Journal evidence">
            <div className="terminal-table__head"><span>Input End</span><span>Action</span><span>Score</span><span>Status</span><span>Forward</span><span>Alpha</span><span>Hit</span></div>
            {prospective.latest_entries.slice(0, 8).map((entry) => (
              <div className="table-row" key={`${entry.id}-${entry.input_end_date}-${entry.horizon_days}`}>
                <span>{entry.input_end_date || "Pending"}<small>{fmtNumber(entry.input_rows, 0)} rows · {fmtNumber(entry.horizon_days, 0)}d</small></span>
                <strong>{entry.action_label || "HOLD"}</strong>
                <span>{fmtNumber(entry.score, 2)}</span>
                <span>{titleCase(entry.forward_status || "pending")}</span>
                <span className={toneClass(entry.forward_result_pct)}>{fmtPct(entry.forward_result_pct)}</span>
                <span className={toneClass(entry.alpha_pct)}>{fmtPct(entry.alpha_pct)}</span>
                <span className={entry.paper_hit ? "tone-positive" : entry.paper_hit === false ? "tone-negative" : ""}>{entry.paper_hit == null ? "Pending" : entry.paper_hit ? "Hit" : "Miss"}</span>
              </div>
            ))}
          </div>
          <p className="report-disclaimer">{prospective.caveat}</p>
        </>
      )}
    </Panel>
  );
}

function BandRow({ label, band, isPct = true }: { label: string; band: EvidenceLabResponse["confidence_bands"]["alpha_pct"]; isPct?: boolean }) {
  const format = isPct ? fmtPct : (value: unknown) => fmtNumber(value, 2);
  return (
    <div className="table-row">
      <strong>{label}<small>{fmtNumber(band.count, 0)} windows</small></strong>
      <span>{format(band.mean)}</span>
      <span>{format(band.p05)}</span>
      <span>{format(band.p50)}</span>
      <span>{format(band.p95)}</span>
      <span>{format(band.ci90_low)} - {format(band.ci90_high)}</span>
    </div>
  );
}

function parseTarget(target: string): { kind: TargetKind; id: string } | null {
  const [kind, ...rest] = target.split(":");
  const id = rest.join(":");
  if ((kind === "model" || kind === "instrument") && id) return { kind, id };
  return null;
}

function parseBoundedInt(value: string, fallback: number) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function toneForSigned(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value >= 0 ? "positive" : "negative" : "neutral";
}

function toneForHit(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value >= 50 ? "positive" : "warning";
}

function toneForFalsePositive(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value > 25 ? "warning" : "positive";
}

function toneClass(value: unknown) {
  return `tone-${toneForSigned(value)}`;
}
