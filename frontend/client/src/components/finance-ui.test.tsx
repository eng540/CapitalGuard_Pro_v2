import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { KpiCard, PreviewNotice, StatusPill } from "./finance-ui";

describe("CapitalGuard finance UI", () => {
  it("renders a financial KPI without hiding its numeric context", () => {
    render(<KpiCard label="PnL المحقق" value="1,240 USDT" change="آخر 30 يومًا" icon={<span>icon</span>} />);
    expect(screen.getByText("PnL المحقق")).toBeTruthy();
    expect(screen.getByText("1,240 USDT")).toBeTruthy();
    expect(screen.getByText("آخر 30 يومًا")).toBeTruthy();
  });

  it("shows a readable temporal safety state", () => {
    render(<StatusPill value="OWNER_REVIEW_REQUIRED" />);
    expect(screen.getByText("بانتظار المراجعة")).toBeTruthy();
    expect(screen.getByTitle("OWNER_REVIEW_REQUIRED")).toBeTruthy();
  });

  it("distinguishes a live Core read model from preview data", () => {
    render(<PreviewNotice isLive />);
    expect(screen.getByText(/قراءة حية من CapitalGuard Core/)).toBeTruthy();
  });
});
