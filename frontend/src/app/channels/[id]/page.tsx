"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import {
  activateChannel,
  archiveChannel,
  getChannel,
  getChannelDNARevisions,
  pauseChannel,
  updateChannelDNA,
  type Channel,
  type ChannelDNARevision,
} from "@/lib/api";
import { ChannelContextBar } from "@/components/ChannelContextBar";
import {
  Alert,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorState,
  FormField,
  LoadingState,
  PageHeader,
  PageSection,
  StatusBadge,
} from "@/components/ui";
import {
  TechnicalDetails,
  WorkflowStatusSummary,
} from "@/components/workflow/WorkflowPrimitives";
import { useOperatorContext } from "@/lib/operator-context";

type LifecycleAction = "activate" | "pause" | "archive";

function channelTone(state: Channel["state"]) {
  if (state === "ACTIVE") return "success" as const;
  if (state === "PAUSED") return "warning" as const;
  if (state === "ARCHIVED") return "danger" as const;
  return "neutral" as const;
}

export default function ChannelDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: channelId } = use(params);
  const { setSelectedChannelId } = useOperatorContext();
  const [channel, setChannel] = useState<Channel | null>(null);
  const [revisions, setRevisions] = useState<ChannelDNARevision[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dnaError, setDnaError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [pendingAction, setPendingAction] = useState<LifecycleAction | null>(
    null,
  );
  const [dnaJson, setDnaJson] = useState("");
  const [changeReason, setChangeReason] = useState("");

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    setSelectedChannelId(channelId);
    try {
      const [channelData, revisionData] = await Promise.all([
        getChannel(channelId),
        getChannelDNARevisions(channelId),
      ]);
      setChannel(channelData);
      setRevisions(revisionData);
      setDnaJson(JSON.stringify(channelData.dna, null, 2));
    } catch (reason: unknown) {
      setError(
        reason instanceof Error ? reason.message : "Failed to load channel",
      );
    } finally {
      setLoading(false);
    }
  }, [channelId, setSelectedChannelId]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  async function executeLifecycleAction() {
    const action = pendingAction;
    setPendingAction(null);
    if (!action) return;
    setBusy(true);
    setError(null);
    try {
      const updated =
        action === "activate"
          ? await activateChannel(channelId)
          : action === "pause"
            ? await pauseChannel(channelId)
            : await archiveChannel(channelId);
      setChannel(updated);
    } catch (reason: unknown) {
      setError(
        reason instanceof Error ? reason.message : `Channel ${action} failed`,
      );
    } finally {
      setBusy(false);
    }
  }

  async function saveDNA(event: React.FormEvent) {
    event.preventDefault();
    if (!changeReason.trim() || changeReason.trim().length < 3) {
      setDnaError("A change reason of at least three characters is required.");
      return;
    }
    setBusy(true);
    setDnaError(null);
    try {
      const parsed = JSON.parse(dnaJson);
      await updateChannelDNA(channelId, parsed, changeReason.trim());
      setEditing(false);
      setChangeReason("");
      await loadData();
    } catch (reason: unknown) {
      setDnaError(
        reason instanceof Error ? reason.message : "The DNA JSON is invalid.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (loading)
    return (
      <LoadingState
        title="Loading channel workspace"
        description="Retrieving channel DNA and revision history."
      />
    );
  if (!channel)
    return (
      <ErrorState
        title="Channel unavailable"
        description={error || "The channel could not be found."}
        action={
          <Link className="btn btn-secondary" href="/channels">
            Back to channels
          </Link>
        }
      />
    );

  const lifecycleDescription =
    pendingAction === "archive"
      ? "Archive this channel? Workflow mutations will remain disabled until the channel is restored through a supported operation."
      : pendingAction === "pause"
        ? "Pause this channel and prevent new workflow activity?"
        : "Activate this channel for workflow activity?";
  return (
    <div className="workflow-page ui-page-stack">
      <ChannelContextBar currentTab="dna" />
      <PageHeader
        eyebrow="Channel workspace"
        title={channel.name}
        description={`/${channel.slug} · ${channel.primary_language} · ${channel.target_region}`}
        actions={
          <div className="ui-inline-actions">
            {["DRAFT", "PAUSED"].includes(channel.state) ? (
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy}
                onClick={() => setPendingAction("activate")}
              >
                Activate channel
              </button>
            ) : null}
            {channel.state === "ACTIVE" ? (
              <button
                type="button"
                className="btn btn-secondary"
                disabled={busy}
                onClick={() => setPendingAction("pause")}
              >
                Pause channel
              </button>
            ) : null}
            {channel.state !== "ARCHIVED" ? (
              <>
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={busy}
                  onClick={() => {
                    setDnaJson(JSON.stringify(channel.dna, null, 2));
                    setEditing(true);
                  }}
                >
                  Edit DNA
                </button>
                <button
                  type="button"
                  className="btn btn-danger"
                  disabled={busy}
                  onClick={() => setPendingAction("archive")}
                >
                  Archive
                </button>
              </>
            ) : null}
          </div>
        }
      />
      {error ? (
        <ErrorState
          title="Channel action failed"
          description={error}
          action={
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => void loadData()}
            >
              Reload
            </button>
          }
        />
      ) : null}
      <WorkflowStatusSummary
        metrics={[
          {
            label: "State",
            value: (
              <StatusBadge tone={channelTone(channel.state)}>
                {channel.state}
              </StatusBadge>
            ),
          },
          { label: "Platform", value: channel.platform },
          { label: "DNA version", value: `v${revisions[0]?.version || 1}` },
          { label: "Timezone", value: channel.timezone },
        ]}
      />
      <TechnicalDetails
        label="Channel identity"
        data={{ id: channel.id, slug: channel.slug, platform_channel_id: channel.platform_channel_id }}
      />
      <nav
        className="workflow-action-bar"
        aria-label="Channel workflow shortcuts"
      >
        <div className="ui-inline-actions">
          <Link
            href={`/channels/${channel.id}/topics`}
            className="btn btn-secondary btn-sm"
          >
            Topics
          </Link>
          <Link
            href={`/channels/${channel.id}/research`}
            className="btn btn-secondary btn-sm"
          >
            Research
          </Link>
          <Link
            href={`/channels/${channel.id}/content`}
            className="btn btn-secondary btn-sm"
          >
            Content
          </Link>
          <Link
            href={`/channels/${channel.id}/production`}
            className="btn btn-secondary btn-sm"
          >
            Production
          </Link>
          <Link
            href={`/missions/new?channel_id=${channel.id}`}
            className="btn btn-primary btn-sm"
          >
            Launch mission
          </Link>
        </div>
      </nav>
      <PageSection
        title="Channel DNA"
        description="Human-readable channel identity and editorial constraints from the active revision."
      >
        <div className="workflow-card-grid">
          <article className="workflow-card">
            <div className="workflow-card-header">
              <h3>Localization and routing</h3>
              <StatusBadge>{channel.platform}</StatusBadge>
            </div>
            <div className="workflow-data-list">
              <div className="workflow-data-row">
                <span>Primary language</span>
                <strong>{channel.primary_language}</strong>
              </div>
              <div className="workflow-data-row">
                <span>Target region</span>
                <strong>{channel.target_region}</strong>
              </div>
              <div className="workflow-data-row">
                <span>Timezone</span>
                <strong>{channel.timezone}</strong>
              </div>
              <div className="workflow-data-row">
                <span>Platform channel</span>
                <strong>{channel.platform_channel_id || "Unlinked"}</strong>
              </div>
            </div>
          </article>
          <article className="workflow-card">
            <div className="workflow-card-header">
              <h3>Audience profile</h3>
              <StatusBadge>Targeting</StatusBadge>
            </div>
            <div className="workflow-data-list">
              <div className="workflow-data-row">
                <span>Age range</span>
                <strong>
                  {channel.dna?.audience?.age_range || "Not specified"}
                </strong>
              </div>
              <div className="workflow-data-row">
                <span>Knowledge level</span>
                <strong>
                  {channel.dna?.audience?.knowledge_level || "All levels"}
                </strong>
              </div>
              <div className="workflow-data-row">
                <span>Interests</span>
                <strong>
                  {channel.dna?.audience?.interests?.join(", ") || "None"}
                </strong>
              </div>
              <div className="workflow-data-row">
                <span>Preferred length</span>
                <strong>
                  {channel.dna?.audience?.preferred_content_length ||
                    "Not specified"}
                </strong>
              </div>
            </div>
          </article>
          <article className="workflow-card">
            <div className="workflow-card-header">
              <h3>Brand voice and strategy</h3>
              <StatusBadge>Editorial</StatusBadge>
            </div>
            <div className="workflow-data-list">
              <div className="workflow-data-row">
                <span>Niche</span>
                <strong>
                  {channel.dna?.content_strategy?.niche || "General"}
                </strong>
              </div>
              <div className="workflow-data-row">
                <span>Content pillars</span>
                <strong>
                  {channel.dna?.content_strategy?.content_pillars?.join(", ") ||
                    "None"}
                </strong>
              </div>
              <div className="workflow-data-row">
                <span>Tone</span>
                <strong>
                  {channel.dna?.brand_voice?.tone?.join(", ") || "Standard"}
                </strong>
              </div>
              <div className="workflow-data-row">
                <span>Pacing</span>
                <strong>{channel.dna?.brand_voice?.pace || "Normal"}</strong>
              </div>
            </div>
          </article>
        </div>
        <TechnicalDetails label="Full DNA configuration" data={channel.dna} />
      </PageSection>
      <PageSection
        title={`DNA revision history (${revisions.length})`}
        description="Immutable audit trail for channel identity changes."
      >
        {revisions.length === 0 ? (
          <EmptyState
            title="No DNA revisions"
            description="No revision history has been recorded for this channel."
          />
        ) : (
          <div className="ui-table-card table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Version</th>
                  <th>Change reason</th>
                  <th>Actor</th>
                  <th>Timestamp</th>
                </tr>
              </thead>
              <tbody>
                {revisions.map((revision, index) => (
                  <tr key={revision.id}>
                    <td data-label="Version">
                      <StatusBadge tone={index === 0 ? "success" : "neutral"}>
                        v{revision.version}
                        {index === 0 ? " · active" : ""}
                      </StatusBadge>
                    </td>
                    <td data-label="Change reason">{revision.change_reason}</td>
                    <td data-label="Actor" className="workflow-id">
                      {revision.actor}
                    </td>
                    <td data-label="Timestamp">
                      <time dateTime={revision.created_at}>
                        {new Date(revision.created_at).toLocaleString()}
                      </time>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </PageSection>
      <Dialog
        open={editing}
        title="Update channel DNA"
        description={`Saving creates immutable revision v${(revisions[0]?.version || 1) + 1}.`}
        onClose={() => setEditing(false)}
        actions={
          <>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setEditing(false)}
            >
              Cancel
            </button>
            <button
              type="submit"
              form="channel-dna-form"
              className="btn btn-primary"
              disabled={busy}
            >
              Save revision
            </button>
          </>
        }
      >
        <form
          id="channel-dna-form"
          className="workflow-dialog-form"
          onSubmit={saveDNA}
        >
          {dnaError ? <Alert tone="danger">{dnaError}</Alert> : null}
          <FormField
            id="dna-change-reason"
            label="Change reason"
            description="Required for the immutable audit trail."
            required
          >
            <input
              className="input"
              value={changeReason}
              onChange={(event) => setChangeReason(event.target.value)}
            />
          </FormField>
          <FormField id="dna-json" label="Channel DNA JSON" required>
            <textarea
              className="input workflow-json-editor"
              rows={14}
              value={dnaJson}
              onChange={(event) => setDnaJson(event.target.value)}
            />
          </FormField>
        </form>
      </Dialog>
      <ConfirmDialog
        open={Boolean(pendingAction)}
        title={`${pendingAction ? pendingAction[0].toUpperCase() + pendingAction.slice(1) : "Update"} channel`}
        description={lifecycleDescription}
        confirmLabel={
          pendingAction === "archive"
            ? "Archive channel"
            : pendingAction === "pause"
              ? "Pause channel"
              : "Activate channel"
        }
        destructive={pendingAction === "archive"}
        busy={busy}
        onCancel={() => setPendingAction(null)}
        onConfirm={() => void executeLifecycleAction()}
      />
    </div>
  );
}
