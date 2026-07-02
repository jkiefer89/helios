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

export interface ModelGovernanceVersionDiff {
  added: Array<{ ticker: string; weight_pct: number }>;
  removed: Array<{ ticker: string; weight_pct: number }>;
  changed_weights: Array<{ ticker: string; from_weight_pct: number; to_weight_pct: number; change_pct: number }>;
  turnover_pct: number;
  summary: string;
}

export interface ModelGovernanceRiskGate {
  can_approve: boolean;
  state: string;
  blocked_reason: string;
  violations: ModelGovernanceViolation[];
}

export interface ModelGovernanceCommitteeIdentity {
  signer_name: string;
  signer_role: string;
  committee: string;
  verification_method: string;
  verified: boolean;
  scope: string;
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
  can_approve: boolean;
  approval_blocked_reason: string;
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
  holdings: Array<{ ticker: string; weight: number; weight_pct: number; source?: string }>;
  source: string;
  provenance: ModelTemplate["provenance"];
  committee_identity?: ModelGovernanceCommitteeIdentity;
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
  committee_note?: string;
  committee_identity?: ModelGovernanceCommitteeIdentity;
  version_diff?: ModelGovernanceVersionDiff;
  risk_gate?: ModelGovernanceRiskGate;
  snapshot?: Record<string, unknown>;
  metadata?: { committee_identity?: ModelGovernanceCommitteeIdentity; [key: string]: unknown };
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
  committee_identity?: ModelGovernanceCommitteeIdentity;
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

export interface ModelGovernanceApprovalPacket {
  available: boolean;
  packet_type: string;
  model: ModelGovernanceRow;
  approval: {
    status: string;
    approved_by: string;
    approval_updated_at?: string | null;
    can_approve: boolean;
    blocked_reason: string;
    committee_identity?: ModelGovernanceCommitteeIdentity;
  };
  committee_identity?: ModelGovernanceCommitteeIdentity;
  risk_gate: ModelGovernanceRiskGate;
  risk_limits: ModelTemplate["risk_limits"];
  version: number;
  version_diff: ModelGovernanceVersionDiff;
  before_snapshot: Record<string, unknown>;
  after_snapshot: {
    model?: Record<string, unknown>;
    version?: number;
    holdings?: Array<{ ticker: string; weight: number; weight_pct: number; source?: string }>;
    risk_limits?: ModelTemplate["risk_limits"];
    risk_limit_violations?: ModelGovernanceViolation[];
    risk_gate?: ModelGovernanceRiskGate;
  };
  committee_notes: Array<{ event_id: number; created_at: string; actor: string; action: string; note: string; committee_identity?: ModelGovernanceCommitteeIdentity }>;
  snapshots: ModelGovernanceSnapshot[];
  audit_trail: ModelGovernanceEvent[];
  export: {
    formats: string[];
    json_url: string;
    html_url: string;
    pdf_url: string;
  };
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

export interface DataQualityAlert {
  id: string;
  category: string;
  severity: "blocker" | "warning" | "info" | string;
  target: string;
  detail: string;
  next_step: string;
  status: "active" | "resolved" | string;
  first_seen_at: string;
  last_seen_at: string;
  last_changed_at: string;
  resolved_at?: string | null;
  occurrence_count: number;
  notification_state: "new" | "reopened" | "changed" | "active" | "resolved" | string;
  should_notify: boolean;
  metadata?: Record<string, unknown>;
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
  alerts: {
    tracking_available: boolean;
    warning: string;
    active: DataQualityAlert[];
    resolved: DataQualityAlert[];
    notifications: DataQualityAlert[];
    summary: {
      active_count: number;
      resolved_count: number;
      notification_count: number;
      new_count: number;
      changed_count: number;
      blocker_count: number;
      warning_count: number;
    };
  };
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
  strategy_curve?: Array<number | null>;
  benchmark_curve?: Array<number | null>;
  drawdown_curve?: Array<number | null>;
  rolling_sharpe_curve?: Array<number | null>;
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
  client_risk_pack: ClientRiskPack;
  methodology: Record<string, unknown>;
}

export interface ClientRiskPack {
  available: boolean;
  summary: {
    model_id?: string;
    model_name?: string;
    risk_posture?: string;
    benchmark_symbol?: string;
    data_mode?: string;
    source_counts?: Record<string, number>;
  };
  stress_scenarios: Array<{
    scenario: string;
    portfolio_impact_pct: number;
    severity: string;
    basis: string;
    what_it_tests: string;
  }>;
  historical_stress_replay: Array<{
    scenario: string;
    window_days: number;
    portfolio_impact_pct: number;
    start_date?: string;
    end_date?: string;
    basis: string;
    severity: string;
    what_it_tests: string;
  }>;
  benchmark_relative_drawdown: {
    status: string;
    benchmark_symbol?: string;
    relative_drawdown_pct?: number | null;
    beta?: number | null;
    correlation?: number | null;
    tracking_error_pct?: number | null;
    overlap_days?: number | null;
    interpretation?: string;
  };
  concentration_warnings: Array<{ type: string; severity: string; title: string; detail: string }>;
  liquidity_flags: {
    items: Array<{
      ticker?: string;
      weight_pct?: number;
      liquidity_score?: number;
      flag?: string;
      estimated_adv_usd?: number | null;
      observed_adv_usd?: number | null;
      adv_source?: string;
      adv_observation_days?: number;
      language?: string;
    }>;
    summary: { flagged_count: number; observed_count?: number; proxy_count?: number; basis?: string };
  };
  correlation_clusters: Array<{
    name?: string;
    type?: string;
    average_correlation?: number | null;
    pairs?: Array<{ tickers: string[]; correlation: number }>;
    language?: string;
  }>;
  what_would_break_this_model: Array<{ driver: string; severity: string; trigger: string; evidence: string; language: string }>;
  required_action?: string;
  missing_tickers?: string[];
  methodology: Record<string, unknown>;
  disclaimer: string;
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

export interface ModelValidationAlert {
  severity: string;
  title: string;
  detail: string;
  model_id?: string;
  model_name?: string;
}

export interface ModelValidationRow {
  model_id: string;
  model_name: string;
  mandate: string;
  role: string;
  validation_state: string;
  validation_score: number;
  validation_grade: string;
  evidence_unavailable: boolean;
  reason: string;
  required_action: string;
  walk_forward: EvidenceLabSummary;
  false_positives: EvidenceLabResponse["false_positives"];
  regime_sensitivity: EvidenceLabResponse["regime_sensitivity"];
  decay: EvidenceLabResponse["decay"];
  confidence_bands: EvidenceLabResponse["confidence_bands"];
  prospective_validation: EvidenceProspectiveValidation;
  governance: {
    version: number;
    approval_status: string;
    risk_limit_state: string;
    risk_limit_violations: Array<Record<string, unknown>>;
    rebalance_status: string;
    snapshot_count: number;
    latest_change_note: string;
    updated_by: string;
  };
  drift_alerts: ModelValidationAlert[];
  methodology: Record<string, unknown>;
  disclaimer: string;
}

export interface ModelValidationResponse {
  models: ModelValidationRow[];
  champion?: ModelValidationRow | null;
  challengers: ModelValidationRow[];
  alerts: ModelValidationAlert[];
  summary: {
    model_count: number;
    eligible_count: number;
    blocked_count: number;
    champion_model_id?: string | null;
    challenger_count: number;
    alert_count: number;
    governance_available: boolean;
  };
  parameters: { horizon_days: number; train_window: number; step: number };
  methodology: Record<string, unknown>;
  disclaimer: string;
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
  report_package: string;
  version: number;
  version_label: string;
  target_kind: "instrument" | "model" | string;
  target_id: string;
  target_name: string;
  prepared_for: string;
  prepared_by: string;
  reviewer: string;
  report_purpose: string;
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
  signal_journal?: ReportSignalJournalEvidence;
  client_risk_pack?: ClientRiskPack;
  warnings: string[];
  ai_narrative_included: boolean;
  ai_narrative_status: string;
  ai_provider: Record<string, unknown>;
  audit_trail: Array<{
    event: string;
    at: string;
    actor: string;
    summary: string;
  }>;
  disclosure_blocks: Array<{
    title: string;
    body: string;
  }>;
  output_formats: string[];
  html_url: string;
  pdf_url: string;
  metadata?: Record<string, unknown>;
}

export interface ReportSnapshotSaveRequest {
  kind: "instrument" | "model" | string;
  id: string;
  ai_narrative?: string;
  include_ai_narrative?: boolean;
  prepared_for?: string;
  prepared_by?: string;
  reviewer?: string;
  report_purpose?: string;
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

export interface ReportSignalJournalEvidence {
  scope: string;
  target_kind: string;
  target_id: string;
  summary: SignalJournalSummary;
  benchmark_comparison: SignalJournalBenchmarkComparison[];
  model_credibility: SignalJournalModelEvidence[];
  drift: SignalJournalDriftPoint[];
  target_history: SignalJournalEntry[];
  methodology: Record<string, unknown>;
  disclaimer: string;
}

export interface AnalysisMarker {
  date: string;
  type: "buy" | "sell" | string;
  price: number;
}

export interface AnalysisSeries {
  dates: string[];
  close: Array<number | null>;
  sma50?: Array<number | null>;
  sma200?: Array<number | null>;
  bb_upper?: Array<number | null>;
  bb_lower?: Array<number | null>;
  rsi?: Array<number | null>;
  macd?: Array<number | null>;
  macd_signal?: Array<number | null>;
  macd_hist?: Array<number | null>;
  markers?: AnalysisMarker[];
}

export interface ForecastBands {
  p05: Array<number | null>;
  p25: Array<number | null>;
  p50: Array<number | null>;
  p75: Array<number | null>;
  p95: Array<number | null>;
}

export interface ForecastQuality {
  r2?: number | null;
  rmse?: number | null;
  directional_accuracy?: number | null;
  n_test?: number;
}

export interface ForecastFeatureWeight {
  feature: string;
  weight: number;
}

export interface TacticalForecast {
  kind?: "short";
  dates: string[];
  bands: ForecastBands;
  horizon_days: number;
  expected_return_pct: number;
  prob_up: number;
  model_daily_drift_pct?: number;
  annualized_drift_pct?: number;
  expected_vol_pct?: number;
  quality?: ForecastQuality;
  feature_weights?: ForecastFeatureWeight[];
}

export interface ForecastPercentiles {
  p05?: number;
  p25?: number;
  p50?: number;
  p75?: number;
  p95?: number;
}

export interface LongHorizonForecast {
  kind: "long";
  label: string;
  horizon_days: number;
  base_value: number;
  dates: string[];
  bands: ForecastBands;
  terminal: ForecastPercentiles;
  cagr_pct: ForecastPercentiles;
  prob_positive: number;
  prob_meets_mandate?: number;
  mandate_target_pct?: number;
  drawdown_median_pct?: number;
  drawdown_p95_pct?: number;
  prob_breach_maxdd?: number;
  params?: {
    mu_long_pct?: number;
    mu_hist_pct?: number;
    mu_anchor_pct?: number;
    anchor_weight_lambda?: number;
    sigma_ann_pct?: number;
    sigma_eff_pct?: number;
    regime_mult?: number;
    n_paths?: number;
    step?: string;
  };
  disclaimer?: string;
}

export type AnalysisForecast = TacticalForecast | LongHorizonForecast;

export interface SentimentItem {
  headline: string;
  score: number;
  label: "positive" | "negative" | "neutral" | string;
}

export interface SentimentPayload {
  items: SentimentItem[];
  aggregate_score: number;
  aggregate_label: string;
  count: number;
}

export interface SignalComponent {
  name: string;
  raw: number;
  base_weight: number;
  effective_weight: number;
  contribution: number;
  clause?: string;
}

export interface AnalysisSignal {
  action: string;
  score?: number;
  conviction_pct: number;
  conviction_band?: string;
  vol_penalty?: number;
  mandate_fit?: number;
  mandate?: string | null;
  components?: SignalComponent[];
  headline_rationale?: string;
  rationale?: string;
  caveats?: string[];
}

export interface BacktestPayload {
  dates: string[];
  strategy_curve: Array<number | null>;
  benchmark_curve: Array<number | null>;
  strategy: MetricSet;
  benchmark: MetricSet;
  n_trades: number;
  win_rate_pct: number;
  exposure_pct: number;
}

export interface AnalysisInsight {
  id: string;
  category: string;
  severity: "high" | "medium" | "low" | string;
  message: string;
  suggested_action: string;
  rationale?: string;
}

export interface AnalysisMandate {
  key: string;
  label: string;
  description?: string;
  target_vol_pct?: number;
  max_drawdown_tolerance_pct?: number;
  target_return_pct?: number;
  single_name_cap?: number;
  growth_orientation?: number;
  income_orientation?: number;
  typical_horizons?: string[];
  weights?: Record<string, number>;
}

export interface AnalysisHorizon {
  kind: "short" | "long" | string;
  value: number;
  label?: string | null;
  available_long?: string[];
}

export interface AnalysisHolding {
  ticker: string;
  weight: number;
  source?: string;
  window_return_pct?: number | null;
  mrc_pct?: number | null;
  excluded?: boolean;
  note?: string;
  signal?: string;
  [key: string]: unknown;
}

export interface AnalysisResponse {
  symbol?: string;
  id?: string;
  name: string;
  source?: string;
  context?: string;
  metrics: MetricSet;
  series: AnalysisSeries;
  signal: AnalysisSignal;
  sentiment?: SentimentPayload;
  forecast: AnalysisForecast;
  forecast_short?: TacticalForecast;
  backtest: BacktestPayload;
  insights?: AnalysisInsight[];
  holdings?: AnalysisHolding[];
  concentration?: { hhi?: number; n_eff?: number; corr_mean?: number };
  warnings?: string[];
  provenance?: Record<string, unknown>;
  mandate?: AnalysisMandate;
  horizon?: AnalysisHorizon;
  signal_journal_entry?: Record<string, unknown> | null;
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
