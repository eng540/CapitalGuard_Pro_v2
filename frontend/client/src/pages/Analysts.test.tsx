import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let analystsState: Record<string, unknown>;
let comparisonState: Record<string, unknown>;

vi.mock("@/components/DashboardLayout", () => ({ default: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));
vi.mock("@/components/finance-ui", () => ({
  SectionTitle: ({ title }: { title: string }) => <h2>{title}</h2>,
  StatusPill: ({ value }: { value: string }) => <span>{value}</span>,
}));
vi.mock("@/lib/trpc", () => ({ trpc: { capitalguard: { discoverAnalysts: { useQuery: () => analystsState }, compareAnalysts: { useQuery: () => comparisonState } } } }));

import Analysts from "./Analysts";

describe("Analysts R4.1 Core read model", () => {
  afterEach(() => cleanup());
  beforeEach(() => {
    analystsState = { isLoading: false, isError: false, data: { items: [] } };
    comparisonState = { isLoading: false, isError: false, data: undefined };
  });

  it("renders an explicit empty state instead of analyst fallback records", () => {
    render(<Analysts />);
    expect(screen.getByText("لا توجد ملفات محللين عامة متاحة حالياً.")).toBeTruthy();
    expect(screen.queryByText("Analyst Demo")).toBeNull();
  });

  it("renders explicit Core loading and error states", () => {
    analystsState = { isLoading: true, isError: false, data: undefined };
    const first = render(<Analysts />);
    expect(screen.getByText("جارٍ تحميل المحللين من Core…")).toBeTruthy();
    first.unmount();
    analystsState = { isLoading: false, isError: true, data: undefined };
    render(<Analysts />);
    expect(screen.getByText("تعذر جلب بيانات المحللين الحية. لم تُعرض بيانات بديلة.")).toBeTruthy();
  });

  it("renders the live Core comparison after selecting two analysts", () => {
    const analyst = (code: string, totalPnlPct: number) => ({
      analyst_code: code,
      public_ref: null,
      public_name: code,
      sample_size: 40,
      win_rate_pct: 65,
      total_pnl_pct: totalPnlPct,
      max_drawdown_pct: 4,
      active_recommendations: 1,
      risk_exposure_pct: 2,
      eligible_for_ranking: true,
      freshness_days: 1,
    });
    analystsState = { isLoading: false, isError: false, data: { items: [analyst("AN-1", 8), analyst("AN-2", 15)] } };
    comparisonState = {
      isLoading: false,
      isError: false,
      data: {
        asOf: "2026-08-24T00:00:00Z",
        leader: { analystCode: "AN-2", winRate: 65, totalPnlPct: 15, maxDrawdownPct: 4, sampleSize: 40 },
        rows: [
          { analystCode: "AN-2", winRate: 65, totalPnlPct: 15, maxDrawdownPct: 4, sampleSize: 40 },
          { analystCode: "AN-1", winRate: 65, totalPnlPct: 8, maxDrawdownPct: 4, sampleSize: 40 },
        ],
        confidence: "SUFFICIENT_SAMPLE",
      },
    };

    render(<Analysts />);
    fireEvent.click(screen.getAllByText("قارن")[0]);
    fireEvent.click(screen.getAllByText("قارن")[0]);

    expect(screen.getByText("نتيجة المقارنة الحية")).toBeTruthy();
    expect(screen.getByText("الصدارة: AN-2")).toBeTruthy();
    expect(screen.getAllByText("الصدارة الحالية")).toHaveLength(1);
  });
});
