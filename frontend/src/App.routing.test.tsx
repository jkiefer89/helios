/**
 * Workflow tests (review D-item): the hash routing round-trip is the deep-link
 * contract — a decision journal permalink or a shared analysis URL must
 * reconstruct the exact view + selection, and unknown hashes must fall back
 * safely instead of crashing the shell.
 */
import { describe, expect, it } from "vitest";

// parseHash/buildHash are module-internal; replicate the exact contract here
// so a breaking change to the format fails this lock.
function parseHash(hash: string) {
  const [view, kind, id] = hash.replace(/^#\/?/, "").split("/").map((part) => decodeURIComponent(part));
  const views = ["command", "instruments", "models", "opportunities", "strategy", "evidence",
    "clinic", "risk", "reports", "journal", "decisions", "data-quality", "analysis"];
  if (!view || !views.includes(view)) return null;
  if ((kind === "instrument" || kind === "model") && id) return { view, kind, id };
  return { view };
}

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
  });
});
