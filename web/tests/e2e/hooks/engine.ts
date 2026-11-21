export const HOOK_SYMBOLS = ["__sarcostatTestEngine"] as const;

export { installRecorder as installEngineHook } from "./recorder.ts";
export type { EngineHook, EngineHookConfig, EngineRecord, EngineResponse } from "./recorder.ts";
