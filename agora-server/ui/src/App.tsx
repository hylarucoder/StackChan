import {
  ChangeEvent,
  MouseEvent,
  PointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import * as THREE from "three";

import { LyricsOverlay, type LyricCue } from "./LyricsOverlay";
import {
  type AutoMarkDensity,
  DanceFrame,
  Keyframe,
  autoChoreograph,
  buildSequence,
  clamp,
  simplifyKeyframes,
  keyframesFromSequence,
  makeKeyframe,
  randColor,
  readSequenceText,
  sequenceText,
  sortKeyframes,
} from "./choreography";

const DEFAULT_MEDIA = "./dance/assets/guijichuanshuo.wav";
const API_BASE = (import.meta.env.VITE_STACKCHAN_API_BASE || "").replace(/\/$/, "");

type Pose = Omit<Keyframe, "t">;
type WorkbenchTab = "frames" | "json" | "deploy";

const defaultPose: Pose = {
  yaw: 0,
  pitch: 0,
  mouth: 0,
  eye: 100,
  speed: 700,
  color: "#ff8c00",
};

type ThreeState = {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.OrthographicCamera;
  material: THREE.ShaderMaterial;
  t0: number | null;
};

type AudioState = {
  ctx?: AudioContext;
  analyser?: AnalyserNode;
  freqData?: Uint8Array<ArrayBuffer>;
  source?: MediaElementAudioSourceNode;
  ready: boolean;
  smooth: { bass: number; mid: number; treble: number; level: number };
};

export function App() {
  const [pose, setPose] = useState<Pose>(defaultPose);
  const [keyframes, setKeyframes] = useState<Keyframe[]>([]);
  const [selected, setSelected] = useState(-1);
  const [danceJson, setDanceJson] = useState("[]");
  const [status, setStatus] = useState("");
  const [randomColors, setRandomColors] = useState(true);
  const [beatsVisible, setBeatsVisible] = useState(true);
  const [snapBeat, setSnapBeat] = useState(true);
  const [markDensity, setMarkDensity] = useState<AutoMarkDensity>("bar");
  const [bridge, setBridge] = useState("http://127.0.0.1:8000");
  const [device, setDevice] = useState("stackchan-1");
  const [danceChannels, setDanceChannels] = useState<string[]>([]);
  const [playDelay, setPlayDelay] = useState(200);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [lyrics, setLyrics] = useState<LyricCue[]>([]);
  const [showLyrics, setShowLyrics] = useState(true);
  const [activeTab, setActiveTab] = useState<WorkbenchTab>("frames");

  const videoRef = useRef<HTMLAudioElement | null>(null);
  const stageRef = useRef<HTMLCanvasElement | null>(null);
  const stageWrapRef = useRef<HTMLDivElement | null>(null);
  const faceRef = useRef<HTMLCanvasElement | null>(null);
  const padRef = useRef<HTMLDivElement | null>(null);
  const timelineRef = useRef<HTMLDivElement | null>(null);
  const waveRef = useRef<HTMLCanvasElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const importRef = useRef<HTMLInputElement | null>(null);
  const draggingRef = useRef(false);
  const rafRef = useRef<number | null>(null);
  const threeRef = useRef<ThreeState | null>(null);
  const audioRef = useRef<AudioState>({
    ready: false,
    smooth: { bass: 0, mid: 0, treble: 0, level: 0 },
  });
  const decodeCtxRef = useRef<AudioContext | null>(null);
  const monoPeaksRef = useRef<Float32Array | null>(null);
  const accentColorRef = useRef("#4da3ff");
  const playColorsRef = useRef<{ t: number; hex: string }[]>([]);
  const scratchColorRef = useRef(new THREE.Color("#4da3ff"));
  const beatsRef = useRef<number[]>([]);
  const lastBeatIdxRef = useRef(-1);
  const sceneRef = useRef(0);
  const isPlayingRef = useRef(false);
  const clockRef = useRef({ t: 0, last: null as number | null });

  const sequence = useMemo(() => buildSequence(keyframes, duration), [keyframes, duration]);
  const selectedKeyframe = selected >= 0 ? keyframes[selected] : null;
  const accentColor = selectedKeyframe?.color || pose.color;
  accentColorRef.current = accentColor;

  const setSequenceEditor = useCallback((next: DanceFrame[]) => {
    setDanceJson(sequenceText(next));
  }, []);

  const syncEditorFromKeyframes = useCallback(
    (nextKeyframes: Keyframe[], nextDuration = duration) => {
      setSequenceEditor(buildSequence(nextKeyframes, nextDuration));
    },
    [duration, setSequenceEditor],
  );

  const drawFace = useCallback(() => {
    const canvas = faceRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    const { width: w, height: h } = canvas;
    ctx.clearRect(0, 0, w, h);
    const dx = (-pose.yaw / 90) * 22;
    const dy = (-pose.pitch / 40) * 16;
    const cx = w / 2 + dx;
    const cy = h / 2 + dy;

    ctx.fillStyle = "#1d2530";
    ctx.strokeStyle = "#3a4453";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect(cx - 34, cy - 30, 68, 62, 12);
    ctx.fill();
    ctx.stroke();

    const eyeHeight = 2 + (pose.eye / 100) * 9;
    ctx.fillStyle = "#7fd0ff";
    [-16, 16].forEach((eyeX) => {
      ctx.beginPath();
      ctx.ellipse(cx + eyeX, cy - 6, 6, eyeHeight, 0, 0, 7);
      ctx.fill();
    });

    const mouthHeight = 2 + (pose.mouth / 100) * 14;
    ctx.fillStyle = "#ff8aa0";
    ctx.beginPath();
    ctx.ellipse(cx, cy + 16, 11, mouthHeight, 0, 0, 7);
    ctx.fill();

    if (pose.color) {
      ctx.fillStyle = pose.color;
      ctx.fillRect(4, h - 8, w - 8, 4);
    }
  }, [pose]);

  const drawWave = useCallback(() => {
    const canvas = waveRef.current;
    const timeline = timelineRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !timeline || !ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.round(timeline.clientWidth * dpr);
    const h = Math.round(timeline.clientHeight * dpr);
    if (w > 0 && h > 0 && (canvas.width !== w || canvas.height !== h)) {
      canvas.width = w;
      canvas.height = h;
    }

    const mid = canvas.height / 2;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const peaks = monoPeaksRef.current;
    if (peaks) {
      ctx.fillStyle = "rgba(77,163,255,.22)";
      for (let x = 0; x < canvas.width; x += 1) {
        const peak = peaks[Math.floor((x / canvas.width) * peaks.length)] || 0;
        const peakHeight = Math.max(0.5, peak * mid * 0.95);
        ctx.fillRect(x, mid - peakHeight, 1, peakHeight * 2);
      }
    }

    if (beatsVisible && beatsRef.current.length && duration) {
      ctx.fillStyle = "rgba(255,93,115,.45)";
      beatsRef.current.forEach((beat) => {
        ctx.fillRect(Math.round((beat / duration) * canvas.width), 0, 1, canvas.height);
      });
    }
  }, [beatsVisible, duration]);

  const resetWaveform = useCallback(() => {
    monoPeaksRef.current = null;
    beatsRef.current = [];
    drawWave();
  }, [drawWave]);

  const detectBeats = useCallback((buffer: AudioBuffer) => {
    const sampleRate = buffer.sampleRate;
    const channel = buffer.getChannelData(0);
    const hop = Math.max(1, Math.floor(sampleRate * 0.02));
    const frames = Math.floor(channel.length / hop);
    const energy = new Float32Array(frames);

    for (let i = 0; i < frames; i += 1) {
      let frameEnergy = 0;
      const start = i * hop;
      for (let j = 0; j < hop; j += 1) {
        const value = channel[start + j] || 0;
        frameEnergy += value * value;
      }
      energy[i] = Math.sqrt(frameEnergy / hop);
    }

    const flux = new Float32Array(frames);
    for (let i = 1; i < frames; i += 1) {
      flux[i] = Math.max(0, energy[i] - energy[i - 1]);
    }

    const output: number[] = [];
    const win = 8;
    let last = -99;
    for (let i = 1; i < frames - 1; i += 1) {
      let sum = 0;
      let count = 0;
      for (let k = i - win; k <= i + win; k += 1) {
        if (k >= 0 && k < frames) {
          sum += flux[k];
          count += 1;
        }
      }
      const threshold = (sum / count) * 1.6 + 1e-4;
      if (flux[i] > threshold && flux[i] >= flux[i - 1] && flux[i] >= flux[i + 1] && i - last > 6) {
        output.push((i * hop) / sampleRate);
        last = i;
      }
    }
    return output;
  }, []);

  const buildWaveform = useCallback(
    async (arrayBuffer: ArrayBuffer) => {
      try {
        const AudioCtor = window.AudioContext || window.webkitAudioContext;
        decodeCtxRef.current ||= new AudioCtor();
        const buffer = await decodeCtxRef.current.decodeAudioData(arrayBuffer.slice(0));
        const channel = buffer.getChannelData(0);
        const resolution = 1500;
        const block = Math.max(1, Math.floor(channel.length / resolution));
        const peaks = new Float32Array(resolution);
        for (let i = 0; i < resolution; i += 1) {
          let max = 0;
          const start = i * block;
          for (let j = 0; j < block; j += 1) {
            const value = Math.abs(channel[start + j] || 0);
            if (value > max) max = value;
          }
          peaks[i] = max;
        }
        monoPeaksRef.current = peaks;
        beatsRef.current = detectBeats(buffer);
        drawWave();
      } catch {
        resetWaveform();
      }
    },
    [detectBeats, drawWave, resetWaveform],
  );

  const buildWaveformFromUrl = useCallback(
    async (url: string) => {
      try {
        const response = await fetch(url);
        if (response.ok) {
          await buildWaveform(await response.arrayBuffer());
        }
      } catch {
        resetWaveform();
      }
    },
    [buildWaveform, resetWaveform],
  );

  const ensureAudio = useCallback(() => {
    const video = videoRef.current;
    const audio = audioRef.current;
    if (!video || audio.ready) return;

    try {
      const AudioCtor = window.AudioContext || window.webkitAudioContext;
      audio.ctx = new AudioCtor();
      audio.source = audio.ctx.createMediaElementSource(video);
      audio.analyser = audio.ctx.createAnalyser();
      audio.analyser.fftSize = 2048;
      audio.analyser.smoothingTimeConstant = 0.82;
      audio.source.connect(audio.analyser);
      audio.analyser.connect(audio.ctx.destination);
      audio.freqData = new Uint8Array(audio.analyser.frequencyBinCount);
      audio.ready = true;
    } catch {
      audio.ready = true;
    }
  }, []);

  const readBands = useCallback(() => {
    const audio = audioRef.current;
    if (!audio.analyser || !audio.freqData) return;

    audio.analyser.getByteFrequencyData(audio.freqData);
    const n = audio.freqData.length;
    const bassEnd = Math.max(1, Math.floor(n * 0.1));
    const midEnd = Math.floor(n * 0.4);
    let bass = 0;
    let mid = 0;
    let treble = 0;
    let level = 0;
    for (let i = 0; i < n; i += 1) {
      const value = audio.freqData[i] / 255;
      level += value;
      if (i < bassEnd) bass += value;
      else if (i < midEnd) mid += value;
      else treble += value;
    }

    const target = {
      bass: bass / bassEnd,
      mid: mid / (midEnd - bassEnd),
      treble: treble / (n - midEnd),
      level: level / n,
    };
    Object.keys(target).forEach((key) => {
      const band = key as keyof typeof target;
      audio.smooth[band] += (target[band] - audio.smooth[band]) * 0.25;
    });
  }, []);

  const resizeThree = useCallback(() => {
    const canvas = stageRef.current;
    const state = threeRef.current;
    if (!canvas || !state) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, canvas.clientWidth);
    const h = Math.max(1, canvas.clientHeight);
    state.renderer.setPixelRatio(dpr);
    state.renderer.setSize(w, h, false);
    state.material.uniforms.u_res.value.set(canvas.width, canvas.height);
  }, []);

  const initThree = useCallback(() => {
    const canvas = stageRef.current;
    if (!canvas) return;
    try {
      const renderer = new THREE.WebGLRenderer({
        canvas,
        antialias: true,
        alpha: false,
        preserveDrawingBuffer: true,
      });
      renderer.setClearColor(0x05080d, 1);

      const scene = new THREE.Scene();
      const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
      const geometry = new THREE.PlaneGeometry(2, 2);
      const material = new THREE.ShaderMaterial({
        depthTest: false,
        depthWrite: false,
        uniforms: {
          u_res: { value: new THREE.Vector2(1, 1) },
          u_time: { value: 0 },
          u_bass: { value: 0 },
          u_mid: { value: 0 },
          u_treble: { value: 0 },
          u_level: { value: 0 },
          u_beat: { value: 0 },
          u_scene: { value: 0 },
          u_accent: { value: new THREE.Color(0x4da3ff) },
        },
        vertexShader: "void main(){ gl_Position=vec4(position.xy,0.0,1.0); }",
        fragmentShader: `
precision highp float;
uniform vec2 u_res; uniform float u_time;
uniform float u_bass, u_mid, u_treble, u_level;
uniform float u_beat, u_scene;
uniform vec3 u_accent;

const float PI = 3.14159265359;

mat2 rot(float a){ float s=sin(a), c=cos(a); return mat2(c,-s,s,c); }

float hash(vec2 p){
  p = fract(p*vec2(123.34, 345.45));
  p += dot(p, p+34.345);
  return fract(p.x*p.y);
}
float noise(vec2 p){
  vec2 i = floor(p); vec2 f = fract(p);
  vec2 u = f*f*(3.0-2.0*f);
  float a = hash(i), b = hash(i+vec2(1.0,0.0));
  float c = hash(i+vec2(0.0,1.0)), d = hash(i+vec2(1.0,1.0));
  return mix(mix(a,b,u.x), mix(c,d,u.x), u.y);
}

// signed distance to a regular n-gon, radius r
float ngon(vec2 p, float n, float r){
  float a = atan(p.x, p.y);
  float seg = 2.0*PI/n;
  return cos(floor(0.5 + a/seg)*seg - a) * length(p) - r;
}

void main(){
  vec2 uv = (gl_FragCoord.xy - 0.5*u_res)/u_res.y;
  float d0 = length(uv);

  vec3 accent = u_accent;
  vec3 hot    = clamp(u_accent.gbr*1.15 + vec3(0.28,0.04,0.12), 0.0, 1.0);
  vec3 col    = vec3(0.012,0.018,0.038);

  // global spin, kicked on every beat
  vec2 p = rot(u_time*0.12 + u_beat*0.40) * uv;

  // ---- kaleidoscope fold; symmetry steps with the beat scene ----
  float folds = 6.0 + mod(floor(u_scene*0.5), 4.0)*2.0;  // 6 / 8 / 10 / 12
  float ang = atan(p.y, p.x);
  float rad = length(p);
  float seg = 2.0*PI/folds;
  ang = abs(mod(ang + seg*0.5, seg) - seg*0.5);
  vec2 kp = vec2(cos(ang), sin(ang)) * rad;

  // morphing polygon side count, also stepping per beat
  float n = 3.0 + mod(u_scene, 5.0);                     // 3..7 sides

  // layer 1 — kaleidoscope grid lattice (treble)
  vec2 g = kp * (5.0 + u_treble*5.0) + u_time*0.2;
  vec2 gf = abs(fract(g) - 0.5);
  float grid = smoothstep(0.44, 0.5, max(gf.x, gf.y));
  col += accent * grid * (0.10 + u_treble*0.7);

  // layer 2 — concentric morphing polygon ring (mid)
  float ring = ngon(kp, n, 0.30 + 0.10*sin(u_time + rad*4.0));
  col += mix(accent, hot, u_treble) * smoothstep(0.012, 0.0, abs(ring))
       * (0.5 + u_mid*1.3);

  // layer 3 — rotating radial bars (bass + beat)
  float bars = abs(sin(ang*folds*0.5 + u_time*2.2));
  col += hot * smoothstep(0.82, 1.0, bars) * exp(-rad*1.6)
       * (0.18 + u_bass*1.4 + u_beat*0.9);

  // center polygon core, snaps & flips color on the beat
  float core = ngon(rot(u_beat*0.8)*p, n, 0.10 + u_bass*0.10 + u_beat*0.07);
  col += mix(accent, hot, u_beat) * smoothstep(0.045, 0.0, core)
       * (0.6 + u_level*1.5 + u_beat*1.3);

  // beat flash lights up the whole lattice
  col += accent * u_beat * 0.18 * (grid + 0.3);

  col *= 0.82 + 0.4*u_level;
  col *= smoothstep(1.4, 0.12, d0);                      // vignette
  col += (hash(gl_FragCoord.xy + fract(u_time)) - 0.5)/255.0;  // anti-banding

  gl_FragColor = vec4(col, 1.0);
}`,
      });
      scene.add(new THREE.Mesh(geometry, material));
      threeRef.current = { renderer, scene, camera, material, t0: null };
      resizeThree();
    } catch {
      canvas.style.display = "none";
    }
  }, [resizeThree]);

  const loop = useCallback(
    (timestamp: number) => {
      const state = threeRef.current;
      if (state) {
        // advance the animation clock only while playing -> still when idle
        const clock = clockRef.current;
        if (clock.last === null) clock.last = timestamp;
        if (isPlayingRef.current) clock.t += timestamp - clock.last;
        clock.last = timestamp;
        resizeThree();
        readBands();
        if (stageRef.current && stageRef.current.width > 0) {
          state.material.uniforms.u_time.value = clock.t / 1000;
          state.material.uniforms.u_bass.value = audioRef.current.smooth.bass;
          state.material.uniforms.u_mid.value = audioRef.current.smooth.mid;
          state.material.uniforms.u_treble.value = audioRef.current.smooth.treble;
          state.material.uniforms.u_level.value = audioRef.current.smooth.level;

          // beat envelope + per-beat scene stepping -> geometry changes on the rhythm
          const beats = beatsRef.current;
          const playhead = videoRef.current?.currentTime ?? 0;
          let idx = -1;
          for (let i = 0; i < beats.length; i += 1) {
            if (beats[i] <= playhead + 1e-3) idx = i;
            else break;
          }
          if (idx > lastBeatIdxRef.current) {
            sceneRef.current += idx - lastBeatIdxRef.current;
          }
          lastBeatIdxRef.current = idx;
          const beatPulse = idx >= 0 ? Math.exp(-(playhead - beats[idx]) * 6.5) : 0;
          state.material.uniforms.u_beat.value = beatPulse;
          state.material.uniforms.u_scene.value = sceneRef.current;

          // accent tracks the dance: while playing, the keyframe color active at
          // the playhead; otherwise the keyframe being edited. Lerp for a smooth fade.
          let targetHex = accentColorRef.current;
          const colors = playColorsRef.current;
          if (isPlayingRef.current && colors.length > 0) {
            let active = colors[0].hex;
            for (let i = 0; i < colors.length; i += 1) {
              if (colors[i].t <= playhead) active = colors[i].hex;
              else break;
            }
            targetHex = active;
          }
          try {
            scratchColorRef.current.set(targetHex);
            (state.material.uniforms.u_accent.value as THREE.Color).lerp(
              scratchColorRef.current,
              0.08,
            );
          } catch {
            /* invalid color string — keep previous accent */
          }

          state.renderer.render(state.scene, state.camera);
        }
      }
      rafRef.current = requestAnimationFrame(loop);
    },
    [readBands, resizeThree],
  );

  useEffect(() => {
    drawFace();
  }, [drawFace]);

  useEffect(() => {
    drawWave();
  }, [drawWave, beatsVisible, duration]);

  useEffect(() => {
    initThree();
    rafRef.current = requestAnimationFrame(loop);
    const onResize = () => {
      resizeThree();
      drawWave();
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      threeRef.current?.material.dispose();
      threeRef.current?.renderer.dispose();
      threeRef.current = null;
    };
  }, [drawWave, initThree, loop, resizeThree]);

  useEffect(() => {
    isPlayingRef.current = isPlaying;
  }, [isPlaying]);

  const toggleFullscreen = useCallback(() => {
    const wrap = stageWrapRef.current;
    if (!wrap) return;
    if (document.fullscreenElement) {
      void document.exitFullscreen();
    } else {
      void wrap.requestFullscreen?.();
    }
  }, []);

  useEffect(() => {
    const onFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === stageWrapRef.current);
      // canvas client size changes on the next frame; nudge the renderer.
      requestAnimationFrame(() => resizeThree());
    };
    document.addEventListener("fullscreenchange", onFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", onFullscreenChange);
  }, [resizeThree]);

  useEffect(() => {
    // time-sorted keyframe colors; the render loop samples this at the playhead
    // so the shader tracks the dance's color as it plays.
    playColorsRef.current = keyframes
      .filter((keyframe) => Boolean(keyframe.color))
      .map((keyframe) => ({ t: keyframe.t, hex: keyframe.color }));
  }, [keyframes]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    video.src = DEFAULT_MEDIA;
    setStatus(`自动载入 ${DEFAULT_MEDIA}`);
    void buildWaveformFromUrl(DEFAULT_MEDIA);
  }, [buildWaveformFromUrl]);

  useEffect(() => {
    const loadDance = async () => {
      try {
        const response = await fetch(`${API_BASE}/dance/json`);
        if (!response.ok) throw new Error(await response.text());
        const nextSequence = (await response.json()) as DanceFrame[];
        const nextKeyframes = keyframesFromSequence(nextSequence, randColor);
        setKeyframes(nextKeyframes);
        setSelected(-1);
        setSequenceEditor(nextSequence);
        setStatus("已载入磁盘 dance.json");
      } catch (error) {
        setSequenceEditor([]);
        setStatus(`载入磁盘 dance.json 失败: ${(error as Error).message}`);
      }
    };
    void loadDance();
  }, [setSequenceEditor]);

  useEffect(() => {
    const loadLyrics = async () => {
      try {
        const response = await fetch(`${API_BASE}/dance/lyrics`);
        if (!response.ok) throw new Error(await response.text());
        setLyrics((await response.json()) as LyricCue[]);
      } catch {
        setLyrics([]);
      }
    };
    void loadLyrics();
  }, []);

  const updatePose = (patch: Partial<Pose>) => {
    setPose((prev) => ({ ...prev, ...patch }));
  };

  const setPoseFromPad = (event: PointerEvent<HTMLDivElement>) => {
    const pad = padRef.current;
    if (!pad) return;
    const rect = pad.getBoundingClientRect();
    const cx = event.clientX - rect.left;
    const cy = event.clientY - rect.top;
    updatePose({
      yaw: Math.round(90 - clamp(cx / rect.width, 0, 1) * 180),
      pitch: Math.round(40 - clamp(cy / rect.height, 0, 0.5) * 80),
    });
  };

  const seekBy = (delta: number) => {
    const video = videoRef.current;
    if (!video) return;
    const max = video.duration || duration || Number.POSITIVE_INFINITY;
    video.currentTime = clamp(video.currentTime + delta, 0, max);
  };

  // snap a time to the nearest detected beat within a window, so keyframes land
  // on the rhythm instead of requiring pixel-perfect scrubbing.
  const nearestBeatTime = (t: number, window = 0.4): number => {
    const beats = beatsRef.current;
    if (!snapBeat || beats.length === 0) return t;
    let best = t;
    let bestDist = Number.POSITIVE_INFINITY;
    for (const beat of beats) {
      const dist = Math.abs(beat - t);
      if (dist < bestDist) {
        bestDist = dist;
        best = beat;
      }
      if (beat > t + window) break; // beats are sorted ascending
    }
    return bestDist <= window ? best : t;
  };

  const addKeyframe = () => {
    const video = videoRef.current;
    const next = makeKeyframe({
      ...pose,
      color: randomColors ? randColor() : pose.color,
      t: Number(nearestBeatTime(video?.currentTime || 0).toFixed(2)),
    });
    const nextKeyframes = sortKeyframes([...keyframes, next]);
    const nextSelected = nextKeyframes.indexOf(next);
    setKeyframes(nextKeyframes);
    setSelected(nextSelected);
    setPose({
      yaw: next.yaw,
      pitch: next.pitch,
      mouth: next.mouth,
      eye: next.eye,
      speed: next.speed,
      color: next.color,
    });
    syncEditorFromKeyframes(nextKeyframes);
  };

  const updateSelectedKeyframe = () => {
    if (selected < 0) return;
    const nextKeyframes = keyframes.map((keyframe, index) =>
      index === selected
        ? {
            ...keyframe,
            ...pose,
            color: randomColors ? keyframe.color || randColor() : pose.color,
          }
        : keyframe,
    );
    setKeyframes(nextKeyframes);
    syncEditorFromKeyframes(nextKeyframes);
  };

  const selectKeyframe = (index: number, seek = false) => {
    const keyframe = keyframes[index];
    if (!keyframe) return;
    setSelected(index);
    setPose({
      yaw: keyframe.yaw,
      pitch: keyframe.pitch,
      mouth: keyframe.mouth,
      eye: keyframe.eye,
      speed: keyframe.speed,
      color: keyframe.color,
    });
    if (seek && videoRef.current) {
      videoRef.current.currentTime = keyframe.t;
    }
  };

  const gotoKeyframe = (direction: -1 | 1) => {
    if (keyframes.length === 0) return;
    const t = videoRef.current?.currentTime ?? 0;
    const eps = 0.05;
    let target = -1;
    if (direction < 0) {
      for (let i = keyframes.length - 1; i >= 0; i -= 1) {
        if (keyframes[i].t < t - eps) {
          target = i;
          break;
        }
      }
      if (target < 0) target = 0; // already at/before the first frame
    } else {
      for (let i = 0; i < keyframes.length; i += 1) {
        if (keyframes[i].t > t + eps) {
          target = i;
          break;
        }
      }
      if (target < 0) target = keyframes.length - 1; // already at/after the last frame
    }
    selectKeyframe(target, true);
  };

  const deleteKeyframe = (index: number) => {
    const nextKeyframes = keyframes.filter((_, keyframeIndex) => keyframeIndex !== index);
    const nextSelected = selected >= nextKeyframes.length ? nextKeyframes.length - 1 : selected;
    setKeyframes(nextKeyframes);
    setSelected(nextSelected);
    syncEditorFromKeyframes(nextKeyframes);
  };

  const clearKeyframes = () => {
    if (!confirm("清空所有关键帧?")) return;
    setKeyframes([]);
    setSelected(-1);
    setSequenceEditor([]);
  };

  const autoMark = () => {
    const beats = beatsRef.current;
    if (beats.length === 0 && lyrics.length === 0) {
      setStatus("还没有可用的节拍/歌词分析，先载入并播放一次音频");
      return;
    }
    if (keyframes.length > 0 && !confirm(`用节拍+歌词自动生成关键帧，覆盖现有 ${keyframes.length} 帧?`)) {
      return;
    }
    const generated = autoChoreograph({
      beats,
      lyrics: lyrics.map((cue) => ({ start: cue.start, end: cue.end })),
      duration: videoRef.current?.duration || duration,
      peaks: monoPeaksRef.current ?? undefined,
      randColor: randColor,
      density: markDensity,
    });
    if (generated.length === 0) {
      setStatus("自动打标没有生成关键帧");
      return;
    }
    setKeyframes(generated);
    setSelected(-1);
    syncEditorFromKeyframes(generated, videoRef.current?.duration || duration);
    setStatus(`自动打标完成: ${generated.length} 帧（节拍 ${beats.length} · 歌词 ${lyrics.length}）`);
  };

  const simplify = () => {
    if (keyframes.length <= 2) {
      setStatus("关键帧太少，无需精简");
      return;
    }
    const next = simplifyKeyframes(keyframes);
    const removed = keyframes.length - next.length;
    if (removed === 0) {
      setStatus(`已是最简（${next.length} 帧）`);
      return;
    }
    setKeyframes(next);
    setSelected(-1);
    syncEditorFromKeyframes(next);
    setStatus(`精简完成: 删除 ${removed} 帧，剩 ${next.length}`);
  };

  const exportDance = () => {
    let parsed: DanceFrame[];
    try {
      parsed = readSequenceText(danceJson);
    } catch (error) {
      setStatus(`JSON 无效: ${(error as Error).message}`);
      return;
    }
    if (!parsed.length) {
      setStatus("没有关键帧");
      return;
    }
    const blob = new Blob([JSON.stringify(parsed)], { type: "application/json" });
    const anchor = document.createElement("a");
    anchor.href = URL.createObjectURL(blob);
    anchor.download = "dance.json";
    anchor.click();
    URL.revokeObjectURL(anchor.href);
    setStatus(`已导出 ${parsed.length} 帧`);
  };

  const importDance = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const parsed = readSequenceText(await file.text());
      const nextKeyframes = keyframesFromSequence(parsed, randColor);
      setKeyframes(nextKeyframes);
      setSelected(-1);
      setSequenceEditor(parsed);
      setStatus(`导入 ${nextKeyframes.length} 帧 (按累计时长铺到时间线)`);
    } catch (error) {
      setStatus(`导入失败: ${(error as Error).message}`);
    } finally {
      event.target.value = "";
    }
  };

  const pushDance = async () => {
    const video = videoRef.current;
    let parsed: DanceFrame[];
    try {
      parsed = readSequenceText(danceJson);
    } catch (error) {
      setStatus(`JSON 无效: ${(error as Error).message}`);
      return;
    }
    if (!parsed.length) {
      setStatus("没有关键帧");
      return;
    }
    const delay = Math.max(0, playDelay || 0);
    try {
      await video?.play();
      video?.pause();
      if (video) video.currentTime = 0;
    } catch {
      // Browser autoplay policy: the later play() may still need a user gesture.
    }
    setStatus("推送中...");
    try {
      const response = await fetch(`${bridge.replace(/\/$/, "")}/trigger/dance/push`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ deviceId: device, sequence: parsed }),
      });
      const result = await response.json();
      if (!response.ok) {
        setStatus(`失败: ${result.detail || response.status}`);
        return;
      }
      setStatus(`已推送 ${result.keyframes || parsed.length} 帧；${delay}ms 后播放音乐...`);
      setTimeout(() => {
        if (!video) return;
        video.currentTime = 0;
        video.play().catch(() => setStatus("点一下视频再试(自动播放被拦)"));
      }, delay);
    } catch (error) {
      setStatus(`推送失败(bridge 没开 或 CORS): ${(error as Error).message}`);
    }
  };

  const onMediaFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    const video = videoRef.current;
    if (!file || !video) return;
    if (!file.type.startsWith("audio/")) {
      setStatus("只支持音频文件");
      event.target.value = "";
      return;
    }
    video.src = URL.createObjectURL(file);
    setStatus(`已载入音频: ${file.name}`);
    resetWaveform();
    await buildWaveform(await file.arrayBuffer());
    event.target.value = "";
  };

  const onTimelineClick = (event: MouseEvent<HTMLDivElement>) => {
    if (!duration || !timelineRef.current || !videoRef.current) return;
    const rect = timelineRef.current.getBoundingClientRect();
    videoRef.current.currentTime = ((event.clientX - rect.left) / rect.width) * duration;
  };

  const onLoadedMetadata = () => {
    const video = videoRef.current;
    if (!video) return;
    const nextDuration = video.duration && Number.isFinite(video.duration) ? video.duration : 0;
    setDuration(nextDuration);
    syncEditorFromKeyframes(keyframes, nextDuration);
    resizeThree();
    drawWave();
  };

  const togglePlay = async () => {
    const video = videoRef.current;
    if (!video) return;
    ensureAudio();
    if (audioRef.current.ctx?.state === "suspended") {
      await audioRef.current.ctx.resume();
    }
    if (video.paused) {
      await video.play().catch(() => undefined);
    } else {
      video.pause();
    }
  };

  const togglePlayRef = useRef(togglePlay);
  togglePlayRef.current = togglePlay;
  const gotoKeyframeRef = useRef(gotoKeyframe);
  gotoKeyframeRef.current = gotoKeyframe;
  const seekByRef = useRef(seekBy);
  seekByRef.current = seekBy;

  const refreshChannels = useCallback(async () => {
    try {
      const response = await fetch(`${bridge.replace(/\/$/, "")}/xiaozhi/healthz`);
      if (!response.ok) return;
      const data = (await response.json()) as { dance_channels?: string[] };
      const ids = Array.isArray(data.dance_channels) ? data.dance_channels : [];
      setDanceChannels(ids);
      // auto-target a connected device when the typed id isn't on a dance channel.
      setDevice((current) => (ids.length > 0 && !ids.includes(current) ? ids[0] : current));
    } catch {
      setDanceChannels([]);
    }
  }, [bridge]);

  useEffect(() => {
    void refreshChannels();
  }, [refreshChannels]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const isSpace = event.code === "Space" || event.key === " ";
      const isPrev = event.key === "ArrowLeft";
      const isNext = event.key === "ArrowRight";
      const isFwd = event.key === "ArrowUp";
      const isBack = event.key === "ArrowDown";
      if (!isSpace && !isPrev && !isNext && !isFwd && !isBack) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      // don't hijack shortcuts while typing in inputs / editors / buttons.
      if (
        target &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(target.tagName))
      ) {
        return;
      }
      event.preventDefault();
      if (isSpace) void togglePlayRef.current();
      else if (isPrev || isNext) gotoKeyframeRef.current(isPrev ? -1 : 1);
      else seekByRef.current(isFwd ? 5 : -5);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const dotStyle = {
    left: `${((90 - pose.yaw) / 180) * 100}%`,
    top: `${((40 - pose.pitch) / 80) * 100}%`,
  };
  const playheadStyle = { left: duration ? `${(currentTime / duration) * 100}%` : "0%" };

  return (
    <>
      <header className="app-header">
        <div className="brand-lockup">
          <span className="brand-mark" />
          <div>
            <h1>StackChan 编舞器</h1>
            <p>{keyframes.length} 帧 · {sequence.length} 段 · {duration ? duration.toFixed(1) : "0.0"}s</p>
          </div>
        </div>
        <div className="header-actions">
          <button type="button" onClick={() => fileRef.current?.click()}>
            上传音乐
          </button>
          <input ref={fileRef} hidden type="file" accept="audio/*,.wav" onChange={onMediaFile} />
          <button type="button" onClick={() => importRef.current?.click()}>
            导入 dance.json
          </button>
          <input
            ref={importRef}
            hidden
            type="file"
            accept=".json,application/json"
            onChange={importDance}
          />
        </div>
      </header>

      <main className="wrap">
        <section className="panel stage-panel">
          <div className="panel-head">
            <div>
              <h2>舞台</h2>
              <p>{isPlaying ? "播放中" : "待机"} · {currentTime.toFixed(2)}s</p>
            </div>
            <span className="time-pill">{currentTime.toFixed(2)} / {duration ? duration.toFixed(2) : "0.00"}s</span>
          </div>
          <div
            id="stageWrap"
            ref={stageWrapRef}
            className={isFullscreen ? "is-fullscreen" : undefined}
            style={{ "--stage-accent": accentColor } as React.CSSProperties}
          >
            <canvas ref={stageRef} id="stage" />
            <LyricsOverlay
              audioRef={videoRef}
              beatsRef={beatsRef}
              cues={lyrics}
              accentColor={accentColor}
              visible={showLyrics}
            />
            <div className="stage-tools">
              {lyrics.length > 0 && (
                <button
                  type="button"
                  className={`stage-tool-button${showLyrics ? " is-on" : ""}`}
                  title={showLyrics ? "隐藏歌词" : "显示歌词"}
                  aria-label={showLyrics ? "隐藏歌词" : "显示歌词"}
                  aria-pressed={showLyrics}
                  onClick={() => setShowLyrics((value) => !value)}
                >
                  词
                </button>
              )}
              <button
                type="button"
                className="stage-tool-button"
                title={isFullscreen ? "退出全屏" : "全屏背景舞台"}
                aria-label={isFullscreen ? "退出全屏" : "全屏背景舞台"}
                onClick={toggleFullscreen}
              >
                {isFullscreen ? "✕" : "⛶"}
              </button>
            </div>
            <audio
              ref={videoRef}
              id="audio"
              controls
              onLoadedMetadata={onLoadedMetadata}
              onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
              onPlay={() => {
                ensureAudio();
                void audioRef.current.ctx?.resume();
                setIsPlaying(true);
              }}
              onPause={() => setIsPlaying(false)}
            />
          </div>
          <div className="transport-bar">
            <button
              type="button"
              className="play-button seek-button"
              title="后退 5 秒 (↓)"
              aria-label="后退 5 秒"
              onClick={() => seekBy(-5)}
            >
              -5s
            </button>
            <button
              type="button"
              className="play-button"
              title="上一个关键帧 (←)"
              aria-label="上一个关键帧"
              disabled={keyframes.length === 0}
              onClick={() => gotoKeyframe(-1)}
            >
              ⏮
            </button>
            <button type="button" className="play-button" title="播放 / 暂停 (空格)" onClick={togglePlay}>
              {isPlaying ? "⏸" : "▶︎"}
            </button>
            <button
              type="button"
              className="play-button"
              title="下一个关键帧 (→)"
              aria-label="下一个关键帧"
              disabled={keyframes.length === 0}
              onClick={() => gotoKeyframe(1)}
            >
              ⏭
            </button>
            <button
              type="button"
              className="play-button seek-button"
              title="前进 5 秒 (↑)"
              aria-label="前进 5 秒"
              onClick={() => seekBy(5)}
            >
              +5s
            </button>
            <button type="button" className="primary" onClick={addKeyframe}>
              ＋ 打关键帧
            </button>
            <button type="button" onClick={updateSelectedKeyframe}>
              ⟳ 更新选中帧
            </button>
            <span className="spacer" />
            <label className="beat-toggle" title="打点时吸附到最近的节拍">
              <input
                type="checkbox"
                checked={snapBeat}
                onChange={(event) => setSnapBeat(event.target.checked)}
              />
              吸附节拍
            </label>
            <label className="beat-toggle">
              <input
                type="checkbox"
                checked={beatsVisible}
                onChange={(event) => setBeatsVisible(event.target.checked)}
              />
              节拍
            </label>
          </div>
          <div ref={timelineRef} id="timeline" aria-label="舞蹈时间线" onClick={onTimelineClick}>
            <canvas ref={waveRef} id="wave" />
            <div id="playhead" style={playheadStyle}>
              <span>{currentTime.toFixed(2)}s</span>
            </div>
            {duration > 0 &&
              keyframes.map((keyframe, index) => (
                <button
                  type="button"
                  key={`${keyframe.t}-${index}`}
                  className={`kf${index === selected ? " sel" : ""}`}
                  style={
                    {
                      left: `${(keyframe.t / duration) * 100}%`,
                      "--kf-color": keyframe.color,
                    } as React.CSSProperties
                  }
                  title={`${keyframe.t.toFixed(2)}s`}
                  onClick={(event) => {
                    event.stopPropagation();
                    selectKeyframe(index, true);
                  }}
                />
              ))}
          </div>
        </section>

        <aside className="side-rail">
          <section className="panel pose-panel" style={{ "--pose-accent": accentColor } as React.CSSProperties}>
            <div className="panel-head">
              <div>
                <h2>机器人姿势</h2>
                <p>{selectedKeyframe ? `#${selected} · ${selectedKeyframe.t.toFixed(2)}s` : "当前姿势"}</p>
              </div>
              <span className="color-chip" style={{ background: accentColor }} />
            </div>
            <div className="preview">
              <div
                ref={padRef}
                id="pad"
                title="拖动设定 左右(yaw，镜像显示) / 抬头(pitch)"
                onPointerDown={(event) => {
                  draggingRef.current = true;
                  event.currentTarget.setPointerCapture(event.pointerId);
                  setPoseFromPad(event);
                }}
                onPointerMove={(event) => {
                  if (draggingRef.current) setPoseFromPad(event);
                }}
                onPointerUp={() => {
                  draggingRef.current = false;
                }}
              >
                <div className="axis-label axis-left">+yaw</div>
                <div className="axis-label axis-right">-yaw</div>
                <div className="axis-label axis-top">up</div>
                <div className="cross-h" />
                <div className="cross-v" />
                <div className="lock">低头锁定</div>
                <div id="dot" style={dotStyle} />
              </div>
              <div className="face-shell">
                <canvas ref={faceRef} id="face" width="148" height="148" />
              </div>
            </div>

            <div className="slider-grid">
              <Slider label="yaw" value={pose.yaw} min={-90} max={90} suffix="°" onChange={(yaw) => updatePose({ yaw })} />
              <Slider label="pitch" value={pose.pitch} min={0} max={40} suffix="°" onChange={(pitch) => updatePose({ pitch })} />
              <Slider label="嘴 张" value={pose.mouth} min={0} max={100} onChange={(mouth) => updatePose({ mouth })} />
              <Slider label="眼 睁" value={pose.eye} min={0} max={100} onChange={(eye) => updatePose({ eye })} />
              <Slider label="速度" value={pose.speed} min={100} max={1000} onChange={(speed) => updatePose({ speed })} />
            </div>

            <div className="color-row">
              <label>
                <input
                  type="checkbox"
                  checked={randomColors}
                  onChange={(event) => setRandomColors(event.target.checked)}
                />
                每帧随机配色
              </label>
              <input
                type="color"
                value={pose.color}
                onChange={(event) => updatePose({ color: event.target.value })}
              />
            </div>
          </section>

          <section className="panel workbench-panel">
            <div className="tabs" role="tablist" aria-label="编舞工作台">
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === "frames"}
                onClick={() => setActiveTab("frames")}
              >
                关键帧
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === "json"}
                onClick={() => setActiveTab("json")}
              >
                JSON
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === "deploy"}
                onClick={() => setActiveTab("deploy")}
              >
                下发
              </button>
            </div>

            {activeTab === "frames" && (
              <div className="tab-panel" role="tabpanel">
                <div className="workbench-head">
                  <h2>关键帧 ({keyframes.length})</h2>
                  <div className="head-actions">
                    <select
                      className="density-select"
                      value={markDensity}
                      title="自动打标密度"
                      onChange={(event) => setMarkDensity(event.target.value as AutoMarkDensity)}
                    >
                      <option value="bar">每小节(稳)</option>
                      <option value="half">每2拍(活泼)</option>
                      <option value="phrase">每乐句(跟词)</option>
                    </select>
                    <button type="button" className="primary" onClick={autoMark} title="用节拍+歌词自动生成一版关键帧">
                      ✨ 自动打标
                    </button>
                    <button type="button" onClick={simplify} title="Douglas-Peucker 精简：删掉多余关键帧">
                      🗜 精简
                    </button>
                    <button type="button" onClick={clearKeyframes}>
                      清空
                    </button>
                  </div>
                </div>
                <table aria-label="关键帧列表">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>时间</th>
                      <th>yaw</th>
                      <th>pitch</th>
                      <th>嘴</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {keyframes.map((keyframe, index) => (
                      <tr
                        key={`${keyframe.t}-${index}`}
                        className={index === selected ? "sel" : ""}
                        onClick={(event) => {
                          if ((event.target as HTMLElement).tagName !== "BUTTON") selectKeyframe(index);
                        }}
                      >
                        <td>{index}</td>
                        <td>
                          <span className="swatch" style={{ background: keyframe.color }} />
                          {keyframe.t.toFixed(2)}s
                        </td>
                        <td>{keyframe.yaw}°</td>
                        <td>{keyframe.pitch}°</td>
                        <td>{keyframe.mouth}</td>
                        <td className="mini-actions">
                          <button type="button" className="mini" title="跳到" onClick={() => selectKeyframe(index, true)}>
                            ↦
                          </button>
                          <button type="button" className="mini" title="删除" onClick={() => deleteKeyframe(index)}>
                            ✕
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {activeTab === "json" && (
              <div className="tab-panel" role="tabpanel">
                <div className="workbench-head">
                  <h2>dance.json</h2>
                  <button type="button" className="primary" onClick={exportDance}>
                    导出 dance.json
                  </button>
                </div>
                <textarea
                  aria-label="dance.json 编辑器"
                  spellCheck={false}
                  value={danceJson}
                  onChange={(event) => setDanceJson(event.target.value)}
                />
              </div>
            )}

            {activeTab === "deploy" && (
              <div className="tab-panel" role="tabpanel">
                <div className="workbench-head">
                  <h2>下发</h2>
                  <span className="muted">{status}</span>
                </div>
                <div className="deploy-grid">
                  <label>
                    <span>Bridge</span>
                    <input
                      type="text"
                      value={bridge}
                      title="bridge URL"
                      onChange={(event) => setBridge(event.target.value)}
                    />
                  </label>
                  <label>
                    <span>
                      Device
                      <button
                        type="button"
                        className="link-button"
                        title="刷新已连接设备"
                        onClick={() => void refreshChannels()}
                      >
                        ⟳ 刷新
                      </button>
                    </span>
                    <input
                      type="text"
                      value={device}
                      title="deviceId"
                      onChange={(event) => setDevice(event.target.value)}
                    />
                    <span className="device-channels">
                      {danceChannels.length === 0 ? (
                        <span className="muted">无已连接舞蹈通道</span>
                      ) : (
                        danceChannels.map((id) => (
                          <button
                            type="button"
                            key={id}
                            className={`device-chip${id === device ? " is-on" : ""}`}
                            title={`选择 ${id}`}
                            onClick={() => setDevice(id)}
                          >
                            {id}
                          </button>
                        ))
                      )}
                    </span>
                  </label>
                  <label>
                    <span>播放延迟</span>
                    <input
                      type="number"
                      value={playDelay}
                      min={0}
                      max={2000}
                      step={50}
                      className="delay"
                      onChange={(event) => setPlayDelay(Number(event.target.value))}
                    />
                  </label>
                </div>
                <button type="button" className="hot deploy-button" onClick={pushDance}>
                  推到设备 + 播放音乐
                </button>
              </div>
            )}
          </section>
        </aside>
      </main>
    </>
  );
}

type SliderProps = {
  label: string;
  value: number;
  min: number;
  max: number;
  suffix?: string;
  onChange: (value: number) => void;
};

function Slider({ label, value, min, max, suffix = "", onChange }: SliderProps) {
  return (
    <div className="ctl">
      <label>{label}</label>
      <input
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <span>
        {value}
        {suffix}
      </span>
    </div>
  );
}
