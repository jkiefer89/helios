import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AnalysisResponse } from "../api/types";

const apiMocks = vi.hoisted(() => ({
  recordInstrumentSignal: vi.fn(),
  recordModelSignal: vi.fn(),
  recordDecision: vi.fn(),
}));

vi.mock("../api/client", () => ({ api: apiMocks }));

import { DecisionQuickLog } from "./Analysis";

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
