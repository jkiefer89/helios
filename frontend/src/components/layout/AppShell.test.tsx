/**
 * The five workspaces must partition the view universe exactly: a view
 * missing from every group would be unreachable from the primary nav, and a
 * view in two groups would make the derived active-workspace ambiguous.
 */
import { describe, expect, it } from "vitest";
import { isViewId, navGroups } from "./AppShell";

describe("workspace navigation mapping", () => {
  it("covers every view exactly once across the five workspaces", () => {
    const all = navGroups.flatMap((group) => group.ids);
    expect(new Set(all).size).toBe(all.length);        // no view in two workspaces
    for (const id of all) expect(isViewId(id)).toBe(true);
    // Every declared ViewId is reachable from some workspace.
    const reachable = new Set(all);
    for (const id of ["command", "instruments", "models", "opportunities", "strategy",
      "evidence", "clinic", "risk", "reports", "journal", "decisions",
      "data-quality", "analysis"]) {
      expect(reachable.has(id as never)).toBe(true);
    }
    expect(navGroups.length).toBe(5);
  });
});
