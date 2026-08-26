import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mutate = vi.fn();
const batchMutate = vi.fn();

vi.mock("@/components/DashboardLayout", () => ({ default: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }));
vi.mock("@/lib/trpc", () => ({
  trpc: { capitalguard: { smartAnalyze: { useMutation: () => ({ mutate, isPending: false, data: undefined, error: null }) }, historicalIntake: { useMutation: () => ({ mutate: batchMutate, isPending: false, data: undefined, error: null }) }, historicalIntakeDetail: { useQuery: () => ({ data: undefined, isLoading: false, isError: false, refetch: vi.fn() }) }, historicalIntakeReport: { useQuery: () => ({ data: undefined, isLoading: false, isError: false, refetch: vi.fn() }) } } },
}));

import SmartDropzone from "./SmartDropzone";

describe("Smart Dropzone interaction", () => {
  beforeEach(() => { mutate.mockReset(); batchMutate.mockReset(); });

  it("enables analysis only after a usable message and sends text to the server mutation", async () => {
    const user = userEvent.setup();
    render(<SmartDropzone />);
    const button = screen.getByRole("button", { name: "حلّل الرسالة" });
    expect(button.hasAttribute("disabled")).toBe(true);
    await user.type(screen.getByPlaceholderText("الصق الرسالة هنا…"), "BTCUSDT LONG Entry 70000 Stop 69500");
    expect(button.hasAttribute("disabled")).toBe(false);
    await user.click(button);
    expect(mutate).toHaveBeenCalledWith({ text: "BTCUSDT LONG Entry 70000 Stop 69500" });
  });
});
