export const YAW_LIM = 1280;
export const PITCH_MIN = -400;
export const PITCH_MAX = 870;

export type Keyframe = {
  t: number;
  yaw: number;
  pitch: number;
  mouth: number;
  eye: number;
  speed: number;
  color: string;
};

type Feature = {
  x?: number;
  y?: number;
  rotation?: number;
  weight?: number;
  size?: number;
};

type Servo = {
  angle?: number;
  speed?: number;
};

export type DanceFrame = {
  leftEye?: Feature;
  rightEye?: Feature;
  mouth?: Feature;
  yawServo?: Servo;
  pitchServo?: Servo;
  durationMs?: number;
  leftRgbColor?: string;
  rightRgbColor?: string;
};

export function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

export function makeKeyframe(overrides: Partial<Keyframe> = {}): Keyframe {
  return {
    t: 0,
    yaw: 0,
    pitch: 0,
    mouth: 0,
    eye: 100,
    speed: 700,
    color: "#ff8c00",
    ...overrides,
  };
}

export function sortKeyframes(keyframes: Keyframe[]): Keyframe[] {
  return [...keyframes].sort((a, b) => a.t - b.t);
}

export function buildSequence(keyframes: Keyframe[], mediaDuration = 0): DanceFrame[] {
  const sorted = sortKeyframes(keyframes);
  if (sorted.length < 1) {
    return [];
  }

  return sorted.map((keyframe, index) => {
    const next =
      index + 1 < sorted.length ? sorted[index + 1].t : mediaDuration || keyframe.t + 0.5;
    const durationMs = Math.max(80, Math.round((next - keyframe.t) * 1000));
    const frame: DanceFrame = {
      leftEye: { x: 0, y: 0, rotation: 0, weight: keyframe.eye, size: 0 },
      rightEye: { x: 0, y: 0, rotation: 0, weight: keyframe.eye, size: 0 },
      mouth: { x: 0, y: 0, rotation: 0, weight: keyframe.mouth, size: 0 },
      yawServo: {
        angle: clamp(Math.round(keyframe.yaw * 10), -YAW_LIM, YAW_LIM),
        speed: keyframe.speed,
      },
      pitchServo: {
        angle: clamp(Math.round(keyframe.pitch * 10), PITCH_MIN, PITCH_MAX),
        speed: keyframe.speed,
      },
      durationMs,
    };

    if (keyframe.color) {
      frame.leftRgbColor = keyframe.color;
      frame.rightRgbColor = keyframe.color;
    }

    return frame;
  });
}

export function keyframesFromSequence(
  sequence: DanceFrame[],
  randColor: () => string,
): Keyframe[] {
  let time = 0;

  return sequence.map((frame) => {
    const keyframe = makeKeyframe({
      t: Number(time.toFixed(2)),
      yaw: Math.round((frame.yawServo?.angle || 0) / 10),
      pitch: Math.round((frame.pitchServo?.angle || 0) / 10),
      mouth: frame.mouth?.weight || 0,
      eye: frame.leftEye?.weight ?? 100,
      speed: frame.yawServo?.speed || 700,
      color: frame.leftRgbColor || randColor(),
    });
    time += (frame.durationMs || 500) / 1000;
    return keyframe;
  });
}

export function readSequenceText(text: string): DanceFrame[] {
  const sequence = JSON.parse(text.trim() || "[]") as unknown;
  if (!Array.isArray(sequence)) {
    throw new Error("dance.json 必须是数组");
  }
  return sequence as DanceFrame[];
}

export function sequenceText(sequence: DanceFrame[]): string {
  return JSON.stringify(sequence, null, 2);
}

export function hslToHex(h: number, s: number, l: number): string {
  const saturation = s / 100;
  const lightness = l / 100;
  const k = (n: number) => (n + h / 30) % 12;
  const a = saturation * Math.min(lightness, 1 - lightness);
  const f = (n: number) => {
    const color = lightness - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1));
    return Math.round(255 * color)
      .toString(16)
      .padStart(2, "0");
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

export function randColor(): string {
  return hslToHex(Math.floor(Math.random() * 360), 85, 55);
}
