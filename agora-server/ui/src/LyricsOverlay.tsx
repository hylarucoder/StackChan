import { useEffect, useRef, useState, type RefObject } from "react";

export type LyricCue = {
  index: number;
  start: number;
  end: number;
  text: string;
};

type Props = {
  audioRef: RefObject<HTMLAudioElement | null>;
  beatsRef: RefObject<number[]>;
  cues: LyricCue[];
  accentColor: string;
  visible: boolean;
};

function activeCueIndex(cues: LyricCue[], t: number): number {
  // cues are ordered; small list, linear scan is fine.
  let found = -1;
  for (let i = 0; i < cues.length; i += 1) {
    if (t >= cues[i].start && t < cues[i].end) return i;
    if (t >= cues[i].start) found = i;
    else break;
  }
  // hold the last sung line through short instrumental gaps (< 1.2s)
  if (found >= 0 && t - cues[found].end < 1.2) return found;
  return -1;
}

function beatPulse(beats: number[], t: number): number {
  if (beats.length === 0) return 0;
  let last = -1;
  for (let i = 0; i < beats.length; i += 1) {
    if (beats[i] <= t + 1e-3) last = beats[i];
    else break;
  }
  return last >= 0 ? Math.exp(-(t - last) * 7.5) : 0;
}

export function LyricsOverlay({ audioRef, beatsRef, cues, accentColor, visible }: Props) {
  const [active, setActive] = useState(-1);
  const fillRef = useRef<HTMLSpanElement | null>(null);
  const throbRef = useRef<HTMLDivElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const activeRef = useRef(-1);

  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  useEffect(() => {
    if (!visible || cues.length === 0) return;
    const tick = () => {
      const t = audioRef.current?.currentTime ?? 0;
      const idx = activeCueIndex(cues, t);
      if (idx !== activeRef.current) {
        activeRef.current = idx;
        setActive(idx);
      }
      const cue = idx >= 0 ? cues[idx] : null;
      // karaoke wipe: imperative width update, no per-frame React render
      if (fillRef.current) {
        const span = cue ? cue.end - cue.start : 0;
        const p = cue && span > 0 ? (t - cue.start) / span : 0;
        fillRef.current.style.width = `${Math.max(0, Math.min(1, p)) * 100}%`;
      }
      // beat-synced throb: whole line punches on every kick
      if (throbRef.current) {
        const pulse = beatPulse(beatsRef.current ?? [], t);
        throbRef.current.style.setProperty("--beat", pulse.toFixed(3));
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [audioRef, beatsRef, cues, visible]);

  if (!visible || cues.length === 0) return null;

  const current = active >= 0 ? cues[active] : null;
  const prev = active > 0 ? cues[active - 1] : null;
  const next = active >= 0 && active + 1 < cues.length ? cues[active + 1] : null;

  return (
    <div className="lyrics-overlay" style={{ "--lyric-accent": accentColor } as React.CSSProperties}>
      <div className="lyric lyric-prev">{prev?.text ?? ""}</div>
      {current ? (
        <div className="lyric lyric-active" ref={throbRef}>
          <div className="lyric-slam" key={current.index}>
            <span className="lyric-base">{current.text}</span>
            <span className="lyric-fill" ref={fillRef} aria-hidden="true">
              {current.text}
            </span>
          </div>
        </div>
      ) : (
        <div className="lyric lyric-idle">♪</div>
      )}
      <div className="lyric lyric-next">{next?.text ?? ""}</div>
    </div>
  );
}
