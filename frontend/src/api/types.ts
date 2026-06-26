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

export interface ReportResponse extends ProvenancePayload {
  kind: "instrument" | "model" | string;
  title: string;
  timestamp: string;
  sections: Record<string, unknown>;
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
