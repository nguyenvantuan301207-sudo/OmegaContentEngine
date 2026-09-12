"use client";

import { useEffect, useState, type CSSProperties } from "react";
import { Alert, EmptyState, PageSection, StatusBadge } from "@/components/ui";
import {
  getMediaArtifactStreamUrl,
  type MediaArtifact,
  type NarrationSegment,
  type ProductionAsset,
  type ProductionQAResult,
  type ProductionRenderJob,
  type ProductionScene,
  type RenderPlan,
  type SubtitleCue,
} from "@/lib/api";
import {
  TechnicalDetails,
  WorkflowStatusSummary,
  statusTone,
} from "./WorkflowPrimitives";

type SubtitlePreviewStyle = {
  preset: "Clean" | "Bold" | "Caption Box" | "Karaoke Highlight" | "Minimal";
  position: "Bottom" | "Lower Third" | "Center";
  size: "Small" | "Medium" | "Large";
  weight: "Regular" | "Medium" | "Bold";
  alignment: "Left" | "Center";
  background: "None" | "Shadow" | "Outline" | "Solid Box";
  safeMargin: "Compact" | "Normal" | "Wide";
};

const SUBTITLE_PREVIEW_DEFAULT: SubtitlePreviewStyle = {
  preset: "Clean",
  position: "Bottom",
  size: "Medium",
  weight: "Medium",
  alignment: "Center",
  background: "Shadow",
  safeMargin: "Normal",
};
const SUBTITLE_PREVIEW_STORAGE_KEY = "omega.subtitle-preview-style.v1";

export function TimelinePanel({
  scenes,
  narration,
  subtitles,
}: {
  scenes: ProductionScene[];
  narration: NarrationSegment[];
  subtitles: SubtitleCue[];
}) {
  if (scenes.length === 0)
    return (
      <EmptyState
        title="No production timeline"
        description="Prepare the production request to compile scenes, narration, and subtitles."
      />
    );
  return (
    <div className="workflow-detail">
      <PageSection
        title="Scene timeline"
        description={`${scenes.length} scene${scenes.length === 1 ? "" : "s"} compiled from the pinned script.`}
      >
        <div className="workflow-detail">
          {scenes.map((scene) => (
            <article className="workflow-card" key={scene.id}>
              <div className="workflow-card-header">
                <div>
                  <span className="ui-eyebrow">Scene {scene.scene_order}</span>
                  <h3>{scene.scene_type.replaceAll("_", " ")}</h3>
                </div>
                <StatusBadge>
                  {(scene.estimated_duration_ms / 1000).toFixed(1)}s
                </StatusBadge>
              </div>
              <p className="workflow-copy">{scene.narration_text}</p>
              {scene.visual_intent ? (
                <p className="workflow-copy">
                  <strong>Visual direction:</strong> {scene.visual_intent}
                </p>
              ) : null}
              <TechnicalDetails data={scene} />
            </article>
          ))}
        </div>
      </PageSection>
      <PageSection
        title="Narration"
        description={`${narration.length} timed narration segment${narration.length === 1 ? "" : "s"}.`}
      >
        {narration.length === 0 ? (
          <EmptyState title="No narration segments" />
        ) : (
          <div className="workflow-card-grid">
            {narration.map((segment) => (
              <article className="workflow-card" key={segment.id}>
                <p className="workflow-copy">{segment.text}</p>
                <span className="workflow-entity-meta">
                  {segment.start_ms}ms–{segment.end_ms}ms ·{" "}
                  {(segment.duration_ms / 1000).toFixed(1)}s
                </span>
              </article>
            ))}
          </div>
        )}
      </PageSection>
      <PageSection
        title="Subtitles"
        description={`${subtitles.length} cue${subtitles.length === 1 ? "" : "s"}.`}
      >
        {subtitles.length === 0 ? (
          <EmptyState title="No subtitle cues" />
        ) : (
          <div className="workflow-data-list">
            {subtitles.map((cue) => (
              <div className="workflow-data-row" key={cue.id}>
                <span>
                  {cue.start_ms}–{cue.end_ms}ms
                </span>
                <strong>{cue.text}</strong>
              </div>
            ))}
          </div>
        )}
      </PageSection>
    </div>
  );
}

export function RenderPanel({
  assets,
  plan,
  jobs,
}: {
  assets: ProductionAsset[];
  plan: RenderPlan | null;
  jobs: ProductionRenderJob[];
}) {
  const failed = jobs.filter((job) => job.state === "FAILED");
  return (
    <div className="workflow-detail">
      {failed.map((job) => (
        <Alert
          tone="danger"
          title={`Render failed · attempt ${job.attempt}/${job.max_attempts}`}
          key={job.id}
        >
          {job.sanitized_error ||
            job.error_code ||
            "No sanitized failure detail was provided."}
        </Alert>
      ))}
      <PageSection
        title="Render plan"
        description="Authoritative render dimensions, codecs, and duration."
      >
        {plan ? (
          <>
            <WorkflowStatusSummary
              metrics={[
                { label: "Dimensions", value: `${plan.width}×${plan.height}` },
                { label: "Frame rate", value: `${plan.fps} fps` },
                {
                  label: "Video / audio",
                  value: `${plan.video_codec} / ${plan.audio_codec}`,
                },
                {
                  label: "Duration",
                  value: `${(plan.total_duration_ms / 1000).toFixed(2)}s`,
                },
                { label: "Version", value: `v${plan.version}` },
              ]}
            />
            <TechnicalDetails
              label="Render manifests"
              data={{
                id: plan.id,
                scene_manifest: plan.scene_manifest,
                audio_manifest: plan.audio_manifest,
                subtitle_manifest: plan.subtitle_manifest,
              }}
            />
          </>
        ) : (
          <EmptyState
            title="No render plan"
            description="Prepare the request before submitting a render."
          />
        )}
      </PageSection>
      <PageSection
        title="Render jobs"
        description="Attempts and failures remain visible in execution order."
      >
        {jobs.length === 0 ? (
          <EmptyState title="No render jobs" />
        ) : (
          <div className="workflow-card-grid">
            {jobs.map((job) => (
              <article
                className={`workflow-card ${job.state === "FAILED" ? "workflow-error-card" : ""}`}
                key={job.id}
              >
                <div className="workflow-card-header">
                  <h3>
                    Attempt {job.attempt} of {job.max_attempts}
                  </h3>
                  <StatusBadge tone={statusTone(job.state)}>
                    {job.state}
                  </StatusBadge>
                </div>
                {job.sanitized_error || job.error_code ? (
                  <p className="workflow-copy">
                    {job.sanitized_error || job.error_code}
                  </p>
                ) : null}
                <TechnicalDetails data={job} />
              </article>
            ))}
          </div>
        )}
      </PageSection>
      <PageSection
        title="Production assets"
        description={`${assets.length} source or generated asset${assets.length === 1 ? "" : "s"} linked to the request.`}
      >
        {assets.length === 0 ? (
          <EmptyState title="No production assets" />
        ) : (
          <div className="workflow-card-grid">
            {assets.map((asset) => (
              <article className="workflow-card" key={asset.id}>
                <div className="workflow-card-header">
                  <h3>{asset.asset_type.replaceAll("_", " ")}</h3>
                  <StatusBadge>{asset.license_status}</StatusBadge>
                </div>
                <div className="workflow-data-list">
                  <div className="workflow-data-row">
                    <span>Provider</span>
                    <strong>{asset.provider_type}</strong>
                  </div>
                  <div className="workflow-data-row">
                    <span>Media type</span>
                    <strong>{asset.mime_type}</strong>
                  </div>
                  <div className="workflow-data-row">
                    <span>Dimensions</span>
                    <strong>
                      {asset.width && asset.height
                        ? `${asset.width}×${asset.height}`
                        : "Not applicable"}
                    </strong>
                  </div>
                </div>
                <TechnicalDetails data={asset} />
              </article>
            ))}
          </div>
        )}
      </PageSection>
    </div>
  );
}

export function ArtifactPanel({
  channelId,
  requestId,
  artifacts,
  selected,
  busy,
  archived,
  onSelect,
  onPublish,
}: {
  channelId: string;
  requestId: string;
  artifacts: MediaArtifact[];
  selected: MediaArtifact | null;
  busy: boolean;
  archived: boolean;
  onSelect: (version: number) => void;
  onPublish: () => void;
}) {
  if (!selected)
    return (
      <EmptyState
        title="No rendered artifact"
        description="Submit a prepared request to the renderer to produce a media artifact."
      />
    );
  return (
    <PageSection
      title="Rendered media"
      description="Preview the current physical artifact and inspect authoritative delivery metadata."
      actions={
        <div className="ui-inline-actions" aria-label="Artifact revisions">
          {artifacts.map((artifact) => (
            <button
              type="button"
              className={`btn btn-sm ${artifact.version === selected.version ? "btn-primary" : "btn-secondary"}`}
              onClick={() => onSelect(artifact.version)}
              key={artifact.id}
            >
              v{artifact.version}
              {artifact.is_current ? " · current" : ""}
            </button>
          ))}
        </div>
      }
    >
      <video
        className="workflow-media"
        controls
        preload="metadata"
        src={getMediaArtifactStreamUrl(channelId, requestId, selected.id)}
      >
        Your browser does not support video playback.
      </video>
      <WorkflowStatusSummary
        metrics={[
          { label: "Type", value: selected.artifact_type },
          {
            label: "Dimensions",
            value:
              selected.width && selected.height
                ? `${selected.width}×${selected.height}`
                : "Not reported",
          },
          {
            label: "Duration",
            value: selected.duration_ms
              ? `${(selected.duration_ms / 1000).toFixed(2)}s`
              : "Not reported",
          },
          {
            label: "File size",
            value: `${(selected.file_size_bytes / 1024 / 1024).toFixed(2)} MB`,
          },
          { label: "Media type", value: selected.mime_type },
        ]}
      />
      <div className="workflow-action-bar">
        <TechnicalDetails
          data={{
            id: selected.id,
            render_job_id: selected.render_job_id,
            storage_uri: selected.storage_uri,
            content_hash: selected.content_hash,
          }}
        />
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy || archived}
          onClick={onPublish}
        >
          Prepare publication
        </button>
      </div>
    </PageSection>
  );
}

export function ProductionQAPanel({ qa }: { qa: ProductionQAResult | null }) {
  if (!qa)
    return (
      <EmptyState
        title="No production QA result"
        description="QA becomes available after a rendered artifact is evaluated."
      />
    );
  return (
    <PageSection
      title="Production quality assurance"
      description="Artifact acceptance and blocking findings are displayed explicitly."
    >
      <WorkflowStatusSummary
        metrics={[
          { label: "QA status", value: qa.status, status: qa.status },
          { label: "Findings", value: qa.findings.length },
        ]}
      />
      {qa.findings.length === 0 ? (
        <Alert tone="success" title="Artifact accepted">
          Production QA completed with no findings.
        </Alert>
      ) : (
        <div className="workflow-card-grid">
          {qa.findings.map((finding, index) => (
            <article
              className={`workflow-card ${finding.severity === "BLOCKING" || finding.severity === "ERROR" ? "workflow-error-card" : ""}`}
              key={`${finding.rule_code}-${index}`}
            >
              <div className="workflow-card-header">
                <h3>{finding.rule_code}</h3>
                <StatusBadge tone={statusTone(finding.severity)}>
                  {finding.severity}
                </StatusBadge>
              </div>
              <p className="workflow-copy">{finding.message}</p>
              <TechnicalDetails data={finding.details} />
            </article>
          ))}
        </div>
      )}
      <TechnicalDetails
        data={{
          id: qa.id,
          artifact_id: qa.artifact_id,
          executed_at: qa.executed_at,
        }}
      />
    </PageSection>
  );
}

export function StoryboardPanel({
  scenes,
  onSelectScene,
}: {
  scenes: ProductionScene[];
  onSelectScene?: (scene: ProductionScene) => void;
}) {
  if (scenes.length === 0) {
    return (
      <EmptyState
        title="No scenes generated"
        description="Prepare the production request to compile visual scenes and storyboards."
      />
    );
  }

  return (
    <div className="workflow-detail">
      <PageSection
        title="Visual Storyboard"
        description={`${scenes.length} scene${scenes.length === 1 ? "" : "s"} planned for production.`}
      >
        <div className="storyboard">
          {scenes.map((scene) => (
            <div
              className="story"
              key={scene.id}
              onClick={() => onSelectScene?.(scene)}
              style={{ cursor: onSelectScene ? "pointer" : "default" }}
            >
              <div className="story-thumb">
                <span style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.08em", color: "var(--text-muted)" }}>
                  {scene.scene_type}
                </span>
              </div>
              <div className="between" style={{ marginBottom: "4px" }}>
                <b style={{ fontSize: "13px", color: "var(--text-primary)" }}>Scene {scene.scene_order}</b>
                <StatusBadge tone="info">{(scene.estimated_duration_ms / 1000).toFixed(1)}s</StatusBadge>
              </div>
              <p style={{ color: "var(--text)", margin: "4px 0", fontSize: "12px", lineHeight: "1.4" }}>
                {scene.narration_text}
              </p>
              {scene.visual_intent && (
                <div className="small muted" style={{ fontSize: "11px", marginTop: "4px" }}>
                  <strong>Visual:</strong> {scene.visual_intent}
                </div>
              )}
            </div>
          ))}
        </div>
      </PageSection>
    </div>
  );
}

export function SubtitlePanel({
  subtitles,
}: {
  subtitles: SubtitleCue[];
}) {
  const [style, setStyle] = useState<SubtitlePreviewStyle>(SUBTITLE_PREVIEW_DEFAULT);
  const [selectedCueId, setSelectedCueId] = useState<string | null>(subtitles[0]?.id ?? null);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(SUBTITLE_PREVIEW_STORAGE_KEY);
      if (stored) setStyle({ ...SUBTITLE_PREVIEW_DEFAULT, ...JSON.parse(stored) });
    } catch {
      // Preview preferences are optional and never affect persisted production data.
    }
  }, []);

  useEffect(() => {
    if (!subtitles.some((cue) => cue.id === selectedCueId)) {
      setSelectedCueId(subtitles[0]?.id ?? null);
    }
  }, [selectedCueId, subtitles]);

  const updateStyle = <K extends keyof SubtitlePreviewStyle>(key: K, value: SubtitlePreviewStyle[K]) => {
    const next = { ...style, [key]: value };
    setStyle(next);
    try {
      window.localStorage.setItem(SUBTITLE_PREVIEW_STORAGE_KEY, JSON.stringify(next));
    } catch {
      // Keep the live preview useful even when browser storage is unavailable.
    }
  };

  const applyPreset = (preset: SubtitlePreviewStyle["preset"]) => {
    const presets: Record<SubtitlePreviewStyle["preset"], SubtitlePreviewStyle> = {
      Clean: { ...SUBTITLE_PREVIEW_DEFAULT, preset: "Clean" },
      Bold: { ...SUBTITLE_PREVIEW_DEFAULT, preset: "Bold", position: "Lower Third", size: "Large", weight: "Bold", background: "Outline" },
      "Caption Box": { ...SUBTITLE_PREVIEW_DEFAULT, preset: "Caption Box", background: "Solid Box" },
      "Karaoke Highlight": { ...SUBTITLE_PREVIEW_DEFAULT, preset: "Karaoke Highlight", position: "Lower Third", weight: "Bold" },
      Minimal: { ...SUBTITLE_PREVIEW_DEFAULT, preset: "Minimal", size: "Small", weight: "Regular", alignment: "Left", background: "None", safeMargin: "Wide" },
    };
    const next = presets[preset];
    setStyle(next);
    try {
      window.localStorage.setItem(SUBTITLE_PREVIEW_STORAGE_KEY, JSON.stringify(next));
    } catch {
      // Local persistence is optional.
    }
  };

  const selectedCue = subtitles.find((cue) => cue.id === selectedCueId) ?? subtitles[0] ?? null;
  const previewStyle: CSSProperties = {
    alignItems: style.position === "Center" ? "center" : "flex-end",
    justifyContent: style.alignment === "Left" ? "flex-start" : "center",
    paddingBottom: style.position === "Lower Third" ? "24%" : style.position === "Center" ? undefined : style.safeMargin === "Compact" ? 18 : style.safeMargin === "Wide" ? 64 : 38,
    paddingInline: style.safeMargin === "Compact" ? 18 : style.safeMargin === "Wide" ? 64 : 38,
    textAlign: style.alignment.toLowerCase() as CSSProperties["textAlign"],
  };
  const captionStyle: CSSProperties = {
    fontSize: style.size === "Small" ? 16 : style.size === "Large" ? 28 : 21,
    fontWeight: style.weight === "Regular" ? 400 : style.weight === "Bold" ? 800 : 600,
    background: style.background === "Solid Box" ? "rgba(4, 8, 15, .88)" : "transparent",
    boxShadow: style.background === "Shadow" ? "0 3px 12px rgba(0, 0, 0, .95)" : "none",
    WebkitTextStroke: style.background === "Outline" ? "1.25px rgba(0, 0, 0, .92)" : undefined,
  };

  const control = <K extends keyof SubtitlePreviewStyle>(label: string, key: K, values: readonly SubtitlePreviewStyle[K][]) => (
    <fieldset className="subtitle-style-control">
      <legend>{label}</legend>
      <div className="subtitle-option-group">
        {values.map((value) => <button type="button" key={String(value)} aria-pressed={style[key] === value} className={style[key] === value ? "active" : ""} onClick={() => updateStyle(key, value)}>{String(value)}</button>)}
      </div>
    </fieldset>
  );

  return (
    <div className="workflow-detail subtitle-workspace">
      <PageSection
        title="Subtitle Style"
        description="Local appearance preview using persisted subtitle cues. Render customization is not yet supported."
        actions={<StatusBadge tone="warning">Preview only</StatusBadge>}
      >
        <div className="subtitle-style-layout">
          <div className="subtitle-controls">
            <fieldset className="subtitle-style-control subtitle-presets">
              <legend>Style preset</legend>
              <div className="subtitle-option-group">
                {(["Clean", "Bold", "Caption Box", "Karaoke Highlight", "Minimal"] as const).map((preset) => <button type="button" key={preset} aria-pressed={style.preset === preset} className={style.preset === preset ? "active" : ""} onClick={() => applyPreset(preset)}>{preset}</button>)}
              </div>
            </fieldset>
            {control("Position", "position", ["Bottom", "Lower Third", "Center"])}
            {control("Size", "size", ["Small", "Medium", "Large"])}
            {control("Weight", "weight", ["Regular", "Medium", "Bold"])}
            {control("Alignment", "alignment", ["Left", "Center"])}
            {control("Background", "background", ["None", "Shadow", "Outline", "Solid Box"])}
            {control("Safe margin", "safeMargin", ["Compact", "Normal", "Wide"])}
            <div className="subtitle-render-truth">
              <strong>Renderer style is fixed</strong>
              <span>Font: renderer-selected Sans / Arial. These controls are stored only in this browser and are not applied to render jobs.</span>
              {style.preset === "Karaoke Highlight" && <span>Karaoke is preview-only here: persisted cues do not expose authoritative word-level timestamps.</span>}
            </div>
          </div>
          <div className="subtitle-preview-card">
            <div className="subtitle-preview-meta"><span>Live cue preview</span><strong>{selectedCue ? `${(selectedCue.start_ms / 1000).toFixed(2)}s – ${(selectedCue.end_ms / 1000).toFixed(2)}s` : "No cue selected"}</strong></div>
            <div className="subtitle-preview-stage" style={previewStyle} aria-live="polite">
              {selectedCue ? <span className={`subtitle-preview-caption background-${style.background.toLowerCase().replaceAll(" ", "-")}`} style={captionStyle}>{selectedCue.text}</span> : <div className="subtitle-preview-empty"><strong>No persisted subtitle text</strong><span>Prepare a production with authoritative cues to preview subtitle appearance.</span></div>}
            </div>
          </div>
        </div>
      </PageSection>
      <PageSection
        title="Subtitle cues"
        description={`${subtitles.length} cue${subtitles.length === 1 ? "" : "s"} aligned to narration.`}
      >
        {subtitles.length === 0 ? <EmptyState title="No subtitle cues" description="No authoritative subtitle track exists for this production." /> : <div className="card subtitle-cue-table-wrap">
          <table className="v2-table">
            <thead>
              <tr>
                <th style={{ width: "60px" }}>Order</th>
                <th style={{ width: "160px" }}>Timestamp</th>
                <th>Subtitle Text</th>
              </tr>
            </thead>
            <tbody>
              {subtitles.map((cue) => (
                <tr key={cue.id} className={selectedCue?.id === cue.id ? "is-selected" : ""}>
                  <td>
                    <span className="small muted">#{cue.cue_order}</span>
                  </td>
                  <td>
                    <span className="text-mono small" style={{ fontSize: "11px" }}>
                      {(cue.start_ms / 1000).toFixed(2)}s – {(cue.end_ms / 1000).toFixed(2)}s
                    </span>
                  </td>
                  <td>
                    <button type="button" className="subtitle-cue-select" aria-pressed={selectedCue?.id === cue.id} onClick={() => setSelectedCueId(cue.id)}>{cue.text}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>}
      </PageSection>
    </div>
  );
}
