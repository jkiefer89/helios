import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import { DataIntakePanel } from "./DataIntakePanel";

afterEach(cleanup);

describe("DataIntakePanel", () => {
  it("renders the full workspace and retries a structured live-data failure", async () => {
    const onFetchLive = vi.fn()
      .mockRejectedValueOnce(new ApiError("Provider rate limit reached.", 503, {
        code: "provider_temporarily_unavailable",
        next_step: "Retry after the provider recovers; the prior history remains available.",
        retryable: true,
        stale_result_preserved: true,
        diagnostics: { symbol: "SPY" },
      }))
      .mockResolvedValueOnce(undefined);
    const { container } = render(<DataIntakePanel
      mandates={[{ key: "balanced", label: "Balanced", target_vol_pct: 12 }]}
      tickers={[]}
      dataStatus={null}
      liveAvailable
      onUploadPrice={vi.fn()}
      onUploadModel={vi.fn()}
      onFetchLive={onFetchLive}
      onRefreshData={vi.fn()}
      fullPage
    />);

    expect(container.querySelector(".data-intake-panel--full")).toBeTruthy();
    expect(container.querySelectorAll(".data-intake-grid form")).toHaveLength(3);

    fireEvent.change(screen.getByLabelText("Live ticker symbol"), { target: { value: "SPY" } });
    fireEvent.click(screen.getByRole("button", { name: "Fetch" }));
    await screen.findByText(/provider_temporarily_unavailable/);
    expect(screen.getByText(/prior history remains available/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(onFetchLive).toHaveBeenCalledTimes(2));
    await screen.findByText("Live history fetched and persisted. Review readiness below.");
  });
});
