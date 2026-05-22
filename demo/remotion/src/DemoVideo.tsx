import React from "react";
import {
  AbsoluteFill,
  Easing,
  Img,
  Sequence,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

type TimelineStep = {
  step_label: string;
  user_prompt: string;
  assistant_summary?: string;
  tool_name?: string;
  operation_kind?: string;
  graph_delta?: Record<string, unknown>;
  validation_status?: string;
  mutation?: boolean;
  screenshot_path?: string | null;
  final_graph_path?: string | null;
};

export type DemoTimeline = {
  title: string;
  classification: string;
  health: {
    status?: string;
    context_verified?: boolean;
    actual_context_tokens?: number;
    desired_context_tokens?: number;
    model_tools?: string[];
  };
  safety_requirements: Record<string, unknown>;
  steps: TimelineStep[];
};

const colors = {
  ink: "#17202a",
  muted: "#53616f",
  paper: "#f7f8f8",
  line: "#d4dadd",
  green: "#1f7a4d",
  blue: "#145da0",
  orange: "#9a5b0d",
  red: "#b42318",
};

const card: React.CSSProperties = {
  border: `1px solid ${colors.line}`,
  borderRadius: 8,
  background: "#ffffff",
  boxShadow: "0 10px 25px rgba(23, 32, 42, 0.08)",
};

const textBlock = (lines: string[], maxChars = 210) => {
  const text = lines.filter(Boolean).join(" ");
  return text.length > maxChars ? `${text.slice(0, maxChars - 1).trim()}…` : text;
};

const imageSrc = (path?: string | null) => {
  if (!path) {
    return null;
  }
  if (path.startsWith("http://") || path.startsWith("https://") || path.startsWith("data:")) {
    return path;
  }
  if (path.startsWith("file://")) {
    return path;
  }
  return `file://${path}`;
};

const Fade: React.FC<{ children: React.ReactNode; delay?: number }> = ({ children, delay = 0 }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame - delay, [0, 24], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const translateY = interpolate(frame - delay, [0, 24], [24, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  return <div style={{ opacity, transform: `translateY(${translateY}px)` }}>{children}</div>;
};

const TitleCard: React.FC<{ timeline: DemoTimeline }> = ({ timeline }) => {
  return (
    <AbsoluteFill style={{ background: colors.paper, padding: 96, justifyContent: "center" }}>
      <Fade>
        <div style={{ fontSize: 76, fontWeight: 800, color: colors.ink, letterSpacing: 0 }}>
          {timeline.title || "GRC Agent Programmatic Demo"}
        </div>
      </Fade>
      <Fade delay={12}>
        <div style={{ marginTop: 30, fontSize: 34, lineHeight: 1.35, color: colors.muted, width: 1220 }}>
          Real GRC Agent prompts, real wrapper calls, real graph deltas, and explicit save/load evidence.
        </div>
      </Fade>
      <Fade delay={24}>
        <div style={{ marginTop: 48, fontSize: 30, color: colors.orange }}>{timeline.classification}</div>
      </Fade>
    </AbsoluteFill>
  );
};

const HealthCard: React.FC<{ timeline: DemoTimeline }> = ({ timeline }) => {
  const tools = timeline.health.model_tools ?? [];
  return (
    <AbsoluteFill style={{ background: "#ffffff", padding: 80 }}>
      <Fade>
        <div style={{ fontSize: 58, fontWeight: 780, color: colors.ink }}>Runtime Health</div>
      </Fade>
      <Fade delay={10}>
        <div style={{ ...card, marginTop: 40, padding: 42, width: 1220 }}>
          <div style={{ display: "flex", gap: 28, alignItems: "center" }}>
            <div
              style={{
                width: 22,
                height: 22,
                borderRadius: 999,
                background: timeline.health.status === "ok" ? colors.green : colors.red,
              }}
            />
            <div style={{ fontSize: 38, color: colors.ink }}>
              health={timeline.health.status ?? "unknown"} · context_verified=
              {String(timeline.health.context_verified)}
            </div>
          </div>
          <div style={{ marginTop: 28, fontSize: 29, color: colors.muted }}>
            context {timeline.health.actual_context_tokens ?? "?"} /{" "}
            {timeline.health.desired_context_tokens ?? "?"} tokens
          </div>
          <div style={{ marginTop: 36, fontSize: 25, color: colors.muted }}>
            Model-facing wrappers: {tools.join(", ") || "not reported"}
          </div>
        </div>
      </Fade>
    </AbsoluteFill>
  );
};

const StepCard: React.FC<{ step: TimelineStep; index: number }> = ({ step, index }) => {
  const src = imageSrc(step.screenshot_path);
  const delta = JSON.stringify(step.graph_delta ?? {}, null, 2);
  return (
    <AbsoluteFill style={{ background: colors.paper, padding: 64 }}>
      <Fade>
        <div style={{ fontSize: 32, color: colors.blue, fontWeight: 750 }}>Step {index + 1}</div>
        <div style={{ fontSize: 58, color: colors.ink, fontWeight: 800, marginTop: 10 }}>
          {step.step_label}
        </div>
      </Fade>
      <div style={{ display: "flex", gap: 32, marginTop: 34 }}>
        <Fade delay={8}>
          <div style={{ ...card, padding: 34, width: 870, minHeight: 650 }}>
            <div style={{ fontSize: 24, color: colors.muted, fontWeight: 700 }}>User prompt</div>
            <div style={{ fontSize: 31, lineHeight: 1.32, color: colors.ink, marginTop: 14 }}>
              {textBlock([step.user_prompt], 300)}
            </div>
            <div style={{ marginTop: 34, fontSize: 24, color: colors.muted, fontWeight: 700 }}>
              Assistant summary
            </div>
            <div style={{ fontSize: 27, lineHeight: 1.34, color: colors.ink, marginTop: 12 }}>
              {textBlock([step.assistant_summary ?? ""], 280) || "No assistant text recorded."}
            </div>
            <div style={{ display: "flex", gap: 18, marginTop: 36, flexWrap: "wrap" }}>
              <Badge label="Tool" value={step.tool_name || "none"} color={colors.blue} />
              <Badge label="Operation" value={step.operation_kind || "n/a"} color={colors.orange} />
              <Badge
                label="Validation"
                value={step.validation_status || "not run"}
                color={step.validation_status === "valid" ? colors.green : colors.muted}
              />
              <Badge label="Mutation" value={step.mutation ? "yes" : "no"} color={step.mutation ? colors.green : colors.muted} />
            </div>
          </div>
        </Fade>
        <Fade delay={18}>
          <div style={{ ...card, padding: 30, width: 830, minHeight: 650 }}>
            <div style={{ fontSize: 24, color: colors.muted, fontWeight: 700 }}>Graph delta</div>
            <pre
              style={{
                marginTop: 16,
                fontSize: 23,
                lineHeight: 1.24,
                whiteSpace: "pre-wrap",
                color: colors.ink,
                fontFamily: "Menlo, Consolas, monospace",
              }}
            >
              {delta.length > 840 ? `${delta.slice(0, 839)}…` : delta}
            </pre>
            {src ? (
              <div style={{ marginTop: 18 }}>
                <Img
                  src={src}
                  style={{
                    maxWidth: "100%",
                    maxHeight: 250,
                    objectFit: "contain",
                    border: `1px solid ${colors.line}`,
                  }}
                />
              </div>
            ) : null}
          </div>
        </Fade>
      </div>
    </AbsoluteFill>
  );
};

const Badge: React.FC<{ label: string; value: string; color: string }> = ({ label, value, color }) => {
  return (
    <div style={{ border: `1px solid ${colors.line}`, borderRadius: 8, padding: "12px 16px" }}>
      <div style={{ color: colors.muted, fontSize: 18 }}>{label}</div>
      <div style={{ color, fontSize: 25, fontWeight: 760, marginTop: 2 }}>{value}</div>
    </div>
  );
};

const FinalCard: React.FC<{ timeline: DemoTimeline }> = ({ timeline }) => {
  const safety = timeline.safety_requirements ?? {};
  const checks = [
    ["Original graph unchanged", safety.original_graph_not_mutated],
    ["Explicit save", safety.explicit_save],
    ["Validation succeeded", safety.validation_succeeded],
    ["Raw legacy attempts", safety.raw_legacy_attempts],
    ["Failed-validation commits", safety.failed_validation_commits],
    ["No secrets in artifacts", safety.no_secrets_in_artifacts],
  ];
  return (
    <AbsoluteFill style={{ background: "#ffffff", padding: 84 }}>
      <Fade>
        <div style={{ fontSize: 58, fontWeight: 800, color: colors.ink }}>Final Evidence</div>
      </Fade>
      <Fade delay={10}>
        <div style={{ ...card, marginTop: 42, padding: 42, width: 1260 }}>
          {checks.map(([label, value]) => (
            <div key={String(label)} style={{ display: "flex", justifyContent: "space-between", fontSize: 32, marginBottom: 20 }}>
              <span style={{ color: colors.ink }}>{String(label)}</span>
              <span style={{ color: value === true || value === 0 ? colors.green : colors.orange, fontWeight: 780 }}>
                {String(value)}
              </span>
            </div>
          ))}
        </div>
      </Fade>
      <Fade delay={24}>
        <div style={{ marginTop: 46, fontSize: 32, color: colors.orange }}>{timeline.classification}</div>
      </Fade>
    </AbsoluteFill>
  );
};

export const DemoVideo: React.FC<DemoTimeline> = (timeline) => {
  const { fps } = useVideoConfig();
  const titleDuration = fps * 4;
  const healthDuration = fps * 4;
  const stepDuration = fps * 5;
  const finalDuration = fps * 5;
  const steps = timeline.steps ?? [];
  return (
    <AbsoluteFill>
      <Sequence from={0} durationInFrames={titleDuration}>
        <TitleCard timeline={timeline} />
      </Sequence>
      <Sequence from={titleDuration} durationInFrames={healthDuration}>
        <HealthCard timeline={timeline} />
      </Sequence>
      {steps.map((step, index) => (
        <Sequence
          key={`${step.step_label}-${index}`}
          from={titleDuration + healthDuration + index * stepDuration}
          durationInFrames={stepDuration}
        >
          <StepCard step={step} index={index} />
        </Sequence>
      ))}
      <Sequence
        from={titleDuration + healthDuration + steps.length * stepDuration}
        durationInFrames={finalDuration}
      >
        <FinalCard timeline={timeline} />
      </Sequence>
    </AbsoluteFill>
  );
};

