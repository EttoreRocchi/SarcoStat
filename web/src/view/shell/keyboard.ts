export type KeyHandler = (event: KeyboardEvent) => void;

export interface Keyboard {
  push(handler: KeyHandler): () => void;
  readonly captured: boolean;
}

export function installKeyboard(doc: Document, base: KeyHandler, blocked: () => boolean = () => false): Keyboard {
  const layers: KeyHandler[] = [];
  doc.addEventListener("keydown", (event) => {
    if (blocked()) {
      return;
    }
    const top = layers[layers.length - 1];
    (top ?? base)(event);
  });
  return {
    push(handler) {
      layers.push(handler);
      return () => {
        const at = layers.lastIndexOf(handler);
        if (at >= 0) {
          layers.splice(at, 1);
        }
      };
    },
    get captured() {
      return layers.length > 0;
    },
  };
}
