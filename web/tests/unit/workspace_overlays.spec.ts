import { describe, expect, it } from "vitest";

import type { ModeState } from "../../src/state/store.ts";
import { BADGE_GAP, kindOfMark, kindOfStroke, overlayModel } from "../../src/view/workspace/overlays.ts";
import { scaleBarModel } from "../../src/view/workspace/scalebar.ts";

const VIEW = { zoom: 4, offsetX: 100, offsetY: 20 };

function state(partial: Partial<ModeState>): ModeState {
  return {
    controls: { can_confirm: false, can_undo: true, can_skip_m_line: false, can_delete: false },
    image: { file_name: "a.png", sha256: "0".repeat(64), width_px: 64, height_px: 48 },
    items: [],
    mode: "shape",
    not_exported: false,
    notice: null,
    pending: { marks: [], strokes: [] },
    settings: { click_perp_ratio: 1, click_sigma_m: null, click_sigma_z: null, pixel_size: 1, reference_direction_deg: null, unit: "px" },
    step: null,
    ...partial,
  } as ModeState;
}

const local = (x: number, y: number): { x: number; y: number } => ({ x: VIEW.offsetX + VIEW.zoom * (x + 0.5), y: VIEW.offsetY + VIEW.zoom * (y + 0.5) });

describe("overlay model", () => {
  it("draws the pending marks and strokes at display + 0.5 with letter badges outside each line", () => {
    const model = overlayModel(
      state({
        pending: {
          marks: [
            { role: "A", x: 10, y: 5 },
            { role: "B", x: 10, y: 40 },
            { role: "C", x: 30, y: 7 },
          ],
          strokes: [{ role: "z1", p1: [10, 5], p2: [10, 40] }],
        },
      }),
      VIEW,
    );
    expect(model).toHaveLength(1);
    const group = model[0];
    expect(group?.pending).toBe(true);
    expect(group?.dots).toEqual([
      { kind: "z", role: "A", at: local(10, 5) },
      { kind: "z", role: "B", at: local(10, 40) },
      { kind: "m", role: "C", at: local(30, 7) },
    ]);
    expect(group?.lines).toEqual([{ kind: "z", role: "z1", from: local(10, 5), to: local(10, 40) }]);
    expect(group?.badges.map((badge) => [badge.letter, badge.kind])).toEqual([
      ["A", "z"],
      ["B", "z"],
      ["C", "m"],
    ]);
    expect(group?.badges[0]?.at).toEqual({ x: local(10, 5).x, y: local(10, 5).y - BADGE_GAP });
    expect(group?.badges[1]?.at).toEqual({ x: local(10, 40).x, y: local(10, 40).y + BADGE_GAP });
    expect(group?.sides).toEqual([]);
    expect(group?.tags).toEqual([]);
  });

  it("draws confirmed sarcomeres with their sides and number, without badges, and nothing without an image or a view", () => {
    const marks = [
      { role: "A", x: 10, y: 5 },
      { role: "B", x: 10, y: 40 },
      { role: "C", x: 30, y: 7 },
      { role: "D", x: 30, y: 38 },
      { role: "E", x: 50, y: 5 },
      { role: "F", x: 50, y: 40 },
    ] as const;
    const item = {
      item_id: "sarcomere-1",
      type: "shape",
      sarcomere_id: 1,
      marks: [...marks],
      strokes: [
        { role: "z1", p1: [10, 5], p2: [10, 40] },
        { role: "m", p1: [30, 7], p2: [30, 38] },
        { role: "z2", p1: [50, 5], p2: [50, 40] },
      ],
    } as ModeState["items"][number];
    const model = overlayModel(state({ items: [item] }), VIEW);
    expect(model).toHaveLength(1);
    expect(model[0]?.pending).toBe(false);
    expect(model[0]?.badges).toEqual([]);
    expect(model[0]?.lines.map((line) => [line.role, line.kind])).toEqual([
      ["z1", "z"],
      ["m", "m"],
      ["z2", "z"],
    ]);
    expect(model[0]?.sides).toEqual([
      [local(10, 5), local(30, 7), local(50, 5)],
      [local(10, 40), local(30, 38), local(50, 40)],
    ]);
    expect(model[0]?.tags).toEqual([{ text: "1", at: { x: local(30, 0).x, y: (local(0, 5).y * 2 + local(0, 7).y + local(0, 38).y + local(0, 40).y * 2) / 6 } }]);
    expect(overlayModel(state({ items: [item] }), null)).toEqual([]);
    expect(overlayModel(state({ items: [item], image: null }), VIEW)).toEqual([]);
    expect(overlayModel(null, VIEW)).toEqual([]);
  });

  it("gives every role its palette entry", () => {
    expect(["A", "B", "C", "D", "E", "F", "chain", "myofibril_1", "myofibril_2"].map(kindOfMark)).toEqual(["z", "z", "m", "m", "z", "z", "chain", "f1", "f2"]);
    expect(["z1", "m", "z2", "chain", "myofibril_1", "myofibril_2"].map(kindOfStroke)).toEqual(["z", "m", "z", "chain", "f1", "f2"]);
  });
});

describe("scale bar", () => {
  it("is hidden without a scale", () => {
    expect(scaleBarModel(null, 1)).toBeNull();
    expect(scaleBarModel({ pixel_size: 1, unit: "px" }, 1)).toBeNull();
    expect(scaleBarModel({ pixel_size: 0, unit: "nm" }, 1)).toBeNull();
  });

  it("shows 1 µm when it fits and a round length otherwise", () => {
    expect(scaleBarModel({ pixel_size: 6.62, unit: "nm" }, 1.1)).toEqual({ length: 1, unit: "µm", label: "1 µm", width: 1.1 / 0.00662 });
    expect(scaleBarModel({ pixel_size: 6.62, unit: "nm" }, 4)).toMatchObject({ length: 200, unit: "nm", label: "200 nm" });
    expect(scaleBarModel({ pixel_size: 6.62, unit: "nm" }, 0.1)).toMatchObject({ length: 10, unit: "µm", label: "10 µm" });
    expect(scaleBarModel({ pixel_size: 0.5, unit: "µm" }, 0.05)).toMatchObject({ length: 1, unit: "mm", label: "1 mm" });
    expect(scaleBarModel({ pixel_size: 0.02, unit: "um" }, 2)).toMatchObject({ length: 1, unit: "µm" });
    expect(scaleBarModel({ pixel_size: 3, unit: "furlong" }, 1)).toMatchObject({ length: 500, unit: "furlong", label: "500 furlong" });
    for (const zoom of [0.03, 0.5, 1, 3, 32]) {
      const model = scaleBarModel({ pixel_size: 6.62, unit: "nm" }, zoom);
      expect(model?.width).toBeGreaterThanOrEqual(48);
      expect(model?.width).toBeLessThanOrEqual(240);
    }
  });
});
