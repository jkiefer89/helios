export type DataMode = "real" | "mixed" | "invalid_for_research" | string;
export type ResearchState = "no_data" | "invalid" | "stale" | "mixed" | "blocked" | "ready";

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
  reason_code?: string;
  retryable?: boolean;
  next_step?: string;
  stale_result_preserved?: boolean;
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
    reason_code?: string;
    retryable?: boolean;
    next_step?: string;
    stale_result_preserved?: boolean;
  }>;
  warnings: string[];
  data_status: DataStatusResponse;
  data_quality: DataQualityResponse;
  incident_sync: Record<string, unknown>;
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
  incident_sync?: Record<string, unknown>;
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
  // "unavailable" means NO benchmark data: score is null and no meter renders.
  status?: "ok" | "unavailable" | string;
  label: "risk-on" | "neutral" | "risk-off" | "unavailable" | string;
  score: number | null;
  summary: string;
  drivers: RegimeDriver[];
  warnings: string[];
  symbol?: string | null;
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

export interface MacroSummary {
  as_of?: string;
  fed_available: boolean;
  fed_stance_label?: string | null;
  fed_stance_score?: number | null;
  gpr_available: boolean;
  gpr_index?: number | null;
  gpr_level?: string | null;
  policy_available: boolean;
  policy_themes?: Record<string, number>;
  fomc_start?: string;
  fomc_days_until?: number | null;
  fomc_imminent?: boolean;
}

export interface CommandCenterResponse extends ProvenancePayload {
  regime: RegimePayload;
  macro?: MacroSummary | null;
  top_opportunities: CommandItem[];
  top_risks: CommandItem[];
  model_alerts: ModelAlert[];
  research_queue: ResearchQueueItem[];
  readiness: {
    state: ResearchState;
    ready: boolean;
    summary: string;
    checks: Array<{
      key: string;
      label: string;
      passed: boolean;
      detail: string;
      view: string;
    }>;
  };
  blockers: Array<{
    id: string;
    severity: string;
    title: string;
    detail: string;
    required_action: string;
    view: string;
  }>;
  recent_changes: Array<{
    id: string;
    kind: string;
    title: string;
    detail: string;
    created_at: string;
    view: string;
  }>;
  next_action: {
    label: string;
    detail: string;
    view: string;
  };
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

// Blocked models omit the scoring/driver fields the engine only computes for
// eligible candidates; everything beyond identity is optional to match reality.
export interface BlockedOpportunityItem {
  id: string;
  kind: "model" | string;
  symbol: string;
  name: string;
  model_id?: string | null;
  action?: string;
  opportunity_score?: number;
  risk_score?: number;
  evidence_score?: number;
  eligible_for_real_research?: boolean;
  data_mode?: DataMode;
  display_label?: string;
  reason?: string;
  required_action?: string;
  missing_tickers?: string[];
  warnings?: string[];
}

export interface OpportunitiesResponse extends ProvenancePayload {
  regime: RegimePayload;
  items: OpportunityItem[];
  blocked_items?: BlockedOpportunityItem[];
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

export interface CapacityHoldingRow {
  ticker: string;
  weight_pct: number;
  position_usd: number;
  adv_source: string;
  proxy_as_of?: string | null;
  status: string;
  estimated_adv_usd?: number;
  one_day_participation_pct?: number;
  days_to_liquidate_10pct?: number;
  days_to_liquidate_20pct?: number;
  impact_estimate_bps?: number;
}

export interface CapacityBlock {
  status: string;
  note?: string;
  aum_usd?: number;
  holdings?: CapacityHoldingRow[];
  max_days_to_liquidate_10pct?: number;
  weighted_days_to_liquidate_10pct?: number;
  holdings_over_5d?: string[];
  holdings_over_20d?: string[];
  unsized_count?: number;
  proxy_based_count?: number;
  basis?: string;
}

export interface RiskAnalyticsResponse extends ProvenancePayload {
  id: string;
  name: string;
  risk_exposure_unavailable?: boolean;
  capacity?: CapacityBlock;
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
  validation_method: "rolling" | "anchored" | string;
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
  avg_alpha_after_default_costs_pct?: number | null;
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
  avg_alpha_after_default_costs_pct?: number | null;
  benchmark_comparison: SignalJournalBenchmarkComparison[];
  latest_entries: EvidenceProspectiveEntry[];
  caveat: string;
}

export interface EvidenceLabResponse extends ProvenancePayload {
  target: { kind: "instrument" | "model" | string; id: string; name: string; mandate?: string; source?: string };
  benchmark: { symbol: string; status: string };
  parameters: { horizon_days?: number; train_window?: number; step?: number; decay_horizons?: number[]; validation_methods?: string[] };
  summary: EvidenceLabSummary;
  validation_methods: Record<"rolling" | "anchored", {
    mode: string;
    training_policy: string;
    summary: EvidenceLabSummary;
    confidence_bands: {
      alpha_pct: EvidenceConfidenceBand;
      hit_rate_pct: EvidenceConfidenceBand;
    };
    regime_sensitivity: Array<{
      regime: string;
      count: number;
      hit_rate_pct?: number | null;
      avg_alpha_pct?: number | null;
      avg_alpha_after_default_costs_pct?: number | null;
      avg_forward_result_pct?: number | null;
      false_positive_rate_pct?: number | null;
    }>;
  }>;
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
    avg_alpha_after_default_costs_pct?: number | null;
    avg_forward_result_pct?: number | null;
    false_positive_rate_pct?: number | null;
  }>;
  regime_robustness: {
    status: string;
    passed: boolean;
    coverage_passed?: boolean;
    performance_consistent?: boolean;
    false_positive_control?: boolean;
    covered_regimes: number;
    required_regimes: number;
    min_windows_per_regime?: number;
    worst_net_alpha_pct?: number | null;
    worst_false_positive_rate_pct?: number | null;
    basis?: string;
  };
  multiplicity: {
    evaluated_horizons: number;
    familywise_alpha: number;
    bonferroni_alpha?: number | null;
    selection_warning?: string;
  };
  decay: Array<{
    horizon_days: number;
    measured_count: number;
    hit_rate_pct?: number | null;
    avg_forward_result_pct?: number | null;
    avg_alpha_pct?: number | null;
    avg_alpha_after_default_costs_pct?: number | null;
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
  validation_score: number | null;
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
  validation_verdicts: {
    policy: {
      version: string;
      profile: string;
      mandate_key: string;
      floors: Record<string, number>;
      non_overridable: boolean;
      basis: string;
    };
    data_valid: ModelValidationVerdict;
    method_valid: ModelValidationVerdict;
    edge_supported: ModelValidationVerdict;
  };
  drift_alerts: ModelValidationAlert[];
  methodology: Record<string, unknown>;
  disclaimer: string;
  // Winner's-curse guards: score ranks on the first ~80% of windows; the
  // untouched last ~20% independently confirms (or refutes) the champion.
  ranking_basis?: { basis: string; window_count: number; measured_count: number;
    hit_rate_pct: number | null; avg_alpha_pct: number | null;
    false_positive_rate_pct: number | null };
  holdout_confirmation?: { status: string; measured_count: number; min_required?: number;
    window_count?: number; hit_rate_pct?: number | null; avg_alpha_pct?: number | null;
    false_positive_rate_pct?: number | null };
  ci_inputs?: Record<string, number>;
}

export interface ModelValidationVerdict {
  passed: boolean;
  detail: string;
  required_action: string;
  failed_checks: string[];
  checks: Array<{
    name: string;
    passed: boolean;
    actual: unknown;
    required: unknown;
    operator: string;
  }>;
}

export interface ModelValidationResponse {
  models: ModelValidationRow[];
  champion?: ModelValidationRow | null;
  challengers: ModelValidationRow[];
  alerts: ModelValidationAlert[];
  selection?: {
    n_trials: number;
    basis: string;
    champion_adjusted?: { status: string; n_trials?: number; z?: number;
      hit_rate_ci_low_pct?: number; hit_rate_ci_high_pct?: number;
      alpha_ci_low_pct?: number; alpha_ci_high_pct?: number; basis?: string };
    prospective_confirmation?: { measured_count?: number | null;
      hit_rate_pct?: number | null; avg_alpha_pct?: number | null;
      avg_alpha_after_default_costs_pct?: number | null; basis?: string };
    deflated_sharpe?: Record<string, number | string>;
    pbo?: Record<string, number | string>;
  };
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

export interface LedgerAccount {
  account_id: string;
  display_name: string;
  model_id: string;
  created_at: string;
}

export interface LedgerActual {
  status: string;
  account_id?: string;
  label?: string;
  note?: string;
  snapshot_count?: number;
  period?: { start: string; end: string; days: number };
  twr_net_pct?: number;
  twr_gross_pct?: number;
  fees_usd?: number;
  external_flows_usd?: number;
  avg_cash_weight_pct?: number | null;
  cash_drag_est_pct?: number | null;
  n_periods?: number;
  periods?: Array<Record<string, unknown>>;
  basis?: string;
}

export interface LedgerShortfallRow {
  decision_id: string;
  ticker: string;
  side: string;
  status: string;
  n_fills?: number;
  shares?: number;
  avg_fill_price?: number;
  decision_date?: string;
  decision_close?: number;
  shortfall_bps?: number;
  fees_usd?: number;
  basis?: string;
}

export interface LedgerPerformanceResponse {
  actual: LedgerActual;
  paper?: { status: string; model_id?: string; model_name?: string; label?: string;
    return_pct?: number; series_basis?: string; note?: string };
  gap_pct?: number;
  gap_note?: string;
  shortfall: LedgerShortfallRow[];
  disclaimer?: string;
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
  capital_context?: {
    aum_usd?: number | null;
    aum_as_of?: string;
    capacity_status?: string;
    capacity_unsized_count?: number;
    capacity?: CapacityBlock;
  };
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
  aum_usd?: number;
  aum_as_of?: string;
  cloud_confirmation?: CloudAIConfirmation;
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

export interface SignalRecordResponse {
  signal_journal_entry: SignalJournalEntry | Record<string, unknown>;
  disclaimer: string;
}

export interface SignalJournalSummary {
  total_count: number;
  outcome_count: number;
  measured_count: number;
  pending_count: number;
  hit_count: number;
  hit_rate_pct?: number | null;
  hold_measured_count: number;
  hold_preserved_count: number;
  hold_preservation_rate_pct?: number | null;
  avg_score?: number | null;
  avg_forward_result_pct?: number | null;
  avg_benchmark_result_pct?: number | null;
  avg_alpha_pct?: number | null;
  avg_alpha_after_default_costs_pct?: number | null;
  model_count: number;
  instrument_count: number;
  research_ready_count: number;
}

export interface SignalJournalBenchmarkComparison {
  benchmark: string;
  outcome_count?: number;
  measured_count: number;
  avg_forward_result_pct?: number | null;
  avg_benchmark_result_pct?: number | null;
  avg_alpha_pct?: number | null;
  avg_alpha_after_default_costs_pct?: number | null;
  hit_rate_pct?: number | null;
}

export interface SignalJournalModelEvidence {
  target_id: string;
  target_name: string;
  signal_count: number;
  measured_count: number;
  outcome_count?: number;
  pending_count: number;
  hit_rate_pct?: number | null;
  hold_preservation_rate_pct?: number | null;
  avg_score?: number | null;
  avg_alpha_pct?: number | null;
  avg_alpha_after_default_costs_pct?: number | null;
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
  hold_preserved?: boolean | null;
  cumulative_measured_count: number;
  cumulative_hit_rate_pct?: number | null;
  cumulative_hold_measured_count?: number;
  cumulative_hold_preservation_rate_pct?: number | null;
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
  markers?: SignalMarker[];
}

export interface SignalMarker {
  date: string;
  type: "buy" | "sell";
  price: number;
}

export type ForecastBandKey = "p05" | "p25" | "p50" | "p75" | "p95";
export type ForecastBands = Record<ForecastBandKey, number[]>;

export interface ForecastCalibrationBin {
  bin: string;
  n: number;
  avg_predicted_pct: number;
  avg_p_up: number;
  realized_up_rate: number;
  realized_accuracy_pct: number;
}

export interface ForecastCalibration {
  status: "ok" | "insufficient_data";
  n_test?: number;
  brier_score?: number;
  brier_reference?: number;
  brier_skill?: number;
  bins?: ForecastCalibrationBin[];
  basis?: string;
}

export interface ForecastQuality {
  r2?: number | null;
  rmse?: number | null;
  directional_accuracy?: number | null;
  n_test?: number;
  calibration?: ForecastCalibration;
}

export interface FeatureWeight {
  feature: string;
  weight: number;
}

export interface TacticalForecast {
  kind?: undefined;
  dates: string[];
  bands: ForecastBands;
  horizon_days: number;
  expected_return_pct: number;
  prob_up: number;
  model_daily_drift_pct: number;
  annualized_drift_pct: number;
  expected_vol_pct: number;
  quality: ForecastQuality;
  feature_weights: FeatureWeight[];
}

export interface LongForecast {
  kind: "long";
  label: string;
  horizon_days: number;
  base_value: number;
  dates: string[];
  bands: ForecastBands;
  terminal: Record<ForecastBandKey, number>;
  cagr_pct: Record<ForecastBandKey, number>;
  prob_positive: number;
  prob_meets_mandate: number;
  mandate_target_pct: number;
  drawdown_median_pct: number;
  drawdown_p95_pct: number;
  prob_breach_maxdd: number;
  params: {
    mu_long_pct: number;
    mu_hist_pct: number;
    mu_anchor_pct: number;
    anchor_weight_lambda: number;
    sigma_ann_pct: number;
    sigma_eff_pct: number;
    regime_mult: number;
    n_paths: number;
    step: string;
  };
  disclaimer: string;
}

export type AnalysisForecast = TacticalForecast | LongForecast;

export interface SignalComponent {
  name: string;
  raw: number;
  base_weight: number;
  effective_weight: number;
  contribution: number;
  clause: string;
}

export interface TacticalTrack {
  action: string;
  score: number;
  conviction_pct: number;
  basis?: string;
}

export interface StrategicTrack {
  usable: boolean;
  reason?: string;
  action?: string;
  score?: number;
  conviction_pct?: number;
  expected_return_pct?: number;
  anchor_pct?: number;
  gap_vs_anchor_pct?: number;
  blocks_pct?: Record<string, number>;
  source?: string;
  quality?: Record<string, number>;
  analyst?: {
    target_mean_price?: number;
    implied_upside_pct?: number;
    rating_mean?: number | null;
    n_analysts?: number | null;
    note?: string;
  };
  basis?: string;
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
  tactical?: TacticalTrack;
  strategic?: StrategicTrack;
  headline_rationale?: string;
  rationale?: string;
  caveats?: string[];
}

export interface SentimentItem {
  headline: string;
  score: number;
  label: string;
}

export interface SentimentPayload {
  items: SentimentItem[];
  aggregate_score: number;
  aggregate_label: string;
  count: number;
}

export interface BacktestStats {
  total_return_pct: number;
  annual_return_pct: number;
  annual_vol_pct: number;
  sharpe: number;
  max_drawdown_pct: number;
}

export interface BacktestPayload {
  dates?: string[];
  strategy_curve?: Array<number | null>;
  benchmark_curve?: Array<number | null>;
  strategy?: BacktestStats;
  benchmark?: BacktestStats;
  n_trades?: number;
  win_rate_pct?: number;
  exposure_pct?: number;
  error?: string;
}

export interface ModelInsight {
  id: string;
  category: string;
  severity: string;
  message: string;
  suggested_action: string;
  rationale: string;
}

export interface AnalysisMandate {
  key: string;
  label: string;
  description?: string;
  target_vol_pct?: number;
  max_drawdown_tolerance_pct?: number;
  target_return_pct?: number;
  single_name_cap?: number;
  typical_horizons?: string[];
}

export interface AnalysisHorizon {
  kind: "short" | "long";
  value: number;
  label?: string | null;
  available_long: string[];
}

export interface AnalysisHolding {
  ticker: string;
  weight: number;
  source?: string;
  signal?: string;
  window_return_pct?: number | null;
  mrc_pct?: number | null;
  excluded?: boolean;
  note?: string;
  [key: string]: unknown;
}

export interface AnalysisResponse {
  symbol?: string;
  id?: string;
  name: string;
  source?: string;
  metrics: MetricSet;
  series: AnalysisSeries;
  signal: AnalysisSignal;
  forecast: AnalysisForecast;
  forecast_short?: TacticalForecast;
  sentiment?: SentimentPayload;
  backtest: BacktestPayload;
  insights?: ModelInsight[];
  // Structured mandate-fit verdicts computed server-side from the same
  // variables the insights use (never inferred from insight-id absence).
  mandate_checks?: {
    vol_ok: boolean;
    vol_band_pct: number;
    dd_hist_ok: boolean;
    dd_sim_breach_prob: number | null;
    dd_sim_ok: boolean | null;
  };
  holdings?: AnalysisHolding[];
  concentration?: { hhi: number; n_eff: number; corr_mean: number };
  // Model NAV is a weight-rescaled research construction, never a track record.
  series_basis?: string;
  series_basis_note?: string;
  // Operator thesis: how this model is actually used (sent to the copilot).
  thesis?: string;
  thesis_params?: { income_monthly_draw_usd?: number;
    income_bucket_min_months?: number; income_bucket_max_months?: number };
  context?: string;
  signal_journal_entry?: Record<string, unknown> | null;
  warnings?: string[];
  provenance?: Record<string, unknown>;
  // Engine-issued provenance verdict (same shape as /api/live). Optional for
  // backward compatibility with older cached responses; when present it is
  // authoritative and the client must not re-derive provenance eligibility.
  data_provenance?: DataQuality;
  mandate?: AnalysisMandate;
  horizon?: AnalysisHorizon;
  fundamentals?: Record<string, unknown>;
  rates?: Record<string, unknown>;
  sec_events?: SecEvents;
  macro?: MacroSummary | null;
}

export interface SecEventItem {
  filing_date: string;
  form: string;
  items: string[];
  labels: string[];
  notable: boolean;
  url?: string;
}

export interface SecInsider {
  filings_in_window: number;
  parsed: Array<{
    filing_date: string;
    owner: string;
    is_officer: boolean;
    buys: number;
    sells: number;
    buy_shares: number;
    sell_shares: number;
  }>;
  open_market_purchases: number;
  open_market_sales: number;
  net_signal: "buying" | "selling" | "mixed" | "none" | string;
  note?: string;
}

export interface SecEvents {
  available: boolean;
  reason?: string;
  symbol?: string;
  window_days?: number;
  eight_ks?: SecEventItem[];
  notable_8k?: boolean;
  insider?: SecInsider;
}

export interface DecisionOutcome {
  end_date: string;
  target_return_pct: number;
  benchmark_return_pct?: number;
  alpha_pct?: number;
  hit?: boolean | null;
  engine_hit?: boolean | null;
}

export interface DecisionEntry {
  decision_id: string;
  created_at: string;
  target_kind: string;
  target_id: string;
  target_name: string;
  mandate: string;
  benchmark: string;
  engine_action: string;
  engine_score?: number | null;
  tactical_action?: string;
  strategic_action?: string;
  my_action: string;
  agreement: string;
  rationale: string;
  decision_date: string;
  decision_price?: number | null;
  data_mode: string;
  context?: Record<string, unknown>;
  outcome_status: string;
  outcomes: Record<string, DecisionOutcome>;
  evaluated_at?: string;
}

export interface DecisionBucketStats {
  count: number;
  measured_count: number;
  hit_count: number;
  hit_rate_pct: number | null;
  avg_target_return_pct: number | null;
  avg_alpha_pct: number | null;
}

export interface DecisionScoreboard {
  total: DecisionBucketStats;
  agree: DecisionBucketStats;
  override: DecisionBucketStats;
  override_vs_engine: { override_won: number; engine_won: number; tied: number; note?: string };
  by_mandate: Record<string, DecisionBucketStats>;
  not_measurable_count: number;
  disclaimer?: string;
}

export interface DecisionsResponse {
  decisions: DecisionEntry[];
  scoreboard: DecisionScoreboard;
  disclaimer?: string;
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
  stance?: string;
  ai_disagrees_with_action?: boolean;
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
  cloud_transfer?: CloudTransferDisclosure;
}

export interface AIChatResponse {
  reply: string;
  provider: string;
  model: string;
  n_history_messages?: number;
  generated_at?: string;
  status?: AIStatusResponse;
  data_quality?: DataQuality;
  disclaimer?: string;
  cloud_transfer?: CloudTransferDisclosure;
}

export interface CloudAIConfirmation {
  confirmed: true;
  disclosure_hash: string;
}

export interface CloudTransferDisclosure {
  disclosure_hash: string;
  dlp_payload_hash: string;
  provider: string;
  model: string;
  task: string;
  transfer_scope: "final_sanitized_provider_request";
  cloud_transfer: boolean;
  confirmation_required: boolean;
  confirmed: boolean;
  redaction_count: number;
  redaction_categories: Record<string, number>;
  redacted_fields: string[];
  raw_values_returned: boolean;
  review_required: boolean;
  basis: string;
}


export interface RebalanceTrade {
  ticker: string;
  side: "BUY" | "SELL";
  current_weight_pct: number;
  target_weight_pct: number;
  proposed_weight_pct: number;
  trade_usd: number;
  est_shares: number;
  price_used: number;
  price_as_of: string;
  est_cost_usd: number;
  est_days_to_trade: number | null;
}

export interface RebalanceProposal {
  status: "proposed" | "infeasible" | "blocked";
  reason?: string;
  account_id?: string;
  model_id?: string;
  model_name?: string;
  snapshot_as_of?: string;
  total_value_usd?: number;
  method?: string;
  trades?: RebalanceTrade[];
  suppressed_dust_trades?: number;
  target_reachable?: boolean;
  violations?: Array<{ constraint: string; detail: string }>;
  summary?: {
    n_trades: number;
    one_way_turnover_pct: number;
    residual_to_target_pct: number;
    est_total_cost_usd: number;
    proposed_cash_pct: number;
  };
  unpriced_tickers?: string[];
  warnings?: string[];
  solver_notes?: string[];
  basis?: string;
  disclaimer?: string;
}

export interface DataJobsResponse {
  auto_live: Record<string, unknown> & { running?: boolean; last_run?: string | null };
  refresh_log: RefreshLogEntry[];
  recent_failures: RefreshLogEntry[];
  freshness: Array<{ symbol: string; last_bar: string; age_calendar_days: number; rows: number; price_provider: string }>;
  stale_count: number;
  audit_chain: { status: string; entries?: number; first_bad_seq?: number };
  vault_recent: Array<{ id: number; provider: string; endpoint: string; symbol: string; retrieved_at: string }>;
  price_revisions_recent: Array<{ symbol: string; bar_date: string; old_close: number; new_close: number; change_pct: number; observed_at: string }>;
  basis: string;
}

export interface TrialProtocol {
  hypothesis: string;
  primary_metric: string;
  horizon_days: number;
  step_days: number;
  benchmark: string;
  cost_assumptions: {
    commission_bps_per_side: number;
    spread_bps_per_side: number;
    slippage_bps_per_side: number;
    market_impact_bps_per_side: number;
    tax_drag_bps: number;
    idle_cash_pct: number;
  };
  success_thresholds: {
    min_observations: number;
    min_hit_rate_pct: number;
    min_net_alpha_pct: number;
    max_false_positive_rate_pct: number;
    confidence_level_pct: number;
  };
  regimes: string[];
  owner: string;
  allowed_sources: string[];
  freshness_days: number;
  expected_aum_usd: number;
  max_position_pct: number;
  max_adv_participation_pct: number;
  planned_variants: string[];
  deleted_variants: string[];
}

export interface TrialThresholdPolicy {
  version: string;
  profile: string;
  mandate_key: string;
  floors: TrialProtocol["success_thresholds"];
  non_overridable: boolean;
}

export interface TrialImplementationLayer {
  status: string;
  measured_count?: number;
  scheduled_count?: number;
  decision_count?: number;
  linked_fill_count?: number;
  linked_decision_count?: number;
  avg_net_directional_alpha_pct?: number | null;
  observed_notional_usd?: number;
  observed_fees_usd?: number;
  observed_fee_bps?: number | null;
  observed_shortfall_bps?: number | null;
  cost_basis?: string;
  observed_components?: string[];
  modeled_components?: string[];
  unobserved_components?: string[];
}

export interface ProspectiveTrialAssessment {
  state: string;
  passed: boolean;
  checks: Record<string, boolean>;
  observations: {
    scheduled_count: number;
    measured_directional_count: number;
    pending_count: number;
    hold_count: number;
  };
  metrics: {
    directional_hit_rate_pct?: number | null;
    avg_net_directional_alpha_pct?: number | null;
    false_positive_rate_pct?: number | null;
    hit_rate_confidence_interval_pct: { low?: number | null; high?: number | null };
  };
  multiplicity: {
    declared_variant_count: number;
    familywise_alpha: number;
    bonferroni_alpha: number;
    adjusted_confidence_pct: number;
    enforced_in_confidence_check: boolean;
    deleted_variants: string[];
  };
  regime_robustness: {
    status: string;
    coverage_passed: boolean;
    performance_passed: boolean;
    minimum_observations_per_regime: number;
    rows: Array<{
      regime: string;
      count: number;
      avg_net_directional_alpha_pct?: number | null;
      false_positive_rate_pct?: number | null;
      coverage_passed: boolean;
      performance_passed: boolean;
    }>;
    basis: string;
  };
  implementation_evidence: {
    paper: TrialImplementationLayer;
    proposed: TrialImplementationLayer;
    actual: TrialImplementationLayer;
    analysis_only: boolean;
    no_execution: boolean;
  };
  capacity: Record<string, unknown> & { passed?: boolean; status?: string; expected_aum_usd?: number };
}

export interface ProspectiveTrial {
  trial_id: string;
  created_at: string;
  actor: string;
  target_kind: "instrument" | "model";
  target_id: string;
  model_version: number;
  starts_on: string;
  status: string;
  closed_at?: string;
  close_note?: string;
  protocol: TrialProtocol;
  protocol_hash: string;
  registration_snapshot_hash: string;
  assessment: ProspectiveTrialAssessment;
}

export interface ProspectiveTrialsResponse {
  trials: ProspectiveTrial[];
  threshold_policy?: TrialThresholdPolicy | null;
  storage_available: boolean;
  disclaimer: string;
}

export interface ProviderControl {
  key: string;
  name: string;
  domains: string[];
  configured: boolean;
  licensed: boolean;
  entitled: boolean;
  sla_owner: string;
  research_only: boolean;
  institutional_ready: boolean;
  roles: string[];
}

export interface ProviderCutover {
  cutover_id: string;
  created_at: string;
  data_domain: string;
  primary_provider: string;
  backup_provider: string;
  reconciliation_id: string;
  status: string;
  note: string;
}

export interface ProviderReconciliation {
  reconciliation_id: string;
  created_at: string;
  data_domain: string;
  primary_provider: string;
  backup_provider: string;
  symbol_count: number;
  compared_count: number;
  mismatch_count: number;
  max_abs_difference_pct: number | null;
  status: string;
  note: string;
}

export interface ProvidersResponse {
  controls_required: boolean;
  providers: ProviderControl[];
  cutovers: ProviderCutover[];
  reconciliations: ProviderReconciliation[];
  reconciliation_policy: {
    minimum_symbol_count: number;
    maximum_age_days: number;
    maximum_absolute_difference_pct: number;
    evidence_origin: "server_fetch" | string;
  };
  disclaimer: string;
}

export interface OperationalIncident {
  incident_id: string;
  category: string;
  severity: string;
  target: string;
  detail: string;
  status: string;
  owner: string;
  updated_at: string;
}

export interface OperationsStatusResponse {
  incidents: OperationalIncident[];
  summary: { open: number; acknowledged: number; critical: number };
  incident_owner: string;
  notification_adapter: { configured: boolean; mode: string; automatic_delivery: boolean };
  backup: { count?: number; latest?: string; encrypted?: boolean; warning?: string };
  latest_backup_verification: {
    created_at: string;
    outcome: string;
    details: {
      passed?: boolean;
      isolated_restore_tested?: boolean;
      live_data_mutated?: boolean;
      rpo_seconds?: number;
      rpo_target_seconds?: number;
      rpo_met?: boolean;
      rto_seconds?: number;
      rto_target_seconds?: number;
      rto_met?: boolean;
      restore_drill_passed?: boolean;
    };
  } | null;
  audit_chain: { status: string; entries?: number; total_entries?: number; warning?: string };
  privileged_chain: { status: string; entries?: number; total_entries?: number; warning?: string };
  persistence_encryption: {
    enabled: boolean;
    key_id?: string;
    key_version?: string;
    custody_mode?: string;
    custody_attested?: boolean;
    external_custody?: boolean;
    rotation_configured?: boolean;
    recovery_key_count?: number;
    recovery_key_used?: boolean;
  };
  backup_export: {
    configured: boolean;
    offhost_attested: boolean;
    plaintext_exported: boolean;
    external_custody_verified_by_helios: boolean;
    latest_export: { created_at?: string; outcome?: string; resource?: string } | null;
  };
  audit_export: {
    configured: boolean;
    required: boolean;
    worm_siem_attested: boolean;
    written: boolean;
    current_checkpoint?: boolean;
    latest_event_hash?: string;
    current_privileged_head?: string;
    checkpoint_application_head?: string;
    current_application_head?: string;
    updated_at: string;
    local_append_only_evidence: boolean;
    external_immutability_verified_by_helios: boolean;
  };
}

export interface SecurityStatusResponse {
  principal: {
    user: string;
    roles: string[];
    auth_method: string;
    mfa_verified: boolean;
    tenant_id: string;
    client_id: string;
  };
  authentication: {
    enabled: boolean;
    sso_enabled: boolean;
    enterprise_identity: Record<string, unknown>;
    mfa_required_for_privileged_actions: boolean;
    session_idle_seconds: number;
    session_absolute_seconds: number;
  };
  transport: { trusted_tls_asserted: boolean; trusted_hosts_configured: boolean; trusted_proxy_count: number };
  audit: {
    privileged_chain: OperationsStatusResponse["privileged_chain"];
    application_chain: OperationsStatusResponse["audit_chain"];
  };
  workspace: {
    tenant_id: string;
    client_id: string;
    scope_hash: string;
    boundary: string;
    database_bound: boolean;
    scope_mismatch: boolean;
  };
  request_protection: {
    header: string;
    token: string;
    expires_at: number;
    expires_in_seconds: number;
    available?: boolean;
    binding?: "session" | "principal" | "session_bootstrap";
    reason?: string;
  };
  external_requirements: string[];
}

export interface IndependentValidationReview {
  review_id: string;
  model_id: string;
  model_version: number;
  sponsor: string;
  validator: string;
  outcome: string;
  reviewed_at: string;
  next_review_due: string;
  controls: Record<string, unknown>;
  findings: unknown[];
  note: string;
}

export interface IndependentValidationException {
  exception_id: string;
  model_id: string;
  model_version: number;
  control_key: string;
  owner: string;
  approver: string;
  reason: string;
  compensating_controls: string[];
  expires_at: string;
  status: string;
}

export interface IndependentValidationResponse {
  status: {
    passed: boolean;
    state: string;
    model_id: string;
    model_version: number;
    detail: string;
    required_action: string;
  };
  reviews: IndependentValidationReview[];
  exceptions: IndependentValidationException[];
}
