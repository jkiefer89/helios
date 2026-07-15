import { useCallback, useRef, useState } from "react";
import { toRequestFailure, type RequestFailureState } from "../components/states/RequestStatus";

interface ViewFetchOptions<T> {
  /** Fallback message when a request throws without a usable message. */
  failureMessage: string;
  /** Keep the previous payload visible while a new request is in flight. */
  keepPayloadWhileLoading?: boolean;
  initialPayload?: T | null;
}

/**
 * Shared request lifecycle for payload views: sequences concurrent requests so
 * only the latest one lands, tracks error/loading state, and remembers the
 * last requested target so selection-sync re-renders do not refetch it.
 */
export function useViewFetch<T>({ failureMessage, keepPayloadWhileLoading = false, initialPayload = null }: ViewFetchOptions<T>) {
  const [payload, setPayload] = useState<T | null>(initialPayload);
  const [error, setError] = useState("");
  const [failure, setFailure] = useState<RequestFailureState | null>(null);
  const [lastSuccessAt, setLastSuccessAt] = useState<string>(initialPayload ? new Date().toISOString() : "");
  const [isLoading, setIsLoading] = useState(false);
  const requestSeq = useRef(0);
  const lastTarget = useRef<string | null>(null);
  const payloadTarget = useRef<string | null>(null);
  const lastGoodByTarget = useRef(new Map<string, T>());
  const initialPayloadRef = useRef<T | null>(initialPayload);
  const lastRequest = useRef<{ target: string; request: () => Promise<T>; onSuccess?: (result: T) => void } | null>(null);
  const optionsRef = useRef({ failureMessage, keepPayloadWhileLoading });
  optionsRef.current = { failureMessage, keepPayloadWhileLoading };

  const load = useCallback(async (target: string, request: () => Promise<T>, onSuccess?: (result: T) => void) => {
    if (lastGoodByTarget.current.size === 0 && initialPayloadRef.current !== null) {
      lastGoodByTarget.current.set(target, initialPayloadRef.current);
      payloadTarget.current = target;
      initialPayloadRef.current = null;
    }
    const requestId = requestSeq.current + 1;
    requestSeq.current = requestId;
    lastTarget.current = target;
    lastRequest.current = { target, request, onSuccess };
    setError("");
    setFailure(null);
    if (!optionsRef.current.keepPayloadWhileLoading || (payloadTarget.current && payloadTarget.current !== target)) {
      setPayload(null);
      payloadTarget.current = null;
    }
    setIsLoading(true);
    try {
      const result = await request();
      if (requestId !== requestSeq.current) return;
      setPayload(result);
      payloadTarget.current = target;
      lastGoodByTarget.current.set(target, result);
      setLastSuccessAt(new Date().toISOString());
      onSuccess?.(result);
    } catch (err) {
      if (requestId !== requestSeq.current) return;
      // keepPayloadWhileLoading views keep their last good data on a FAILED
      // refresh too: nulling it made a transient network error render as the
      // data-provenance "Locked" gate — a misdiagnosis (review finding). The
      // role=alert error banner still reports the failure.
      const normalized = toRequestFailure(err, optionsRef.current.failureMessage);
      const lastGood = lastGoodByTarget.current.get(target);
      if (lastGood !== undefined) {
        setPayload(lastGood);
        payloadTarget.current = target;
      } else {
        setPayload(null);
        payloadTarget.current = null;
      }
      setError(normalized.message);
      setFailure(normalized);
    } finally {
      if (requestId === requestSeq.current) setIsLoading(false);
    }
  }, []);

  // True when the target matches the in-flight/last request. Views use this to
  // skip the identical second fetch that fires when load() updates the App
  // selection and the recomputed default target re-triggers their effect.
  const isCurrentTarget = useCallback((target: string) => lastTarget.current === target, []);
  const retry = useCallback(() => {
    const previous = lastRequest.current;
    if (!previous) return;
    void load(previous.target, previous.request, previous.onSuccess);
  }, [load]);

  return {
    payload, error, failure, isLoading, load, retry, isCurrentTarget,
    lastSuccessAt,
    staleResult: Boolean(payload && failure),
  };
}
