import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  listDecisions: vi.fn(),
  ledgerAccounts: vi.fn(),
  models: vi.fn(),
  recordDecision: vi.fn(),
}));

vi.mock("../api/client", () => ({ api: apiMocks }));

import { Decisions } from "./Decisions";

beforeEach(() => {
  apiMocks.listDecisions.mockReset().mockResolvedValue({
    decisions: [{
      decision_id: "decision-1",
      created_at: "2026-07-13T10:00:00Z",
      target_id: "AAPL",
      engine_action: "BUY",
      my_action: "HOLD",
      agreement: "override",
      outcome_status: "pending",
      outcomes: {},
      rationale: "Valuation requires another review.",
    }],
  });
  apiMocks.ledgerAccounts.mockReset().mockResolvedValue({ accounts: [] });
  apiMocks.models.mockReset().mockResolvedValue({ models: [] });
  apiMocks.recordDecision.mockReset().mockResolvedValue({});
});

afterEach(cleanup);

describe("Decision Journal accessibility", () => {
  it("uses labeled controls and a semantic history table", async () => {
    render(<Decisions />);

    expect(screen.getByLabelText("Target type")).toBeTruthy();
    expect(screen.getByLabelText("Ticker")).toBeTruthy();
    expect(screen.getByLabelText("Your action")).toBeTruthy();
    expect(screen.getByLabelText("Decision rationale")).toBeTruthy();
    await waitFor(() => expect(screen.getByRole("table")).toBeTruthy());
    expect(screen.getByRole("columnheader", { name: "Rationale" })).toBeTruthy();
    expect(screen.getByRole("rowheader", { name: "AAPL" })).toBeTruthy();
  });
});
