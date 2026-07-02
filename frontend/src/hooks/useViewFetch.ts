import { useCallback, useRef, useState } from "react";

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
  const [isLoading, setIsLoading] = useState(false);
  const requestSeq = useRef(0);
  const lastTarget = useRef<string | null>(null);
  const optionsRef = useRef({ failureMessage, keepPayloadWhileLoading });
  optionsRef.current = { failureMessage, keepPayloadWhileLoading };

  const load = useCallback(async (target: string, request: () => Promise<T>, onSuccess?: (result: T) => void) => {
    const requestId = requestSeq.current + 1;
    requestSeq.current = requestId;
    lastTarget.current = target;
    setError("");
    if (!optionsRef.current.keepPayloadWhileLoading) setPayload(null);
    setIsLoading(true);
    try {
      const result = await request();
      if (requestId !== requestSeq.current) return;
      setPayload(result);
      onSuccess?.(result);
    } catch (err) {
      if (requestId !== requestSeq.current) return;
      setPayload(null);
      setError(err instanceof Error ? err.message : optionsRef.current.failureMessage);
    } finally {
      if (requestId === requestSeq.current) setIsLoading(false);
    }
  }, []);

  // True when the target matches the in-flight/last request. Views use this to
  // skip the identical second fetch that fires when load() updates the App
  // selection and the recomputed default target re-triggers their effect.
  const isCurrentTarget = useCallback((target: string) => lastTarget.current === target, []);

  return { payload, error, isLoading, load, isCurrentTarget };
}
