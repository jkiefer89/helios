import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { EvidenceLabResponse, ModelSummary, ProspectiveTrial, TickerSummary, TrialProtocol, TrialThresholdPolicy } from "../api/types";
import { DataQualityBanner } from "../components/badges/DataModeBadge";
import { Panel, StatTile } from "../components/cards/Panel";
import { ChartSummary, LineChart, MiniBars } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import { TerminalSelect } from "../components/forms/TerminalSelect";
import { RequestStatus } from "../components/states/RequestStatus";
import { useViewFetch } from "../hooks/useViewFetch";
import { fmtNumber, fmtPct, titleCase } from "../utils/format";

type TargetKind = "model" | "instrument";
const EVIDENCE_LIMITS = {
  horizon: { min: 5, max: 252, label: "Horizon" },
  trainWindow: { min: 90, max: 756, label: "Train rows" },
  step: { min: 5, max: 63, label: "Step" },
} as const;

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
    // No first-model/ticker fallback: evidence targets are operator-chosen.
    return "";
  }, [selectedInstrument, selectedModel]);
  const [target, setTarget] = useState(defaultTarget);
  const [horizon, setHorizon] = useState("21");
  const [trainWindow, setTrainWindow] = useState("252");
  const [step, setStep] = useState("21");
  const [trial, setTrial] = useState<ProspectiveTrial | null>(null);
  const [trialPolicy, setTrialPolicy] = useState<TrialThresholdPolicy | null>(null);
  const [trialLoading, setTrialLoading] = useState(false);
  const [trialError, setTrialError] = useState("");
  const trialLoadGeneration = useRef(0);
  const { payload, failure, staleResult, isLoading, load, retry, isCurrentTarget } = useViewFetch<EvidenceLabResponse>({ failureMessage: "Evidence Lab failed." });

  const targetOptions = [
    ...models.map((model) => ({ value: `model:${model.id}`, label: `${model.name} · model` })),
    ...tickers.map((ticker) => ({ value: `instrument:${ticker.symbol}`, label: `${ticker.symbol} · ${ticker.name}` })),
  ];
  const selected = parseTarget(target);
  const horizonError = integerInputError(horizon, EVIDENCE_LIMITS.horizon);
  const trainWindowError = integerInputError(trainWindow, EVIDENCE_LIMITS.trainWindow);
  const stepError = integerInputError(step, EVIDENCE_LIMITS.step);
  const controlError = horizonError || trainWindowError || stepError;

  const loadTrial = useCallback(async (requestedTarget: string) => {
    const loadGeneration = ++trialLoadGeneration.current;
    const parsed = parseTarget(requestedTarget);
    if (!parsed) {
      setTrial(null);
      setTrialPolicy(null);
      return;
    }
    setTrialLoading(true);
    setTrialError("");
    setTrialPolicy(null);
    try {
      const response = await api.trials({ kind: parsed.kind, id: parsed.id });
      if (loadGeneration !== trialLoadGeneration.current) return;
      setTrial(response.trials.find((row) => row.status === "open") || response.trials[0] || null);
      setTrialPolicy(response.threshold_policy || null);
    } catch (caught) {
      if (loadGeneration !== trialLoadGeneration.current) return;
      setTrialError(caught instanceof Error ? caught.message : "Trial registry unavailable.");
    } finally {
      if (loadGeneration === trialLoadGeneration.current) setTrialLoading(false);
    }
  }, []);

  const run = useCallback((requestedTarget: string) => {
    const parsed = parseTarget(requestedTarget);
    if (!parsed || controlError) return;
    if (parsed.kind === "model") onSelectModel(parsed.id);
    if (parsed.kind === "instrument") onSelectInstrument(parsed.id);
    void load(requestedTarget, () => api.evidenceLab({
      kind: parsed.kind,
      id: parsed.id,
      horizon: Number(horizon),
      trainWindow: Number(trainWindow),
      step: Number(step),
    }));
  }, [controlError, horizon, load, onSelectInstrument, onSelectModel, step, trainWindow]);

  useEffect(() => {
    if (!defaultTarget || isCurrentTarget(defaultTarget)) return;
    setTarget(defaultTarget);
    run(defaultTarget);
  }, [defaultTarget, isCurrentTarget, run]);

  useEffect(() => {
    if (!target) return;
    void loadTrial(target);
  }, [loadTrial, target]);

  const registerTrial = useCallback(async (owner: string, hypothesis: string) => {
    const parsed = parseTarget(target);
    if (!parsed) return;
    if (controlError) {
      setTrialError(controlError);
      return;
    }
    if (!trialPolicy) {
      setTrialError("The server trial threshold policy is unavailable for this target.");
      return;
    }
    setTrialLoading(true);
    setTrialError("");
    try {
      const response = await api.registerTrial({
        target_kind: parsed.kind,
        target_id: parsed.id,
        actor: owner,
        protocol: trialProtocol(parsed, Number(horizon), Number(step), owner, hypothesis, trialPolicy.floors),
      });
      setTrial(response.trial);
    } catch (caught) {
      setTrialError(caught instanceof Error ? caught.message : "Trial registration failed.");
    } finally {
      setTrialLoading(false);
    }
  }, [controlError, horizon, step, target, trialPolicy]);

  const assessTrial = useCallback(async () => {
    if (!trial) return;
    setTrialLoading(true);
    setTrialError("");
    try {
      await api.assessTrial(trial.trial_id);
      await loadTrial(target);
    } catch (caught) {
      setTrialError(caught instanceof Error ? caught.message : "Trial assessment failed.");
      setTrialLoading(false);
    }
  }, [loadTrial, target, trial]);

  const closeTrial = useCallback(async (status: "completed" | "withdrawn" | "invalidated", note: string, actor: string) => {
    if (!trial) return;
    setTrialLoading(true);
    setTrialError("");
    try {
      const response = await api.closeTrial(trial.trial_id, { status, note, actor });
      setTrial(response.trial);
    } catch (caught) {
      setTrialError(caught instanceof Error ? caught.message : "Trial close failed.");
    } finally {
      setTrialLoading(false);
    }
  }, [trial]);

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
          <label>Horizon<input aria-label="Horizon" type="number" min={EVIDENCE_LIMITS.horizon.min} max={EVIDENCE_LIMITS.horizon.max} step="1" value={horizon} onChange={(event) => setHorizon(event.currentTarget.value)} aria-invalid={Boolean(horizonError)} aria-describedby={horizonError ? "evidence-horizon-error" : undefined} />{horizonError && <small id="evidence-horizon-error" className="field-error">{horizonError}</small>}</label>
          <label>Train rows<input aria-label="Train rows" type="number" min={EVIDENCE_LIMITS.trainWindow.min} max={EVIDENCE_LIMITS.trainWindow.max} step="1" value={trainWindow} onChange={(event) => setTrainWindow(event.currentTarget.value)} aria-invalid={Boolean(trainWindowError)} aria-describedby={trainWindowError ? "evidence-train-error" : undefined} />{trainWindowError && <small id="evidence-train-error" className="field-error">{trainWindowError}</small>}</label>
          <label>Step<input aria-label="Step" type="number" min={EVIDENCE_LIMITS.step.min} max={EVIDENCE_LIMITS.step.max} step="1" value={step} onChange={(event) => setStep(event.currentTarget.value)} aria-invalid={Boolean(stepError)} aria-describedby={stepError ? "evidence-step-error" : undefined} />{stepError && <small id="evidence-step-error" className="field-error">{stepError}</small>}</label>
          <button type="submit" disabled={!selected || isLoading || Boolean(controlError)}>{isLoading ? "Running..." : "Run evidence"}</button>
        </form>
      </header>

      <RequestStatus failure={failure} stale={staleResult} onRetry={retry} />
      {isLoading && <div className="loading" role="status">Running walk-forward evidence windows...</div>}
      {targetOptions.length === 0 && (
        <Panel title="Evidence Lab Gate" meta="target required">
          <EmptyState title="No models or instruments" body="Fetch live data or import a model before running walk-forward evidence." />
        </Panel>
      )}
      {payload && <DataQualityBanner payload={payload} />}
      {selected && (
        <ProspectiveTrialPanel
          trial={trial}
          policy={trialPolicy}
          loading={trialLoading}
          error={trialError}
          eligible={Boolean(payload && !payload.evidence_unavailable && payload.eligible_for_real_research)}
          onRegister={registerTrial}
          onAssess={assessTrial}
          onClose={closeTrial}
        />
      )}
      {payload?.evidence_unavailable && (
        <Panel title="Walk-Forward Evidence Locked" meta="real data required">
          <EmptyState title={payload.display_label || "Evidence unavailable"} body={payload.required_action || payload.reason || "Real price history is required before running walk-forward evidence."} actions={payload.missing_tickers?.slice(0, 5) || []} />
        </Panel>
      )}
      {payload && !payload.evidence_unavailable && (
        <>
          <section className="dashboard-grid three">
            <Panel
              title="Hit Rate"
              meta={`${fmtNumber(payload.summary.measured_count, 0)} windows · ${payload.methodology?.signal_basis_short || "trend+momentum proxy — not the live composite rating"}`}
            >
              <div className="metric-grid compact-metrics">
                <StatTile label="Paper hit rate" value={fmtPct(payload.summary.hit_rate_pct)} tone={toneForHit(payload.summary.hit_rate_pct)} />
                <StatTile label="Hit count" value={`${fmtNumber(payload.summary.hit_count, 0)} / ${fmtNumber(payload.summary.window_count, 0)}`} />
                <StatTile label="Avg score" value={fmtNumber(payload.summary.avg_score, 2)} />
                <StatTile label="Positive alpha" value={fmtPct(payload.summary.positive_alpha_rate_pct)} />
              </div>
            </Panel>
            <Panel title="Alpha vs Benchmark" meta={payload.benchmark.symbol}>
              <div className="metric-grid compact-metrics">
                <StatTile label="Avg alpha (gross)" value={fmtPct(payload.summary.avg_alpha_pct)} tone={toneForSigned(payload.summary.avg_alpha_pct)} />
                <StatTile
                  label="Alpha net of default costs"
                  value={fmtPct(payload.summary.avg_alpha_after_default_costs_pct)}
                  tone={toneForSigned(payload.summary.avg_alpha_after_default_costs_pct)}
                />
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

          <Panel title="Out-of-Sample Methods" meta="rolling + anchored">
            <div className="metric-grid compact-metrics">
              <StatTile label="Rolling windows" value={fmtNumber(payload.validation_methods.rolling.summary.window_count, 0)} />
              <StatTile label="Rolling net alpha" value={fmtPct(payload.validation_methods.rolling.summary.avg_alpha_after_default_costs_pct)} tone={toneForSigned(payload.validation_methods.rolling.summary.avg_alpha_after_default_costs_pct)} />
              <StatTile label="Anchored windows" value={fmtNumber(payload.validation_methods.anchored.summary.window_count, 0)} />
              <StatTile label="Anchored net alpha" value={fmtPct(payload.validation_methods.anchored.summary.avg_alpha_after_default_costs_pct)} tone={toneForSigned(payload.validation_methods.anchored.summary.avg_alpha_after_default_costs_pct)} />
              <StatTile label="Regime robustness" value={titleCase(payload.regime_robustness.status)} tone={payload.regime_robustness.passed ? "positive" : "warning"} />
              <StatTile label="Multiplicity" value={`${payload.multiplicity.evaluated_horizons} horizons`} />
            </div>
            <p className="muted">{payload.validation_methods.rolling.training_policy}</p>
            <p className="muted">{payload.validation_methods.anchored.training_policy}</p>
            <p className="forecast-note">{payload.regime_robustness.basis}</p>
            {payload.multiplicity.selection_warning && <p className="report-disclaimer">{payload.multiplicity.selection_warning}</p>}
          </Panel>

          <ProspectiveValidationPanel payload={payload} />

          <section className="dashboard-grid">
            <Panel title="Signal Decay" meta="horizon comparison">
              {payload.decay.length === 0 ? (
                <EmptyState title="No decay rows" body="Signal decay appears when enough windows exist across the configured horizons." />
              ) : (
                <>
                  <MiniBars rows={decayRows} />
                  <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable signal decay evidence">
                    <table className="terminal-data-table evidence-decay-data-table">
                      <thead><tr><th scope="col">Horizon</th><th scope="col">Hit Rate</th><th scope="col">Alpha</th><th scope="col">FP Rate</th><th scope="col">IC</th></tr></thead>
                      <tbody>{payload.decay.map((row) => (
                      <tr key={row.horizon_days}>
                        <th scope="row">{row.horizon_days}d</th>
                        <td>{fmtPct(row.hit_rate_pct)}</td>
                        <td className={toneClass(row.avg_alpha_pct)}>{fmtPct(row.avg_alpha_pct)}</td>
                        <td>{fmtPct(row.false_positive_rate_pct)}</td>
                        <td>{fmtNumber(row.information_coefficient, 2)}</td>
                      </tr>
                    ))}</tbody>
                    </table>
                  </div>
                </>
              )}
            </Panel>

            <Panel title="Regime Sensitivity" meta="price-regime buckets">
              {payload.regime_sensitivity.length === 0 ? (
                <EmptyState title="No regime buckets" body="Regime sensitivity appears once walk-forward windows are available." />
              ) : (
                <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable regime sensitivity evidence">
                  <table className="terminal-data-table evidence-regime-data-table">
                    <thead><tr><th scope="col">Regime</th><th scope="col">Windows</th><th scope="col">Hit Rate</th><th scope="col">Alpha</th><th scope="col">FP Rate</th></tr></thead>
                    <tbody>{payload.regime_sensitivity.map((row) => (
                    <tr key={row.regime}>
                      <th scope="row">{titleCase(row.regime)}</th>
                      <td>{fmtNumber(row.count, 0)}</td>
                      <td>{fmtPct(row.hit_rate_pct)}</td>
                      <td className={toneClass(row.avg_alpha_pct)}>{fmtPct(row.avg_alpha_pct)}</td>
                      <td>{fmtPct(row.false_positive_rate_pct)}</td>
                    </tr>
                  ))}</tbody>
                  </table>
                </div>
              )}
            </Panel>
          </section>

          <section className="dashboard-grid">
            <Panel title="Confidence Bands" meta="historical paper windows">
              <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable evidence confidence bands">
                <table className="terminal-data-table evidence-confidence-data-table">
                  <thead><tr><th scope="col">Metric</th><th scope="col">Mean</th><th scope="col">P05</th><th scope="col">P50</th><th scope="col">P95</th><th scope="col">CI90</th></tr></thead>
                  <tbody>
                    <BandRow label="Forward" band={payload.confidence_bands.forward_result_pct} />
                    <BandRow label="Alpha" band={payload.confidence_bands.alpha_pct} />
                    <BandRow label="Hit rate" band={payload.confidence_bands.hit_rate_pct} isPct={false} />
                  </tbody>
                </table>
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
            <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable walk-forward evidence windows">
              <table className="terminal-data-table evidence-window-data-table">
                <thead><tr><th scope="col">Signal Date</th><th scope="col">Action</th><th scope="col">Score</th><th scope="col">Forward</th><th scope="col">Benchmark</th><th scope="col">Alpha</th><th scope="col">Regime</th><th scope="col">Hit</th></tr></thead>
                <tbody>{payload.windows.slice(0, 80).map((row) => (
                <tr key={`${row.signal_date}-${row.horizon_days}-${row.action_label}`}>
                  <th scope="row">{row.signal_date}<small>{row.input_rows} rows to {row.forward_end_date}</small></th>
                  <td><strong>{row.action_label}</strong></td>
                  <td>{fmtNumber(row.signal_score, 2)}</td>
                  <td className={toneClass(row.forward_result_pct)}>{fmtPct(row.forward_result_pct)}</td>
                  <td>{fmtPct(row.benchmark_result_pct)}</td>
                  <td className={toneClass(row.alpha_pct)}>{fmtPct(row.alpha_pct)}</td>
                  <td>{titleCase(row.regime)}</td>
                  <td className={row.paper_hit ? "tone-positive" : row.paper_hit === false ? "tone-negative" : ""}>{row.paper_hit == null ? "N/A" : row.paper_hit ? "Hit" : "Miss"}</td>
                </tr>
              ))}</tbody>
              </table>
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
            <StatTile label="Avg alpha (gross)" value={fmtPct(prospective.avg_alpha_pct)} tone={toneForSigned(prospective.avg_alpha_pct)} />
            <StatTile
              label="Alpha net of default costs"
              value={fmtPct(prospective.avg_alpha_after_default_costs_pct)}
              tone={toneForSigned(prospective.avg_alpha_after_default_costs_pct)}
            />
          </div>
          <p className="muted">{prospective.basis}</p>
          <p className="forecast-note">
            These entries record the composite score and action at signal time; entries from
            2026-07-12 onward also carry the full component breakdown, weights, and dampers.
            Model entries have no fundamentals leg (technicals-only) — recorded, not hidden.
          </p>
          <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable prospective Signal Journal evidence">
            <table className="terminal-data-table evidence-prospective-data-table">
              <thead><tr><th scope="col">Input End</th><th scope="col">Action</th><th scope="col">Score</th><th scope="col">Status</th><th scope="col">Forward</th><th scope="col">Alpha</th><th scope="col">Hit</th></tr></thead>
              <tbody>{prospective.latest_entries.slice(0, 8).map((entry) => (
              <tr key={`${entry.id}-${entry.input_end_date}-${entry.horizon_days}`}>
                <th scope="row">{entry.input_end_date || "Pending"}<small>{fmtNumber(entry.input_rows, 0)} rows · {fmtNumber(entry.horizon_days, 0)}d</small></th>
                <td><strong>{entry.action_label || "HOLD"}</strong></td>
                <td>{fmtNumber(entry.score, 2)}</td>
                <td>{titleCase(entry.forward_status || "pending")}</td>
                <td className={toneClass(entry.forward_result_pct)}>{fmtPct(entry.forward_result_pct)}</td>
                <td className={toneClass(entry.alpha_pct)}>{fmtPct(entry.alpha_pct)}</td>
                <td className={entry.paper_hit ? "tone-positive" : entry.paper_hit === false ? "tone-negative" : ""}>{entry.paper_hit == null ? "Pending" : entry.paper_hit ? "Hit" : "Miss"}</td>
              </tr>
            ))}</tbody>
            </table>
          </div>
          <p className="report-disclaimer">{prospective.caveat}</p>
        </>
      )}
    </Panel>
  );
}

function ProspectiveTrialPanel({
  trial,
  policy,
  loading,
  error,
  eligible,
  onRegister,
  onAssess,
  onClose,
}: {
  trial: ProspectiveTrial | null;
  policy: TrialThresholdPolicy | null;
  loading: boolean;
  error: string;
  eligible: boolean;
  onRegister: (owner: string, hypothesis: string) => Promise<void>;
  onAssess: () => Promise<void>;
  onClose: (status: "completed" | "withdrawn" | "invalidated", note: string, actor: string) => Promise<void>;
}) {
  const [owner, setOwner] = useState("Research Committee");
  const [hypothesis, setHypothesis] = useState("Positive directional alpha persists after implementation costs.");
  const [closeStatus, setCloseStatus] = useState<"completed" | "withdrawn" | "invalidated">("completed");
  const [closeNote, setCloseNote] = useState("");

  if (!trial) {
    return (
      <Panel title="Prospective Trial" meta={eligible ? "registration ready" : "real-data gate"}>
        {error && <div className="notice danger" role="alert">{error}</div>}
        <div className="form-grid">
          <label>Owner<input value={owner} onChange={(event) => setOwner(event.currentTarget.value)} /></label>
          <label className="span-two">Preregistered hypothesis<textarea value={hypothesis} onChange={(event) => setHypothesis(event.currentTarget.value)} rows={2} /></label>
        </div>
        <p className="muted">Protocol: 21-session horizon and step, SPY benchmark, {policy ? `${policy.floors.min_observations}-observation minimum, ${policy.floors.confidence_level_pct}% familywise confidence, and ${policy.profile.replace(/_/g, " ")} non-overridable deployment floors` : "server policy required"}; all three price regimes; full fee/spread/slippage/impact/tax/idle-cash stack; and $1B expected AUM capacity review.</p>
        <button
          type="button"
          disabled={!eligible || !policy || loading || owner.trim().length < 2 || hypothesis.trim().length < 12}
          onClick={() => void onRegister(owner.trim(), hypothesis.trim())}
        >
          {loading ? "Registering..." : "Register immutable trial"}
        </button>
        {!eligible && <p className="report-disclaimer">Eligible live or uploaded history is required before trial registration.</p>}
      </Panel>
    );
  }

  const assessment = trial.assessment;
  const layers = assessment.implementation_evidence;
  return (
    <Panel title="Prospective Trial" meta={`${trial.trial_id} · ${titleCase(trial.status)}`}>
      {error && <div className="notice danger" role="alert">{error}</div>}
      <div className="metric-grid compact-metrics">
        <StatTile label="Assessment" value={titleCase(assessment.state)} tone={assessment.passed ? "positive" : "warning"} />
        <StatTile label="Scheduled" value={fmtNumber(assessment.observations.scheduled_count, 0)} />
        <StatTile label="Measured" value={fmtNumber(assessment.observations.measured_directional_count, 0)} />
        <StatTile label="Net alpha" value={fmtPct(assessment.metrics.avg_net_directional_alpha_pct)} tone={toneForSigned(assessment.metrics.avg_net_directional_alpha_pct)} />
        <StatTile label="Adjusted confidence" value={fmtPct(assessment.multiplicity.adjusted_confidence_pct)} />
        <StatTile label="Regime coverage" value={assessment.regime_robustness.coverage_passed ? "Passed" : "Collecting"} tone={assessment.regime_robustness.coverage_passed ? "positive" : "warning"} />
      </div>
      <p><strong>{trial.protocol.hypothesis}</strong></p>
      <p className="muted">Owner: {trial.protocol.owner} · registered {trial.starts_on} · protocol {trial.protocol_hash.slice(0, 12)} · expected AUM ${fmtNumber(trial.protocol.expected_aum_usd, 0)}</p>
      <section className="dashboard-grid three">
        <ImplementationLayer title="Paper" layer={layers.paper} />
        <ImplementationLayer title="Proposed" layer={layers.proposed} />
        <ImplementationLayer title="Actual" layer={layers.actual} />
      </section>
      <div className="button-row">
        <button type="button" disabled={loading} onClick={() => void onAssess()}>{loading ? "Refreshing..." : "Refresh assessment"}</button>
      </div>
      {trial.status === "open" && (
        <div className="form-grid">
          <label>Close status
            <TerminalSelect
              ariaLabel="Trial close status"
              value={closeStatus}
              onChange={(value) => setCloseStatus(value as typeof closeStatus)}
              options={[
                { value: "completed", label: "Completed" },
                { value: "withdrawn", label: "Withdrawn" },
                { value: "invalidated", label: "Invalidated" },
              ]}
            />
          </label>
          <label className="span-two">Required close note<input value={closeNote} onChange={(event) => setCloseNote(event.currentTarget.value)} /></label>
          <button type="button" disabled={loading || closeNote.trim().length < 5} onClick={() => void onClose(closeStatus, closeNote.trim(), owner.trim())}>Close trial</button>
        </div>
      )}
      <p className="report-disclaimer">Prospective paper research and implementation evidence only. Helios does not execute orders or guarantee outcomes.</p>
    </Panel>
  );
}

function ImplementationLayer({ title, layer }: { title: string; layer: ProspectiveTrial["assessment"]["implementation_evidence"]["paper"] }) {
  return (
    <article className="data-card">
      <div className="panel-title"><span>{title}</span><small>{titleCase(layer.status)}</small></div>
      <strong>{fmtPct(layer.avg_net_directional_alpha_pct)}</strong>
      <p>{fmtNumber(layer.measured_count, 0)} measured</p>
      {layer.linked_fill_count != null && <p>{fmtNumber(layer.linked_fill_count, 0)} linked fills · ${fmtNumber(layer.observed_fees_usd, 2)} fees</p>}
      {layer.cost_basis && <small>{layer.cost_basis}</small>}
    </article>
  );
}

function trialProtocol(
  target: { kind: TargetKind; id: string },
  horizonDays: number,
  stepDays: number,
  owner: string,
  hypothesis: string,
  thresholdFloors: TrialProtocol["success_thresholds"],
): TrialProtocol {
  return {
    hypothesis,
    primary_metric: "net_directional_alpha_pct",
    horizon_days: horizonDays,
    step_days: stepDays,
    benchmark: "SPY",
    cost_assumptions: {
      commission_bps_per_side: 1,
      spread_bps_per_side: 2,
      slippage_bps_per_side: 3,
      market_impact_bps_per_side: 4,
      tax_drag_bps: 5,
      idle_cash_pct: 2,
    },
    success_thresholds: { ...thresholdFloors },
    regimes: ["risk_on", "neutral", "risk_off"],
    owner,
    allowed_sources: ["live", "upload"],
    freshness_days: 3,
    expected_aum_usd: 1_000_000_000,
    max_position_pct: target.kind === "model" ? 20 : 100,
    max_adv_participation_pct: 10,
    planned_variants: ["Registered deterministic Helios composite"],
    deleted_variants: [],
  };
}

function BandRow({ label, band, isPct = true }: { label: string; band: EvidenceLabResponse["confidence_bands"]["alpha_pct"]; isPct?: boolean }) {
  const format = isPct ? fmtPct : (value: unknown) => fmtNumber(value, 2);
  return (
    <tr>
      <th scope="row"><strong>{label}<small>{fmtNumber(band.count, 0)} windows</small></strong></th>
      <td>{format(band.mean)}</td>
      <td>{format(band.p05)}</td>
      <td>{format(band.p50)}</td>
      <td>{format(band.p95)}</td>
      <td>{format(band.ci90_low)} - {format(band.ci90_high)}</td>
    </tr>
  );
}

function parseTarget(target: string): { kind: TargetKind; id: string } | null {
  const [kind, ...rest] = target.split(":");
  const id = rest.join(":");
  if ((kind === "model" || kind === "instrument") && id) return { kind, id };
  return null;
}

function integerInputError(value: string, limits: { min: number; max: number; label: string }) {
  if (!value.trim()) return `${limits.label} is required.`;
  const parsed = Number(value);
  if (!Number.isInteger(parsed)) return `${limits.label} must be a whole number.`;
  if (parsed < limits.min || parsed > limits.max) {
    return `${limits.label} must be between ${limits.min} and ${limits.max}.`;
  }
  return "";
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
