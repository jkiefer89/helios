/**
 * Honesty-critical rendering locks: the provenance badges and banners are the
 * operator's first line of defense against demo/mixed data being read as real
 * evidence — their tones and gate labels must never silently regress.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { DataModeBadge, DataQualityBanner, modeTone, SourcePill } from "./DataModeBadge";

afterEach(cleanup);

describe("modeTone", () => {
  it("maps every data mode to its honest tone", () => {
    expect(modeTone("real")).toBe("positive");
    expect(modeTone("mixed")).toBe("warning");
    expect(modeTone("demo")).toBe("neutral");
    expect(modeTone("invalid_for_research")).toBe("negative");
    expect(modeTone(undefined)).toBe("neutral");
    expect(modeTone("something_new")).toBe("neutral"); // unknown is never green
  });
});

describe("DataModeBadge", () => {
  it("never renders an empty label — absence is stated, not blank", () => {
    render(<DataModeBadge />);
    expect(screen.getByText("Data status unavailable")).toBeTruthy();
  });

  it("carries the tone class for real data", () => {
    render(<DataModeBadge mode="real" label="Live Data" />);
    expect(screen.getByText("Live Data").className).toContain("tone-positive");
  });
});

describe("DataQualityBanner", () => {
  it("shows the mixed gate and missing tickers when ineligible", () => {
    render(
      <DataQualityBanner
        payload={{
          data_mode: "mixed",
          display_label: "Mixed Data Warning",
          eligible_for_real_research: false,
          reason: "Some model weight lacks live/uploaded price history.",
          data_provenance: {
            data_mode: "mixed",
            missing_tickers: ["ZZZT", "QQQX"],
          },
        }}
      />,
    );
    expect(screen.getByText("Mixed")).toBeTruthy();
    expect(screen.getByText(/Missing ZZZT, QQQX/)).toBeTruthy();
    expect(screen.getByText("Mixed Data Warning").closest("section")?.className).toContain("research-gate--mixed");
  });

  it("shows the eligible chip for fully real data", () => {
    render(
      <DataQualityBanner
        payload={{ data_mode: "real", display_label: "Real Research Mode", eligible_for_real_research: true }}
      />,
    );
    expect(screen.getByText("Ready")).toBeTruthy();
  });
});

describe("SourcePill", () => {
  it("states unavailability instead of rendering nothing", () => {
    render(<SourcePill />);
    expect(screen.getByText("unavailable")).toBeTruthy();
  });
});
