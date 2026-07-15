/**
 * Workflow tests (review D-item): the hash routing round-trip is the deep-link
 * contract — a decision journal permalink or a shared analysis URL must
 * reconstruct the exact view + selection, and unknown hashes must fall back
 * safely instead of crashing the shell.
 */
import { describe, expect, it } from "vitest";
import { parseHash, refreshIncompleteError } from "./App";
import type { DataRefreshResponse } from "./api/types";

describe("hash routing contract", () => {
  it("round-trips instrument and model deep links", () => {
    expect(parseHash("#/analysis/instrument/JPM")).toEqual({ view: "analysis", kind: "instrument", id: "JPM" });
    expect(parseHash("#/clinic/model/ULTRA-CORE")).toEqual({ view: "clinic", kind: "model", id: "ULTRA-CORE" });
    expect(parseHash("#/decisions")).toEqual({ view: "decisions" });
    expect(parseHash("#/setup")).toEqual({ view: "setup" });
  });
  it("decodes URI components in ids", () => {
    expect(parseHash("#/analysis/instrument/BRK%2EB")).toEqual({ view: "analysis", kind: "instrument", id: "BRK.B" });
  });
  it("rejects unknown views and malformed hashes instead of crashing", () => {
    expect(parseHash("#/nonsense")).toBeNull();
    expect(parseHash("")).toBeNull();
    expect(parseHash("#/analysis/instrument/")).toEqual({ view: "analysis" });
    expect(parseHash("#/analysis/instrument/%E0%A4%A")).toBeNull();
  });

  it("turns HTTP-200 aggregate refresh failures into retryable structured failures", () => {
    const error = refreshIncompleteError({
      requested: "all",
      refreshed: 2,
      failed: 1,
      skipped: 0,
      results: [{
        symbol: "MSFT",
        status: "error",
        rows_added: 0,
        message: "Provider timed out.",
        reason_code: "provider_temporarily_unavailable",
        retryable: true,
        next_step: "Retry after recovery.",
        stale_result_preserved: true,
      }],
      warnings: [],
      data_status: {} as DataRefreshResponse["data_status"],
      data_quality: {} as DataRefreshResponse["data_quality"],
      incident_sync: {},
    });

    expect(error?.code).toBe("live_refresh_incomplete");
    expect(error?.details.stale_result_preserved).toBe(true);
    expect(error?.details.diagnostics).toEqual({
      refreshed: 2,
      failed: 1,
      skipped: 0,
      reason_codes: ["provider_temporarily_unavailable"],
    });
  });
});
