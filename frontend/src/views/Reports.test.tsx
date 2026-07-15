import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import type {
  CloudTransferDisclosure, ReportResponse, ReportSnapshot, ReportSnapshotStorage,
} from "../api/types";
import { Reports } from "./Reports";

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
});

const storage: ReportSnapshotStorage = {
  backend: "sqlite", scope: "local", durable: true, configured: true,
  encrypted_at_rest: true, at_rest_format: "encrypted_snapshot",
};

const report = {
  kind: "instrument",
  title: "SPY Evidence Report",
  timestamp: "2026-07-14T12:00:00Z",
  data_mode: "real",
  display_label: "Real market data",
  eligible_for_real_research: true,
  data_provenance: { data_mode: "real", eligible_for_real_research: true },
  warnings: [],
  sections: { executive_summary: { headline: "Deterministic report facts." } },
} as ReportResponse;

const snapshot = {
  id: "report-1", created_at: "2026-07-14T12:01:00Z",
  report_package: "institutional_advisor_report", version: 1, version_label: "v1",
  target_kind: "instrument", target_id: "SPY", target_name: "S&P 500 ETF",
  prepared_for: "", prepared_by: "", reviewer: "", report_purpose: "advisor_review",
  title: "SPY Evidence Report", data_mode: "real", display_label: "Real market data",
  eligible_for_real_research: true, source: "upload", row_count: 300,
  first_date: "2025-05-01", last_date: "2026-07-14", source_counts: { upload: 1 },
  model_metadata: {}, warnings: [], ai_narrative_included: true,
  ai_narrative_status: "generated", ai_provider: { provider: "anthropic" },
  audit_trail: [], disclosure_blocks: [], output_formats: ["html", "pdf"],
  html_url: "/report-1.html", pdf_url: "/report-1.pdf",
} as ReportSnapshot;

describe("Reports cloud narrative confirmation", () => {
  it("shows the disclosure and resubmits the exact fingerprint", async () => {
    localStorage.setItem("helios_report_ai", "on");
    const disclosure: CloudTransferDisclosure = {
      disclosure_hash: "exact-transfer-fingerprint",
      dlp_payload_hash: "sanitized-payload-fingerprint",
      provider: "anthropic", model: "claude-test", task: "report_narrative",
      transfer_scope: "final_sanitized_provider_request", cloud_transfer: true,
      confirmation_required: true, confirmed: false, redaction_count: 2,
      redaction_categories: { client_name: 2 }, redacted_fields: ["prepared_for"],
      raw_values_returned: false, review_required: true,
      basis: "Local DLP ran before the optional provider transfer.",
    };
    vi.spyOn(api, "reportSnapshots").mockResolvedValue({
      snapshots: [], count: 0, storage, disclaimer: "Analysis only.",
    });
    vi.spyOn(api, "signalJournal").mockResolvedValue({ entries: [] } as never);
    vi.spyOn(api, "reportInstrument").mockResolvedValue(report);
    vi.spyOn(api, "aiStatus").mockResolvedValue({
      enabled: false, provider: "none", mode: "disabled", model: "",
      available: false, reason: "AI disabled.", keys_exposed: false, secrets_stored: false,
    });
    const save = vi.spyOn(api, "saveReportSnapshot")
      .mockRejectedValueOnce(new ApiError(
        "Confirm the locally redacted payload.", 409,
        { code: "cloud_ai_confirmation_required", cloud_transfer: disclosure },
      ))
      .mockResolvedValueOnce({
        snapshot, html_url: snapshot.html_url, pdf_url: snapshot.pdf_url,
        storage, disclaimer: "Analysis only.",
      });

    render(
      <Reports
        tickers={[{ symbol: "SPY", name: "S&P 500 ETF", source: "upload", last_price: 500, change_pct: 0 }]}
        models={[]}
        selectedInstrument="SPY"
        onSelectInstrument={vi.fn()}
        onSelectModel={vi.fn()}
        dataStatus={null}
      />,
    );

    await screen.findByRole("heading", { level: 1, name: "SPY Evidence Report" });
    fireEvent.click(screen.getByRole("button", { name: "Save snapshot" }));
    await screen.findByText("Confirm report narrative cloud transfer");
    expect(screen.getByText(/Local DLP redacted 2 sensitive value/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Confirm and save" }));

    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    expect(save.mock.calls[1][0].cloud_confirmation).toEqual({
      confirmed: true,
      disclosure_hash: "exact-transfer-fingerprint",
    });
  });
});
