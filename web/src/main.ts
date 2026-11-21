import "./styles/app.css";

import { Store } from "./state/store.ts";
import { createApp } from "./view/app.ts";
import { createFatalScreen } from "./view/shell/fatal.ts";
import { createStatusLine } from "./view/shell/statusline.ts";
import { EngineClient } from "./worker/client.ts";
import { parseBuildInfo, type BuildInfo } from "./worker/protocol.ts";

async function readBuild(): Promise<BuildInfo> {
  const response = await fetch("./BUILD.json", { cache: "no-store", credentials: "same-origin" });
  if (!response.ok) {
    throw new Error(`BUILD.json answered ${response.status}`);
  }
  return parseBuildInfo(await response.json());
}

async function start(root: HTMLElement): Promise<void> {
  const store = new Store();
  const status = createStatusLine(document);
  const app = createApp(document, root, store, status);
  const fatal = createFatalScreen(document, root, () => window.location.reload());
  document.body.append(fatal.element);
  const stop = (code: Parameters<typeof fatal.show>[0], message: string): void => {
    status.showStopped();
    store.setEngine("stopped");
    fatal.show(code, message);
  };
  let build: BuildInfo;
  try {
    build = await readBuild();
  } catch (error) {
    stop("boot_failed", error instanceof Error ? error.message : String(error));
    return;
  }
  const worker = new Worker(new URL("./worker/py.worker.ts", import.meta.url), { type: "module" });
  const client = new EngineClient(
    worker,
    { build, base: new URL("./", document.baseURI).href },
    {
      boot: (stage) => status.showBoot(stage),
      ready: (identity) => {
        status.showIdentity(identity);
        store.setEngine("ready", identity);
      },
      fatal: stop,
    },
  );
  app.attachEngine(client);
  client.start();
}

const root = document.getElementById("app");
if (root) {
  void start(root);
}
