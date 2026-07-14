import type {
  AIChatResponse,
  AIResponse,
  AIStatusResponse,
  DecisionEntry,
  DecisionsResponse,
  AnalysisResponse,
  ClinicResponse,
  CommandCenterResponse,
  DataRefreshResponse,
  DataQualityResponse,
  DataStatusResponse,
  EvidenceLabResponse,
  MandateSummary,
  ModelEditHoldingInput,
  ModelEditPreviewResponse,
  ModelEditSaveResponse,
  ModelGovernanceCommitteeIdentity,
  ModelGovernanceApprovalPacket,
  ModelSummary,
  ModelGovernanceResponse,
  ModelTemplate,
  ModelTemplateImportResponse,
  ModelValidationResponse,
  OpportunitiesResponse,
  ReportSnapshotHistoryResponse,
  ReportSnapshotSaveRequest,
  ReportSnapshotSaveResponse,
  ReportResponse,
  RiskAnalyticsResponse,
  SignalJournalResponse,
  SignalRecordResponse,
  StrategyResponse,
  TickersResponse,
  DataJobsResponse,
  LedgerAccount,
  RebalanceProposal,
  LedgerPerformanceResponse,
  ProspectiveTrial,
  ProspectiveTrialAssessment,
  ProspectiveTrialsResponse,
  TrialProtocol,
  IndependentValidationResponse,
  OperationsStatusResponse,
  ProvidersResponse,
  SecurityStatusResponse,
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
  dataQuality: () => request<DataQualityResponse>("/api/data-quality"),
  syncDataQuality: () => request<DataQualityResponse>("/api/data-quality/sync", { method: "POST" }),
  refreshData: (params: { symbol?: string; all?: boolean } = {}) =>
    request<DataRefreshResponse>("/api/data/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params.all ? { all: true } : { symbol: params.symbol || "all" }),
    }),
  tickers: () => request<TickersResponse>("/api/tickers"),
  mandates: () => request<{ mandates: MandateSummary[] }>("/api/mandates"),
  models: () => request<{ models: ModelSummary[] }>("/api/models"),
  modelGovernance: () => request<ModelGovernanceResponse>("/api/model-governance"),
  modelApprovalPacket: (id: string) =>
    request<{ packet: ModelGovernanceApprovalPacket }>(`/api/model-governance/${encodeURIComponent(id)}/approval-packet`),
  modelValidation: () => request<ModelValidationResponse>("/api/model-validation"),
  recordModelGovernanceEvent: (
    id: string,
    payload: {
      actor?: string;
      action?: string;
      note?: string;
      approval_status?: string;
      committee_identity?: Partial<ModelGovernanceCommitteeIdentity> & { secret?: string };
    },
  ) =>
    request<{ event: ModelGovernanceResponse["change_log"][number] & { snapshot?: Record<string, unknown> } }>(
      `/api/model-governance/${encodeURIComponent(id)}/events`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    ),
  previewModelEdit: (
    id: string,
    payload: { holdings: ModelEditHoldingInput[]; rebalance_to_target?: boolean },
  ) =>
    request<ModelEditPreviewResponse>(`/api/models/${encodeURIComponent(id)}/editor/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  saveModelEdit: (
    id: string,
    payload: { holdings: ModelEditHoldingInput[]; change_note: string; actor?: string; rebalance_to_target?: boolean },
  ) =>
    request<ModelEditSaveResponse>(`/api/models/${encodeURIComponent(id)}/editor`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
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
  modelRisk: (id: string, aumUsd?: number) => request<RiskAnalyticsResponse>(
    `/api/model/risk?id=${encodeURIComponent(id)}${aumUsd ? `&aum=${encodeURIComponent(String(aumUsd))}` : ""}`),
  reportInstrument: (symbol: string) =>
    request<ReportResponse>(`/api/report/instrument?ticker=${encodeURIComponent(symbol)}`),
  reportModel: (id: string) => request<ReportResponse>(`/api/report/model?id=${encodeURIComponent(id)}`),
  reportSnapshots: () => request<ReportSnapshotHistoryResponse>("/api/report/snapshots"),
  saveReportSnapshot: (payload: ReportSnapshotSaveRequest) =>
    request<ReportSnapshotSaveResponse>("/api/report/snapshots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  signalJournal: () => request<SignalJournalResponse>("/api/signal-journal"),
  evidenceLab: (params: { kind: "instrument" | "model"; id: string; horizon?: number; trainWindow?: number; step?: number }) => {
    const query = new URLSearchParams({
      kind: params.kind,
      id: params.id,
      horizon: String(params.horizon ?? 21),
      train_window: String(params.trainWindow ?? 252),
      step: String(params.step ?? 21),
    });
    return request<EvidenceLabResponse>(`/api/evidence-lab?${query}`);
  },
  trials: (params: { kind?: "instrument" | "model"; id?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.kind) query.set("target_kind", params.kind);
    if (params.id) query.set("target_id", params.id);
    return request<ProspectiveTrialsResponse>(`/api/trials${query.size ? `?${query}` : ""}`);
  },
  registerTrial: (body: {
    target_kind: "instrument" | "model";
    target_id: string;
    protocol: TrialProtocol;
    actor: string;
  }) => request<{ trial: ProspectiveTrial; disclaimer: string }>("/api/trials", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }),
  assessTrial: (trialId: string) =>
    request<{ trial_id: string; assessment: ProspectiveTrialAssessment; disclaimer: string }>(
      `/api/trials/${encodeURIComponent(trialId)}/assess`, { method: "POST" },
    ),
  closeTrial: (trialId: string, body: { status: "completed" | "withdrawn" | "invalidated"; note: string; actor: string }) =>
    request<{ trial: ProspectiveTrial; disclaimer: string }>(
      `/api/trials/${encodeURIComponent(trialId)}/close`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    ),
  analyzeInstrument: (symbol: string, horizon: number) =>
    request<AnalysisResponse>(`/api/analyze?ticker=${encodeURIComponent(symbol)}&horizon=${horizon}`),
  analyzeModel: (id: string, horizon: string | number) =>
    request<AnalysisResponse>(`/api/model/analyze?id=${encodeURIComponent(id)}&horizon=${encodeURIComponent(String(horizon))}`),
  recordInstrumentSignal: (ticker: string, horizon: number) =>
    request<SignalRecordResponse>("/api/signals/record", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker, horizon }),
    }),
  recordModelSignal: (id: string, horizon: number) =>
    request<SignalRecordResponse>("/api/model/signals/record", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, horizon }),
    }),
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
  aiChat: (messages: Array<{ role: "user" | "assistant"; content: string }>, payload: Record<string, unknown>) =>
    request<AIChatResponse>("/api/ai/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages, payload }),
    }),
  macro: () => request<Record<string, unknown>>("/api/macro"),
  aiMacroBrief: (payload: Record<string, unknown>, regenerate = false) =>
    aiPost("/api/ai/macro/brief", payload, undefined, regenerate),
  listDecisions: (limit = 200) => request<DecisionsResponse>(`/api/decisions?limit=${limit}`),
  setModelThesis: (body: { id: string; thesis: string; thesis_params?: Record<string, number> }) =>
    request<{ id: string; thesis: string; thesis_params: Record<string, number> }>("/api/model/thesis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  ledgerAccounts: () => request<{ accounts: LedgerAccount[] }>("/api/ledger/accounts"),
  ledgerFlexImport: () =>
    request<Record<string, unknown>>("/api/ledger/flex/import", { method: "POST" }),
  rebalancePropose: (body: { account_id: string; model_id: string; constraints?: Record<string, number> }) =>
    request<RebalanceProposal>("/api/rebalance/propose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  dataJobs: () => request<DataJobsResponse>("/api/data/jobs"),
  providers: () => request<ProvidersResponse>("/api/providers"),
  reconcilePriceSources: (symbol: string, primaryProvider: string, backupProvider: string, period = "3mo") =>
    request<Record<string, unknown>>(
      `/api/data/price-reconciliation?ticker=${encodeURIComponent(symbol)}&primary=${encodeURIComponent(primaryProvider)}&backup=${encodeURIComponent(backupProvider)}&period=${encodeURIComponent(period)}`,
      { method: "POST" },
    ),
  recordProviderReconciliation: (payload: {
    data_domain: string;
    primary_provider: string;
    backup_provider: string;
    symbols: string[];
    period?: string;
    note: string;
  }) => request<{ reconciliation: import("./types").ProviderReconciliation }>("/api/providers/reconciliations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }),
  approveProviderCutover: (payload: {
    data_domain: string;
    primary_provider: string;
    backup_provider: string;
    reconciliation_id: string;
    note: string;
  }) => request<{ cutover: import("./types").ProviderCutover; registry: ProvidersResponse }>("/api/providers/cutovers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }),
  operationsStatus: () => request<OperationsStatusResponse>("/api/operations/status"),
  syncOperationalIncidents: () => request<{ incident_sync: Record<string, unknown> }>("/api/operations/incidents/sync", { method: "POST" }),
  updateOperationalIncident: (incidentId: string, payload: { status: string; owner: string; note: string }) =>
    request<{ incident: import("./types").OperationalIncident }>(`/api/operations/incidents/${encodeURIComponent(incidentId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  verifyBackup: () => request<{ verification: Record<string, unknown> }>("/api/operations/backup/verify", { method: "POST" }),
  securityStatus: () => request<SecurityStatusResponse>("/api/security/status"),
  createSession: () => request<{ session: Record<string, unknown> }>("/api/auth/session", { method: "POST" }),
  deleteSession: () => request<{ revoked: boolean }>("/api/auth/session", { method: "DELETE" }),
  independentValidation: (modelId: string) =>
    request<IndependentValidationResponse>(`/api/models/${encodeURIComponent(modelId)}/independent-validation`),
  recordIndependentValidation: (
    modelId: string,
    payload: {
      model_version?: number;
      sponsor: string;
      outcome: string;
      controls: Record<string, string>;
      findings?: string[];
      next_review_due?: string;
      note?: string;
    },
  ) => request<{ review: IndependentValidationResponse["reviews"][number]; status: IndependentValidationResponse["status"] }>(
    `/api/models/${encodeURIComponent(modelId)}/independent-validation`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
  ),
  recordValidationException: (
    modelId: string,
    payload: {
      model_version?: number;
      control_key: string;
      owner: string;
      reason: string;
      compensating_controls: string[];
      expires_at: string;
    },
  ) => request<{ exception: IndependentValidationResponse["exceptions"][number]; status: IndependentValidationResponse["status"] }>(
    `/api/models/${encodeURIComponent(modelId)}/validation-exceptions`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
  ),
  resolveValidationException: (modelId: string, exceptionId: string, note: string) =>
    request<{ exception: IndependentValidationResponse["exceptions"][number]; status: IndependentValidationResponse["status"] }>(
      `/api/models/${encodeURIComponent(modelId)}/validation-exceptions/${encodeURIComponent(exceptionId)}`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "resolved", note }) },
    ),
  ledgerPerformance: (account: string) =>
    request<LedgerPerformanceResponse>(`/api/ledger/performance?account=${encodeURIComponent(account)}`),
  ledgerMapAccount: (body: { account_id: string; model_id: string; display_name?: string }) =>
    request<{ accounts: LedgerAccount[] }>("/api/ledger/account/map", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  ledgerUpload: (kind: "fills" | "positions", file: File, accountId: string, asOf?: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("account_id", accountId);
    if (asOf) form.append("as_of", asOf);
    return request<Record<string, unknown>>(`/api/ledger/${kind}/upload`, { method: "POST", body: form });
  },
  recordDecision: (body: {
    target_kind: string;
    target_id: string;
    my_action: string;
    rationale?: string;
    signal?: Record<string, unknown>;
    mandate?: string;
    context?: Record<string, unknown>;
  }) =>
    request<{ decision: DecisionEntry; disclaimer?: string }>("/api/decisions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
};

function aiPost(url: string, payload: Record<string, unknown>, question?: string, regenerate = false) {
  return request<AIResponse>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ payload, question, regenerate }),
  });
}
