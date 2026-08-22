import React from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

let queryState: Record<string, unknown>;

vi.mock("@/components/DashboardLayout", () => ({ default: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));
vi.mock("@/components/CoreConnectionStatus", () => ({ default: () => <div>Core connected</div> }));
vi.mock("@/components/LiveSignalStatus", () => ({ default: () => <div>Signals live</div> }));
vi.mock("@/components/finance-ui", () => ({
  KpiCard: ({ label, value }: { label: string; value: string }) => <div>{label}: {value}</div>,
  SectionTitle: ({ title }: { title: string }) => <h2>{title}</h2>,
  StatusPill: ({ value }: { value: string }) => <span>{value}</span>,
}));
vi.mock("@/lib/trpc", () => ({
  trpc: { capitalguard: { core: { traderSnapshot: { useQuery: () => queryState } } } },
}));

import Workspace from "./Workspace";

describe("Workspace R4.1 live read model", () => {
  beforeEach(() => {
    queryState = {
      isLoading: false,
      isError: false,
      data: {
        ok: true,
        as_of: "2026-08-22T12:00:00Z",
        portfolio: { open_position_count: 1, positions: [{ id: 42, asset: "BTCUSDT", side: "LONG", entry: 70000, stop_loss: 69000, live_price: 70500, pnl_live_pct: 0.71, status: "ACTIVE", source_type: "RECOMMENDATION", targets: [{ price: 71000, percent: 100, hit: false }] }] },
        performance: { total_pnl_pct: 1.2, win_rate_pct: 75 },
        funnel: { activated: 3 },
      },
    };
  });

  it("renders owned live Core positions without preview portfolio data", () => {
    render(<Workspace />);
    expect(screen.getAllByText("UT-42").length).toBeGreaterThan(0);
    expect(screen.getAllByText("BTCUSDT").length).toBeGreaterThan(0);
    expect(screen.queryByText("إجمالي القيمة")).toBeNull();
  });

  it("renders an explicit empty state when Core reports no open positions", () => {
    queryState = { ...queryState, data: { ...(queryState.data as object), portfolio: { open_position_count: 0, positions: [] } } };
    render(<Workspace />);
    expect(screen.getByText("لا توجد مراكز مفتوحة في قراءة Core الحالية.")).toBeTruthy();
  });
});
