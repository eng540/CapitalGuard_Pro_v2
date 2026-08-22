import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let batchesState: Record<string, unknown>;
const noopMutation = { isPending: false, mutate: vi.fn() };
const stableQuery = { isLoading: false, isError: false, isSuccess: true, data: { status: "HOLD", reasons: [], snapshot: { outbox_backlog: 0, owner_review_backlog: 0, replay_backlog: 0, sample_size: 0, replay_coverage_percent: 0, reviewed_attributions: 0, pending_attributions: 0 }, execution_controls: { auto_trade_enabled: false, trade_live_enabled: false }, observation: { elapsed_hours: 0, required_hours: 0 }, quality: { total_signals: 0, replay_coverage_percent: 0, reviewed_attributions: 0, excluded_signals: 0, verified_replay_events: 0, market_evidence_artifacts: 0, pending_attributions: 0 }, commercial_enabled: false } };

vi.mock("@/components/DashboardLayout", () => ({ default: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));
vi.mock("@/components/finance-ui", () => ({ KpiCard: ({ label, value }: { label: string; value: string }) => <div>{label}: {value}</div>, PreviewNotice: () => <div>Preview</div>, SectionTitle: ({ title }: { title: string }) => <h2>{title}</h2>, StatusPill: ({ value }: { value: string }) => <span>{value}</span> }));
vi.mock("@/components/ui/button", () => ({ Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => <button {...props}>{children}</button> }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/lib/trpc", () => ({ trpc: { capitalguard: { admin: { overview: { useQuery: () => ({ data: { users: 0, channels: 0, pendingReviews: 0 } }) }, historicalReviewBatches: { useQuery: () => batchesState }, operationsFeed: { useQuery: () => ({ ...stableQuery, data: { events: [], summary: { critical: 0, warning: 0, total: 0 } } }) }, r5Readiness: { useQuery: () => stableQuery }, historicalTrustQuality: { useQuery: () => stableQuery }, historicalTrustReadiness: { useQuery: () => stableQuery }, reviewHistoricalBatch: { useMutation: () => noopMutation }, ingestHistoricalEvidence: { useMutation: () => noopMutation }, replayHistoricalSignalFromBinance: { useMutation: () => noopMutation } } } } }));

import Admin from "./Admin";

describe("Admin R4.1 Core read model", () => {
  afterEach(() => cleanup());
  beforeEach(() => { batchesState = { isLoading: false, isError: false, isSuccess: true, data: [], refetch: vi.fn() }; });
  it("renders an explicit empty owner queue without fallback data", () => { render(<Admin />); expect(screen.getByText("لا توجد دفعات تاريخية مؤهلة للمراجعة حالياً.")).toBeTruthy(); });
  it("renders an explicit Core error for the owner queue", () => { batchesState = { isLoading: false, isError: true, isSuccess: false, data: undefined, refetch: vi.fn() }; render(<Admin />); expect(screen.getByText("تعذر جلب طابور Core الحي. لم تُعرض أي بيانات بديلة؛ راجع اتصال API وصلاحية المالك ثم أعد المحاولة.")).toBeTruthy(); });
  it("renders an explicit Core loading state for the owner queue", () => { batchesState = { isLoading: true, isError: false, isSuccess: false, data: undefined, refetch: vi.fn() }; render(<Admin />); expect(screen.getByText("جارٍ تحميل طابور Core…")).toBeTruthy(); });
});
