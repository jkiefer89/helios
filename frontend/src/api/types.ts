export type DataMode = "demo" | "real" | "mixed" | "invalid_for_research" | string;

export interface DataQuality {
  data_mode?: DataMode;
  display_label?: string;
  eligible_for_real_research?: boolean;
  reason?: string;
  required_action?: string;
  source_counts?: Record<string, number>;
  source_weight_pct?: Record<string, number>;
  warnings?: string[];
  missing_tickers?: string[];
  history_days?: number;
  [key: string]: unknown;
}

export interface ProvenancePayload {
  data_mode?: DataMode;
  display_label?: string;
  eligible_for_real_research?: boolean;
  reason?: string;
  required_action?: string;
  data_provenance?: DataQuality;
  warnings?: string[];
  disclaimer?: string;
}

export interface TickerSummary {
  symbol: string;
  name: string;
  source: string;
  last_price: number | null;
  change_pct: number | null;
  row_count?: number;
  first_date?: string | null;
  last_date?: string | null;
  last_refresh?: RefreshLogEntry | null;
  eligible_for_real_research?: boolean;
}

export interface TickersResponse {
  tickers: TickerSummary[];
  live_available: boolean;
}

export interface MandateSummary {
  key: string;
  label: string;
  target_vol_pct: number;
  [key: string]: unknown;
}

export interface ModelSummary {
  id: string;
  name: string;
  mandate: string;
  mandate_label: string;
  n_holdings: number;
  top?: string | null;
  holdings: Array<{ ticker: string; weight: number; weight_pct: number; source?: string }>;
  real_coverage_count?: number;
  missing_tickers?: string[];
  coverage_state?: "real" | "mixed" | "blocked" | "empty" | string;
}

export interface ModelTemplate {
  slug: string;
  model_id: string;
  name: string;
  category: string;
  mandate: string;
  benchmark: string;
  thesis: string;
  template_only: boolean;
  holdings: Array<{ ticker: string; weight: number }>;
  rebalance_rules: {
    frequency: string;
    drift_band_pct: number;
    review_trigger: string;
  };
  risk_limits: {
    max_single_position_pct: number;
    max_theme_position_pct: number;
    max_etf_position_pct: number;
    min_holdings: number;
  };
  provenance: {
    source_type: string;
    version: string;
    basis: string;
    caveat: string;
  };
}

export interface ModelTemplateImportResponse {
  id: string;
  name: string;
  mandate: string;
  n_holdings: number;
  coverage_state: string;
  missing_tickers: string[];
  template_only: boolean;
  benchmark: string;
  rebalance_rules: ModelTemplate["rebalance_rules"];
  risk_limits: ModelTemplate["risk_limits"];
  provenance: ModelTemplate["provenance"];
}

export interface ModelGovernanceViolation {
  field: string;
  ticker?: string;
  limit: number;
  actual: number;
  message: string;
}

export interface ModelGovernanceRow {
  id: string;
  name: string;
  mandate: string;
  mandate_label: string;
  mandate_context: string;
  version: number;
  approval_status: string;
  approved_by: string;
  approval_updated_at?: string | null;
  risk_limits: ModelTemplate["risk_limits"];
  risk_limit_state: "within_limits" | "breach" | string;
  risk_limit_violations: ModelGovernanceViolation[];
  rebalance_rules: ModelTemplate["rebalance_rules"];
  rebalance_status: string;
  last_rebalance_at?: string | null;
  version_count: number;
  snapshot_count: number;
  change_note_count: number;
  latest_change_note: string;
  updated_by: string;
  updated_at?: string | null;
  holdings_count: number;
  top_holding: string;
  top_weight_pct: number;
  source: string;
  provenance: ModelTemplate["provenance"];
}

export interface ModelGovernanceEvent {
  id: number;
  model_id: string;
  created_at: string;
  version: number;
  actor: string;
  action: string;
  note: string;
  approval_status: string;
}

export interface ModelEditHoldingInput {
  ticker: string;
  weight_pct: number;
}

export interface ModelEditPreviewResponse {
  model: {
    id: string;
    name: string;
    mandate: string;
    mandate_label: string;
    holdings: Array<{ ticker: string; weight: number; weight_pct: number; source?: string }>;
  };
  current_holdings: Array<{ ticker: string; weight: number; weight_pct: number; source?: string }>;
  proposed_holdings: Array<{ ticker: string; weight: number; weight_pct: number; source?: string }>;
  rebalance_to_target: boolean;
  risk_limits: ModelTemplate["risk_limits"];
  risk_limit_state: "within_limits" | "breach" | string;
  risk_limit_violations: ModelGovernanceViolation[];
  can_save: boolean;
  requires_change_note: boolean;
  disclaimer: string;
}

export interface ModelEditSaveResponse extends ModelEditPreviewResponse {
  saved: boolean;
  event: ModelGovernanceEvent & { snapshot?: Record<string, unknown> };
}

export interface ModelGovernanceSnapshot {
  event_id: number;
  model_id: string;
  model_name: string;
  version: number;
  created_at: string;
  actor: string;
  action: string;
  approval_status: string;
  holding_count: number;
  risk_limit_state: string;
}

export interface ModelGovernanceResponse {
  models: ModelGovernanceRow[];
  snapshots: ModelGovernanceSnapshot[];
  change_log: ModelGovernanceEvent[];
  rebalance_history: ModelGovernanceEvent[];
  summary: {
    available: boolean;
    model_count: number;
    approved_count: number;
    pending_count: number;
    draft_count: number;
    archived_count: number;
    breach_count: number;
    snapshot_count: number;
    change_count: number;
  };
  warning?: string;
  disclaimer: string;
}

export interface RefreshLogEntry {
  symbol: string;
  attempted_at: string;
  status: string;
  rows_added: number;
  message: string;
  source: string;
}

export interface ModelCoverageStatus {
  id: string;
  name: string;
  n_holdings: number;
  real_coverage_count: number;
  missing_tickers: string[];
  source_counts: Record<string, number>;
  coverage_state: string;
}

export interface DataStatusResponse {
  database: {
    configured: boolean;
    available: boolean;
    path: string;
    warning?: string;
    schema_version?: number | null;
    encryption: {
      enabled: boolean;
      required: boolean;
      mode: string;
      key_source: string;
      algorithm: string;
      at_rest_format: string;
      plaintext_lookup_keys: string[];
      warning?: string;
    };
    real_instrument_count: number;
    persisted_model_count: number;
    last_refresh?: RefreshLogEntry | null;
  };
  real_instrument_count: number;
  persisted_model_count: number;
  loaded_model_count: number;
  last_refresh?: RefreshLogEntry | null;
  data_mode_summary: DataQuality;
  source_counts: Record<string, number>;
  auto_live: AutoLiveStatus;
  warnings: string[];
  missing_data: {
    models: ModelCoverageStatus[];
    missing_tickers: string[];
    blocked_model_count: number;
  };
  refresh_log: RefreshLogEntry[];
}

export interface DataRefreshResponse {
  requested: string;
  refreshed: number;
  failed: number;
  skipped: number;
  results: Array<{
    symbol: string;
    status: "ok" | "error" | "skipped" | string;
    rows_added: number;
    rows?: number;
    message: string;
  }>;
  warnings: string[];
  data_status: DataStatusResponse;
}

export interface DataQualityIssue {
  category: "stale_symbols" | "missing_data" | "short_histories" | "source_conflicts" | "refresh_failures" | "refresh_observability_gaps" | "coverage_gaps" | string;
  severity: "blocker" | "warning" | "info" | string;
  target: string;
  detail: string;
  next_step: string;
}

export interface DataQualitySymbol {
  symbol: string;
  name: string;
  source: string;
  row_count: number;
  first_date?: string | null;
  last_date?: string | null;
  days_stale?: number | null;
  freshness_basis: string;
  is_stale: boolean;
  is_short: boolean;
  research_ready: boolean;
  last_refresh?: RefreshLogEntry | null;
  refresh_evidence: {
    requires_refresh_log: boolean;
    has_refresh_log: boolean;
    last_attempted_at?: string | null;
    status?: string | null;
    rows_added?: number | null;
    source?: string | null;
  };
  next_step: string;
}

export interface DataQualityModel {
  id: string;
  name: string;
  mandate: string;
  n_holdings: number;
  real_coverage_count: number;
  missing_tickers: string[];
  source_counts: Record<string, number>;
  coverage_state: string;
  research_ready: boolean;
}

export interface DataQualityCoverageGap {
  model_id: string;
  model_name: string;
  coverage_state: string;
  real_coverage_count: number;
  n_holdings: number;
  missing_tickers: string[];
}

export interface DataQualityResponse {
  generated_at: string;
  research_ready: boolean;
  thresholds: {
    stale_days: number;
    min_research_rows: number;
    institutional_history_rows: number;
  };
  threshold_config: {
    source: string;
    env: Record<string, string>;
    range_guards: Record<string, string>;
  };
  refresh_observability: {
    requires_log_for_sources: string[];
    observed_count: number;
    gap_count: number;
    refresh_log_window: number;
    basis: string;
  };
  summary: {
    symbol_count: number;
    model_count: number;
    issue_count: number;
    blocker_count: number;
    warning_count: number;
    research_ready_count: number;
    stale_symbol_count: number;
    missing_symbol_count: number;
    refresh_failure_count: number;
    refresh_observability_gap_count: number;
    coverage_gap_count: number;
  };
  symbols: DataQualitySymbol[];
  models: DataQualityModel[];
  issues: DataQualityIssue[];
  stale_symbols: DataQualitySymbol[];
  short_histories: DataQualitySymbol[];
  missing_data: Array<{ symbol: string; model_id: string; model_name: string }>;
  refresh_failures: RefreshLogEntry[];
  refresh_observability_gaps: DataQualitySymbol[];
  coverage_gaps: DataQualityCoverageGap[];
  source_conflicts: Array<{ model_id: string; model_name: string; source_counts: Record<string, number>; coverage_state: string }>;
  disclaimer: string;
}

export interface AutoLiveStatus {
  enabled: boolean;
  live_available: boolean;
  symbols: string[];
  source: string;
  period: string;
  interval_seconds: number;
  max_workers: number;
  running: boolean;
  last_run?: string | null;
  last_result?: {
    requested: string[];
    refreshed: number;
    failed: number;
    results: Array<{
      symbol: string;
      status: string;
      rows_added: number;
      rows?: number;
      message: string;
    }>;
  } | null;
}

export interface RegimeDriver {
  name: string;
  value: string;
  impact: number;
  detail?: string;
}

export interface RegimePayload {
  label: "risk-on" | "neutral" | "risk-off" | string;
  score: number;
  summary: string;
  drivers: RegimeDriver[];
  warnings: string[];
  symbol?: string;
}

export interface CommandItem {
  id: string;
  kind: string;
  symbol: string;
  name: string;
  source: string;
  action: "BUY" | "SELL" | "HOLD" | "REVIEW" | string;
  score: number;
  risk_score: number;
  evidence_score: number;
  expected_return_pct?: number;
  expected_vol_pct?: number;
  max_drawdown_pct?: number;
  reason?: string;
  warnings?: string[];
}

export interface ModelAlert {
  id: string;
  name: string;
  severity: "high" | "medium" | "low" | string;
  message: string;
  next_step: string;
  eligible_for_real_research?: boolean;
}

export interface ResearchQueueItem {
  priority: "high" | "medium" | "low" | string;
  title: string;
  detail: string;
}

export interface CommandCenterResponse extends ProvenancePayload {
  regime: RegimePayload;
  top_opportunities: CommandItem[];
  top_risks: CommandItem[];
  model_alerts: ModelAlert[];
  research_queue: ResearchQueueItem[];
  generated_at: string;
}

export interface OpportunityItem {
  id: string;
  kind: "instrument" | "model" | string;
  symbol: string;
  name: string;
  source: string;
  action: string;
  opportunity_score: number;
  evidence_score: number;
  risk_score: number;
  expected_return_pct: number;
  expected_vol_pct?: number;
  max_drawdown_pct?: number;
  plain_english_summary: string;
  recommended_next_step: string;
  top_positive_drivers: string[];
  top_negative_drivers: string[];
  warnings: string[];
  eligible_for_real_research?: boolean;
  model_id?: string;
}

export interface OpportunitiesResponse extends ProvenancePayload {
  regime: RegimePayload;
  items: OpportunityItem[];
  blocked_items: OpportunityItem[];
  count: number;
  total_candidates: number;
  methodology: Record<string, unknown>;
}

export interface StrategyResponse extends ProvenancePayload {
  series_kind: "instrument" | "model";
  id?: string;
  symbol?: string;
  name: string;
  source?: string;
  strategy?: MetricSet;
  benchmark?: MetricSet;
  trade_stats?: Record<string, number | string | null>;
  methodology?: Record<string, unknown>;
  assumptions?: Record<string, number | string | boolean>;
  dates?: string[];
  strategy_curve?: number[];
  benchmark_curve?: number[];
  drawdown_curve?: number[];
  rolling_sharpe_curve?: number[];
  beat_benchmark?: boolean;
}

export interface MetricSet {
  total_return_pct?: number;
  annual_vol_pct?: number;
  sharpe?: number;
  max_drawdown_pct?: number;
  [key: string]: unknown;
}

export interface ClinicResponse extends ProvenancePayload {
  id: string;
  name: string;
  mandate: { key: string; label: string; [key: string]: unknown };
  constraints: { long_only: boolean; single_name_cap: number; no_short_weights: boolean };
  diagnostics: Record<string, number | boolean>;
  risk_contributions: Array<{ ticker: string; weight: number; mrc_pct: number }>;
  suggestions: Array<{
    type: string;
    ticker?: string;
    current_weight: number;
    suggested_weight: number;
    rationale: string;
  }>;
  before: { weights: Record<string, number>; estimates: Record<string, number> };
  after: { weights: Record<string, number>; estimates: Record<string, number> };
  refusals: string[];
  explanation: string;
}

export interface RiskAnalyticsResponse extends ProvenancePayload {
  id: string;
  name: string;
  risk_exposure_unavailable?: boolean;
  mandate: { key: string; label: string; [key: string]: unknown };
  benchmark?: { symbol: string; status: string };
  sector_exposure: Array<{ name: string; weight_pct: number; tickers: string[] }>;
  theme_exposure: Array<{ name: string; weight_pct: number; tickers: string[] }>;
  factor_exposure: Record<string, number>;
  single_name_concentration: {
    hhi?: number;
    effective_holdings?: number;
    status?: string;
    top_holding?: { ticker: string; weight_pct: number };
    top_5_weight_pct?: number;
  };
  volatility_budget: {
    annual_vol_pct?: number;
    target_vol_pct?: number;
    gap_pct?: number;
    status?: string;
  };
  volatility_contribution: Array<{ ticker: string; weight_pct: number; mrc_pct: number; source: string }>;
  correlation_clusters: Array<{
    name: string;
    type: string;
    average_correlation?: number | null;
    pairs: Array<{ tickers: string[]; correlation: number }>;
  }>;
  drawdown_stress: {
    max_drawdown_pct?: number | null;
    worst_21d_pct?: number | null;
    worst_63d_pct?: number | null;
    current_drawdown_pct?: number | null;
  };
  scenario_shocks: Array<{ scenario: string; portfolio_impact_pct: number; basis: string }>;
  liquidity_flags: {
    items: Array<{ ticker: string; weight_pct: number; liquidity_score: number; flag: string; estimated_adv_usd?: number | null }>;
    summary: { flagged_count: number; basis?: string };
  };
  benchmark_relative: {
    status: string;
    benchmark_symbol?: string;
    beta?: number;
    correlation?: number;
    active_vol_pct?: number;
    tracking_error_pct?: number;
    relative_drawdown_pct?: number;
    overlap_days?: number;
    message?: string;
  };
  methodology: Record<string, unknown>;
}

export interface EvidenceLabWindow {
  signal_date: string;
  forward_end_date: string;
  input_start_date: string;
  input_rows: number;
  horizon_days: number;
  signal_score: number;
  action_label: string;
  forward_result_pct?: number | null;
  benchmark_result_pct?: number | null;
  alpha_pct?: number | null;
  paper_hit?: boolean | null;
  false_positive: boolean;
  regime: string;
}

export interface EvidenceLabSummary {
  window_count: number;
  measured_count: number;
  hit_count: number;
  hit_rate_pct?: number | null;
  avg_score?: number | null;
  avg_forward_result_pct?: number | null;
  avg_benchmark_result_pct?: number | null;
  avg_alpha_pct?: number | null;
  positive_alpha_rate_pct?: number | null;
  first_signal_date?: string | null;
  last_signal_date?: string | null;
}

export interface EvidenceConfidenceBand {
  count: number;
  mean?: number | null;
  p05?: number | null;
  p25?: number | null;
  p50?: number | null;
  p75?: number | null;
  p95?: number | null;
  ci90_low?: number | null;
  ci90_high?: number | null;
}

export interface EvidenceProspectiveEntry {
  id?: number | null;
  created_at?: string | null;
  target_kind?: string | null;
  target_id?: string | null;
  target_name?: string | null;
  benchmark?: string | null;
  input_start_date?: string | null;
  input_end_date?: string | null;
  input_rows?: number | null;
  horizon_days?: number | null;
  score?: number | null;
  action_label?: string | null;
  forward_status?: string | null;
  forward_end_date?: string | null;
  forward_result_pct?: number | null;
  benchmark_result_pct?: number | null;
  alpha_pct?: number | null;
  paper_hit?: boolean | null;
}

export interface EvidenceProspectiveValidation {
  status: string;
  basis: string;
  total_count: number;
  measured_count: number;
  pending_count: number;
  hit_count: number;
  hit_rate_pct?: number | null;
  avg_score?: number | null;
  avg_forward_result_pct?: number | null;
  avg_benchmark_result_pct?: number | null;
  avg_alpha_pct?: number | null;
  benchmark_comparison: SignalJournalBenchmarkComparison[];
  latest_entries: EvidenceProspectiveEntry[];
  caveat: string;
}

export interface EvidenceLabResponse extends ProvenancePayload {
  target: { kind: "instrument" | "model" | string; id: string; name: string; mandate?: string; source?: string };
  benchmark: { symbol: string; status: string };
  parameters: { horizon_days?: number; train_window?: number; step?: number; decay_horizons?: number[] };
  summary: EvidenceLabSummary;
  false_positives: {
    count: number;
    rate_pct?: number | null;
    directional_signal_count?: number;
    basis: string;
  };
  confidence_bands: {
    forward_result_pct: EvidenceConfidenceBand;
    alpha_pct: EvidenceConfidenceBand;
    hit_rate_pct: EvidenceConfidenceBand;
  };
  regime_sensitivity: Array<{
    regime: string;
    count: number;
    hit_rate_pct?: number | null;
    avg_alpha_pct?: number | null;
    avg_forward_result_pct?: number | null;
    false_positive_rate_pct?: number | null;
  }>;
  decay: Array<{
    horizon_days: number;
    measured_count: number;
    hit_rate_pct?: number | null;
    avg_forward_result_pct?: number | null;
    avg_alpha_pct?: number | null;
    false_positive_rate_pct?: number | null;
    information_coefficient?: number | null;
    confidence_bands: EvidenceConfidenceBand;
  }>;
  windows: EvidenceLabWindow[];
  prospective_validation: EvidenceProspectiveValidation;
  evidence_unavailable?: boolean;
  missing_tickers?: string[];
  methodology: Record<string, unknown>;
}

export interface ReportResponse extends ProvenancePayload {
  kind: "instrument" | "model" | string;
  title: string;
  timestamp: string;
  sections: Record<string, unknown>;
}

export interface ReportSnapshot {
  id: string;
  created_at: string;
  target_kind: "instrument" | "model" | string;
  target_id: string;
  target_name: string;
  title: string;
  data_mode: DataMode;
  display_label: string;
  eligible_for_real_research: boolean;
  source: string;
  row_count: number;
  first_date?: string | null;
  last_date?: string | null;
  source_counts: Record<string, number>;
  model_metadata: Record<string, unknown>;
  warnings: string[];
  ai_narrative_included: boolean;
  ai_narrative_status: string;
  ai_provider: Record<string, unknown>;
  html_url: string;
  pdf_url: string;
  metadata?: Record<string, unknown>;
}

export interface ReportSnapshotSaveRequest {
  kind: "instrument" | "model" | string;
  id: string;
  ai_narrative?: string;
  include_ai_narrative?: boolean;
}

export interface ReportSnapshotSaveResponse {
  snapshot: ReportSnapshot;
  html_url: string;
  pdf_url: string;
  storage: ReportSnapshotStorage;
  disclaimer: string;
}

export interface ReportSnapshotHistoryResponse {
  snapshots: ReportSnapshot[];
  count: number;
  storage: ReportSnapshotStorage;
  warning?: string;
  disclaimer: string;
}

export interface ReportSnapshotStorage {
  backend: string;
  scope: string;
  durable: boolean;
  configured: boolean;
  encrypted_at_rest: boolean;
  at_rest_format: string;
  warning?: string;
}

export interface SignalJournalEntry {
  id: number;
  created_at: string;
  target_kind: string;
  target_id: string;
  target_name: string;
  benchmark: string;
  input_start_date: string;
  input_end_date: string;
  input_rows: number;
  horizon_days: number;
  score: number;
  action_label: string;
  data_mode: string;
  eligible_for_real_research: boolean;
  source_counts: Record<string, number>;
  forward_status: string;
  forward_start_date?: string | null;
  forward_end_date?: string | null;
  forward_result_pct?: number | null;
  benchmark_result_pct?: number | null;
  alpha_pct?: number | null;
  evaluated_at?: string | null;
}

export interface SignalJournalSummary {
  total_count: number;
  measured_count: number;
  pending_count: number;
  hit_count: number;
  hit_rate_pct?: number | null;
  avg_score?: number | null;
  avg_forward_result_pct?: number | null;
  avg_benchmark_result_pct?: number | null;
  avg_alpha_pct?: number | null;
  model_count: number;
  instrument_count: number;
  research_ready_count: number;
}

export interface SignalJournalBenchmarkComparison {
  benchmark: string;
  measured_count: number;
  avg_forward_result_pct?: number | null;
  avg_benchmark_result_pct?: number | null;
  avg_alpha_pct?: number | null;
  hit_rate_pct?: number | null;
}

export interface SignalJournalModelEvidence {
  target_id: string;
  target_name: string;
  signal_count: number;
  measured_count: number;
  pending_count: number;
  hit_rate_pct?: number | null;
  avg_score?: number | null;
  avg_alpha_pct?: number | null;
  latest_action_label: string;
  latest_score?: number | null;
  latest_input_end_date: string;
  benchmark: string;
  data_modes: string[];
  source_counts: Record<string, number>;
  research_ready_count: number;
}

export interface SignalJournalDriftPoint {
  index: number;
  created_at?: string | null;
  input_end_date?: string | null;
  target_kind?: string | null;
  target_id?: string | null;
  target_name?: string | null;
  action_label?: string | null;
  score?: number | null;
  forward_status?: string | null;
  forward_result_pct?: number | null;
  benchmark_result_pct?: number | null;
  alpha_pct?: number | null;
  paper_hit?: boolean | null;
  cumulative_measured_count: number;
  cumulative_hit_rate_pct?: number | null;
  cumulative_avg_alpha_pct?: number | null;
}

export interface SignalJournalResponse {
  entries: SignalJournalEntry[];
  count: number;
  summary: SignalJournalSummary;
  benchmark_comparison: SignalJournalBenchmarkComparison[];
  model_evidence: SignalJournalModelEvidence[];
  drift: SignalJournalDriftPoint[];
  methodology: Record<string, unknown>;
  disclaimer: string;
}

export interface AnalysisResponse {
  symbol?: string;
  id?: string;
  name: string;
  source?: string;
  metrics: MetricSet;
  series: {
    dates: string[];
    close: Array<number | null>;
    sma50?: Array<number | null>;
    sma200?: Array<number | null>;
  };
  signal: {
    action: string;
    conviction_pct: number;
    headline_rationale?: string;
    rationale?: string;
    caveats?: string[];
  };
  forecast: Record<string, unknown>;
  backtest: Record<string, unknown>;
  holdings?: Array<Record<string, unknown>>;
  warnings?: string[];
  provenance?: Record<string, unknown>;
  mandate?: { label: string; key: string };
  horizon?: Record<string, unknown>;
}

export interface AIStatusResponse {
  enabled: boolean;
  provider: "none" | "local" | "anthropic" | "openai" | "hybrid" | string;
  mode: "disabled" | "local" | "cloud" | "composite" | string;
  model: string;
  available: boolean;
  reason: string;
  privacy_warning?: string;
  security_warnings?: string[];
  keys_exposed: boolean;
  secrets_stored: boolean;
}

export interface AIResult {
  summary: string;
  key_points: string[];
  risks: string[];
  what_would_invalidate: string[];
  advisor_language: string;
  compliance_caveats: string[];
  used_numbers: string[];
  missing_information: string[];
  data_quality_statement: string;
  provider: string;
  model: string;
  generated_at: string;
  task?: string;
  cached?: boolean;
  needs_review?: boolean;
  malformed_json?: boolean;
  unsupported_numbers?: string[];
  blocked_phrases?: string[];
  deterministic_action?: string;
  data_mode?: string;
}

export interface AIResponse {
  result: AIResult;
  status: AIStatusResponse;
  provider: string;
  model: string;
  data_quality: DataQuality;
  disclaimer: string;
}
