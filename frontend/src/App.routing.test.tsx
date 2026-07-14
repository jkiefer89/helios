/**
 * Workflow tests (review D-item): the hash routing round-trip is the deep-link
 * contract — a decision journal permalink or a shared analysis URL must
 * reconstruct the exact view + selection, and unknown hashes must fall back
 * safely instead of crashing the shell.
 */
import { describe, expect, it } from "vitest";
import { parseHash } from "./App";

describe("hash routing contract", () => {
  it("round-trips instrument and model deep links", () => {
    expect(parseHash("#/analysis/instrument/JPM")).toEqual({ view: "analysis", kind: "instrument", id: "JPM" });
    expect(parseHash("#/clinic/model/ULTRA-CORE")).toEqual({ view: "clinic", kind: "model", id: "ULTRA-CORE" });
    expect(parseHash("#/decisions")).toEqual({ view: "decisions" });
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
});
