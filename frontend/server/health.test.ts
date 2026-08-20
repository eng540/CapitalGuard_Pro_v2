import { once } from "node:events";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { afterAll, describe, expect, it } from "vitest";

const previousVercel = process.env.VERCEL;
process.env.VERCEL = "1";
const modulePromise = import("./_core/index");

afterAll(() => {
  if (previousVercel === undefined) delete process.env.VERCEL;
  else process.env.VERCEL = previousVercel;
});

describe("Web healthcheck", () => {
  it("returns a public liveness response without requiring an authenticated session", async () => {
    const { createCapitalGuardApp } = await modulePromise;
    const server = createServer(createCapitalGuardApp());
    server.listen(0, "127.0.0.1");
    await once(server, "listening");

    try {
      const { port } = server.address() as AddressInfo;
      const response = await fetch(`http://127.0.0.1:${port}/health`);
      await expect(response.json()).resolves.toEqual({ status: "ok", service: "capitalguard-web" });
      expect(response.status).toBe(200);
    } finally {
      server.close();
      await once(server, "close");
    }
  });
});
