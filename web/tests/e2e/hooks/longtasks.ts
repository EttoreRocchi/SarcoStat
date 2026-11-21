export const HOOK_SYMBOLS = ["__sarcostatTestLongTasks"] as const;

export interface LongTask {
  start: number;
  duration: number;
}

export function installLongTaskHook(): void {
  const tasks: LongTask[] = [];
  Object.defineProperty(globalThis, "__sarcostatTestLongTasks", { value: tasks, configurable: false, writable: false });
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      tasks.push({ start: entry.startTime, duration: entry.duration });
    }
  }).observe({ type: "longtask", buffered: true });
}
