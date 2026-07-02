import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { z } from "zod";

export const FPS = 30;

export const overlaySchema = z.object({
  durationSeconds: z.number(),
  captions: z.array(
    z.object({
      text: z.string(),
      start: z.number(), // seconds, in the RENDERED reel (assembler.frame_timecodes)
      end: z.number(),
      kind: z.enum(["story", "hero"]),
      accent: z.string().optional(),
    })
  ),
});

type OverlayProps = z.infer<typeof overlaySchema>;

const SERIF = 'Baskerville, "Libre Baskerville", Georgia, serif';

// story-line: 1–2 line serif caption near the bottom, soft rise + fade.
const StoryLine: React.FC<{ text: string; start: number; end: number }> = ({
  text,
  start,
  end,
}) => {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  if (t < start || t > end) return null;
  const inP = interpolate(t, [start, start + 0.35], [0, 1], {
    extrapolateRight: "clamp",
  });
  const outP = interpolate(t, [end - 0.3, end], [1, 0], {
    extrapolateLeft: "clamp",
  });
  const o = Math.min(inP, outP);
  return (
    <div
      style={{
        position: "absolute",
        left: 70,
        right: 70,
        bottom: 210,
        textAlign: "center",
        fontFamily: SERIF,
        fontSize: 52,
        lineHeight: 1.3,
        color: "white",
        opacity: o,
        transform: `translateY(${(1 - inP) * 24}px)`,
        textShadow: "0 2px 14px rgba(0,0,0,.85), 0 0 3px rgba(0,0,0,.9)",
      }}
    >
      {text}
    </div>
  );
};

// hero-line: the emotional peak — larger, centered, springs in word by word.
const HeroLine: React.FC<{
  text: string;
  start: number;
  end: number;
  accent?: string;
}> = ({ text, start, end, accent }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / FPS;
  if (t < start || t > end) return null;
  const words = text.split(/\s+/);
  const outP = interpolate(t, [end - 0.35, end], [1, 0], {
    extrapolateLeft: "clamp",
  });
  return (
    <div
      style={{
        position: "absolute",
        left: 60,
        right: 60,
        top: "38%",
        textAlign: "center",
        fontFamily: SERIF,
        fontSize: 84,
        fontWeight: 700,
        lineHeight: 1.22,
        color: "white",
        opacity: outP,
        textShadow: "0 4px 26px rgba(0,0,0,.9), 0 0 4px rgba(0,0,0,.95)",
      }}
    >
      {words.map((w, i) => {
        const wordStart = (start + i * 0.14) * fps;
        const s = spring({
          frame: frame - wordStart,
          fps,
          config: { damping: 200, stiffness: 120 },
        });
        const isLast = i === words.length - 1;
        return (
          <span
            key={i}
            style={{
              display: "inline-block",
              marginRight: "0.28em",
              opacity: s,
              transform: `translateY(${(1 - s) * 30}px) scale(${0.9 + s * 0.1})`,
              color: isLast && accent ? accent : "white",
            }}
          >
            {w}
          </span>
        );
      })}
    </div>
  );
};

export const CaptionOverlay: React.FC<OverlayProps> = ({ captions }) => {
  return (
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      {captions.map((c, i) =>
        c.kind === "hero" ? (
          <HeroLine key={i} text={c.text} start={c.start} end={c.end} accent={c.accent} />
        ) : (
          <StoryLine key={i} text={c.text} start={c.start} end={c.end} />
        )
      )}
    </AbsoluteFill>
  );
};
