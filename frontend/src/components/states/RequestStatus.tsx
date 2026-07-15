import { ApiError } from "../../api/client";

export interface RequestFailureState {
  message: string;
  status: number | null;
  code: string;
  nextStep: string;
  retryable: boolean;
  diagnostics: Record<string, unknown>;
  staleResultPreserved: boolean;
}

export function toRequestFailure(error: unknown, fallback: string): RequestFailureState {
  if (error instanceof ApiError) {
    const nextStep = typeof error.details.next_step === "string" ? error.details.next_step : "";
    const diagnostics = error.details.diagnostics && typeof error.details.diagnostics === "object"
      ? error.details.diagnostics as Record<string, unknown>
      : {};
    return {
      message: error.message || fallback,
      status: error.status,
      code: error.code,
      nextStep,
      retryable: error.details.retryable !== false,
      diagnostics,
      staleResultPreserved: error.details.stale_result_preserved === true,
    };
  }
  return {
    message: error instanceof Error ? error.message : fallback,
    status: null,
    code: "request_failed",
    nextStep: "Review the connection and retry the request.",
    retryable: true,
    diagnostics: {},
    staleResultPreserved: false,
  };
}

export function RequestStatus({
  failure,
  stale,
  onRetry,
}: {
  failure: RequestFailureState | null;
  stale?: boolean;
  onRetry?: () => void;
}) {
  if (!failure) return null;
  const hasDiagnostics = Object.keys(failure.diagnostics).length > 0;
  return (
    <div className="request-failure" role="alert">
      <div>
        <strong>{stale ? "Refresh failed — last good result retained" : "Request failed"}</strong>
        <span>{failure.message}</span>
        {failure.nextStep && <small>{failure.nextStep}</small>}
      </div>
      <div className="request-failure__actions">
        {failure.retryable && onRetry && <button type="button" onClick={onRetry}>Retry</button>}
        <code>{failure.code}{failure.status ? ` · ${failure.status}` : ""}</code>
      </div>
      {hasDiagnostics && <details><summary>Diagnostics</summary><pre>{JSON.stringify(failure.diagnostics, null, 2)}</pre></details>}
    </div>
  );
}
