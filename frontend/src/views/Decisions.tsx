import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type {
  DecisionBucketStats, DecisionEntry, DecisionsResponse,
  LedgerAccount, LedgerPerformanceResponse, ModelSummary,
  RebalanceProposal,
} from "../api/types";
import { Panel, StatTile } from "../components/cards/Panel";
import { EmptyState } from "../components/empty-states/EmptyState";
import { RequestStatus } from "../components/states/RequestStatus";
import { useViewFetch } from "../hooks/useViewFetch";
import { fmtMoney, fmtNumber, fmtTimestamp } from "../utils/format";

const ACTIONS = ["BUY", "ADD", "HOLD", "TRIM", "SELL"] as const;

/**
 * Decision Journal — the operator's calls vs the engine's, scored over time.
 * Records live from the Analysis quick-log or manually here (for trades made
 * directly in the trading platform).
 */
export function Decisions() {
  const {
    payload: data, failure, isLoading: loading, load, retry, staleResult,
  } = useViewFetch<DecisionsResponse>({
    failureMessage: "Could not load the decision journal.",
    keepPayloadWhileLoading: true,
  });

  // Manual record form (for decisions made away from the Analysis view).
  const [targetKind, setTargetKind] = useState("instrument");
  const [targetId, setTargetId] = useState("");
  const [myAction, setMyAction] = useState<string>("BUY");
  const [rationale, setRationale] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  const refresh = useCallback(async () => {
    await load("decision-journal", api.listDecisions);
  }, [load]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const record = async () => {
    if (!targetId.trim() || saving) return;
    setSaving(true);
    setSaveError("");
    try {
      await api.recordDecision({
        target_kind: targetKind,
        target_id: targetId.trim(),
        my_action: myAction,
        rationale: rationale.trim(),
      });
      setTargetId("");
      setRationale("");
      await refresh();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Could not record the decision.");
    } finally {
      setSaving(false);
    }
  };

  const board = data?.scoreboard;
  const decisions = data?.decisions || [];
  const mandates = useMemo(() => Object.entries(board?.by_mandate || {}), [board]);

  return (
    <>
      <div>
        <div className="section-label">Decision Journal</div>
        <h1>Your calls vs the engine — scored</h1>
        <p>
          Every decision records what Helios said and what you did. Outcomes are measured at 21/63/252
          trading days so agree-vs-override value is evidence, not memory.
        </p>
      </div>

      {/* The form must not depend on the list GET: recording is a POST and
          stayed usable through /api/decisions hiccups only the Scoreboard
          panel actually needs (review finding). */}
      <section className="dashboard-grid">
        {board && (
          <Panel title="Scoreboard" className="span-2">
            <div className="stat-grid">
              <StatTile label="Decisions" value={board.total.count} />
              <StatTile label="Measured" value={board.total.measured_count} />
              <StatTile
                label="Hit rate (all)"
                value={board.total.hit_rate_pct != null ? `${fmtNumber(board.total.hit_rate_pct, 0)}%` : "—"}
                tone={tone(board.total)}
              />
              <StatTile
                label="Hit rate · agree"
                value={board.agree.hit_rate_pct != null ? `${fmtNumber(board.agree.hit_rate_pct, 0)}%` : "—"}
                tone={tone(board.agree)}
              />
              <StatTile
                label="Hit rate · override"
                value={board.override.hit_rate_pct != null ? `${fmtNumber(board.override.hit_rate_pct, 0)}%` : "—"}
                tone={tone(board.override)}
              />
              <StatTile
                label="Override vs engine"
                value={`${board.override_vs_engine.override_won}–${board.override_vs_engine.engine_won}`}
                tone={board.override_vs_engine.override_won >= board.override_vs_engine.engine_won ? "positive" : "negative"}
              />
            </div>
            {mandates.length > 1 && (
              <div className="decision-mandates">
                {mandates.map(([key, stats]) => (
                  <span key={key}>
                    <b>{key}</b> {stats.count} decisions
                    {stats.hit_rate_pct != null ? ` · ${fmtNumber(stats.hit_rate_pct, 0)}% hit` : " · pending"}
                  </span>
                ))}
              </div>
            )}
            {board.disclaimer && <p className="forecast-note">{board.disclaimer}</p>}
          </Panel>
        )}

        <Panel title="Log a decision">
            <div className="decision-form">
              <div className="decision-form__row">
                <label className="decision-form__field">
                  <span>Target type</span>
                  <select value={targetKind} onChange={(e) => setTargetKind(e.target.value)}>
                    <option value="instrument">Instrument</option>
                    <option value="model">Model</option>
                  </select>
                </label>
                <label className="decision-form__field decision-form__field--wide">
                  <span>{targetKind === "model" ? "Model ID" : "Ticker"}</span>
                  <input
                    value={targetId}
                    onChange={(e) => setTargetId(e.target.value)}
                    placeholder={targetKind === "model" ? "e.g. ULTRA-CORE" : "e.g. JPM"}
                  />
                </label>
                <label className="decision-form__field">
                  <span>Your action</span>
                  <select value={myAction} onChange={(e) => setMyAction(e.target.value)}>
                    {ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
                  </select>
                </label>
              </div>
              <label className="decision-form__field">
                <span>Decision rationale</span>
                <textarea
                  value={rationale}
                  rows={2}
                  onChange={(e) => setRationale(e.target.value)}
                  placeholder="Your rationale becomes the record you learn from"
                />
              </label>
              <button type="button" onClick={() => void record()} disabled={saving || !targetId.trim()}>
                {saving ? "Recording…" : "Record decision"}
              </button>
              {saveError && <div className="notice danger" role="alert">{saveError}</div>}
              <p className="forecast-note">
                Tip: from the Analysis view you can log with one click and the engine snapshot attaches automatically.
              </p>
            </div>
          </Panel>
      </section>

      <LedgerSection />

      <Panel title="Decisions" meta={`${decisions.length} recorded`}>
        <RequestStatus failure={failure} stale={staleResult} onRetry={retry} />
        {loading && !data ? (
          <EmptyState title="Loading" body="Reading the decision journal…" />
        ) : decisions.length === 0 ? (
          <EmptyState
            title="No decisions recorded yet"
            body="Log your first call — from here or the Analysis quick-log — and Helios starts building your personal evidence base."
          />
        ) : (
          <div className="decision-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable decision history">
            <table className="terminal-data-table decision-data-table">
              <thead><tr>
                <th scope="col">When</th><th scope="col">Target</th><th scope="col">Engine</th><th scope="col">You</th>
                <th scope="col">Stance</th><th scope="col">21d</th><th scope="col">63d</th><th scope="col">252d</th><th scope="col">Rationale</th>
              </tr></thead>
              <tbody>{decisions.map((d) => (
                <tr key={d.decision_id}>
                  <td>{fmtTimestamp(d.created_at)}</td>
                  <th scope="row"><b>{d.target_id}</b></th>
                  <td><span className={`signal-action action-${(d.engine_action || "review").toLowerCase()}`}>{d.engine_action || "—"}</span></td>
                  <td><span className={`signal-action action-${bucketClass(d.my_action)}`}>{d.my_action}</span></td>
                  <td><span className={d.agreement === "override" ? "decision-override" : "decision-agree"}>{d.agreement || "—"}</span></td>
                  {["21", "63", "252"].map((h) => <td key={h}>{outcomeCell(d, h)}</td>)}
                  <td className="decision-rationale" title={d.rationale}>{d.rationale || "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}

function tone(stats: DecisionBucketStats): string {
  if (stats.hit_rate_pct == null) return "neutral";
  return stats.hit_rate_pct >= 55 ? "positive" : stats.hit_rate_pct < 45 ? "negative" : "neutral";
}

function bucketClass(action: string): string {
  const a = (action || "").toUpperCase();
  if (a === "BUY" || a === "ADD") return "buy";
  if (a === "SELL" || a === "TRIM") return "sell";
  return "hold";
}

function outcomeCell(d: DecisionEntry, horizon: string) {
  if (d.outcome_status === "not_measurable") return "n/m";
  const o = d.outcomes?.[horizon];
  if (!o) return "…";
  const ret = o.target_return_pct;
  const mark = o.hit == null ? "" : o.hit ? " ✓" : " ✗";
  return `${ret >= 0 ? "+" : ""}${fmtNumber(ret, 1)}%${mark}`;
}

/**
 * Ledger — ACTUAL account outcomes from custodian exports, next to the paper
 * research series. Fills/positions CSVs import idempotently; two dated
 * snapshots unlock a real Modified-Dietz TWR (gross + net of observed fees).
 */
function LedgerSection() {
  const [accounts, setAccounts] = useState<LedgerAccount[]>([]);
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [accountId, setAccountId] = useState("");
  const [perf, setPerf] = useState<LedgerPerformanceResponse | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [asOf, setAsOf] = useState("");
  const [newAccount, setNewAccount] = useState("");
  const [accountsError, setAccountsError] = useState("");

  const refreshAccounts = useCallback(async () => {
    try {
      const [acct, mods] = await Promise.all([api.ledgerAccounts(), api.models()]);
      setAccounts(acct.accounts);
      setModels(mods.models);
      setAccountsError("");
      if (!accountId && acct.accounts.length) setAccountId(acct.accounts[0].account_id);
    } catch (err) {
      setAccountsError(err instanceof Error ? err.message : "Ledger accounts and model mappings are unavailable.");
    }
  }, [accountId]);

  useEffect(() => { void refreshAccounts(); }, [refreshAccounts]);

  const loadPerformance = useCallback(async (id: string) => {
    if (!id) return;
    try {
      setPerf(await api.ledgerPerformance(id));
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Could not load ledger performance.");
    }
  }, []);

  useEffect(() => { if (accountId) void loadPerformance(accountId); }, [accountId, loadPerformance]);

  const upload = async (kind: "fills" | "positions", file: File | null) => {
    const target = accountId || newAccount.trim();
    if (!file || !target || busy) {
      if (!target) setMessage("Enter or select an account id first.");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const result = await api.ledgerUpload(kind, file, target, kind === "positions" ? asOf : undefined);
      const warnings = Array.isArray(result.warnings) ? result.warnings.length : 0;
      setMessage(`${kind === "fills"
        ? `Imported ${String(result.inserted)} fill(s) (${String(result.duplicates)} duplicate, ${String(result.skipped_non_trade)} non-trade)`
        : `Snapshot ${String(result.as_of)} saved: ${fmtMoney(Number(result.total_value))} across ${String(result.n_positions)} positions`}${warnings ? ` · ${warnings} warning(s)` : ""}.`);
      await refreshAccounts();
      if (!accountId) setAccountId(target);
      await loadPerformance(target);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  };

  const mapModel = async (modelId: string) => {
    if (!accountId) return;
    try {
      const result = await api.ledgerMapAccount({ account_id: accountId, model_id: modelId });
      setAccounts(result.accounts);
      await loadPerformance(accountId);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Could not map the account.");
    }
  };

  const actual = perf?.actual;
  const paper = perf?.paper;
  const mappedModel = accounts.find((a) => a.account_id === accountId)?.model_id || "";
  const shortfall = (perf?.shortfall || []).filter((row) => row.status === "measured");

  return (
    <Panel title="Ledger — Actual vs Paper" meta="custodian fills + snapshots · Modified-Dietz TWR">
      {accountsError && (
        <div className="notice warning" role="alert">{accountsError} <button type="button" onClick={() => void refreshAccounts()}>Retry ledger setup</button></div>
      )}
      <div className="decision-form">
        <div className="decision-form__row">
          <select value={accountId} onChange={(e) => setAccountId(e.target.value)} aria-label="Ledger account">
            <option value="">{accounts.length ? "Select account" : "No accounts yet"}</option>
            {accounts.map((a) => <option key={a.account_id} value={a.account_id}>{a.display_name || a.account_id}</option>)}
          </select>
          <input value={newAccount} onChange={(e) => setNewAccount(e.target.value)}
            placeholder="…or new account id" aria-label="New account id" />
          <select value={mappedModel} onChange={(e) => void mapModel(e.target.value)}
            aria-label="Mapped model" disabled={!accountId}>
            <option value="">Map to model…</option>
            {models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        </div>
        <div className="decision-form__row">
          <label>Fills CSV
            <input type="file" accept=".csv" disabled={busy}
              onChange={(e) => { void upload("fills", e.target.files?.[0] ?? null); e.target.value = ""; }} />
          </label>
          <label>Positions CSV
            <input type="file" accept=".csv" disabled={busy}
              onChange={(e) => { void upload("positions", e.target.files?.[0] ?? null); e.target.value = ""; }} />
          </label>
          <input value={asOf} onChange={(e) => setAsOf(e.target.value)}
            placeholder="Positions as-of (YYYY-MM-DD)" aria-label="Positions as-of date" />
        </div>
        {message && <p className="forecast-note">{message}</p>}
      </div>

      {actual && actual.status === "measured" ? (
        <>
          <div className="stat-grid">
            <StatTile label="Return, Dietz est. (net)" value={`${fmtNumber(actual.twr_net_pct, 2)}%`}
              tone={(actual.twr_net_pct ?? 0) >= 0 ? "positive" : "negative"} />
            <StatTile label="Return, Dietz est. (gross)" value={`${fmtNumber(actual.twr_gross_pct, 2)}%`} />
            <StatTile label="Fees observed" value={fmtMoney(actual.fees_usd ?? 0)} />
            <StatTile label="Paper (research series)"
              value={paper?.status === "measured" ? `${fmtNumber(paper.return_pct, 2)}%` : paper?.status === "no_model_mapped" ? "map a model" : "—"} />
            <StatTile label="Actual − paper gap"
              value={perf?.gap_pct != null ? `${fmtNumber(perf.gap_pct, 2)}%` : "—"}
              tone={perf?.gap_pct != null ? (perf.gap_pct >= 0 ? "positive" : "negative") : undefined} />
          </div>
          <p className="forecast-note">
            {actual.period ? `${actual.period.start} → ${actual.period.end} · ${actual.n_periods} Dietz sub-period(s)` : ""}
            {actual.cash_drag_est_pct != null ? ` · est. cash drag ${fmtNumber(actual.cash_drag_est_pct, 2)}%` : ""}
            {" — actual numbers from custodian data; paper stays a cost-free research construction."}
          </p>
        </>
      ) : actual ? (
        <EmptyState title={actual.status === "insufficient_snapshots" ? "Awaiting snapshots" : "No actual performance yet"}
          body={actual.note || "Upload custodian fills and at least two dated positions snapshots to measure a real return."} />
      ) : (
        <EmptyState title="No ledger data" body="Upload custodian fills and positions CSVs to start measuring actual outcomes." />
      )}

      {shortfall.length > 0 && (
        <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable implementation shortfall">
          <table className="terminal-data-table implementation-shortfall-data-table">
            <thead><tr><th scope="col">Decision</th><th scope="col">Ticker</th><th scope="col">Side</th><th scope="col">Avg fill</th><th scope="col">Decision close</th><th scope="col">Shortfall</th></tr></thead>
            <tbody>{shortfall.slice(0, 8).map((row) => (
            <tr key={`${row.decision_id}-${row.ticker}-${row.side}`}>
              <th scope="row">{row.decision_date}<small>{row.decision_id.slice(0, 12)}</small></th>
              <td><strong>{row.ticker}</strong></td>
              <td>{row.side}</td>
              <td>{fmtNumber(row.avg_fill_price, 2)}</td>
              <td>{fmtNumber(row.decision_close, 2)}</td>
              <td className={(row.shortfall_bps ?? 0) > 0 ? "tone-negative" : "tone-positive"}>{fmtNumber(row.shortfall_bps, 0)} bps</td>
            </tr>
          ))}</tbody>
          </table>
        </div>
      )}

      <RebalancePanel key={`${accountId}:${mappedModel}`} accountId={accountId} modelId={mappedModel} />
    </Panel>
  );
}

function RebalancePanel({ accountId, modelId }: { accountId: string; modelId: string }) {
  const [proposal, setProposal] = useState<RebalanceProposal | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const propose = async () => {
    if (!accountId || !modelId || busy) return;
    setBusy(true);
    setError("");
    try {
      setProposal(await api.rebalancePropose({ account_id: accountId, model_id: modelId }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Proposal failed.");
    } finally {
      setBusy(false);
    }
  };

  if (!accountId || !modelId) {
    return (
      <p className="forecast-note">
        Map this account to a model to unlock constrained current-to-target proposals.
      </p>
    );
  }
  return (
    <div className="rebalance-panel">
      <div className="decision-form__row">
        <button type="button" onClick={() => void propose()} disabled={busy}>
          {busy ? "Solving…" : "Propose rebalance to target"}
        </button>
        {proposal?.method && <small>{proposal.method === "cvxpy_qp" ? "convex QP" : "capped projection"}</small>}
      </div>
      {error && (
        <div className="notice danger" role="alert">
          {error} <button type="button" onClick={() => void propose()}>Retry proposal</button>
        </div>
      )}
      {proposal?.status === "blocked" && (
        <EmptyState title="Proposal blocked" body={proposal.reason || "Missing data."} />
      )}
      {proposal?.status === "infeasible" && (
        <div className="forecast-note tone-negative">
          <strong>Constraints cannot be satisfied:</strong>
          <ul>{(proposal.violations || []).map((v) => <li key={v.detail}>{v.detail}</li>)}</ul>
        </div>
      )}
      {proposal?.status === "proposed" && proposal.summary && (
        <>
          <p className="forecast-note">
            Proposal for <b>{proposal.account_id}</b> → <b>{proposal.model_name || proposal.model_id}</b>
            {" "}(snapshot {proposal.snapshot_as_of})
          </p>
          <div className="stat-grid">
            <StatTile label="Trades" value={proposal.summary.n_trades} />
            <StatTile label="One-way turnover" value={`${fmtNumber(proposal.summary.one_way_turnover_pct, 1)}%`} />
            <StatTile label="Residual to target" value={`${fmtNumber(proposal.summary.residual_to_target_pct, 1)}%`}
              tone={proposal.target_reachable ? "positive" : "negative"} />
            <StatTile label="Est. cost" value={fmtMoney(proposal.summary.est_total_cost_usd)} />
            <StatTile label="Cash after" value={`${fmtNumber(proposal.summary.proposed_cash_pct, 1)}%`} />
          </div>
          {proposal.target_reachable === false && (
            <div className="forecast-note tone-negative">
              <strong>Target not fully reachable under current constraints:</strong>
              <ul>{(proposal.violations || []).map((v) => <li key={v.detail}>{v.detail}</li>)}</ul>
            </div>
          )}
          {(proposal.trades || []).length > 0 && (
            <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable proposed trades">
              <table className="terminal-data-table proposed-trades-data-table">
                <thead><tr><th scope="col">Ticker</th><th scope="col">Side</th><th scope="col">Now → Target</th><th scope="col">Proposed</th><th scope="col">Trade</th><th scope="col">Days @ cap</th></tr></thead>
                <tbody>{(proposal.trades || []).slice(0, 20).map((t) => (
                <tr key={t.ticker}>
                  <th scope="row">{t.ticker}</th>
                  <td className={t.side === "BUY" ? "tone-positive" : "tone-negative"}>{t.side}</td>
                  <td>{fmtNumber(t.current_weight_pct, 1)}% → {fmtNumber(t.target_weight_pct, 1)}%</td>
                  <td>{fmtNumber(t.proposed_weight_pct, 1)}%</td>
                  <td>{fmtMoney(t.trade_usd)}<small>≈{fmtNumber(t.est_shares, 0)} sh @ {fmtNumber(t.price_used, 2)}</small></td>
                  <td>{t.est_days_to_trade != null ? fmtNumber(t.est_days_to_trade, 1) : "ADV n/a"}</td>
                </tr>
              ))}</tbody>
              </table>
            </div>
          )}
          <p className="forecast-note">{proposal.basis} {proposal.disclaimer}</p>
        </>
      )}
    </div>
  );
}
