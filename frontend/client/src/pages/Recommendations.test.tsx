import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mutate = vi.fn();
const cancelMutate = vi.fn();
const partialMutate = vi.fn();
let recommendationState: Record<string, unknown>;
let detailState: Record<string, unknown>;

vi.mock("@/components/DashboardLayout", () => ({ default: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));
vi.mock("@/components/finance-ui", () => ({
  SectionTitle: ({ title }: { title: string }) => <h2>{title}</h2>,
  StatusPill: ({ value }: { value: string }) => <span>{value}</span>,
}));
vi.mock("@/lib/trpc", () => ({
  trpc: {
    capitalguard: {
      recommendations: { useQuery: () => recommendationState },
      recommendationDetail: { useQuery: () => detailState },
      trader: {
        closeUserTrade: { useMutation: () => ({ mutate, isPending: false }) },
        cancelPendingUserTrade: { useMutation: () => ({ mutate: cancelMutate, isPending: false }) },
        partialCloseUserTrade: { useMutation: () => ({ mutate: partialMutate, isPending: false }) },
      },
    },
  },
}));

import Recommendations from "./Recommendations";

const activeItem = {
  entity_type: "USER_TRADE",
  public_ref: "USR-000012/T-0003",
  display_ref: "USR-000012/T-0003",
  asset: "BTCUSDT",
  side: "LONG",
  market: "Futures",
  entry: 70000,
  stop_loss: 69000,
  targets: [],
  status: "ACTIVATED",
  source_type: "TRACKED_RECOMMENDATION",
  source: null,
  created_at: null,
  activated_at: null,
  closed_at: null,
  open_size_percent: 100,
  timeline: [],
};

describe("UserTrade close surface", () => {
  afterEach(cleanup);

  beforeEach(() => {
    mutate.mockReset();
    cancelMutate.mockReset();
    partialMutate.mockReset();
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: { randomUUID: () => "close-command-key-0001" } });
    recommendationState = { isLoading: false, isError: false, data: { as_of: "2026-08-22T12:00:00Z", items: [activeItem] }, refetch: vi.fn() };
    detailState = { isLoading: false, isError: false, data: { as_of: "2026-08-22T12:00:00Z", schema_version: "2026-08-22.1", item: activeItem }, refetch: vi.fn() };
  });

  it("requires explicit confirmation before close and sends only public_ref with an idempotency key", () => {
    render(<Recommendations />);
    fireEvent.click(screen.getByText("عرض التفاصيل"));
    expect(screen.getByText("طلب إغلاق يدوي كامل")).toBeTruthy();
    expect(mutate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("طلب إغلاق يدوي كامل"));
    expect(screen.getByText("تأكيد الإغلاق الكامل")).toBeTruthy();
    fireEvent.click(screen.getByText("تأكيد الإغلاق الكامل"));
    expect(mutate).toHaveBeenCalledWith({ publicRef: "USR-000012/T-0003", idempotencyKey: "close-command-key-0001" });
  });

  it("does not offer a duplicate close action for a Core-closed record", () => {
    detailState = { ...detailState, data: { ...(detailState.data as object), item: { ...activeItem, status: "CLOSED" } } };
    render(<Recommendations />);
    fireEvent.click(screen.getByText("عرض التفاصيل"));
    expect(screen.queryByText("طلب إغلاق يدوي كامل")).toBeNull();
    expect(screen.getByText("هذا السجل طرفي بالفعل؛ لا يظهر أمر إغلاق أو إلغاء مكرر.")).toBeTruthy();
  });

  it("offers cancellation without a market price for a pending Core record", () => {
    detailState = { ...detailState, data: { ...(detailState.data as object), item: { ...activeItem, status: "WATCHLIST" } } };
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: { randomUUID: () => "cancel-command-key-0001" } });
    render(<Recommendations />);
    fireEvent.click(screen.getByText("عرض التفاصيل"));
    expect(screen.getByText("طلب إلغاء التتبع")).toBeTruthy();
    expect(screen.queryByText("طلب إغلاق يدوي كامل")).toBeNull();
    fireEvent.click(screen.getByText("طلب إلغاء التتبع"));
    fireEvent.click(screen.getByText("تأكيد الإلغاء"));
    expect(cancelMutate).toHaveBeenCalledWith({ publicRef: "USR-000012/T-0003", idempotencyKey: "cancel-command-key-0001" });
  });

  it("requires an in-range percentage and explicit confirmation before partial close", () => {
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: { randomUUID: () => "partial-command-key-001" } });
    render(<Recommendations />);
    fireEvent.click(screen.getByText("عرض التفاصيل"));
    fireEvent.change(screen.getByLabelText("نسبة الإغلاق الجزئي"), { target: { value: "25" } });
    fireEvent.click(screen.getByText("طلب إغلاق جزئي"));
    expect(partialMutate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("تأكيد الإغلاق الجزئي"));
    expect(partialMutate).toHaveBeenCalledWith({ publicRef: "USR-000012/T-0003", closePercent: 25, idempotencyKey: "partial-command-key-001" });
  });

  it("rejects a full or oversized percentage in the client before an action is requested", () => {
    render(<Recommendations />);
    fireEvent.click(screen.getByText("عرض التفاصيل"));
    fireEvent.change(screen.getByLabelText("نسبة الإغلاق الجزئي"), { target: { value: "100" } });
    fireEvent.click(screen.getByText("طلب إغلاق جزئي"));
    expect(screen.getByText("أدخل نسبة موجبة أقل من المتبقي (100%). الإغلاق الكامل له إجراء منفصل.")).toBeTruthy();
    expect(partialMutate).not.toHaveBeenCalled();
  });
});
