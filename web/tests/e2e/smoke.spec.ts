import { expect, test } from "./fixtures.ts";

test("the app page loads from 127.0.0.1 with its own CSP", async ({ page, baseURL }) => {
  expect(baseURL, "SARCOSTAT_APP_URL from global_setup").toMatch(/^http:\/\/127\.0\.0\.1:\d+\//);
  const response = await page.goto("./");
  expect(response?.status()).toBe(200);
  await expect(page).toHaveTitle("SarcoStat");
  const csp = await page.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute("content");
  expect(csp).toContain("default-src 'self'");
  expect(csp).not.toContain("wasm-unsafe-eval");
  await expect(page.locator("#app")).not.toBeEmpty();
});
