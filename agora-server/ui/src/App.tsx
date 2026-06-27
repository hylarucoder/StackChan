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

import {
  DanceFrame,
  Keyframe,
  buildSequence,
  clamp,
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
  const [bridge, setBridge] = useState("http://127.0.0.1:8000");
  const [device, setDevice] = useState("stackchan-1");
  const [playDelay, setPlayDelay] = useState(200);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [activeTab, setActiveTab] = useState<WorkbenchTab>("frames");

  const videoRef = useRef<HTMLAudioElement | null>(null);
  const stageRef = useRef<HTMLCanvasElement | null>(null);
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
  const beatsRef = useRef<number[]>([]);

  const sequence = useMemo(() => buildSequence(keyframes, duration), [keyframes, duration]);
  const selectedKeyframe = selected >= 0 ? keyframes[selected] : null;
  const accentColor = selectedKeyframe?.color || pose.color;

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
      audio.analyser.fftSize = 256;
      audio.analyser.smoothingTimeConstant = 0.8;
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
        },
        vertexShader: "void main(){ gl_Position=vec4(position.xy,0.0,1.0); }",
        fragmentShader: `
precision highp float;
uniform vec2 u_res; uniform float u_time;
uniform float u_bass, u_mid, u_treble, u_level;
const vec3 accent = vec3(0.302,0.639,1.0);
const vec3 hot    = vec3(1.0,0.365,0.451);
void main(){
  vec2 uv = (gl_FragCoord.xy - 0.5*u_res)/u_res.y;
  float r = length(uv);
  float ang = atan(uv.y, uv.x);
  vec3 col = vec3(0.02,0.03,0.05);
  vec2 gf = fract(uv*8.0); vec2 gd = min(gf, 1.0-gf);
  col += accent * smoothstep(0.045,0.0,min(gd.x,gd.y)) * 0.05;
  float rings = sin(r*22.0 - u_time*2.5 - u_bass*6.0);
  col += accent * smoothstep(0.6,1.0,rings)*exp(-r*1.5) * (0.3 + u_bass*1.7);
  float core = exp(-r*r*(12.0 - u_bass*8.0));
  col += mix(accent,hot,clamp(u_treble,0.0,1.0)) * core * (0.5 + u_level*2.2);
  col += hot * (0.5+0.5*sin(ang*12.0 + u_time*1.5)) * u_treble * exp(-r*2.5) * 0.7;
  col += accent*0.15*u_mid * exp(-abs(r-0.5-0.1*sin(u_time))*8.0);
  col *= 1.0 - 0.4*r;
  gl_FragColor = vec4(col,1.0);
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
        if (state.t0 === null) state.t0 = timestamp;
        resizeThree();
        readBands();
        if (stageRef.current && stageRef.current.width > 0) {
          state.material.uniforms.u_time.value = (timestamp - state.t0) / 1000;
          state.material.uniforms.u_bass.value = audioRef.current.smooth.bass;
          state.material.uniforms.u_mid.value = audioRef.current.smooth.mid;
          state.material.uniforms.u_treble.value = audioRef.current.smooth.treble;
          state.material.uniforms.u_level.value = audioRef.current.smooth.level;
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

  const addKeyframe = () => {
    const video = videoRef.current;
    const next = makeKeyframe({
      ...pose,
      color: randomColors ? randColor() : pose.color,
      t: Number((video?.currentTime || 0).toFixed(2)),
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
          <div id="stageWrap" style={{ "--stage-accent": accentColor } as React.CSSProperties}>
            <canvas ref={stageRef} id="stage" />
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
            <button type="button" className="play-button" title="播放 / 暂停" onClick={togglePlay}>
              {isPlaying ? "⏸" : "▶︎"}
            </button>
            <button type="button" className="primary" onClick={addKeyframe}>
              ＋ 打关键帧
            </button>
            <button type="button" onClick={updateSelectedKeyframe}>
              ⟳ 更新选中帧
            </button>
            <span className="spacer" />
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
                  <button type="button" onClick={clearKeyframes}>
                    清空
                  </button>
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
                    <span>Device</span>
                    <input
                      type="text"
                      value={device}
                      title="deviceId"
                      onChange={(event) => setDevice(event.target.value)}
                    />
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
