import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("Telegram Mini App bootstrap", () => {
  it("loads Telegram's official WebApp bridge before the React application", () => {
    const html = readFileSync(resolve(process.cwd(), "client/index.html"), "utf8");
    const bridgeIndex = html.indexOf("https://telegram.org/js/telegram-web-app.js?63");
    const appIndex = html.indexOf('type="module" src="/src/main.tsx"');

    expect(bridgeIndex).toBeGreaterThan(-1);
    expect(appIndex).toBeGreaterThan(bridgeIndex);
  });
});
