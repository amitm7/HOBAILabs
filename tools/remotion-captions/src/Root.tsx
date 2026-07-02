import React from "react";
import { Composition } from "remotion";
import { CaptionOverlay, overlaySchema, FPS } from "./CaptionOverlay";

// Duration follows the props (the reel's length), not a hardcoded number —
// calculateMetadata reads durationSeconds from the props JSON at render time.
export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="CaptionOverlay"
      component={CaptionOverlay}
      schema={overlaySchema}
      width={1080}
      height={1920}
      fps={FPS}
      durationInFrames={10 * FPS}
      defaultProps={{
        durationSeconds: 10,
        captions: [
          { text: "Papa was an auto driver,", start: 0.4, end: 3.2, kind: "story" as const },
          { text: "I got selected…", start: 3.6, end: 6.8, kind: "hero" as const, accent: "#e0a32e" },
          { text: "Life changed overnight.", start: 7.0, end: 9.6, kind: "story" as const },
        ],
      }}
      calculateMetadata={({ props }) => ({
        durationInFrames: Math.max(1, Math.round(props.durationSeconds * FPS)),
        props,
      })}
    />
  );
};
