import { createPlaceholderMode, type PlaceholderView } from "./placeholder.ts";

export function createRegisterMode(doc: Document): PlaceholderView {
  return createPlaceholderMode(doc, "register");
}
