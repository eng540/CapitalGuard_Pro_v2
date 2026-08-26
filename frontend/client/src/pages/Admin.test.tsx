import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let batchesState: Record<string, unknown>;
const noopMutation = { isPending: false, mutate: vi.fn() };
let replayMutation = vi.fn();
const stableQuery = { isLoading: false, isError: false, isSuccess: true, data: { status: "HOLD", reasons: [], snapshot: { outbox_backlog: 0, owner_review_backlog: 0, replay_backlog: 0, sample_size: 0, replay_coverage_percent: 0, reviewed_attributions: 0, pending_attributions: 0 }, execution_controls: { auto_trade_enabled: false, trade_live_enabled: false }, observation: { elapsed_hours: 0, required_hours: 0 }, quality: { total_signals: 0, replay_coverage_percent: 0, reviewed_attributions: 0, excluded_signals: 0, verified_replay_events: 0, market_evidence_artifacts: 0, pending_attributions: 0 }, commercial_enabled: false } };

vi.mock("@/components/DashboardLayout", () => ({ default: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));
vi.mock("@/components/finance-ui", () => ({ KpiCard: ({ label, value }: { label: string; value: string }) => <div>{label}: {value}</div>, PreviewNotice: () => <div>Preview</div>, SectionTitle: ({ title }: { title: string }) => <h2>{title}</h2>, StatusPill: ({ value }: { value: string }) => <span>{value}</span> }));
vi.mock("@/components/ui/button", () => ({ Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => <button {...props}>{children}</button> }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/trpc", () => ({ trpc: { capitalguard: { admin: { overview: { useQuery: () => ({ data: { users: 0, channels: 0, pendingReviews: 0 } }) }, historicalReviewBatches: { useQuery: () => batchesState }, operationsFeed: { useQuery: () => ({ ...stableQuery, data: { events: [], summary: { critical: 0, warning: 0, total: 0 } } }) }, r5Readiness: { useQuery: () => stableQuery }, historicalTrustQuality: { useQuery: () => stableQuery }, historicalTrustReadiness: { useQuery: () => stableQuery }, reviewHistoricalBatch: { useMutation: () => noopMutation }, ingestHistoricalEvidence: { useMutation: () => noopMutation }, replayReviewedBatchFromBinance: { useMutation: () => ({ isPending: false, mutate: replayMutation }) } } } } }));

import Admin from "./Admin";

describe("Admin R4.1 Core read model", () => {
  afterEach(() => cleanup());
  beforeEach(() => { batchesState = { isLoading: false, isError: false, isSuccess: true, data: [], refetch: vi.fn() }; replayMutation = vi.fn(); vi.spyOn(window, "confirm").mockReturnValue(true); });
  it("renders an explicit empty owner queue without fallback data", () => { render(<Admin />); expect(screen.getByText("لا توجد دفعات تاريخية مؤهلة للمراجعة حالياً.")).toBeTruthy(); });
  it("does not expose manual signal or UTC replay inputs", () => { render(<Admin />); expect(screen.queryByText(/signal_id|UTC|رقم السجل التاريخي/i)).toBeNull(); });
  it("shows Replay only for an EVIDENCE_INGESTED batch and sends only its batchId", () => {
    batchesState = { isLoading: false, isError: false, isSuccess: true, refetch: vi.fn(), data: [
      { id: 7, ref: "HB-000007", status: "EVIDENCE_INGESTED", source_kind: "FORWARD", total_records: 1, accepted_records: 1, rejected_records: 0, replay_ready: true, replay_signal_count: 1, replay_block_reason: null },
      { id: 9, ref: "HB-000009", status: "EVIDENCE_INGESTED", source_kind: "FORWARD", total_records: 1, accepted_records: 1, rejected_records: 0, replay_ready: true, replay_signal_count: 1, replay_block_reason: null },
    ] };
    render(<Admin />);
    expect(screen.getAllByRole("button", { name: /تشغيل Replay Binance/ })).toHaveLength(2);
    fireEvent.click(screen.getAllByRole("button", { name: /تشغيل Replay Binance/ })[1]);
    expect(replayMutation).toHaveBeenCalledWith({ batchId: 9 });
    expect(screen.getAllByRole("button", { name: "جارٍ تشغيل replay…" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /تشغيل Replay Binance/ })).toHaveLength(1);
  });
  it("does not offer Binance replay when Core has no reviewed signals", () => { batchesState = { isLoading: false, isError: false, isSuccess: true, refetch: vi.fn(), data: [{ id: 52, ref: "HB-000052", status: "EVIDENCE_INGESTED", source_kind: "FORWARD", total_records: 1, accepted_records: 1, rejected_records: 0, replay_ready: false, replay_signal_count: 0, replay_block_reason: "HISTORICAL_REPLAY_NOT_READY" }] }; render(<Admin />); expect(screen.queryByRole("button", { name: /تشغيل Replay Binance/ })).toBeNull(); expect(screen.getByText("بانتظار تجهيز إشارات المراجعة")).toBeTruthy(); });
  it("renders an explicit Core error for the owner queue", () => { batchesState = { isLoading: false, isError: true, isSuccess: false, data: undefined, refetch: vi.fn() }; render(<Admin />); expect(screen.getByText("تعذر جلب طابور Core الحي. لم تُعرض أي بيانات بديلة؛ راجع اتصال API وصلاحية المالك ثم أعد المحاولة.")).toBeTruthy(); });
  it("renders an explicit Core loading state for the owner queue", () => { batchesState = { isLoading: true, isError: false, isSuccess: false, data: undefined, refetch: vi.fn() }; render(<Admin />); expect(screen.getByText("جارٍ تحميل طابور Core…")).toBeTruthy(); });
});
