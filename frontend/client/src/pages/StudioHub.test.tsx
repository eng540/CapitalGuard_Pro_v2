import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/components/DashboardLayout", () => ({ default: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));

import StudioHub from "./StudioHub";

describe("Studio hub", () => {
  it("presents advanced tasks without mixing them into the trader flow", () => {
    render(<StudioHub />);
    expect(screen.getByRole("heading", { name: "الاستوديو والعمليات" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /إنشاء ونشر توصية/ }).getAttribute("href")).toBe("/analyst/workspace");
    expect(screen.getByRole("link", { name: /مركز المراجعة/ }).getAttribute("href")).toBe("/admin");
    expect(screen.getByText(/لا تستخدم هذه المساحة لاستقبال رسالة أو متابعة صفقة/)).toBeTruthy();
  });
});
