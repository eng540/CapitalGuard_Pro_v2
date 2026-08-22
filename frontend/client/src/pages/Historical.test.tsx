import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let historicalState: Record<string, unknown>;

vi.mock("@/components/DashboardLayout", () => ({ default: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));
vi.mock("@/components/finance-ui", () => ({
  SectionTitle: ({ title }: { title: string }) => <h2>{title}</h2>,
  StatusPill: ({ value }: { value: string }) => <span>{value}</span>,
}));
vi.mock("@/lib/trpc", () => ({ trpc: { capitalguard: { historicalWallet: { useQuery: () => historicalState } } } }));

import Historical from "./Historical";

describe("Historical R4.1 Core read model", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    historicalState = { isLoading: false, isError: false, data: { as_of: "2026-08-23T00:00:00Z", items: [] } };
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
});
