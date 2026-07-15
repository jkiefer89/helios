import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StrategyResponse } from "../api/types";

const apiMocks = vi.hoisted(() => ({
  strategyInstrument: vi.fn(),
  strategyModel: vi.fn(),
  saveResearchContext: vi.fn(),
}));

vi.mock("../api/client", () => ({ api: apiMocks }));
vi.mock("../components/ai/AICopilotPanel", () => ({
  AICopilotPanel: () => null,
  strategyCopilotActions: [],
}));
vi.mock("../components/charts/Charts", () => ({
  ChartSummary: () => null,
  DrawdownChart: () => null,
  EquityCurveChart: () => null,
  RollingSharpeChart: () => null,
}));

import { StrategyLab } from "./StrategyLab";

function strategyPayload(configured = true): StrategyResponse {
  return {
    series_kind: "instrument",
    symbol: "SOXX",
    name: "Semiconductor ETF",
    source: "upload",
    data_mode: "real",
    display_label: "Real Research Mode",
    eligible_for_real_research: true,
    strategy: { total_return_pct: 8.2, sharpe: 0.6, max_drawdown_pct: -7.1 },
    benchmark: { total_return_pct: 7.4 },
    trade_stats: { completed_trades: 4, exposure_pct: 62 },
    current_signal: {
      framework: "strategy_threshold_state",
      action_label: "MAINTAIN_LONG",
      signal_state: "long",
      position_on_last_observed_session: "long",
      score: 0.22,
      as_of_date: "2026-07-14",
      effective_session: "next_trading_session",
      entry_threshold: 0.15,
      exit_threshold: -0.05,
      basis: "Observed at close and effective next session.",
    },
    path_evidence: {
      trade_summary: { completed_count: 4, winning_count: 3, losing_count: 1 },
      drawdown_summary: { maximum_drawdown_pct: -7.1, deepest_episodes: [] },
      rolling_sharpe_summary: { latest: 0.6, negative_window_pct: 20 },
      privacy_basis: "Full curves remain local.",
    },
    oos_evidence: {
      status: "ok",
      policy: { selected_on_test: false },
      fold_count: 4,
      primary: {
        entry_threshold: 0.15, exit_threshold: -0.05, primary: true,
        fold_count: 4, oos_sessions: 84, strategy_return_pct: 5.1,
        benchmark_return_pct: 4.2, net_excess_return_pct: 0.9, sharpe: 0.5,
        max_drawdown_pct: -5.2, profitable_fold_pct: 75,
        benchmark_beating_fold_pct: 50, worst_fold_excess_pct: -1.2,
      },
      sensitivity: {
        diagnostic_only: true, winner_selected: false, variant_count: 1,
        primary_rank_by_net_excess: 1, positive_excess_variant_count: 1,
        net_excess_range_pct: [0.9, 0.9], variants: [], basis: "Diagnostic only.",
      },
    },
    freshness: {
      status: "uploaded_source_date", row_count: 320,
      first_bar_date: "2025-04-01", latest_bar_date: "2026-07-14",
    },
    research_context: configured ? {
      configured: true, target_kind: "instrument", target_id: "SOXX",
      target_name: "Semiconductor ETF", version: 1,
      thesis: "Semiconductor demand should compound faster than the governed benchmark.",
      mandate_key: "pure_growth", mandate_label: "Pure Growth", benchmark: "QQQ",
      horizon_days: 63, invalidation_criteria: ["Relative strength remains negative."],
    } : {
      configured: false, target_kind: "instrument", target_id: "SOXX",
    },
    dates: [], strategy_curve: [], benchmark_curve: [], drawdown_curve: [], rolling_sharpe_curve: [],
    beat_benchmark: true,
  };
}

const baseProps = {
  tickers: [{ symbol: "SOXX", name: "Semiconductor ETF", source: "upload", last_price: 100, change_pct: 1 }],
  models: [],
  mandates: [{ key: "pure_growth", label: "Pure Growth", target_vol_pct: 22 }],
  selectedInstrument: "SOXX",
  onSelectInstrument: vi.fn(),
  onSelectModel: vi.fn(),
};

beforeEach(() => {
  apiMocks.strategyInstrument.mockReset().mockResolvedValue(strategyPayload());
  apiMocks.strategyModel.mockReset();
  apiMocks.saveResearchContext.mockReset().mockResolvedValue({ research_context: strategyPayload().research_context });
});

afterEach(cleanup);

describe("Strategy Lab evidence completeness", () => {
  it("renders an explicit action, path evidence, freshness, and walk-forward result", async () => {
    render(<StrategyLab {...baseProps} />);

    await waitFor(() => expect(screen.getAllByText("Maintain Long").length).toBeGreaterThan(0));
    expect(screen.getByText("Walk-Forward Evidence")).toBeTruthy();
    expect(screen.getByText("Path Evidence")).toBeTruthy();
    expect(screen.getByText("Uploaded Source Date")).toBeTruthy();
    expect(screen.getByText("Semiconductor demand should compound faster than the governed benchmark.")).toBeTruthy();
  });

  it("saves complete governed instrument context with a required change note", async () => {
    apiMocks.strategyInstrument.mockResolvedValue(strategyPayload(false));
    render(<StrategyLab {...baseProps} />);

    const thesis = await screen.findByLabelText("Investment thesis");
    fireEvent.change(thesis, { target: { value: "Own semiconductor breadth while demand and relative strength support the mandate." } });
    fireEvent.click(screen.getByRole("combobox", { name: "Research mandate" }));
    fireEvent.pointerDown(screen.getByRole("option", { name: "Pure Growth" }));
    fireEvent.change(screen.getByLabelText("Benchmark"), { target: { value: "QQQ" } });
    fireEvent.change(screen.getByLabelText("Horizon, sessions"), { target: { value: "63" } });
    fireEvent.change(screen.getByLabelText("Invalidation criteria, one per line"), { target: { value: "Relative strength remains negative for two reviews." } });
    fireEvent.change(screen.getByLabelText("Required change note"), { target: { value: "Set initial governed context." } });
    fireEvent.click(screen.getByRole("button", { name: "Save governed context" }));

    await waitFor(() => expect(apiMocks.saveResearchContext).toHaveBeenCalledWith({
      target_kind: "instrument",
      target_id: "SOXX",
      thesis: "Own semiconductor breadth while demand and relative strength support the mandate.",
      mandate_key: "pure_growth",
      benchmark: "QQQ",
      horizon_days: 63,
      invalidation_criteria: ["Relative strength remains negative for two reviews."],
      change_note: "Set initial governed context.",
    }));
  });
});
