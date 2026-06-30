import type {
  AIResponse,
  AIStatusResponse,
  AnalysisResponse,
  ClinicResponse,
  CommandCenterResponse,
  DataRefreshResponse,
  DataStatusResponse,
  MandateSummary,
  ModelSummary,
  ModelTemplate,
  ModelTemplateImportResponse,
  OpportunitiesResponse,
  ReportResponse,
  StrategyResponse,
  TickersResponse,
} from "./types";

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json().catch(() => null)
    : await response.text();

  if (!response.ok || (payload && typeof payload === "object" && "error" in payload)) {
    const message = payload && typeof payload === "object" && "error" in payload
      ? String(payload.error)
      : typeof payload === "string" && payload.trim()
        ? payload.trim()
        : `Request failed (${response.status})`;
    throw new Error(message);
  }
  return payload as T;
}

export const api = {
  commandCenter: () => request<CommandCenterResponse>("/api/command-center"),
  dataStatus: () => request<DataStatusResponse>("/api/data/status"),
  refreshData: (params: { symbol?: string; all?: boolean } = {}) =>
    request<DataRefreshResponse>("/api/data/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params.all ? { all: true } : { symbol: params.symbol || "all" }),
    }),
  tickers: () => request<TickersResponse>("/api/tickers"),
  mandates: () => request<{ mandates: MandateSummary[] }>("/api/mandates"),
  models: () => request<{ models: ModelSummary[] }>("/api/models"),
  modelLibrary: () => request<{ templates: ModelTemplate[] }>("/api/model-library"),
  importModelTemplate: (slug: string) =>
    request<ModelTemplateImportResponse>("/api/model-library/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug }),
    }),
  opportunities: (params: { kind: string; includeHold: boolean; minScore: number; limit?: number }) => {
    const query = new URLSearchParams({
      kind: params.kind,
      include_hold: params.includeHold ? "1" : "0",
      min_score: String(params.minScore),
      limit: String(params.limit ?? 50),
    });
    return request<OpportunitiesResponse>(`/api/opportunities?${query}`);
  },
  strategyInstrument: (symbol: string, costBps: number, slippageBps: number) =>
    request<StrategyResponse>(
      `/api/strategy/analyze?${new URLSearchParams({
        ticker: symbol,
        cost_bps: String(costBps),
        slippage_bps: String(slippageBps),
      })}`,
    ),
  strategyModel: (id: string, costBps: number, slippageBps: number) =>
    request<StrategyResponse>(
      `/api/model/strategy/analyze?${new URLSearchParams({
        id,
        cost_bps: String(costBps),
        slippage_bps: String(slippageBps),
      })}`,
    ),
  clinic: (id: string) => request<ClinicResponse>(`/api/model/clinic?id=${encodeURIComponent(id)}`),
  reportInstrument: (symbol: string) =>
    request<ReportResponse>(`/api/report/instrument?ticker=${encodeURIComponent(symbol)}`),
  reportModel: (id: string) => request<ReportResponse>(`/api/report/model?id=${encodeURIComponent(id)}`),
  analyzeInstrument: (symbol: string, horizon: number) =>
    request<AnalysisResponse>(`/api/analyze?ticker=${encodeURIComponent(symbol)}&horizon=${horizon}`),
  analyzeModel: (id: string, horizon: string | number) =>
    request<AnalysisResponse>(`/api/model/analyze?id=${encodeURIComponent(id)}&horizon=${encodeURIComponent(String(horizon))}`),
  uploadPrice: (file: File, symbol: string) => {
    const form = new FormData();
    form.append("file", file);
    if (symbol.trim()) form.append("symbol", symbol.trim());
    return request<{ symbol: string; name: string; rows: number }>("/api/upload", { method: "POST", body: form });
  },
  uploadModel: (file: File, name: string, mandate: string, context: string) => {
    const form = new FormData();
    form.append("file", file);
    if (name.trim()) form.append("name", name.trim());
    form.append("mandate", mandate);
    if (context.trim()) form.append("context", context.trim());
    return request<{ id: string; name: string; mandate: string; n_holdings: number }>("/api/model/upload", {
      method: "POST",
      body: form,
    });
  },
  fetchLive: (symbol: string) =>
    request<{ symbol: string; name: string; rows: number; headlines: number }>("/api/live", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol }),
    }),
  aiStatus: () => request<AIStatusResponse>("/api/ai/status"),
  aiOpportunityExplain: (payload: Record<string, unknown>, regenerate = false) =>
    aiPost("/api/ai/opportunity/explain", payload, undefined, regenerate),
  aiOpportunityCritique: (payload: Record<string, unknown>, regenerate = false) =>
    aiPost("/api/ai/opportunity/critique", payload, undefined, regenerate),
  aiStrategySummary: (payload: Record<string, unknown>, regenerate = false) =>
    aiPost("/api/ai/strategy/summary", payload, undefined, regenerate),
  aiClinicSummary: (payload: Record<string, unknown>, regenerate = false) =>
    aiPost("/api/ai/clinic/summary", payload, undefined, regenerate),
  aiReport: (payload: Record<string, unknown>, regenerate = false) =>
    aiPost("/api/ai/report", payload, undefined, regenerate),
  aiQuestion: (payload: Record<string, unknown>, question: string, regenerate = false) =>
    aiPost("/api/ai/question", payload, question, regenerate),
};

function aiPost(url: string, payload: Record<string, unknown>, question?: string, regenerate = false) {
  return request<AIResponse>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ payload, question, regenerate }),
  });
}
