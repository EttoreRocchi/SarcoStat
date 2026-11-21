import { createPlaceholderMode, type PlaceholderView } from "./placeholder.ts";

export function createPlannerMode(doc: Document): PlaceholderView {
  return createPlaceholderMode(doc, "planner");
}
