import { createPlaceholderMode, type PlaceholderView } from "./placeholder.ts";

export function createStraightnessMode(doc: Document): PlaceholderView {
  return createPlaceholderMode(doc, "straightness");
}
