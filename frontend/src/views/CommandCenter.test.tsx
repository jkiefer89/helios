import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CommandCenter } from "./CommandCenter";

afterEach(cleanup);

describe("Command Center failure recovery", () => {
  it("offers a working retry when the command payload is unavailable", async () => {
    const onRetry = vi.fn().mockResolvedValue(undefined);
    render(
      <CommandCenter
        payload={null}
        dataStatus={null}
        onOpenInstrument={vi.fn()}
        onOpenModel={vi.fn()}
        onOpenView={vi.fn()}
        onRetry={onRetry}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Retry Command Center" }));
    await waitFor(() => expect(onRetry).toHaveBeenCalledTimes(1));
  });
});
