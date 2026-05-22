import { Composition, type CalculateMetadataFunction } from "remotion";
import { DemoVideo, type DemoTimeline } from "./DemoVideo";

const defaultTimeline: DemoTimeline = {
  title: "GRC Agent Programmatic Demo",
  classification:
    "Release-validated subset + beta-validated graph operations; not production-ready",
  health: {
    status: "unknown",
    context_verified: false,
  },
  safety_requirements: {},
  steps: [],
};

const calculateMetadata: CalculateMetadataFunction<DemoTimeline> = ({ props }) => {
  const stepCount = Math.max(1, props.steps?.length ?? 0);
  return {
    durationInFrames: 150 + stepCount * 150,
    fps: 30,
    width: 1920,
    height: 1080,
    props,
  };
};

export const RemotionRoot = () => {
  return (
    <Composition
      id="Demo"
      component={DemoVideo}
      durationInFrames={1200}
      fps={30}
      width={1920}
      height={1080}
      defaultProps={defaultTimeline}
      calculateMetadata={calculateMetadata}
    />
  );
};

