import { useCallback, useEffect, useId, useMemo, useRef, useState, type FocusEvent, type FormEvent, type ReactNode } from "react";
import type { DataMode, DataStatusResponse, MandateSummary, ModelSummary, TickerSummary } from "../../api/types";
import { DataModeBadge, SourcePill } from "../badges/DataModeBadge";
import { DataIntakePanel } from "../data/DataIntakePanel";
import { fmtMoney, fmtPct } from "../../utils/format";
import { api } from "../../api/client";
import type { SecurityStatusResponse } from "../../api/types";

export type ViewId = "command" | "setup" | "instruments" | "models" | "opportunities" | "strategy" | "evidence" | "clinic" | "risk" | "reports" | "journal" | "decisions" | "data-quality" | "analysis";

interface ShellProps {
  activeView: ViewId;
  onViewChange: (view: ViewId) => void;
  dataMode?: { mode?: DataMode; label?: string };
  tickers: TickerSummary[];
  models: ModelSummary[];
  mandates: MandateSummary[];
  selectedInstrument?: string;
  selectedModel?: string;
  onSelectInstrument: (symbol: string) => void;
  onSelectModel: (id: string) => void;
  onUploadPrice: (file: File, symbol: string) => Promise<void>;
  onUploadModel: (file: File, name: string, mandate: string, context: string) => Promise<void>;
  onFetchLive: (symbol: string) => Promise<void>;
  onRefreshData: (symbol?: string, all?: boolean) => Promise<void>;
  liveAvailable: boolean;
  notice?: string;
  dataStatus: DataStatusResponse | null;
  children: ReactNode;
}

const views: Array<{ id: ViewId; label: string }> = [
  { id: "command", label: "Command Center" },
  { id: "setup", label: "Data Setup" },
  { id: "instruments", label: "Instruments" },
  { id: "models", label: "Models" },
  { id: "opportunities", label: "Opportunity Radar" },
  { id: "strategy", label: "Strategy Lab" },
  { id: "evidence", label: "Evidence Lab" },
  { id: "clinic", label: "Portfolio Clinic" },
  { id: "risk", label: "Risk Analytics" },
  { id: "reports", label: "Reports" },
  { id: "journal", label: "Signal Journal" },
  { id: "decisions", label: "Decision Journal" },
  { id: "data-quality", label: "Data Quality" },
  { id: "analysis", label: "Analysis" },
];

// The five first-class workspaces follow the review-to-decision workflow.
export const navGroups: Array<{ label: string; ids: ViewId[] }> = [
  { label: "Setup", ids: ["command", "setup", "instruments", "data-quality", "models"] },
  { label: "Research", ids: ["opportunities", "analysis", "strategy"] },
  { label: "Evidence & Risk", ids: ["evidence", "clinic", "risk"] },
  { label: "Decisions", ids: ["journal", "decisions"] },
  { label: "Reports", ids: ["reports"] },
];

export function isViewId(value: string): value is ViewId {
  return views.some((view) => view.id === value);
}

function viewLabel(id: ViewId): string {
  return views.find((view) => view.id === id)?.label || id;
}

function compactDataModeLabel(mode: DataMode | undefined, label: string) {
  if (mode === "real") return "Live Data";
  if (mode === "mixed") return "Mixed Data";
  if (mode === "invalid_for_research") return "Research Locked";
  return label.length > 18 ? "Data Status" : label;
}

export function AppShell(props: ShellProps) {
  const { tickers, models, onViewChange, onSelectInstrument, onSelectModel } = props;
  const [searchQuery, setSearchQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchActiveIndex, setSearchActiveIndex] = useState(0);
  const [advisorOpen, setAdvisorOpen] = useState(false);
  const [securityStatus, setSecurityStatus] = useState<SecurityStatusResponse | null>(null);
  const [securityError, setSecurityError] = useState("");
  const [sessionBusy, setSessionBusy] = useState(false);
  const [density, setDensity] = useState<"comfortable" | "compact">(() => {
    try {
      return localStorage.getItem("helios_density") === "compact" ? "compact" : "comfortable";
    } catch {
      return "comfortable";
    }
  });
  const toggleDensity = () => {
    setDensity((current) => {
      const next = current === "compact" ? "comfortable" : "compact";
      try {
        localStorage.setItem("helios_density", next);
      } catch {
        // Persistence is best-effort; the toggle still applies for this session.
      }
      return next;
    });
  };
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    // Collapsed rail is the default; keeping it open is the persisted choice.
    try {
      return localStorage.getItem("helios_sidebar") !== "open";
    } catch {
      return true;
    }
  });
  const navRef = useRef<HTMLElement | null>(null);
  // Workspace is DERIVED from the active view (never stored separately, so
  // deep links, the search palette, and every onOpenView call keep working);
  // clicking a workspace returns to its last-visited view.
  const activeGroup = navGroups.find((group) => group.ids.includes(props.activeView));
  const lastViewByGroup = useRef<Record<string, ViewId>>({});
  useEffect(() => {
    if (activeGroup) lastViewByGroup.current[activeGroup.label] = props.activeView;
  }, [activeGroup, props.activeView]);
  // The active view must never sit scrolled out of the (scrollbar-less) nav:
  // clipped at 1280px, navigating right hid Command Center entirely.
  useEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    const active = nav.querySelector("button.active");
    active?.scrollIntoView({ behavior: "auto", inline: "nearest", block: "nearest" });
  }, [props.activeView]);
  // The compact sidebar remains available for repeat work, while every setup
  // CTA routes to the full-page Data Setup workspace.
  const revealDataIntake = useCallback(() => {
    onViewChange("setup");
  }, [onViewChange]);
  useEffect(() => {
    // Views deep in the tree (Command Center CTA, Instruments empty state)
    // request the reveal via this event — no prop drilling through App.
    const onReveal = () => revealDataIntake();
    window.addEventListener("helios:reveal-data-intake", onReveal);
    return () => window.removeEventListener("helios:reveal-data-intake", onReveal);
  }, [revealDataIntake]);
  const toggleSidebar = () => {
    setSidebarCollapsed((current) => {
      const next = !current;
      try {
        localStorage.setItem("helios_sidebar", next ? "collapsed" : "open");
      } catch {
        // Persistence is best-effort; the toggle still applies for this session.
      }
      return next;
    });
  };
  const searchListId = useId();
  const searchShellRef = useRef<HTMLDivElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const advisorShellRef = useRef<HTMLDivElement | null>(null);
  const activeViewLabel = views.find((view) => view.id === props.activeView)?.label || "Workspace";
  const selectedContext = props.selectedModel
    ? props.models.find((model) => model.id === props.selectedModel)?.name || props.selectedModel
    : props.selectedInstrument
      ? props.tickers.find((ticker) => ticker.symbol === props.selectedInstrument)?.symbol || props.selectedInstrument
      : "";
  const dataModeFullLabel = props.dataMode?.label || "Data status pending";
  const dataModeShortLabel = compactDataModeLabel(props.dataMode?.mode, dataModeFullLabel);

  const refreshSecurityStatus = useCallback(async () => {
    try {
      setSecurityStatus(await api.securityStatus());
      setSecurityError("");
    } catch (error) {
      setSecurityError(error instanceof Error ? error.message : "Security status unavailable.");
    }
  }, []);

  useEffect(() => { void refreshSecurityStatus(); }, [refreshSecurityStatus]);

  // "/" focuses the global search from anywhere outside a form control.
  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable)) return;
      event.preventDefault();
      searchInputRef.current?.focus();
    };
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, []);

  const searchResults = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    const viewResults = views.map((view) => ({
      key: `view-${view.id}`,
      label: view.label,
      meta: "Workspace",
      action: () => onViewChange(view.id),
    }));
    // Feature aliases: things operators search for by capability name rather
    // than by the view that hosts them.
    const aliasResults: Array<{ key: string; label: string; meta: string; action: () => void }> = [
      {
        key: "alias-ledger",
        label: "Ledger — Actual vs Paper",
        meta: "Feature · Decision Journal",
        action: () => onViewChange("decisions"),
      },
      {
        key: "alias-report-snapshots",
        label: "Saved report snapshots",
        meta: "Feature · Reports",
        action: () => onViewChange("reports"),
      },
    ];
    const instrumentResults = tickers.map((ticker) => ({
      key: `instrument-${ticker.symbol}`,
      label: `${ticker.symbol} · ${ticker.name}`,
      meta: `Instrument · ${ticker.source || "source pending"}`,
      action: () => onSelectInstrument(ticker.symbol),
    }));
    const modelResults = models.map((model) => ({
      key: `model-${model.id}`,
      label: model.name,
      meta: `Model · ${model.mandate_label} · ${model.n_holdings} holdings`,
      action: () => onSelectModel(model.id),
    }));
    const all = [...viewResults, ...aliasResults, ...instrumentResults, ...modelResults];
    if (!query) return all.slice(0, 7);
    return all
      .filter((item) => `${item.label} ${item.meta}`.toLowerCase().includes(query))
      .slice(0, 8);
  }, [models, onSelectInstrument, onSelectModel, onViewChange, tickers, searchQuery]);
  const activeResultIndex = Math.min(searchActiveIndex, Math.max(0, searchResults.length - 1));
  const runSearchResult = (result: { action: () => void }) => {
    result.action();
    setSearchOpen(false);
    setSearchQuery("");
    setSearchActiveIndex(0);
  };
  const moveSearchSelection = (direction: -1 | 1) => {
    if (!searchResults.length) return;
    setSearchActiveIndex((activeResultIndex + direction + searchResults.length) % searchResults.length);
  };
  const submitSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const active = searchResults[activeResultIndex];
    if (active) runSearchResult(active);
  };
  const runAdvisorAction = (view: ViewId) => {
    props.onViewChange(view);
    setAdvisorOpen(false);
  };
  const closeSearchOnBlur = (event: FocusEvent<HTMLDivElement>) => {
    const nextTarget = event.relatedTarget;
    if (nextTarget && searchShellRef.current?.contains(nextTarget as Node)) return;
    setSearchOpen(false);
  };
  const closeAdvisorOnBlur = (event: FocusEvent<HTMLDivElement>) => {
    const nextTarget = event.relatedTarget;
    if (nextTarget && advisorShellRef.current?.contains(nextTarget as Node)) return;
    setAdvisorOpen(false);
  };
  return (
    <div className={`app-shell ${density === "compact" ? "density-compact" : ""}`}>
      <header className="topbar">
        <div className="topbar__main">
          <button className="brand" type="button" onClick={() => props.onViewChange("command")} aria-label="Open Command Center">
            <span className="brand-mark" aria-hidden="true" />
            <span><b>Helios <i>Pro</i></b><small>Research Terminal</small></span>
          </button>
          <div className="topbar__center">
            <div className="search-shell" ref={searchShellRef} onBlur={closeSearchOnBlur}>
              <form className="terminal-search" onSubmit={submitSearch}>
                <span aria-hidden="true">⌕</span>
                <input
                  ref={searchInputRef}
                  role="combobox"
                  aria-label="Search instruments, models, and reports"
                  aria-expanded={searchOpen}
                  aria-controls={`${searchListId}-listbox`}
                  aria-autocomplete="list"
                  aria-activedescendant={searchOpen && searchResults[activeResultIndex] ? `${searchListId}-option-${activeResultIndex}` : undefined}
                  placeholder="Search instruments, models, reports..."
                  value={searchQuery}
                  onChange={(event) => {
                    setSearchQuery(event.currentTarget.value);
                    setSearchActiveIndex(0);
                    setSearchOpen(true);
                  }}
                  onFocus={() => setSearchOpen(true)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") {
                      setSearchOpen(false);
                      return;
                    }
                    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                      event.preventDefault();
                      if (!searchOpen) setSearchOpen(true);
                      else moveSearchSelection(event.key === "ArrowDown" ? 1 : -1);
                      return;
                    }
                    if (event.key !== "Enter") return;
                    event.preventDefault();
                    const active = searchResults[activeResultIndex];
                    if (active) runSearchResult(active);
                  }}
                />
                <kbd aria-hidden="true">/</kbd>
              </form>
              {searchOpen && (
                <div className="search-popover" id={`${searchListId}-listbox`} role="listbox" aria-label="Search results">
                  {searchResults.length === 0 ? (
                    <span className="search-empty">No matching instruments, models, or views.</span>
                  ) : searchResults.map((result, index) => (
                    <button
                      type="button"
                      key={result.key}
                      id={`${searchListId}-option-${index}`}
                      role="option"
                      aria-selected={index === activeResultIndex}
                      className={index === activeResultIndex ? "active" : ""}
                      onClick={() => runSearchResult(result)}
                    >
                      <strong>{result.label}</strong>
                      <small>{result.meta}</small>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
          <div className="topbar__tools">
            <DataModeBadge mode={props.dataMode?.mode} label={dataModeShortLabel} title={dataModeFullLabel} />
            <div className="advisor-shell" ref={advisorShellRef} onBlur={closeAdvisorOnBlur}>
              <button
                className="advisor-identity"
                type="button"
                aria-label="Advisor Console"
                aria-expanded={advisorOpen}
                aria-haspopup="menu"
                onClick={() => setAdvisorOpen((open) => !open)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setAdvisorOpen(false);
                }}
              >
                <span className="advisor-avatar" aria-hidden="true">AC</span>
                <span className="advisor-label">Advisor Console<small>Local workspace</small></span>
              </button>
              {advisorOpen && (
                <div
                  className="advisor-menu"
                  role="menu"
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setAdvisorOpen(false);
                  }}
                >
                  <header>
                    <strong>Advisor Console</strong>
                    <small>Local Helios workspace</small>
                  </header>
                  <dl>
                    <div><dt>Data mode</dt><dd>{props.dataMode?.label || "Pending"}</dd></div>
                    <div><dt>SQLite</dt><dd>{props.dataStatus?.database.available ? "Ready" : "Warning"}</dd></div>
                    <div><dt>Real histories</dt><dd>{props.dataStatus?.real_instrument_count ?? 0}</dd></div>
                    <div><dt>Current view</dt><dd>{activeViewLabel}</dd></div>
                    <div><dt>Selection</dt><dd>{selectedContext || "No active selection"}</dd></div>
                    <div><dt>Identity</dt><dd>{securityStatus?.principal.user || "Unavailable"}</dd></div>
                    <div><dt>Roles</dt><dd>{securityStatus?.principal.roles.join(", ") || "Unavailable"}</dd></div>
                    <div><dt>MFA</dt><dd>{securityStatus?.principal.mfa_verified ? "Verified" : "Not verified"}</dd></div>
                    <div><dt>Transport</dt><dd>{securityStatus?.transport.trusted_tls_asserted ? "Trusted TLS" : "Local / untrusted"}</dd></div>
                  </dl>
                  {securityError && <p className="advisor-menu__warning" role="alert">{securityError}</p>}
                  <div className="advisor-menu__actions">
                    <button type="button" role="menuitem" aria-pressed={density === "compact"} onClick={toggleDensity}>
                      {density === "compact" ? "Density: Compact ●" : "Density: Comfortable ●"}
                    </button>
                    <button type="button" role="menuitem" onClick={() => runAdvisorAction("command")}>Command Center</button>
                    <button type="button" role="menuitem" onClick={() => runAdvisorAction("setup")}>Real Data Setup</button>
                    <button type="button" role="menuitem" onClick={() => runAdvisorAction("models")}>Client Models</button>
                    <button type="button" role="menuitem" onClick={() => runAdvisorAction("journal")}>Signal Journal</button>
                    <button type="button" role="menuitem" onClick={() => runAdvisorAction("evidence")}>Evidence Lab</button>
                    <button type="button" role="menuitem" onClick={() => runAdvisorAction("risk")}>Risk Analytics</button>
                    <button type="button" role="menuitem" onClick={() => runAdvisorAction("data-quality")}>Data Quality</button>
                    <button type="button" role="menuitem" onClick={() => runAdvisorAction("reports")}>Reports & Disclosures</button>
                    <button
                      type="button"
                      role="menuitem"
                      disabled={sessionBusy}
                      onClick={async () => {
                        setSessionBusy(true);
                        try {
                          await api.createSession();
                          await refreshSecurityStatus();
                        } catch (error) {
                          setSecurityError(error instanceof Error ? error.message : "Session creation failed.");
                        } finally {
                          setSessionBusy(false);
                        }
                      }}
                    >Start secured session</button>
                    <button
                      type="button"
                      role="menuitem"
                      disabled={sessionBusy}
                      onClick={async () => {
                        setSessionBusy(true);
                        try {
                          await api.deleteSession();
                          await refreshSecurityStatus();
                        } catch (error) {
                          setSecurityError(error instanceof Error ? error.message : "Session revocation failed.");
                        } finally {
                          setSessionBusy(false);
                        }
                      }}
                    >End secured session</button>
                  </div>
                  <p>Authentication and data permissions are controlled by the Flask backend. Real research unlocks only after provenance checks pass.</p>
                </div>
              )}
            </div>
          </div>
        </div>
        <nav className="workspace-nav" aria-label="Primary workspace" ref={navRef}>
          {navGroups.map((group) => {
            const isActiveGroup = group.ids.includes(props.activeView);
            return (
              <button
                key={group.label}
                className={`workspace-tab ${isActiveGroup ? "active" : ""}`}
                type="button"
                aria-current={isActiveGroup ? "page" : undefined}
                title={group.ids.map(viewLabel).join(" · ")}
                onClick={() => props.onViewChange(lastViewByGroup.current[group.label] ?? group.ids[0])}
              >
                {group.label}
              </button>
            );
          })}
        </nav>
      {activeGroup && activeGroup.ids.length > 1 && (
        <div className="view-tabs" role="tablist" aria-label={`${activeGroup.label} views`}>
          {activeGroup.ids.map((id) => (
            <button
              key={id}
              role="tab"
              type="button"
              aria-selected={props.activeView === id}
              className={props.activeView === id ? "active" : ""}
              onClick={() => props.onViewChange(id)}
            >
              <ShellIcon id={id} />
              <span>{viewLabel(id)}</span>
            </button>
          ))}
          {activeGroup.label === "Setup" && (
            <button type="button" className="view-tabs__action" onClick={revealDataIntake}>
              + Add data
            </button>
          )}
        </div>
      )}
      <div className="context-bar" aria-label="Session context">
        <span><b>{activeGroup?.label || "Workspace"}</b> · {activeViewLabel}</span>
        <span>{selectedContext ? <>Selected <b>{selectedContext}</b></> : "No selection"}</span>
        <span title={dataModeFullLabel}>{dataModeShortLabel}</span>
        <span>{props.dataStatus?.real_instrument_count ?? 0} real histories</span>
      </div>
      </header>
      <div className={`workspace ${props.activeView === "command" ? "workspace-command" : ""} ${sidebarCollapsed ? "workspace-docked" : ""}`}>
        <main className="content" id="main-content">
          {props.notice && <div className="notice" role="status" aria-live="polite">{props.notice}</div>}
          {props.children}
        </main>
        {sidebarCollapsed ? (
          <button
            className="sidebar-rail"
            type="button"
            onClick={toggleSidebar}
            aria-expanded={false}
            aria-label="Show data intake panel"
            title="Show the data intake panel"
          >
            <span aria-hidden="true">⟩</span>
            <b>Data Intake &amp; Selection</b>
          </button>
        ) : (
        <aside className="sidebar">
          <div className="sidebar-head">
            <span>Data Intake</span>
            <button
              type="button"
              onClick={toggleSidebar}
              aria-expanded={true}
              aria-label="Hide data intake panel"
              title="Hide the data intake panel"
            >
              ⟨ Hide
            </button>
          </div>
          <DataIntakePanel {...props} />
          <section className="side-section">
            <h2>Client Models</h2>
            <div className="side-list">
              {props.models.length === 0 ? <p className="muted">No models imported.</p> : props.models.map((model) => (
                <button
                  type="button"
                  key={model.id}
                  className={props.selectedModel === model.id ? "active" : ""}
                  onClick={() => props.onSelectModel(model.id)}
                >
                  <span><b>{model.name}</b><small>{model.mandate_label} · {model.n_holdings} holdings</small></span>
                  <em>{model.top || ""}</em>
                </button>
              ))}
            </div>
          </section>
          <section className="side-section">
            <h2>Instruments</h2>
            <div className="side-list">
              {props.tickers.map((ticker) => (
                <button
                  type="button"
                  key={ticker.symbol}
                  className={props.selectedInstrument === ticker.symbol ? "active" : ""}
                  onClick={() => props.onSelectInstrument(ticker.symbol)}
                >
                  <span><b>{ticker.symbol}</b><small>{ticker.name}</small></span>
                  <em>{fmtMoney(ticker.last_price)} <i className={(ticker.change_pct || 0) >= 0 ? "up" : "down"}>{fmtPct(ticker.change_pct)}</i></em>
                  <SourcePill source={ticker.source} />
                </button>
              ))}
            </div>
          </section>
        </aside>
        )}
      </div>
    </div>
  );
}

function ShellIcon({ id }: { id: ViewId }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  return (
    <span className="nav-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24">
        {id === "command" && (
          <>
            <path {...common} d="M5 6h5v5H5zM14 6h5v5h-5zM5 15h5v3H5zM14 15h5v3h-5z" />
          </>
        )}
        {id === "setup" && <path {...common} d="M4 6h16v12H4zM8 10h8M8 14h5M6 3v3m12-3v3" />}
        {id === "instruments" && <path {...common} d="M7 17V7m5 10V4m5 13v-6M4 17h16" />}
        {id === "models" && <path {...common} d="M7 8a4 4 0 0 1 8 0v1h1a3 3 0 0 1 0 6h-2m-4 0H8a3 3 0 0 1-.6-5.9" />}
        {id === "opportunities" && <path {...common} d="M12 4v3m0 10v3M4 12h3m10 0h3m-5.5-4.5 2-2m-9 9-2 2m0-11 2 2m9 9-2-2" />}
        {id === "strategy" && <path {...common} d="M5 18 12 5l7 13M8.2 14h7.6M10 10h4" />}
        {id === "evidence" && <path {...common} d="M4 17h16M6 14l3-4 4 3 5-7M7 20v-3m5 3v-7m5 7V8" />}
        {id === "clinic" && <path {...common} d="M12 21s7-4.5 7-11V5l-7-3-7 3v5c0 6.5 7 11 7 11Z" />}
        {id === "risk" && <path {...common} d="M4 18h16M6 15l4-5 4 3 4-7M7 18V8m5 10v-5m5 5V6" />}
        {id === "reports" && <path {...common} d="M7 3h7l4 4v14H7zM14 3v5h5M10 12h6M10 16h6" />}
        {id === "journal" && <path {...common} d="M5 19V5m4 14V9m4 10v-7m4 7v-4M4 19h16" />}
        {id === "data-quality" && <path {...common} d="M5 19V5m0 14h14M9 15l2-3 3 2 4-7M8 7h.01M8 11h.01" />}
        {id === "analysis" && <path {...common} d="M5 17l4-4 3 3 7-8M5 21h14" />}
      </svg>
    </span>
  );
}
