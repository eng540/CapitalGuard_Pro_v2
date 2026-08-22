import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let authState: Record<string, unknown>;

vi.mock("@/lib/tma", () => ({ getTelegramInitData: () => "raw-init-data-present" }));
vi.mock("@/lib/trpc", () => ({
  trpc: {
    capitalguard: { core: { health: { useQuery: () => ({ data: { status: "ok" } }) } } },
    auth: { me: { useQuery: () => authState } },
  },
}));

import CoreConnectionStatus from "./CoreConnectionStatus";

describe("CoreConnectionStatus Telegram verification", () => {
  afterEach(cleanup);

  beforeEach(() => {
    authState = { data: null, isLoading: false };
  });

  it("does not claim Telegram identity is ready from raw initData alone", () => {
    render(<CoreConnectionStatus />);
    expect(screen.getByText("جلسة Telegram غير موثقة")).toBeTruthy();
    expect(screen.queryByText("هوية Telegram جاهزة")).toBeNull();
  });

  it("shows secured only after the authenticated session carries a Telegram identity", () => {
    authState = { data: { openId: "telegram:123456" }, isLoading: false };
    render(<CoreConnectionStatus />);
    expect(screen.getByText("جلسة Telegram مؤمنة")).toBeTruthy();
  });
});
