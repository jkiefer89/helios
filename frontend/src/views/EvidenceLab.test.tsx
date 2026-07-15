import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EvidenceLabResponse, TickerSummary } from "../api/types";

const apiMocks = vi.hoisted(() => ({
  evidenceLab: vi.fn(),
  trials: vi.fn(),
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, api: { ...actual.api, ...apiMocks } };
});

import { EvidenceLab } from "./EvidenceLab";

const lockedPayload = {
  target: { kind: "instrument", id: "SPY", name: "SPY" },
  benchmark: { symbol: "SPY", status: "unavailable" },
  parameters: {},
  windows: [],
  decay: [],
  evidence_unavailable: true,
  eligible_for_real_research: false,
  data_mode: "invalid_for_research",
  display_label: "Evidence unavailable",
  reason: "More history is required.",
  required_action: "Fetch a longer approved history.",
  data_provenance: { source_counts: {} },
} as unknown as EvidenceLabResponse;

beforeEach(() => {
  apiMocks.evidenceLab.mockReset().mockResolvedValue(lockedPayload);
  apiMocks.trials.mockReset().mockResolvedValue({ trials: [], threshold_policy: null });
});

afterEach(cleanup);

describe("EvidenceLab controls", () => {
  it("enforces server-aligned integer bounds before running evidence", async () => {
    const tickers = [{ symbol: "SPY", name: "S&P 500 ETF", source: "live" }] as TickerSummary[];
    render(<EvidenceLab
      tickers={tickers}
      models={[]}
      selectedInstrument="SPY"
      onSelectInstrument={vi.fn()}
      onSelectModel={vi.fn()}
    />);

    await waitFor(() => expect(apiMocks.evidenceLab).toHaveBeenCalledTimes(1));
    await waitFor(() => expect((screen.getByRole("button", { name: "Run evidence" }) as HTMLButtonElement).disabled).toBe(false));

    fireEvent.change(screen.getByLabelText("Horizon"), { target: { value: "1" } });
    expect(screen.getByText("Horizon must be between 5 and 252.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Run evidence" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("Horizon"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("Train rows"), { target: { value: "90" } });
    fireEvent.change(screen.getByLabelText("Step"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Run evidence" }));

    await waitFor(() => expect(apiMocks.evidenceLab).toHaveBeenCalledTimes(2));
    expect(apiMocks.evidenceLab).toHaveBeenLastCalledWith({
      kind: "instrument",
      id: "SPY",
      horizon: 5,
      trainWindow: 90,
      step: 5,
    });

    fireEvent.change(screen.getByLabelText("Horizon"), { target: { value: "253" } });
    expect(screen.getByText("Horizon must be between 5 and 252.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Horizon"), { target: { value: "" } });
    expect(screen.getByText("Horizon is required.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Horizon"), { target: { value: "21.5" } });
    expect(screen.getByText("Horizon must be a whole number.")).toBeTruthy();
    expect(apiMocks.evidenceLab).toHaveBeenCalledTimes(2);
  });
});
