import { afterEach, describe, expect, test, vi } from "vitest";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

function protection(token: string, binding: string) {
  return {
    request_protection: {
      header: "X-Helios-Request-Token",
      token,
      expires_at: Math.floor(Date.now() / 1000) + 1800,
      expires_in_seconds: 1800,
      available: true,
      binding,
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("unsafe request protection", () => {
  test("protects session bootstrap and refreshes the token after the session starts", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(protection("bootstrap-token", "session_bootstrap")))
      .mockResolvedValueOnce(jsonResponse({ session: { user: "advisor" } }))
      .mockResolvedValueOnce(jsonResponse(protection("session-token", "session")))
      .mockResolvedValueOnce(jsonResponse({
        requested: "SPY", refreshed: 1, failed: 0, skipped: 0,
        results: [], warnings: [], data_status: {},
      }));
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("./client");

    await api.createSession();
    await api.refreshData({ symbol: "SPY" });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/security/status");
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get("X-Helios-Request-Token"))
      .toBe("bootstrap-token");
    expect(fetchMock.mock.calls[2][0]).toBe("/api/security/status");
    expect(new Headers(fetchMock.mock.calls[3][1]?.headers).get("X-Helios-Request-Token"))
      .toBe("session-token");
  });

  test("re-fetches and retries once when a request token is rejected", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(protection("expired-token", "session")))
      .mockResolvedValueOnce(jsonResponse({
        error: "A valid unsafe-request token is required.",
        code: "request_token_invalid",
      }, 403))
      .mockResolvedValueOnce(jsonResponse(protection("replacement-token", "session")))
      .mockResolvedValueOnce(jsonResponse({
        requested: "SPY", refreshed: 1, failed: 0, skipped: 0,
        results: [], warnings: [], data_status: {},
      }));
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("./client");

    const result = await api.refreshData({ symbol: "SPY" });

    expect(result.refreshed).toBe(1);
    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get("X-Helios-Request-Token"))
      .toBe("expired-token");
    expect(new Headers(fetchMock.mock.calls[3][1]?.headers).get("X-Helios-Request-Token"))
      .toBe("replacement-token");
  });
});
