import { afterAll, describe, expect, it } from "vitest";

const previousVercel = process.env.VERCEL;
process.env.VERCEL = "1";

const modulePromise = import("./[...path]");

afterAll(() => {
  if (previousVercel === undefined) delete process.env.VERCEL;
  else process.env.VERCEL = previousVercel;
});

describe("Vercel catch-all Function", () => {
  it("exports a request handler without starting a persistent listener", async () => {
    const module = await modulePromise;
    expect(typeof module.default).toBe("function");
  });
});
