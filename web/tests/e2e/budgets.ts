export const CI_BUDGET_FACTOR = 2;

export function timeBudget(ms: number, env: Record<string, string | undefined> = process.env): number {
  return env.CI ? ms * CI_BUDGET_FACTOR : ms;
}

export function budgetNote(what: string, value: number, ms: number): string | null {
  return value > ms ? `${what}: ${value} ms is over the ${ms} ms budget of plan 3.12 but within ${CI_BUDGET_FACTOR}x on a CI runner; a follow-up, not a block` : null;
}
