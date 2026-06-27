import { describe, expect, it, vi } from "vitest";

import {
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

  it("accepts only JSON arrays in the editor", () => {
    expect(readSequenceText("[{\"durationMs\":80}]")).toEqual([{ durationMs: 80 }]);
    expect(() => readSequenceText("{\"durationMs\":80}")).toThrow("dance.json 必须是数组");
  });
});
