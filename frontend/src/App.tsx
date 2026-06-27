import { useEffect, useMemo, useState } from "react";
import { api } from "./api/client";
import type {
  CommandCenterResponse,
  MandateSummary,
  ModelSummary,
  OpportunitiesResponse,
  TickerSummary,
} from "./api/types";
import { AppShell, type ViewId } from "./components/layout/AppShell";
import { CommandCenter } from "./views/CommandCenter";
import { OpportunityRadar } from "./views/OpportunityRadar";
import { StrategyLab } from "./views/StrategyLab";
import { PortfolioClinic } from "./views/PortfolioClinic";
import { Reports } from "./views/Reports";
import { Analysis } from "./views/Analysis";
import { Instruments } from "./views/Instruments";
import { Models } from "./views/Models";

export default function App() {
  const [activeView, setActiveView] = useState<ViewId>("command");
  const [tickers, setTickers] = useState<TickerSummary[]>([]);
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [mandates, setMandates] = useState<MandateSummary[]>([]);
  const [liveAvailable, setLiveAvailable] = useState(false);
  const [selectedInstrument, setSelectedInstrument] = useState<string>("");
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [command, setCommand] = useState<CommandCenterResponse | null>(null);
  const [opportunities, setOpportunities] = useState<OpportunitiesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string>("");

  const refreshLists = async () => {
    const [tickerPayload, modelPayload, mandatePayload] = await Promise.all([
      api.tickers(),
      api.models(),
      api.mandates(),
    ]);
    setTickers(tickerPayload.tickers);
    setModels(modelPayload.models);
    setMandates(mandatePayload.mandates);
    setLiveAvailable(tickerPayload.live_available);
    setSelectedInstrument((current) => current || tickerPayload.tickers[0]?.symbol || "");
    setSelectedModel((current) => current || (tickerPayload.tickers.length ? "" : modelPayload.models[0]?.id || ""));
  };

  const refreshCommand = async () => {
    const payload = await api.commandCenter();
    setCommand(payload);
  };

  useEffect(() => {
    let cancelled = false;
    Promise.all([refreshLists(), refreshCommand()])
      .catch((error: Error) => {
        if (!cancelled) setNotice(error.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
      await Promise.all([refreshLists(), refreshCommand()]);
      selectInstrumentOnly(result.symbol);
      setActiveView("analysis");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Price upload failed.");
      throw error;
    }
  };

  const onUploadModel = async (file: File, name: string, mandate: string, context: string) => {
    try {
      const result = await api.uploadModel(file, name, mandate, context);
      setNotice(`Imported ${result.name} with ${result.n_holdings} holdings.`);
      await Promise.all([refreshLists(), refreshCommand()]);
      selectModelOnly(result.id);
      setActiveView("clinic");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Model upload failed.");
      throw error;
    }
  };

  const onFetchLive = async (symbol: string) => {
    try {
      const result = await api.fetchLive(symbol);
      setNotice(`Fetched ${result.symbol} with ${result.rows} rows.`);
      await Promise.all([refreshLists(), refreshCommand()]);
      selectInstrumentOnly(result.symbol);
      setActiveView("analysis");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Live data fetch failed.");
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
      return <CommandCenter payload={command} onOpenInstrument={openInstrument} onOpenModel={openModel} onOpenView={setActiveView} />;
    }
    if (activeView === "instruments") {
      return <Instruments tickers={tickers} onOpenInstrument={openInstrument} />;
    }
    if (activeView === "models") {
      return <Models models={models} onOpenModel={openModel} onOpenClinic={(id) => { selectModelOnly(id); setActiveView("clinic"); }} />;
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
    if (activeView === "reports") {
      return (
        <Reports
          tickers={tickers}
          models={models}
          selectedInstrument={selectedInstrument}
          selectedModel={selectedModel}
          onSelectInstrument={selectInstrumentOnly}
          onSelectModel={selectModelOnly}
        />
      );
    }
    return (
      <Analysis
        tickers={tickers}
        models={models}
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
      notice={notice}
    >
      {content}
    </AppShell>
  );
}
