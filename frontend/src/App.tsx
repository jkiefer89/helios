import { useEffect, useMemo, useState } from "react";
import { ApiError, api } from "./api/client";
import type {
  CommandCenterResponse,
  DataRefreshResponse,
  DataStatusResponse,
  MandateSummary,
  ModelGovernanceResponse,
  ModelSummary,
  ModelTemplate,
  OpportunitiesResponse,
  TickerSummary,
} from "./api/types";
import { AppShell, isViewId, type ViewId } from "./components/layout/AppShell";
import { CommandCenter } from "./views/CommandCenter";
import { OpportunityRadar } from "./views/OpportunityRadar";
import { StrategyLab } from "./views/StrategyLab";
import { PortfolioClinic } from "./views/PortfolioClinic";
import { Reports } from "./views/Reports";
import { Analysis } from "./views/Analysis";
import { Instruments } from "./views/Instruments";
import { Models } from "./views/Models";
import { DataQuality } from "./views/DataQuality";
import { SignalJournal } from "./views/SignalJournal";
import { Decisions } from "./views/Decisions";
import { RiskAnalytics } from "./views/RiskAnalytics";
import { EvidenceLab } from "./views/EvidenceLab";
import { DataSetup } from "./views/DataSetup";
import { toRequestFailure, type RequestFailureState } from "./components/states/RequestStatus";

interface HashRoute {
  view: ViewId;
  kind?: "instrument" | "model";
  id?: string;
}

export function refreshIncompleteError(result: DataRefreshResponse): ApiError | null {
  const failures = result.results.filter((row) => row.status === "error" || row.status === "skipped");
  if (!failures.length) return null;
  const nextStep = [...new Set(failures.map((row) => row.next_step).filter(Boolean))].join(" ");
  return new ApiError(
    `${failures.length} live refresh ${failures.length === 1 ? "request did" : "requests did"} not complete.`,
    207,
    {
      code: "live_refresh_incomplete",
      retryable: failures.some((row) => row.retryable !== false),
      next_step: nextStep,
      stale_result_preserved: failures.every((row) => row.stale_result_preserved === true),
      diagnostics: {
        refreshed: result.refreshed,
        failed: result.failed,
        skipped: result.skipped,
        reason_codes: failures.map((row) => row.reason_code || row.status),
      },
    },
  );
}

function decodeHashPart(part: string): string | null {
  try {
    return decodeURIComponent(part);
  } catch {
    return null;
  }
}

// Minimal hash routing (#/view or #/view/instrument|model/id) without a router library.
export function parseHash(hash: string): HashRoute | null {
  const decoded = hash.replace(/^#\/?/, "").split("/").map(decodeHashPart);
  if (decoded.some((part) => part === null)) return null;
  const [view, kind, id] = decoded as string[];
  if (!view || !isViewId(view)) return null;
  if ((kind === "instrument" || kind === "model") && id) return { view, kind, id };
  return { view };
}

export function buildHash(view: ViewId, selectedInstrument: string, selectedModel: string): string {
  if (selectedModel) return `#/${view}/model/${encodeURIComponent(selectedModel)}`;
  if (selectedInstrument) return `#/${view}/instrument/${encodeURIComponent(selectedInstrument)}`;
  return `#/${view}`;
}

export default function App() {
  const [activeView, setActiveView] = useState<ViewId>(() => parseHash(window.location.hash)?.view || "command");
  const [tickers, setTickers] = useState<TickerSummary[]>([]);
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [modelGovernance, setModelGovernance] = useState<ModelGovernanceResponse | null>(null);
  const [modelLibrary, setModelLibrary] = useState<ModelTemplate[]>([]);
  const [mandates, setMandates] = useState<MandateSummary[]>([]);
  const [liveAvailable, setLiveAvailable] = useState(false);
  const [selectedInstrument, setSelectedInstrument] = useState<string>(() => {
    const route = parseHash(window.location.hash);
    return route?.kind === "instrument" ? route.id || "" : "";
  });
  const [selectedModel, setSelectedModel] = useState<string>(() => {
    const route = parseHash(window.location.hash);
    return route?.kind === "model" ? route.id || "" : "";
  });
  const [command, setCommand] = useState<CommandCenterResponse | null>(null);
  const [dataStatus, setDataStatus] = useState<DataStatusResponse | null>(null);
  const [opportunities, setOpportunities] = useState<OpportunitiesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string>("");
  const [setupNotice, setSetupNotice] = useState<string>("");
  const [commandFailure, setCommandFailure] = useState<RequestFailureState | null>(null);

  const refreshLists = async () => {
    const requests = [
      ["instruments", api.tickers(), (value: TickerSummary[] | unknown) => {
        const payload = value as Awaited<ReturnType<typeof api.tickers>>;
        setTickers(payload.tickers);
        setLiveAvailable(payload.live_available);
      }],
      ["models", api.models(), (value: unknown) => setModels((value as Awaited<ReturnType<typeof api.models>>).models)],
      ["model governance", api.modelGovernance(), (value: unknown) => setModelGovernance(value as ModelGovernanceResponse)],
      ["model library", api.modelLibrary(), (value: unknown) => setModelLibrary((value as Awaited<ReturnType<typeof api.modelLibrary>>).templates)],
      ["mandates", api.mandates(), (value: unknown) => setMandates((value as Awaited<ReturnType<typeof api.mandates>>).mandates)],
      ["data status", api.dataStatus(), (value: unknown) => setDataStatus(value as DataStatusResponse)],
    ] as const;
    const results = await Promise.allSettled(requests.map(([, request]) => request));
    const failures: string[] = [];
    results.forEach((result, index) => {
      if (result.status === "fulfilled") requests[index][2](result.value);
      else failures.push(requests[index][0]);
    });
    setSetupNotice(
      failures.length ? `Some setup panels could not refresh: ${failures.join(", ")}.` : "",
    );
    // No automatic first-ticker/model selection (review finding): analysis
    // context is always something the operator chose — via the sidebar, the
    // search palette, or a deep link — never a silent default.
  };

  const refreshCommand = async () => {
    try {
      const payload = await api.commandCenter();
      setCommand(payload);
      setCommandFailure(null);
    } catch (error) {
      setCommandFailure(toRequestFailure(error, "Command Center could not refresh."));
    }
  };

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([refreshLists(), refreshCommand()])
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Restore view + selection on back/forward and hand-edited hashes.
  useEffect(() => {
    const applyHash = () => {
      const route = parseHash(window.location.hash);
      if (!route) return;
      setActiveView(route.view);
      if (route.kind === "instrument" && route.id) {
        setSelectedInstrument(route.id);
        setSelectedModel("");
      } else if (route.kind === "model" && route.id) {
        setSelectedModel(route.id);
        setSelectedInstrument("");
      }
    };
    window.addEventListener("hashchange", applyHash);
    return () => window.removeEventListener("hashchange", applyHash);
  }, []);

  // Keep the hash in sync with the active view and selection.
  useEffect(() => {
    const nextHash = buildHash(activeView, selectedInstrument, selectedModel);
    if (window.location.hash === nextHash) return;
    if (!window.location.hash) window.history.replaceState(null, "", nextHash);
    else window.location.hash = nextHash;
  }, [activeView, selectedInstrument, selectedModel]);

  const dataMode = useMemo(() => ({
    mode: command?.data_mode,
    label: command?.display_label,
  }), [command]);

  const selectInstrumentOnly = (symbol: string) => {
    setSelectedInstrument(symbol);
    setSelectedModel("");
  };

  const selectModelOnly = (id: string) => {
    setSelectedModel(id);
    setSelectedInstrument("");
  };

  const onUploadPrice = async (file: File, symbol: string) => {
    try {
      const result = await api.uploadPrice(file, symbol);
      setNotice(`Uploaded ${result.symbol} with ${result.rows} rows.`);
      selectInstrumentOnly(result.symbol);
      setActiveView("setup");
      await Promise.all([refreshLists(), refreshCommand()]);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Price upload failed.");
      throw error;
    }
  };

  const onUploadModel = async (file: File, name: string, mandate: string, context: string) => {
    try {
      const result = await api.uploadModel(file, name, mandate, context);
      setNotice(`Imported ${result.name} with ${result.n_holdings} holdings.`);
      selectModelOnly(result.id);
      setActiveView("models");
      await Promise.all([refreshLists(), refreshCommand()]);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Model upload failed.");
      throw error;
    }
  };

  const onImportTemplate = async (slug: string) => {
    try {
      const result = await api.importModelTemplate(slug);
      setNotice(`Imported ${result.name} with ${result.n_holdings} governed holdings.`);
      selectModelOnly(result.id);
      setActiveView("models");
      await Promise.all([refreshLists(), refreshCommand()]);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Model template import failed.");
      throw error;
    }
  };

  const onRecordModelGovernance = async (
    id: string,
    payload: {
      actor?: string;
      action?: string;
      note?: string;
      approval_status?: string;
      committee_identity?: {
        signer_name?: string;
        signer_role?: string;
        committee?: string;
        secret?: string;
      };
    },
  ) => {
    try {
      const result = await api.recordModelGovernanceEvent(id, payload);
      setNotice(`Recorded model governance event v${result.event.version}.`);
      await refreshLists();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Model governance update failed.");
      throw error;
    }
  };

  const onModelEdited = async () => {
    await Promise.all([refreshLists(), refreshCommand()]);
    setNotice("Model edited and governance snapshot recorded.");
  };

  const onFetchLive = async (symbol: string) => {
    try {
      const result = await api.fetchLive(symbol);
      setNotice(`Fetched ${result.symbol} with ${result.rows} rows.`);
      selectInstrumentOnly(result.symbol);
      setActiveView("setup");
      await Promise.all([refreshLists(), refreshCommand()]);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Live data fetch failed.");
      throw error;
    }
  };

  const onRefreshData = async (symbol?: string, all = false) => {
    try {
      const result = await api.refreshData({ symbol, all });
      setDataStatus(result.data_status);
      const target = all ? "live symbols" : result.requested;
      const failures = result.results.filter((row) => row.status === "error" || row.status === "skipped");
      const remediation = failures.slice(0, 2).map((row) =>
        `${row.symbol || "request"}: ${row.reason_code || row.status}${row.next_step ? ` — ${row.next_step}` : ""}`,
      ).join(" ");
      setNotice(`Refresh checked ${target}: ${result.refreshed} refreshed, ${result.failed} failed, ${result.skipped} skipped.${remediation ? ` ${remediation}` : ""}`);
      await Promise.all([refreshLists(), refreshCommand()]);
      const incomplete = refreshIncompleteError(result);
      if (incomplete) throw incomplete;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Data refresh failed.");
      throw error;
    }
  };

  const openInstrument = (symbol: string) => {
    selectInstrumentOnly(symbol);
    setActiveView("analysis");
  };

  const openModel = (id: string) => {
    selectModelOnly(id);
    setActiveView("analysis");
  };

  const content = (() => {
    if (loading) return <div className="loading">Loading Helios Pro APIs...</div>;
    if (activeView === "command") {
      return <CommandCenter payload={command} failure={commandFailure} dataStatus={dataStatus} onOpenInstrument={openInstrument} onOpenModel={openModel} onOpenView={setActiveView} onRetry={refreshCommand} />;
    }
    if (activeView === "instruments") {
      return (
        <Instruments
          tickers={tickers}
          models={models}
          dataStatus={dataStatus}
          onOpenInstrument={openInstrument}
          onRefreshData={onRefreshData}
        />
      );
    }
    if (activeView === "setup") {
      return (
        <DataSetup
          tickers={tickers}
          models={models}
          mandates={mandates}
          dataStatus={dataStatus}
          liveAvailable={liveAvailable}
          onUploadPrice={onUploadPrice}
          onUploadModel={onUploadModel}
          onFetchLive={onFetchLive}
          onRefreshData={onRefreshData}
          onOpenInstrument={openInstrument}
        />
      );
    }
    if (activeView === "models") {
      return (
        <Models
          models={models}
          governance={modelGovernance}
          templates={modelLibrary}
          onImportTemplate={onImportTemplate}
          onRecordGovernance={onRecordModelGovernance}
          onModelEdited={onModelEdited}
          onOpenModel={openModel}
          onOpenClinic={(id) => { selectModelOnly(id); setActiveView("clinic"); }}
        />
      );
    }
    if (activeView === "opportunities") {
      return (
        <OpportunityRadar
          initialPayload={opportunities}
          onPayload={setOpportunities}
          onOpenInstrument={openInstrument}
          onOpenModel={openModel}
        />
      );
    }
    if (activeView === "strategy") {
      return (
        <StrategyLab
          tickers={tickers}
          models={models}
          mandates={mandates}
          selectedInstrument={selectedInstrument}
          selectedModel={selectedModel}
          onSelectInstrument={selectInstrumentOnly}
          onSelectModel={selectModelOnly}
        />
      );
    }
    if (activeView === "clinic") {
      return <PortfolioClinic models={models} selectedModel={selectedModel} onSelectModel={selectModelOnly} />;
    }
    if (activeView === "risk") {
      return <RiskAnalytics models={models} selectedModel={selectedModel} onSelectModel={selectModelOnly} />;
    }
    if (activeView === "evidence") {
      return (
        <EvidenceLab
          tickers={tickers}
          models={models}
          selectedInstrument={selectedInstrument}
          selectedModel={selectedModel}
          onSelectInstrument={selectInstrumentOnly}
          onSelectModel={selectModelOnly}
        />
      );
    }
    if (activeView === "reports") {
      return (
        <Reports
          tickers={tickers}
          models={models}
          selectedInstrument={selectedInstrument}
          selectedModel={selectedModel}
          onSelectInstrument={selectInstrumentOnly}
          onSelectModel={selectModelOnly}
          dataStatus={dataStatus}
        />
      );
    }
    if (activeView === "data-quality") {
      return <DataQuality />;
    }
    if (activeView === "journal") {
      return <SignalJournal />;
    }
    if (activeView === "decisions") {
      return <Decisions />;
    }
    return (
      <Analysis
        tickers={tickers}
        models={models}
        mandates={mandates}
        selectedInstrument={selectedInstrument}
        selectedModel={selectedModel}
        onSelectInstrument={selectInstrumentOnly}
        onSelectModel={selectModelOnly}
      />
    );
  })();

  return (
    <AppShell
      activeView={activeView}
      onViewChange={setActiveView}
      dataMode={dataMode}
      tickers={tickers}
      models={models}
      mandates={mandates}
      selectedInstrument={selectedInstrument}
      selectedModel={selectedModel}
      onSelectInstrument={openInstrument}
      onSelectModel={openModel}
      onUploadPrice={onUploadPrice}
      onUploadModel={onUploadModel}
      onFetchLive={onFetchLive}
      liveAvailable={liveAvailable}
      notice={[notice, setupNotice].filter(Boolean).join(" ")}
      dataStatus={dataStatus}
      onRefreshData={onRefreshData}
    >
      {content}
    </AppShell>
  );
}
