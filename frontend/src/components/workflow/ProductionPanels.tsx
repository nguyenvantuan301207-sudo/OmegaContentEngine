"use client";

import { useEffect, useState, type CSSProperties } from "react";
import { Alert, EmptyState, PageSection, StatusBadge } from "@/components/ui";
import {
  getChannelStyleProfile,
  updateChannelStyleProfile,
  getMediaArtifactStreamUrl,
  type ChannelStyleProfile,
  type MediaArtifact,
  type NarrationSegment,
  type ProductionAsset,
  type ProductionQAResult,
  type ProductionRenderCapabilities,
  type ProductionRenderJob,
  type ProductionRequest,
  type ProductionScene,
  type RenderArtifactProvenance,
  type RenderPlan,
  type SubtitleRenderStyle,
  type SubtitleCue,
} from "@/lib/api";
import {
  findMatchingPresetId,
  resolveSubtitleDraft,
  subtitleHorizontalAlignment,
  subtitleVerticalAlignment,
} from "@/lib/subtitle-render-ui";
import {
  TechnicalDetails,
  WorkflowStatusSummary,
  statusTone,
} from "./WorkflowPrimitives";

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

export function ProductionQAPanel({
  qa,
  renderProvenance,
}: {
  qa: ProductionQAResult | null;
  renderProvenance?: RenderArtifactProvenance | null;
}) {
  return (
    <PageSection
      title="Production quality assurance"
      description="Artifact acceptance and blocking findings are displayed explicitly."
    >
      {qa ? (
        <>
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
        </>
      ) : (
        <EmptyState
          title="No production QA result"
          description="QA becomes available after a rendered artifact is evaluated."
        />
      )}
      {renderProvenance ? (
        <PageSection
          title="Rendered artifact provenance"
          description="Read-only values returned by the renderer for the current production artifact."
        >
          {renderProvenance.text_truncated ? (
            <Alert tone="warning" title="Text truncation fallback used">
              One or more rendered scenes reached the explicit final truncation fallback.
            </Alert>
          ) : (
            <Alert tone="success" title="No text truncation reported">
              The renderer fitted scene text without its truncation fallback.
            </Alert>
          )}
          <WorkflowStatusSummary metrics={[
            { label: "Source", value: "From Artifact" },
            { label: "FPS mode", value: renderProvenance.effective_fps_mode },
            { label: "Target FPS", value: renderProvenance.target_fps },
            { label: "Subtitle style", value: renderProvenance.subtitle_style_applied ? "Recorded" : "Unavailable" },
          ]} />
          <TechnicalDetails data={renderProvenance} />
        </PageSection>
      ) : (
        <Alert tone="info" title="Artifact render provenance unavailable">
          Existing artifacts rendered before P15-B may not expose subtitle style or text-fitting provenance.
        </Alert>
      )}
      {qa ? (
        <TechnicalDetails
          data={{
            id: qa.id,
            artifact_id: qa.artifact_id,
            executed_at: qa.executed_at,
          }}
        />
      ) : null}
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
  request,
  capabilities,
  channelId,
  busy = false,
  onApply,
}: {
  subtitles: SubtitleCue[];
  request: ProductionRequest | null;
  capabilities: ProductionRenderCapabilities | null;
  channelId?: string;
  busy?: boolean;
  onApply: (style: SubtitleRenderStyle) => Promise<void>;
}) {
  const [style, setStyle] = useState<SubtitleRenderStyle | null>(null);
  const [selectedCueId, setSelectedCueId] = useState<string | null>(subtitles[0]?.id ?? null);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [channelProfile, setChannelProfile] = useState<ChannelStyleProfile | null>(null);
  const [channelNotice, setChannelNotice] = useState<string | null>(null);

  useEffect(() => {
    if (capabilities) setStyle(resolveSubtitleDraft(capabilities, request));
  }, [capabilities, request]);

  useEffect(() => {
    if (channelId) {
      void getChannelStyleProfile(channelId)
        .then(setChannelProfile)
        .catch(() => setChannelProfile(null));
    }
  }, [channelId]);

  useEffect(() => {
    if (!subtitles.some((cue) => cue.id === selectedCueId)) {
      setSelectedCueId(subtitles[0]?.id ?? null);
    }
  }, [selectedCueId, subtitles]);

  if (!capabilities || !style) {
    return <EmptyState title="Loading renderer capabilities" description="Subtitle controls wait for authoritative backend defaults." />;
  }

  const updateStyle = <K extends keyof SubtitleRenderStyle>(key: K, value: SubtitleRenderStyle[K]) => {
    setApplyError(null);
    setStyle({ ...style, [key]: value });
  };

  const activePresetId = findMatchingPresetId(capabilities, style);

  const handlePresetChange = (presetId: string) => {
    if (presetId === "custom") return;
    const found = capabilities.subtitle.presets?.find((p) => p.id === presetId);
    if (found) {
      setApplyError(null);
      setStyle(found.style);
    }
  };

  const handleSaveAsChannelDefault = async () => {
    if (!channelId) return;
    try {
      setChannelNotice(null);
      const updated = await updateChannelStyleProfile(channelId, {
        preset_id: activePresetId ?? "default",
        custom_subtitle_style: activePresetId ? null : style,
      });
      setChannelProfile(updated);
      setChannelNotice("Channel style profile saved as default.");
    } catch (err) {
      setChannelNotice(err instanceof Error ? err.message : "Failed to save channel style profile.");
    }
  };

  const appliedStyle = request?.metadata?.render_settings?.subtitle_style ?? null;
  const artifactProvenance = request?.metadata?.render_provenance ?? null;
  const artifactStyle = artifactProvenance?.subtitle_style_applied ?? null;
  const draftChanged = JSON.stringify(style) !== JSON.stringify(appliedStyle ?? capabilities.subtitle.defaults);
  const fieldState = (name: keyof SubtitleRenderStyle) =>
    capabilities.subtitle.fields.find((field) => field.name === name)?.truth_state ?? "UNSUPPORTED";

  const selectedCue = subtitles.find((cue) => cue.id === selectedCueId) ?? subtitles[0] ?? null;
  const vertical = subtitleVerticalAlignment(style.alignment);
  const horizontal = subtitleHorizontalAlignment(style.alignment);
  const scaledMargin = Math.max(8, Math.round(style.margin_v * 0.21));
  const previewStyle: CSSProperties = {
    alignItems: vertical,
    justifyContent: horizontal === "left" ? "flex-start" : horizontal === "right" ? "flex-end" : "center",
    paddingBottom: vertical === "flex-end" ? scaledMargin : undefined,
    paddingTop: vertical === "flex-start" ? scaledMargin : undefined,
    paddingInline: "4%",
    textAlign: horizontal,
  };
  const captionStyle: CSSProperties = {
    maxWidth: `${style.max_width_ratio * 100}%`,
    fontFamily: style.font_family,
    fontSize: Math.max(12, Math.round(style.font_size * 0.5)),
    fontWeight: style.bold ? 700 : 400,
    color: style.primary_color,
    background: style.background_box ? "rgba(4, 8, 15, .82)" : "transparent",
    textShadow: style.shadow ? `0 ${Math.max(1, style.shadow * 0.5)}px ${Math.max(2, style.shadow * 2)}px rgba(0,0,0,.9)` : "none",
    WebkitTextStroke: style.outline_width ? `${Math.max(0.5, style.outline_width * 0.5)}px ${style.outline_color}` : undefined,
    display: "-webkit-box",
    WebkitBoxOrient: "vertical",
    WebkitLineClamp: style.max_lines,
    overflow: "hidden",
  };

  return (
    <div className="workflow-detail subtitle-workspace">
      <PageSection
        title="Subtitle Style"
        description="Draft values preview the same fields the backend validates and applies to the next render."
        actions={<StatusBadge tone={draftChanged ? "warning" : "success"}>{draftChanged ? "Draft" : "Render Applied"}</StatusBadge>}
      >
        <div className="subtitle-style-layout">
          <div className="subtitle-controls">
            {capabilities.subtitle.presets && capabilities.subtitle.presets.length > 0 && (
              <label className="subtitle-field">
                <span>Preset <small>RENDER_APPLIED</small></span>
                <select
                  value={activePresetId ?? "custom"}
                  onChange={(event) => handlePresetChange(event.target.value)}
                >
                  {capabilities.subtitle.presets.map((preset) => (
                    <option key={preset.id} value={preset.id}>
                      {preset.name}
                    </option>
                  ))}
                  {!activePresetId && <option value="custom">Custom Configuration</option>}
                </select>
              </label>
            )}
            <label className="subtitle-field"><span>Font family <small>{fieldState("font_family")}</small></span><select value={style.font_family} onChange={(event) => updateStyle("font_family", event.target.value)}>{capabilities.subtitle.font_families.map((font) => <option key={font}>{font}</option>)}</select></label>
            <label className="subtitle-field"><span>Font size <small>{fieldState("font_size")}</small></span><input type="range" min="24" max="96" value={style.font_size} onChange={(event) => updateStyle("font_size", Number(event.target.value))} /><output>{style.font_size}px</output></label>
            <label className="subtitle-field subtitle-checkbox"><input type="checkbox" checked={style.bold} onChange={(event) => updateStyle("bold", event.target.checked)} /><span>Bold <small>{fieldState("bold")}</small></span></label>
            <label className="subtitle-field"><span>Text color <small>{fieldState("primary_color")}</small></span><span className="subtitle-color"><input type="color" value={style.primary_color} onChange={(event) => updateStyle("primary_color", event.target.value.toUpperCase())} /><output>{style.primary_color}</output></span></label>
            <label className="subtitle-field"><span>Outline color <small>{fieldState("outline_color")}</small></span><span className="subtitle-color"><input type="color" value={style.outline_color} onChange={(event) => updateStyle("outline_color", event.target.value.toUpperCase())} /><output>{style.outline_color}</output></span></label>
            <label className="subtitle-field"><span>Outline width <small>{fieldState("outline_width")}</small></span><input type="range" min="0" max="8" step="0.5" value={style.outline_width} onChange={(event) => updateStyle("outline_width", Number(event.target.value))} /><output>{style.outline_width}px</output></label>
            <label className="subtitle-field"><span>Shadow <small>{fieldState("shadow")}</small></span><input type="range" min="0" max="8" step="0.5" value={style.shadow} onChange={(event) => updateStyle("shadow", Number(event.target.value))} /><output>{style.shadow}</output></label>
            <label className="subtitle-field subtitle-checkbox"><input type="checkbox" checked={style.background_box} onChange={(event) => updateStyle("background_box", event.target.checked)} /><span>Background box <small>{fieldState("background_box")}</small></span></label>
            <label className="subtitle-field"><span>Alignment <small>{fieldState("alignment")}</small></span><select value={style.alignment} onChange={(event) => updateStyle("alignment", Number(event.target.value))}>{Object.entries(capabilities.subtitle.alignments).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            <label className="subtitle-field"><span>Vertical margin <small>{fieldState("margin_v")}</small></span><input type="range" min="0" max="400" step="5" value={style.margin_v} onChange={(event) => updateStyle("margin_v", Number(event.target.value))} /><output>{style.margin_v}px</output></label>
            <label className="subtitle-field"><span>Maximum lines <small>{fieldState("max_lines")}</small></span><select value={style.max_lines} onChange={(event) => updateStyle("max_lines", Number(event.target.value))}><option value="1">1</option><option value="2">2</option><option value="3">3</option></select></label>
            <label className="subtitle-field"><span>Maximum width <small>{fieldState("max_width_ratio")}</small></span><input type="range" min="0.4" max="0.95" step="0.01" value={style.max_width_ratio} onChange={(event) => updateStyle("max_width_ratio", Number(event.target.value))} /><output>{Math.round(style.max_width_ratio * 100)}%</output></label>
            <div className="subtitle-field subtitle-readonly"><span>Minimum fitted font size <small>RENDER_APPLIED · advanced</small></span><strong>{style.min_font_size}px</strong></div>
            <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
              <button type="button" className="btn primary" disabled={!request || busy || !draftChanged} onClick={async () => { try { setApplyError(null); await onApply(style); } catch (error) { setApplyError(error instanceof Error ? error.message : "Could not apply render settings."); } }}>Apply to next render</button>
              {channelId && (
                <button type="button" className="btn secondary" disabled={busy} onClick={handleSaveAsChannelDefault}>Save as Channel Default</button>
              )}
            </div>
            {applyError && <Alert tone="danger" title="Settings were not applied">{applyError}</Alert>}
            {channelNotice && <Alert tone="success" title="Channel Style Profile">{channelNotice}</Alert>}
            <div className="subtitle-render-truth">
              <strong>Renderer truth</strong>
              <span>Preset: {activePresetId ?? "custom"} · Wrap: supported · Font downscale: supported.</span>
              <span>Timing: {capabilities.subtitle_timing_label}. It is not forced alignment.</span>
              <span>FPS mode: {capabilities.video.fps_mode} · Target FPS: {capabilities.video.target_fps} · read-only.</span>
            </div>
          </div>
          <div className="subtitle-preview-card">
            <div className="subtitle-preview-meta"><span>Live cue preview</span><strong>{selectedCue ? `${(selectedCue.start_ms / 1000).toFixed(2)}s – ${(selectedCue.end_ms / 1000).toFixed(2)}s` : "No cue selected"}</strong></div>
            <div className="subtitle-preview-stage" style={previewStyle} aria-live="polite">
              {selectedCue ? <span className="subtitle-preview-caption" style={captionStyle}>{selectedCue.text}</span> : <div className="subtitle-preview-empty"><strong>No persisted subtitle text</strong><span>Prepare a production with subtitle cues to preview renderer-backed appearance.</span></div>}
            </div>
            <div className="subtitle-state-stack">
              <span><b>Preset</b>{activePresetId ? capabilities.subtitle.presets?.find((p) => p.id === activePresetId)?.name ?? activePresetId : "Custom"}</span>
              <span><b>Channel Default</b>{channelProfile ? `${channelProfile.preset_id}${channelProfile.custom_subtitle_style ? " (custom)" : ""}` : "Default"}</span>
              <span><b>Draft</b>{draftChanged ? "Unsaved selection" : "Matches applied configuration"}</span>
              <span><b>Render Applied</b>{appliedStyle ? `${appliedStyle.font_size}px · ${appliedStyle.primary_color} · margin ${appliedStyle.margin_v}` : "Backend defaults until explicitly applied"}</span>
              <span><b>From Artifact</b>{artifactStyle ? `${artifactStyle.font_size}px · ${artifactStyle.primary_color} · margin ${artifactStyle.margin_v}` : "No persisted artifact provenance"}</span>
            </div>
          </div>
        </div>
        {artifactProvenance?.text_truncated && <Alert tone="warning" title="Rendered text was truncated">At least one scene used the explicit renderer truncation fallback. Review scene provenance in QA.</Alert>}
      </PageSection>
      <PageSection
        title="Subtitle cues"
        description={`${subtitles.length} cue${subtitles.length === 1 ? "" : "s"} using ${capabilities.subtitle_timing_label.toLowerCase()}.`}
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
