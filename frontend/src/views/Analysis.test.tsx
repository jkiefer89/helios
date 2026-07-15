import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AnalysisResponse } from "../api/types";

const apiMocks = vi.hoisted(() => ({
  recordInstrumentSignal: vi.fn(),
  recordModelSignal: vi.fn(),
  recordDecision: vi.fn(),
  setModelThesis: vi.fn(),
}));

vi.mock("../api/client", () => ({ api: apiMocks }));

import { ConvictionGuidancePanel, DecisionQuickLog, ThesisEditor } from "./Analysis";

function payload(kind: "instrument" | "model"): AnalysisResponse {
  return {
    ...(kind === "model"
      ? { id: "MODEL-1", mandate: { key: "balanced", label: "Balanced" } }
      : { symbol: "AAPL", source: "upload" }),
    name: kind === "model" ? "Model One" : "Apple",
    metrics: {},
    series: { dates: [], close: [] },
    signal: { action: "BUY", score: 0.6, conviction_pct: 60, rationale: "Prospective signal." },
    forecast: { horizon_days: 21 } as AnalysisResponse["forecast"],
    backtest: {} as AnalysisResponse["backtest"],
  } as AnalysisResponse;
}

beforeEach(() => {
  apiMocks.recordInstrumentSignal.mockReset().mockResolvedValue({ signal_journal_entry: {}, disclaimer: "Analysis only." });
  apiMocks.recordModelSignal.mockReset().mockResolvedValue({ signal_journal_entry: {}, disclaimer: "Analysis only." });
  apiMocks.recordDecision.mockReset().mockResolvedValue({});
  apiMocks.setModelThesis.mockReset().mockResolvedValue({});
});

afterEach(cleanup);

describe("prospective signal recording", () => {
  it("labels the advisor action and rationale controls", () => {
    render(<DecisionQuickLog payload={payload("instrument")} />);

    expect(screen.getByLabelText("Your action")).toBeTruthy();
    expect(screen.getByLabelText("Rationale")).toBeTruthy();
  });

  it("records an instrument only after the operator clicks the explicit action", async () => {
    render(<DecisionQuickLog payload={payload("instrument")} />);
    expect(apiMocks.recordInstrumentSignal).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Record Helios signal" }));

    await waitFor(() => expect(apiMocks.recordInstrumentSignal).toHaveBeenCalledWith("AAPL", 21));
    expect(screen.getByText("Signal recorded")).toBeTruthy();
  });

  it("uses the model signal endpoint for model analysis", async () => {
    render(<DecisionQuickLog payload={payload("model")} />);
    fireEvent.click(screen.getByRole("button", { name: "Record Helios signal" }));

    await waitFor(() => expect(apiMocks.recordModelSignal).toHaveBeenCalledWith("MODEL-1", 21));
    expect(apiMocks.recordInstrumentSignal).not.toHaveBeenCalled();
  });
});

describe("governed model thesis editing", () => {
  it("requires and submits a governance change note", async () => {
    const modelPayload = {
      ...payload("model"),
      thesis: "Capture durable growth with measured downside control.",
      thesis_params: {},
    } as AnalysisResponse;
    render(<ThesisEditor payload={modelPayload} />);

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const save = screen.getByRole("button", { name: "Save thesis" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("Thesis governance change note"), {
      target: { value: "Document committee rationale." },
    });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);

    await waitFor(() => expect(apiMocks.setModelThesis).toHaveBeenCalledWith({
      id: "MODEL-1",
      thesis: "Capture durable growth with measured downside control.",
      change_note: "Document committee rationale.",
      thesis_params: {},
    }));
  });
});

describe("conviction guidance", () => {
  it("renders backend score constraints without recomputing or promising an outcome", () => {
    const signal = {
      action: "HOLD",
      score: 0.18,
      conviction_pct: 18,
      conviction_guidance: {
        title: "How conviction can improve",
        summary: "Current HOLD conviction is 18.0% (low). 2 evidence constraints or gaps are active.",
        direction: "bullish" as const,
        limiter_count: 2,
        score_bridge: {
          base_component_conviction_pct: 24,
          volatility_multiplier: 0.9,
          mandate_multiplier: 0.85,
          event_risk_multiplier: 1,
          final_conviction_pct: 18,
        },
        aligned_components: ["Trend"],
        conflicting_components: ["Momentum"],
        paths: [{
          key: "forecast_edge",
          title: "Measured forecast edge",
          status: "evidence_gap" as const,
          current: "Directional accuracy is 48% across 80 rolling-origin observations; the forecast receives 0% of its mandate weight.",
          what_changes_it: "Accuracy must reach 55% for full forecast weight.",
          next_evidence: "Accumulate untouched outcomes; do not tune on held-out windows.",
        }],
        guardrail: "Conviction measures evidence strength, not expected return. Higher conviction can support BUY or SELL. No path guarantees a favorable outcome.",
      },
    };

    render(<ConvictionGuidancePanel signal={signal} />);

    expect(screen.getByRole("heading", { name: "How Conviction Can Improve" })).toBeTruthy();
    expect(screen.getByText("18.0%", { exact: true })).toBeTruthy();
    expect(screen.getByText("Measured forecast edge")).toBeTruthy();
    expect(screen.getByText(/forecast receives 0%/)).toBeTruthy();
    expect(screen.getByText(/Higher conviction can support BUY or SELL/)).toBeTruthy();
  });

  it("renders nothing for older responses without guidance", () => {
    const { container } = render(
      <ConvictionGuidancePanel signal={{ action: "HOLD", conviction_pct: 12 }} />,
    );

    expect(container.childElementCount).toBe(0);
  });
});
