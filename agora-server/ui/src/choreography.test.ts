import { describe, expect, it, vi } from "vitest";

import {
  autoChoreograph,
  simplifyKeyframes,
  buildSequence,
  keyframesFromSequence,
  makeKeyframe,
  readSequenceText,
} from "./choreography";

describe("choreography conversion", () => {
  it("builds firmware frames from timeline keyframes and clamps servo limits", () => {
    const sequence = buildSequence(
      [
        makeKeyframe({
          t: 0,
          yaw: 200,
          pitch: 100,
          mouth: 45,
          eye: 90,
          speed: 700,
          color: "#ff8c00",
        }),
        makeKeyframe({
          t: 0.42,
          yaw: -200,
          pitch: -10,
          mouth: 5,
          eye: 80,
          speed: 350,
          color: "#00ccff",
        }),
      ],
      1.2,
    );

    expect(sequence).toEqual([
      {
        leftEye: { x: 0, y: 0, rotation: 0, weight: 90, size: 0 },
        rightEye: { x: 0, y: 0, rotation: 0, weight: 90, size: 0 },
        mouth: { x: 0, y: 0, rotation: 0, weight: 45, size: 0 },
        yawServo: { angle: 1280, speed: 700 },
        pitchServo: { angle: 870, speed: 700 },
        durationMs: 420,
        leftRgbColor: "#ff8c00",
        rightRgbColor: "#ff8c00",
      },
      {
        leftEye: { x: 0, y: 0, rotation: 0, weight: 80, size: 0 },
        rightEye: { x: 0, y: 0, rotation: 0, weight: 80, size: 0 },
        mouth: { x: 0, y: 0, rotation: 0, weight: 5, size: 0 },
        yawServo: { angle: -1280, speed: 350 },
        pitchServo: { angle: -100, speed: 350 },
        durationMs: 780,
        leftRgbColor: "#00ccff",
        rightRgbColor: "#00ccff",
      },
    ]);
  });

  it("reconstructs timeline keyframes from firmware frames with cumulative time", () => {
    const randColor = vi.fn(() => "#123456");

    expect(
      keyframesFromSequence(
        [
          {
            leftEye: { weight: 66 },
            mouth: { weight: 25 },
            yawServo: { angle: 310, speed: 500 },
            pitchServo: { angle: 120, speed: 500 },
            durationMs: 300,
            leftRgbColor: "#abcdef",
          },
          {
            yawServo: { angle: -80, speed: 600 },
            pitchServo: { angle: 0, speed: 600 },
            durationMs: 450,
          },
        ],
        randColor,
      ),
    ).toEqual([
      makeKeyframe({
        t: 0,
        yaw: 31,
        pitch: 12,
        mouth: 25,
        eye: 66,
        speed: 500,
        color: "#abcdef",
      }),
      makeKeyframe({
        t: 0.3,
        yaw: -8,
        pitch: 0,
        mouth: 0,
        eye: 100,
        speed: 600,
        color: "#123456",
      }),
    ]);
  });

  it("keeps generated motion within servo limits (amplitude, slew, dwell, speed)", () => {
    // a long steady beat grid so units span ~4 beats apiece
    const beats = Array.from({ length: 32 }, (_, i) => Number((i * 0.4).toFixed(2)));
    const keyframes = autoChoreograph({
      beats,
      lyrics: [{ start: 2.0, end: 5.0 }],
      duration: 13,
      randColor: () => "#123456",
      density: "bar",
    });

    expect(keyframes.length).toBeGreaterThan(0);
    for (let i = 0; i < keyframes.length; i += 1) {
      const kf = keyframes[i];
      expect(Math.abs(kf.yaw)).toBeLessThanOrEqual(70);
      expect(Math.abs(kf.pitch)).toBeLessThanOrEqual(28);
      expect(kf.speed).toBeGreaterThanOrEqual(250);
      expect(kf.speed).toBeLessThanOrEqual(1000); // firmware speed range
      if (i > 0) {
        const dt = kf.t - keyframes[i - 1].t;
        expect(dt).toBeGreaterThanOrEqual(0.33 - 1e-6); // minimum dwell
        // slew limit: angle change can't exceed what the spring can track
        expect(Math.abs(kf.yaw - keyframes[i - 1].yaw)).toBeLessThanOrEqual(110 * dt + 1e-6);
        expect(Math.abs(kf.pitch - keyframes[i - 1].pitch)).toBeLessThanOrEqual(80 * dt + 1e-6);
      }
    }
  });

  it("opens the mouth only while a lyric line is being sung", () => {
    const beats = Array.from({ length: 32 }, (_, i) => Number((i * 0.4).toFixed(2)));
    const keyframes = autoChoreograph({
      beats,
      lyrics: [{ start: 2.0, end: 5.0 }],
      duration: 13,
      randColor: () => "#123456",
      density: "bar",
    });
    for (const kf of keyframes) {
      const singing = kf.t >= 2.0 - 0.02 && kf.t < 5.0;
      if (kf.mouth > 0) expect(singing).toBe(true);
    }
  });

  it("simplifies away frames that linear interpolation already covers (Douglas-Peucker)", () => {
    // a linear ramp (t0..3) then a flat hold (t3..5): midpoints are redundant
    const kf = (t: number, yaw: number) => makeKeyframe({ t, yaw, pitch: 0, mouth: 0 });
    const result = simplifyKeyframes(
      [kf(0, 0), kf(1, 10), kf(2, 20), kf(3, 30), kf(4, 30), kf(5, 30)],
      9,
      9,
      35,
    );
    // collinear ramp points and the held tail collapse to the turn at t=3
    expect(result.map((k) => k.t)).toEqual([0, 3, 5]);
  });

  it("never drops the endpoints and keeps tiny sets intact", () => {
    const two = [makeKeyframe({ t: 0 }), makeKeyframe({ t: 1, yaw: 50 })];
    expect(simplifyKeyframes(two)).toHaveLength(2);
  });

  it("returns nothing when there is no beat or lyric data", () => {
    expect(
      autoChoreograph({ beats: [], lyrics: [], duration: 0, randColor: () => "#000000" }),
    ).toEqual([]);
  });

  it("accepts only JSON arrays in the editor", () => {
    expect(readSequenceText("[{\"durationMs\":80}]")).toEqual([{ durationMs: 80 }]);
    expect(() => readSequenceText("{\"durationMs\":80}")).toThrow("dance.json 必须是数组");
  });
});
