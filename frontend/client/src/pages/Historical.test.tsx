import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let historicalState: Record<string, unknown>;
let intakeListState: Record<string, unknown>;
let intakeDetailState: Record<string, unknown>;
let intakeReportState: Record<string, unknown>;

vi.mock("@/components/DashboardLayout", () => ({ default: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));
vi.mock("@/components/finance-ui", () => ({
  SectionTitle: ({ title }: { title: string }) => <h2>{title}</h2>,
  StatusPill: ({ value }: { value: string }) => <span>{value}</span>,
}));
vi.mock("@/components/ui/button", () => ({ Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => <button {...props}>{children}</button> }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/trpc", () => ({ trpc: { capitalguard: {
  historicalWallet: { useQuery: () => historicalState },
  historicalIntakeList: { useQuery: () => intakeListState },
  historicalIntake: { useMutation: () => ({ isPending: false, mutate: vi.fn() }) },
  historicalIntakeDetail: { useQuery: () => intakeDetailState },
  historicalIntakeReport: { useQuery: () => intakeReportState },
} } }));

import Historical, { parseIntakeText } from "./Historical";
import { IntakeBatchView } from "@/components/ForwardResultsInspector";

describe("Historical R4.1 Core read model and 1..N intake", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    historicalState = { isLoading: false, isError: false, data: { as_of: "2026-08-23T00:00:00Z", items: [] } };
    intakeListState = { isLoading: false, isError: false, data: { batches: [] } };
    intakeDetailState = { isLoading: false, isError: false, data: undefined };
    intakeReportState = { isLoading: false, isError: false, data: undefined, refetch: vi.fn() };
  });

  it("renders an explicit empty state instead of historical fallback data", () => {
    render(<Historical />);
    expect(screen.getByText("لا توجد إشارات تاريخية منسوبة إلى هذا الحساب حالياً.")).toBeTruthy();
    expect(screen.queryByText("REC-DEMO")).toBeNull();
  });

  it("renders explicit Core loading and error states", () => {
    historicalState = { isLoading: true, isError: false, data: undefined };
    const first = render(<Historical />);
    expect(screen.getByText("جارٍ تحميل السجل التاريخي من Core…")).toBeTruthy();
    first.unmount();
    historicalState = { isLoading: false, isError: true, data: undefined };
    render(<Historical />);
    expect(screen.getByText("تعذر جلب السجل التاريخي الحي. لم تُعرض دفعات عرض بديلة.")).toBeTruthy();
  });

  it("shows extracted fields and explains what Core did for a single message", () => {
    intakeDetailState = { isLoading: false, isError: false, data: { batch: { id: 7, ref: "HB-000007", status: "REVIEW_REQUIRED", source_kind: "MANUAL_ADMIN_IMPORT", total_records: 1, accepted_records: 1, rejected_records: 0, created_at: "2026-08-26T12:00:00Z", items: [{ id: 1, order: 1, item_key: "item-1", status: "STAGED", semantic_status: "SUCCESS", parse_status: "PARSED", source_verification: "UNVERIFIED", source_timestamp: "2026-08-26T11:55:00Z", raw_text: "#BTCUSDT LONG Entry 100 SL 95 TP1 105", missing_fields: [], conflicting_fields: [], canonical: { asset: "BTCUSDT", side: "LONG", entry: 100, stop_loss: 95, targets: [{ price: 105, close_percent: 100 }] }, rejection_reason: null }] } } };
    intakeReportState = { isLoading: false, isError: false, data: { report: { counts: { evidence_records: 0, historical_signals: 0, replay_events: 0, verified_replay_events: 0 }, readiness: { commercial_enabled: false }, next_action: "OWNER_REVIEW", signals: [] } }, refetch: vi.fn() };
    const view = render(<IntakeBatchView batch={(intakeDetailState.data as { batch: Parameters<typeof IntakeBatchView>[0]["batch"] }).batch} report={(intakeReportState.data as { report: Parameters<typeof IntakeBatchView>[0]["report"] }).report} onRefresh={vi.fn()} />);
    expect(view.getByText("ماذا عمل النظام؟")).toBeTruthy();
    expect(view.getByText("عرض البيانات المستخرجة")).toBeTruthy();
    expect(view.getByText(/لا يتم إنشاء توصية أو صفقة حية/)).toBeTruthy();
  });

  it("maps one message, separated multiple messages, and Telegram export to one 1..N contract", () => {
    const single = parseIntakeText("#BTCUSDT LONG\nEntry: 100\nSL: 95\nTP1: 105", "PASTE");
    expect(single.items).toHaveLength(1);
    const multiple = parseIntakeText("signal one\n---\nupdate two", "PASTE");
    expect(multiple.items).toHaveLength(2);
    const exported = parseIntakeText(JSON.stringify({ id: -100123, messages: [{ id: 4, date: "2025-01-01T10:00:00+00:00", text: "#ETHUSDT SHORT" }] }), "TELEGRAM_EXPORT");
    expect(exported.sourceKind).toBe("TELEGRAM_EXPORT");
    expect(exported.items[0].sourceMessageId).toBe(4);
    expect(exported.items[0].sourceChatId).toBe(-100123);
  });
});
