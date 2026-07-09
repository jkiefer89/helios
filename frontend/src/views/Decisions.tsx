import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { DecisionBucketStats, DecisionEntry, DecisionsResponse } from "../api/types";
import { Panel, StatTile } from "../components/cards/Panel";
import { EmptyState } from "../components/empty-states/EmptyState";
import { fmtNumber, fmtTimestamp } from "../utils/format";

const ACTIONS = ["BUY", "ADD", "HOLD", "TRIM", "SELL"] as const;

/**
 * Decision Journal — the operator's calls vs the engine's, scored over time.
 * Records live from the Analysis quick-log or manually here (for trades made
 * directly in the trading platform).
 */
export function Decisions() {
  const [data, setData] = useState<DecisionsResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  // Manual record form (for decisions made away from the Analysis view).
  const [targetKind, setTargetKind] = useState("instrument");
  const [targetId, setTargetId] = useState("");
  const [myAction, setMyAction] = useState<string>("BUY");
  const [rationale, setRationale] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api.listDecisions());
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the decision journal.");
    } finally {
      setLoading(false);
    }
  }, []);

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

      {board && (
        <section className="dashboard-grid">
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

          <Panel title="Log a decision">
            <div className="decision-form">
              <div className="decision-form__row">
                <select value={targetKind} onChange={(e) => setTargetKind(e.target.value)}>
                  <option value="instrument">Instrument</option>
                  <option value="model">Model</option>
                </select>
                <input
                  value={targetId}
                  onChange={(e) => setTargetId(e.target.value)}
                  placeholder={targetKind === "model" ? "Model id (e.g. ULTRA-CORE)" : "Ticker (e.g. JPM)"}
                />
                <select value={myAction} onChange={(e) => setMyAction(e.target.value)}>
                  {ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </div>
              <textarea
                value={rationale}
                rows={2}
                onChange={(e) => setRationale(e.target.value)}
                placeholder="Why? (your rationale becomes the record you learn from)"
              />
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
      )}

      <Panel title="Decisions" meta={`${decisions.length} recorded`}>
        {error && <div className="notice danger" role="alert">{error}</div>}
        {loading && !data ? (
          <EmptyState title="Loading" body="Reading the decision journal…" />
        ) : decisions.length === 0 ? (
          <EmptyState
            title="No decisions recorded yet"
            body="Log your first call — from here or the Analysis quick-log — and Helios starts building your personal evidence base."
          />
        ) : (
          <div className="decision-table">
            <div className="decision-table__head">
              <span>When</span><span>Target</span><span>Engine</span><span>You</span>
              <span>Stance</span><span>21d</span><span>63d</span><span>252d</span><span>Rationale</span>
            </div>
            {decisions.map((d) => (
              <div key={d.decision_id} className="decision-table__row">
                <span>{fmtTimestamp(d.created_at)}</span>
                <span><b>{d.target_id}</b></span>
                <span className={`signal-action action-${(d.engine_action || "review").toLowerCase()}`}>{d.engine_action || "—"}</span>
                <span className={`signal-action action-${bucketClass(d.my_action)}`}>{d.my_action}</span>
                <span className={d.agreement === "override" ? "decision-override" : "decision-agree"}>
                  {d.agreement || "—"}
                </span>
                {["21", "63", "252"].map((h) => <span key={h}>{outcomeCell(d, h)}</span>)}
                <span className="decision-rationale" title={d.rationale}>{d.rationale || "—"}</span>
              </div>
            ))}
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
