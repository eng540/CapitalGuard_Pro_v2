import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let analystsState: Record<string, unknown>;

vi.mock("@/components/DashboardLayout", () => ({ default: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));
vi.mock("@/components/finance-ui", () => ({
  SectionTitle: ({ title }: { title: string }) => <h2>{title}</h2>,
  StatusPill: ({ value }: { value: string }) => <span>{value}</span>,
}));
vi.mock("@/lib/trpc", () => ({ trpc: { capitalguard: { discoverAnalysts: { useQuery: () => analystsState } } } }));

import Analysts from "./Analysts";

describe("Analysts R4.1 Core read model", () => {
  afterEach(() => cleanup());
  beforeEach(() => { analystsState = { isLoading: false, isError: false, data: { items: [] } }; });

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
});
