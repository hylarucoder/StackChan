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

export type LyricSpan = { start: number; end: number };
export type AutoMarkDensity = "bar" | "half" | "phrase";

export type AutoMarkInput = {
  beats: number[];
  lyrics: LyricSpan[];
  duration: number;
  peaks?: ArrayLike<number>;
  randColor: () => string;
  density?: AutoMarkDensity;
};

// Servo reality: the head is spring-driven (firmware moveWithSpeed maps speed
// 0-1000 to spring stiffness/damping). Big reversals on short intervals never
// settle, so motion amplitude is capped, a comfortable slew rate is enforced,
// and a minimum dwell guarantees the spring can reach each target.
const SERVO = {
  yawAmp: { verse: 32, chorus: 56, break: 0 }, // held lean to one side
  lift: { verse: 5, chorus: 12, break: 0 }, // base head pitch while grooving
  nod: { verse: 8, chorus: 16, break: 0 }, // on-beat nod depth
  yawCap: 70, // editor units; well inside the firmware ±128 clamp
  pitchCap: 28,
  slewYaw: 110, // deg/s the spring comfortably tracks
  slewPitch: 80,
  minDwell: 0.33, // s; merge frames closer than this
  speedMin: 250,
  speedMax: 1000,
  tolYaw: 9, // Douglas-Peucker tolerance per channel (editor units)
  tolPitch: 9,
  tolMouth: 35,
};

type Section = "verse" | "chorus" | "break";
type RawFrame = { t: number; yaw: number; pitch: number; mouth: number; color: string };

type PosePoint = { t: number; yaw: number; pitch: number; mouth: number };

/**
 * Douglas-Peucker keep-mask over a pose timeline. A frame survives if, on any
 * channel, it deviates from the straight line between the segment's endpoints
 * by more than that channel's tolerance — i.e. it's a real turn, not a frame
 * that linear interpolation already covers. Endpoints are always kept.
 */
function rdpKeepMask(
  points: PosePoint[],
  tolYaw: number,
  tolPitch: number,
  tolMouth: number,
): boolean[] {
  const n = points.length;
  const keep = new Array<boolean>(n).fill(false);
  if (n === 0) return keep;
  keep[0] = true;
  keep[n - 1] = true;

  const stack: [number, number][] = [[0, n - 1]];
  while (stack.length > 0) {
    const [a, b] = stack.pop() as [number, number];
    if (b - a < 2) continue;
    const span = points[b].t - points[a].t || 1;
    let maxDev = 0;
    let idx = -1;
    for (let i = a + 1; i < b; i += 1) {
      const r = (points[i].t - points[a].t) / span;
      const eYaw = points[a].yaw + (points[b].yaw - points[a].yaw) * r;
      const ePitch = points[a].pitch + (points[b].pitch - points[a].pitch) * r;
      const eMouth = points[a].mouth + (points[b].mouth - points[a].mouth) * r;
      const dev = Math.max(
        Math.abs(points[i].yaw - eYaw) / tolYaw,
        Math.abs(points[i].pitch - ePitch) / tolPitch,
        Math.abs(points[i].mouth - eMouth) / tolMouth,
      );
      if (dev > maxDev) {
        maxDev = dev;
        idx = i;
      }
    }
    if (maxDev > 1 && idx >= 0) {
      keep[idx] = true;
      stack.push([a, idx]);
      stack.push([idx, b]);
    }
  }
  return keep;
}

function rdpSimplify<T extends PosePoint>(
  frames: T[],
  tolYaw: number,
  tolPitch: number,
  tolMouth: number,
): T[] {
  if (frames.length <= 2) return frames;
  const mask = rdpKeepMask(frames, tolYaw, tolPitch, tolMouth);
  return frames.filter((_, i) => mask[i]);
}

/**
 * Public keyframe thinner: Douglas-Peucker simplification of a hand-made or
 * generated timeline. Drops frames that linear interpolation already covers,
 * within the given per-channel tolerances. Order-independent and pure.
 */
export function simplifyKeyframes(
  keyframes: Keyframe[],
  tolYaw = 9,
  tolPitch = 9,
  tolMouth = 35,
): Keyframe[] {
  if (keyframes.length <= 2) return keyframes;
  return rdpSimplify(sortKeyframes(keyframes), tolYaw, tolPitch, tolMouth);
}

/**
 * Generate a servo-safe first-pass choreography from beat + lyric analysis.
 * Pipeline: split the song into musical units (bars / half-bars / lyric phrases),
 * classify each unit's energy (verse / chorus / break), assign a move from the
 * library, then run a servo-safety pass (amplitude cap, slew limit, minimum
 * dwell, speed 0-1000) so the spring-driven head can actually perform it.
 */
export function autoChoreograph(input: AutoMarkInput): Keyframe[] {
  const { beats, lyrics, duration, peaks, randColor, density = "bar" } = input;
  const span = duration > 0 ? duration : beats[beats.length - 1] || 0;

  const energyAt = (t: number): number => {
    if (!peaks || peaks.length === 0 || span <= 0) return 0.6;
    const idx = clamp(Math.floor((t / span) * peaks.length), 0, peaks.length - 1);
    return clamp(peaks[idx], 0, 1);
  };
  const avgEnergy = (a: number, b: number): number => {
    if (!peaks || peaks.length === 0 || span <= 0) return 0.6;
    const i0 = clamp(Math.floor((a / span) * peaks.length), 0, peaks.length - 1);
    const i1 = clamp(Math.ceil((b / span) * peaks.length), i0 + 1, peaks.length);
    let sum = 0;
    for (let i = i0; i < i1; i += 1) sum += peaks[i];
    return clamp(sum / (i1 - i0), 0, 1);
  };
  const lineIndexAt = (t: number): number => {
    for (let i = 0; i < lyrics.length; i += 1) {
      if (t >= lyrics[i].start - 0.02 && t < lyrics[i].end) return i;
    }
    return -1;
  };

  // 1) unit starts ----------------------------------------------------------
  let starts: number[] = [];
  if (density === "phrase" && lyrics.length > 0) {
    starts = lyrics.map((line) => line.start);
  } else if (beats.length >= 2) {
    const step = density === "half" ? 2 : 4;
    for (let i = 0; i < beats.length; i += step) starts.push(beats[i]);
  } else if (span > 0) {
    const grid = density === "half" ? 0.9 : 1.6;
    for (let t = 0; t < span; t += grid) starts.push(t);
  } else {
    starts = lyrics.map((line) => line.start);
  }
  starts = starts.filter((t) => t >= 0 && (span <= 0 || t <= span)).sort((a, b) => a - b);
  if (starts.length === 0) return [];

  // 2) classify each unit by energy percentile ------------------------------
  const units = starts.map((start, i) => {
    const end = i + 1 < starts.length ? starts[i + 1] : span || start + 1.5;
    return { start, end: Math.max(end, start + 0.12), energy: avgEnergy(start, end) };
  });
  const sorted = units.map((u) => u.energy).sort((a, b) => a - b);
  const p = (q: number) => sorted[clamp(Math.floor(q * sorted.length), 0, sorted.length - 1)];
  const lo = p(0.35);
  const hi = p(0.7);

  // 3) groove: lean to one side and HOLD, switching sides every 2 units so the
  // motion reads as a repeating step-touch motif instead of constant wobble.
  // The only in-bar motion is a nod on the beat -> movement has a clear pulse,
  // with stillness between. Breaks rest near center for contrast.
  const lineColors = lyrics.map(() => randColor());
  const gapColor = randColor();
  const raw: RawFrame[] = [];

  const pushFrame = (time: number, yaw: number, pitch: number) => {
    const t = Number(time.toFixed(2));
    const line = lineIndexAt(t);
    raw.push({
      t,
      yaw: clamp(Math.round(yaw), -SERVO.yawCap, SERVO.yawCap),
      pitch: clamp(Math.round(pitch), -SERVO.pitchCap, SERVO.pitchCap),
      mouth: line >= 0 ? clamp(Math.round(45 + energyAt(t) * 45), 0, 100) : 0,
      color: line >= 0 ? lineColors[line] : gapColor,
    });
  };

  units.forEach((unit, barIndex) => {
    const dur = unit.end - unit.start;
    const singing = lineIndexAt(unit.start) >= 0 || lineIndexAt((unit.start + unit.end) / 2) >= 0;
    let section: Section = unit.energy >= hi ? "chorus" : unit.energy <= lo ? "break" : "verse";
    if (section === "break" && singing) section = "verse";
    if (section === "break") {
      pushFrame(unit.start, 0, 0); // rest near center for contrast
      return;
    }

    // 4-bar phrase, 2-bar side cycle: hold a side two bars, then switch.
    const phrasePos = barIndex % 4;
    const side = Math.floor(barIndex / 2) % 2 === 0 ? 1 : -1;
    const amp = SERVO.yawAmp[section];
    const lift = SERVO.lift[section];
    const nod = SERVO.nod[section];

    // accent the phrase downbeat ("1"): a bigger, higher hit -> reads as a beat.
    const isAccent = phrasePos === 0;
    const yawDown = side * amp * (isAccent ? 1.3 : 1);
    const liftDown = lift + (isAccent ? (section === "chorus" ? 8 : 4) : 0);
    pushFrame(unit.start, yawDown, liftDown);

    // on-beat nod mid-bar; yaw holds so it grooves rather than wobbles.
    if (dur >= 0.9) pushFrame(unit.start + dur * 0.5, side * amp, liftDown - nod);

    // phrase-end answer: a turnaround toward the next phrase's side, punctuating
    // the 4-bar group so the dance has call-and-response shape.
    if (phrasePos === 3 && dur >= 0.7) {
      pushFrame(unit.start + dur * 0.72, -side * amp * 0.45, lift - Math.round(nod * 0.5));
    }
  });
  raw.sort((a, b) => a.t - b.t);

  // 4) min-dwell merge, then Douglas-Peucker simplify to drop redundant frames
  // (held sides, collinear ramps), keeping only the shape-defining ones.
  const merged: RawFrame[] = [];
  for (const frame of raw) {
    const prev = merged[merged.length - 1];
    if (prev && frame.t - prev.t < SERVO.minDwell) continue; // too close; the spring can't keep up
    merged.push(frame);
  }
  const simplified = rdpSimplify(merged, SERVO.tolYaw, SERVO.tolPitch, SERVO.tolMouth);

  // 5) servo-safety: slew clamp, then derive speed -------------------------
  const kept: RawFrame[] = [];
  for (const frame of simplified) {
    const prev = kept[kept.length - 1];
    if (prev) {
      const dt = frame.t - prev.t;
      const maxYaw = SERVO.slewYaw * dt;
      const maxPitch = SERVO.slewPitch * dt;
      frame.yaw = clamp(frame.yaw, prev.yaw - maxYaw, prev.yaw + maxYaw);
      frame.pitch = clamp(frame.pitch, prev.pitch - maxPitch, prev.pitch + maxPitch);
    }
    kept.push(frame);
  }

  return kept.map((frame, i) => {
    const prev = kept[i - 1];
    const next = kept[i + 1];
    const dt = next ? next.t - frame.t : 0.5;
    const dYaw = prev ? Math.abs(frame.yaw - prev.yaw) : Math.abs(frame.yaw);
    const dPitch = prev ? Math.abs(frame.pitch - prev.pitch) : Math.abs(frame.pitch);
    const need = Math.max(dYaw / SERVO.slewYaw, dPitch / SERVO.slewPitch) / Math.max(0.12, dt);
    const speed = clamp(
      Math.round(SERVO.speedMin + Math.min(1, need) * (SERVO.speedMax - SERVO.speedMin)),
      SERVO.speedMin,
      SERVO.speedMax,
    );
    return makeKeyframe({
      t: frame.t,
      yaw: frame.yaw,
      pitch: frame.pitch,
      mouth: frame.mouth,
      eye: 100,
      speed,
      color: frame.color,
    });
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
