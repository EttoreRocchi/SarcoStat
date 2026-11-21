import fs from "node:fs";
import path from "node:path";

import { laneName, WEB_DIR } from "../../scripts/lane.mjs";
import { startServer } from "../../scripts/serve.mjs";

const VERSION_DIR = /^v\d[0-9A-Za-z.+-]*$/;

export function findAppFolder(siteRoot: string, wanted?: string): string {
  const candidates: string[] = [];
  for (const prefix of ["", "SarcoStat"]) {
    const dir = path.join(siteRoot, prefix);
    if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) {
      continue;
    }
    for (const name of fs.readdirSync(dir)) {
      if (VERSION_DIR.test(name) && fs.existsSync(path.join(dir, name, "index.html"))) {
        candidates.push(prefix === "" ? name : `${prefix}/${name}`);
      }
    }
  }
  const chosen = wanted ? candidates.filter((rel) => rel.endsWith(`/${wanted}`) || rel === wanted) : candidates;
  if (chosen.length !== 1) {
    throw new Error(
      `global_setup: expected one version folder v<version>/index.html under ${siteRoot} (or ${siteRoot}/SarcoStat), found ${JSON.stringify(candidates)}; build it with make site or set SITE_DIR and SITE_VERSION`,
    );
  }
  return chosen[0] as string;
}

export default async function globalSetup(): Promise<() => Promise<void>> {
  const siteRoot = path.resolve(process.env.SITE_DIR ?? path.join(WEB_DIR, "dist", laneName(), "site"));
  const appRel = findAppFolder(siteRoot, process.env.SITE_VERSION);
  const port = Number(process.env.PORT ?? 0);
  const started = await startServer({ root: siteRoot, port });
  const appUrl = `${started.url}${appRel}/`;
  process.env.SARCOSTAT_APP_URL = appUrl;
  process.env.SARCOSTAT_SITE_ROOT = siteRoot;
  process.env.SARCOSTAT_APP_DIR = path.join(siteRoot, appRel);
  console.log(`global_setup: serving ${siteRoot} at ${started.url}; app ${appUrl}`);
  return async () => {
    await new Promise<void>((resolve) => started.server.close(() => resolve()));
  };
}
