import type { Request } from "@playwright/test";

import { expect, test } from "./fixtures.ts";

test("the app loads everything from inside its own version folder, wherever that folder is served", async ({ page, engine, baseURL }) => {
  expect(baseURL, "SARCOSTAT_APP_URL from global_setup").toBeDefined();
  const app = new URL(baseURL as string);
  const urls: string[] = [];
  page.context().on("requestfinished", (request: Request) => {
    urls.push(request.url());
  });
  await page.goto("./");
  await engine.ready();
  const build = (await (await page.request.get(new URL("BUILD.json", app).href)).json()) as { version: string };
  expect(app.pathname).toMatch(new RegExp(`/v${build.version.replace(/[.+]/g, "\\$&")}/$`));
  const site = process.env.SARCOSTAT_SITE_ROOT ?? "";
  const deployed = app.pathname.startsWith("/SarcoStat/");
  test.info().annotations.push({ type: "served", description: `${app.pathname} from ${site}${deployed ? " (Pages layout)" : ""}` });
  console.log(`site: app at ${app.pathname} from ${site}; ${urls.length} requests, all checked against the folder`);
  expect(urls.length).toBeGreaterThan(5);
  const outside = urls.filter((url) => {
    const parsed = new URL(url);
    return parsed.origin !== app.origin || !parsed.pathname.startsWith(app.pathname);
  });
  expect(outside, `requests outside ${app.pathname}`).toEqual([]);
});
