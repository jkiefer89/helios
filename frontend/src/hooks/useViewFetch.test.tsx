import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { RequestStatus } from "../components/states/RequestStatus";
import { useViewFetch } from "./useViewFetch";

function Harness({ request }: { request: () => Promise<{ value: string }> }) {
  const { payload, failure, staleResult, load, retry } = useViewFetch<{ value: string }>({
    failureMessage: "Research refresh failed.",
    keepPayloadWhileLoading: true,
  });
  return (
    <div>
      <button type="button" onClick={() => void load("target", request)}>Load</button>
      <span data-testid="payload">{payload?.value || "none"}</span>
      <span data-testid="stale">{String(staleResult)}</span>
      <RequestStatus failure={failure} stale={staleResult} onRetry={retry} />
    </div>
  );
}

function TargetHarness({ request }: { request: (target: string) => Promise<{ value: string }> }) {
  const { payload, failure, staleResult, load } = useViewFetch<{ value: string }>({
    failureMessage: "Target refresh failed.",
    keepPayloadWhileLoading: true,
  });
  return (
    <div>
      <button type="button" onClick={() => void load("A", () => request("A"))}>Load A</button>
      <button type="button" onClick={() => void load("B", () => request("B"))}>Load B</button>
      <span data-testid="payload">{payload?.value || "none"}</span>
      <span data-testid="stale">{String(staleResult)}</span>
      <RequestStatus failure={failure} stale={staleResult} />
    </div>
  );
}

afterEach(cleanup);

describe("useViewFetch", () => {
  it("retains the last good result, exposes diagnostics, and retries the same request", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce({ value: "current" })
      .mockRejectedValueOnce(new ApiError("Provider timed out.", 503, {
        code: "provider_temporarily_unavailable",
        next_step: "Retry after the provider recovers.",
        retryable: true,
        stale_result_preserved: true,
        diagnostics: { provider: "approved-primary" },
      }))
      .mockResolvedValueOnce({ value: "updated" });
    render(<Harness request={request} />);

    fireEvent.click(screen.getByRole("button", { name: "Load" }));
    await waitFor(() => expect(screen.getByTestId("payload").textContent).toBe("current"));

    fireEvent.click(screen.getByRole("button", { name: "Load" }));
    await screen.findByText("Refresh failed — last good result retained");
    expect(screen.getByTestId("payload").textContent).toBe("current");
    expect(screen.getByTestId("stale").textContent).toBe("true");
    expect(screen.getByText("Retry after the provider recovers.")).toBeTruthy();
    expect(screen.getByText(/provider_temporarily_unavailable/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByTestId("payload").textContent).toBe("updated"));
    expect(request).toHaveBeenCalledTimes(3);
  });

  it("never restores a different target's last-good payload", async () => {
    const request = vi.fn(async (target: string) => {
      if (target === "A") return { value: "analysis A" };
      throw new ApiError("Target B failed.", 503, { retryable: true });
    });
    render(<TargetHarness request={request} />);

    fireEvent.click(screen.getByRole("button", { name: "Load A" }));
    await waitFor(() => expect(screen.getByTestId("payload").textContent).toBe("analysis A"));

    fireEvent.click(screen.getByRole("button", { name: "Load B" }));
    await screen.findByText("Target B failed.");
    expect(screen.getByTestId("payload").textContent).toBe("none");
    expect(screen.getByTestId("stale").textContent).toBe("false");
  });
});
