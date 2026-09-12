"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  approveTask,
  createYouTubeAuthorizeUrl,
  disconnectPlatformAccount,
  executePublish,
  getMissionGuardianStatus,
  type GuardianConsolidatedStatus,
  type PlatformAccount,
  type PublishAttempt,
  type PublishIntent,
  rejectTask,
  type UploadProgress,
} from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";
import {
  Alert,
  ConfirmDialog,
  Dialog,
  EmptyState,
  FormField,
  PageSection,
  StatusBadge,
  type StatusTone,
} from "@/components/ui";
import {
  TechnicalDetails,
  WorkflowStatusSummary,
} from "@/components/workflow/WorkflowPrimitives";

interface PublishingStatusCardProps {
  channelId: string;
  account: PlatformAccount | null;
  latestIntent?: PublishIntent | null;
  latestAttempt?: PublishAttempt | null;
  uploadProgress?: UploadProgress | null;
  onRefresh?: () => void;
  isArchived?: boolean;
}

function publishTone(state?: string | null): StatusTone {
  if (!state) return "neutral";
  if (["PUBLISHED", "SUCCEEDED", "APPROVED", "OPEN"].includes(state))
    return "success";
  if (["UPLOADING", "FINALIZING", "CLAIMED"].includes(state)) return "info";
  if (["DRAFT", "SUPERSEDED", "RETRYABLE_FAILED", "UNKNOWN"].includes(state))
    return "warning";
  if (
    ["FAILED", "PERMANENT_FAILED", "BLOCKED_GUARDIAN", "BLOCKED"].includes(
      state,
    )
  )
    return "danger";
  return "neutral";
}

export function PublishingStatusCard({
  channelId,
  account,
  latestIntent,
  latestAttempt,
  uploadProgress,
  onRefresh,
  isArchived: propIsArchived,
}: PublishingStatusCardProps) {
  const { selectedChannel } = useOperatorContext();
  const isArchived = propIsArchived ?? selectedChannel?.state === "ARCHIVED";
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [guardianStatus, setGuardianStatus] =
    useState<GuardianConsolidatedStatus | null>(null);
  const [showPublishConfirm, setShowPublishConfirm] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [showRejectDialog, setShowRejectDialog] = useState(false);
  const [showDisconnectConfirm, setShowDisconnectConfirm] = useState(false);
  const [showApproveConfirm, setShowApproveConfirm] = useState(false);

  useEffect(() => {
    const missionId = latestIntent?.mission_id;
    if (!missionId) {
      setGuardianStatus(null);
      return;
    }
    const capturedMissionId = missionId;
    let active = true;
    async function loadGuardian() {
      try {
        const status = await getMissionGuardianStatus(capturedMissionId);
        if (active) setGuardianStatus(status);
      } catch (requestError: unknown) {
        if (!active) return;
        setGuardianStatus(null);
        setError(
          requestError instanceof Error
            ? requestError.message
            : "Guardian status is unavailable.",
        );
      }
    }
    void loadGuardian();
    return () => {
      active = false;
    };
  }, [latestIntent?.mission_id]);

  const handleConnect = async () => {
    if (isArchived) return;
    setLoading(true);
    setError(null);
    try {
      const response = await createYouTubeAuthorizeUrl(channelId);
      if (!response.authorization_url)
        throw new Error(
          "The authorization service did not return a redirect URL.",
        );
      window.location.assign(response.authorization_url);
    } catch (requestError: unknown) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Failed to initiate YouTube authorization.",
      );
      setLoading(false);
    }
  };

  const handleDisconnect = async () => {
    if (!account || isArchived) return;
    setLoading(true);
    setError(null);
    setShowDisconnectConfirm(false);
    try {
      await disconnectPlatformAccount(account.id, true);
      onRefresh?.();
    } catch (requestError: unknown) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Failed to disconnect account.",
      );
    } finally {
      setLoading(false);
    }
  };

  const handleApprove = async () => {
    if (!latestIntent || isArchived) return;
    setLoading(true);
    setError(null);
    setShowApproveConfirm(false);
    try {
      await approveTask(latestIntent.task_id);
      onRefresh?.();
    } catch (requestError: unknown) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Failed to approve publication task.",
      );
    } finally {
      setLoading(false);
    }
  };

  const handleReject = async () => {
    if (!latestIntent || isArchived) return;
    setLoading(true);
    setError(null);
    try {
      await rejectTask(latestIntent.task_id, rejectReason.trim());
      setShowRejectDialog(false);
      setRejectReason("");
      onRefresh?.();
    } catch (requestError: unknown) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Failed to reject publication task.",
      );
    } finally {
      setLoading(false);
    }
  };

  const handlePublish = async () => {
    if (!latestIntent || isArchived) return;
    setLoading(true);
    setError(null);
    setShowPublishConfirm(false);
    try {
      await executePublish(latestIntent.task_id);
      onRefresh?.();
    } catch (requestError: unknown) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Failed to execute publication.",
      );
    } finally {
      setLoading(false);
    }
  };

  const guardianBlocked = guardianStatus?.overall_gate_state === "BLOCKED";
  const executionPermitted =
    latestIntent?.state === "APPROVED" && !guardianBlocked && !isArchived;
  const accountAction = account ? (
    <div className="ui-inline-actions">
      <StatusBadge tone={account.status === "ACTIVE" ? "success" : "danger"}>
        {account.status}
      </StatusBadge>
      <button
        type="button"
        className="btn btn-danger btn-sm"
        disabled={loading || isArchived}
        title={
          isArchived
            ? "Activate this channel before modifying accounts."
            : undefined
        }
        onClick={() => setShowDisconnectConfirm(true)}
      >
        Disconnect
      </button>
    </div>
  ) : (
    <button
      type="button"
      className="btn btn-primary btn-sm"
      disabled={loading || isArchived}
      title={
        isArchived
          ? "Activate this channel before connecting platform accounts."
          : undefined
      }
      onClick={() => void handleConnect()}
    >
      {loading ? "Connecting…" : "Connect YouTube"}
    </button>
  );

  return (
    <PageSection
      title="YouTube publisher"
      description="Account readiness, approval gates, upload progress, and provider outcomes."
      actions={accountAction}
      className="ui-route-card"
    >
      <div className="ui-page-stack">
        {error && (
          <Alert tone="danger" title="Publishing operation failed">
            {error}
          </Alert>
        )}
        {account && (
          <WorkflowStatusSummary
            metrics={[
              {
                label: "Connected channel",
                value: account.account_display_name,
              },
              { label: "Platform", value: account.platform },
              {
                label: "Account status",
                value: account.status,
                status: account.status,
              },
              { label: "Scopes", value: account.scopes.length },
            ]}
          />
        )}

        {!latestIntent ? (
          <EmptyState
            title="No active publish intent"
            description="A publish intent appears here after a mission produces an approved media artifact."
          />
        ) : (
          <div className="workflow-data-list">
            <div className="workflow-card-header">
              <div>
                <h3>{latestIntent.title}</h3>
                <p>Revision {latestIntent.revision_number}</p>
              </div>
              <div className="ui-inline-actions">
                <Link
                  href={`/schedule?channel_id=${channelId}&intent_id=${latestIntent.id}`}
                  className="btn btn-secondary btn-sm"
                >
                  Open scheduler
                </Link>
                <StatusBadge tone={publishTone(latestIntent.state)}>
                  {latestIntent.state}
                </StatusBadge>
              </div>
            </div>
            <WorkflowStatusSummary
              metrics={[
                {
                  label: "Requested privacy",
                  value: latestIntent.requested_privacy_status,
                },
                {
                  label: "Effective privacy",
                  value:
                    latestAttempt?.effective_privacy_status || "Not published",
                },
                {
                  label: "Audience",
                  value: latestIntent.made_for_kids
                    ? "Made for kids"
                    : "Standard audience",
                },
                { label: "Category", value: latestIntent.category_id },
              ]}
            />

            {guardianStatus && (
              <Alert
                tone={
                  guardianStatus.overall_gate_state === "OPEN"
                    ? "success"
                    : guardianBlocked
                      ? "danger"
                      : "warning"
                }
                title={`Guardian gate: ${guardianStatus.overall_gate_state}`}
              >
                Epoch {guardianStatus.guardian_epoch}; accumulated cost $
                {guardianStatus.accumulated_cost_usd}.
                {guardianStatus.blocking_checkpoints.length > 0 && (
                  <>
                    {" "}
                    Blocking checkpoints:{" "}
                    {guardianStatus.blocking_checkpoints.join(", ")}.
                  </>
                )}
              </Alert>
            )}

            {(latestIntent.state === "DRAFT" ||
              latestIntent.state === "SUPERSEDED") && (
              <Alert
                tone="warning"
                title="Waiting for operator approval"
                actions={
                  <>
                    <button
                      type="button"
                      className="btn btn-danger btn-sm"
                      disabled={loading || isArchived}
                      onClick={() => setShowRejectDialog(true)}
                    >
                      Reject
                    </button>
                    <button
                      type="button"
                      className="btn btn-success btn-sm"
                      disabled={loading || isArchived || guardianBlocked}
                      onClick={() => setShowApproveConfirm(true)}
                    >
                      Approve intent
                    </button>
                  </>
                }
              >
                Review title, metadata, privacy, and audience declaration before
                authorizing publication.
              </Alert>
            )}

            {uploadProgress && !uploadProgress.is_complete && (
              <div className="ui-upload-progress">
                <div className="workflow-data-row">
                  <span>Upload progress</span>
                  <strong>{uploadProgress.progress_percentage}%</strong>
                </div>
                <progress max={100} value={uploadProgress.progress_percentage}>
                  {uploadProgress.progress_percentage}%
                </progress>
                <small>
                  {Math.round(uploadProgress.bytes_uploaded / 1_048_576)} MB of{" "}
                  {Math.round(uploadProgress.total_bytes / 1_048_576)} MB
                </small>
              </div>
            )}

            {latestAttempt?.provider_video_id && (
              <Alert
                tone="success"
                title="Published to YouTube"
                actions={
                  latestAttempt.provider_url ? (
                    <a
                      href={latestAttempt.provider_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="btn btn-success btn-sm"
                    >
                      Open on YouTube
                    </a>
                  ) : undefined
                }
              >
                Provider video ID:{" "}
                <span className="text-mono">
                  {latestAttempt.provider_video_id}
                </span>
              </Alert>
            )}
            {(latestAttempt?.state === "UNKNOWN" ||
              latestAttempt?.reconciliation_status === "PENDING") && (
              <Alert
                tone="warning"
                title="Result unknown — reconciliation required"
              >
                The provider outcome is not deterministic. Reconcile it before
                retrying to prevent a duplicate upload.
              </Alert>
            )}
            {latestAttempt?.error_message &&
              latestAttempt.state !== "UNKNOWN" && (
                <Alert
                  tone="danger"
                  title={latestAttempt.error_category || "Publish error"}
                >
                  {latestAttempt.error_message}
                  {latestAttempt.retry_after_seconds
                    ? ` Retry in ${latestAttempt.retry_after_seconds} seconds.`
                    : ""}
                </Alert>
              )}

            {latestIntent.state === "APPROVED" && (
              <button
                type="button"
                className="btn btn-primary"
                disabled={loading || !executionPermitted}
                title={
                  isArchived
                    ? "Activate this channel before publishing."
                    : guardianBlocked
                      ? "Guardian gate is blocked."
                      : undefined
                }
                onClick={() => setShowPublishConfirm(true)}
              >
                {loading
                  ? "Starting publication…"
                  : "Execute publication to YouTube"}
              </button>
            )}

            <TechnicalDetails label="Publishing identifiers">
              <dl className="ui-dialog-facts">
                <div>
                  <dt>Intent ID</dt>
                  <dd className="text-mono">{latestIntent.id}</dd>
                </div>
                <div>
                  <dt>Task ID</dt>
                  <dd className="text-mono">{latestIntent.task_id}</dd>
                </div>
                <div>
                  <dt>Account ID</dt>
                  <dd className="text-mono">{account?.id || "Unavailable"}</dd>
                </div>
                <div>
                  <dt>Artifact checksum</dt>
                  <dd className="text-mono">
                    {latestIntent.media_artifact_checksum}
                  </dd>
                </div>
              </dl>
            </TechnicalDetails>
          </div>
        )}
      </div>

      <Dialog
        open={showRejectDialog}
        title="Reject publication intent"
        description="Record the operator reason before rejecting this persisted intent."
        onClose={() => setShowRejectDialog(false)}
        actions={
          <>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={loading}
              onClick={() => setShowRejectDialog(false)}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-danger"
              disabled={loading || !rejectReason.trim()}
              onClick={() => void handleReject()}
            >
              Reject intent
            </button>
          </>
        }
      >
        <FormField
          id="publish-rejection-reason"
          label="Rejection reason"
          required
        >
          <textarea
            value={rejectReason}
            onChange={(event) => setRejectReason(event.target.value)}
          />
        </FormField>
      </Dialog>

      {latestIntent && (
        <Dialog
          open={showPublishConfirm}
          title="Confirm external publication"
          description="This starts an external provider upload. Verify the target and privacy state before continuing."
          onClose={() => setShowPublishConfirm(false)}
          actions={
            <>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={loading}
                onClick={() => setShowPublishConfirm(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                disabled={loading}
                onClick={() => void handlePublish()}
              >
                {loading ? "Starting publication…" : "Publish to YouTube"}
              </button>
            </>
          }
        >
          <dl className="ui-dialog-facts">
            <div>
              <dt>Title</dt>
              <dd>{latestIntent.title}</dd>
            </div>
            <div>
              <dt>Account</dt>
              <dd>{account?.account_display_name || "Unavailable"}</dd>
            </div>
            <div>
              <dt>Privacy</dt>
              <dd>{latestIntent.requested_privacy_status}</dd>
            </div>
            <div>
              <dt>Guardian gate</dt>
              <dd>{guardianStatus?.overall_gate_state || "Unavailable"}</dd>
            </div>
          </dl>
        </Dialog>
      )}

      <ConfirmDialog
        open={showApproveConfirm}
        title="Approve publish intent?"
        description="Approval authorizes this persisted intent to advance when its other readiness gates permit."
        confirmLabel="Approve intent"
        busy={loading}
        onCancel={() => setShowApproveConfirm(false)}
        onConfirm={() => void handleApprove()}
      />
      <ConfirmDialog
        open={showDisconnectConfirm}
        title="Disconnect platform account?"
        description="The selected channel will no longer be able to publish through this account."
        confirmLabel="Disconnect account"
        destructive
        busy={loading}
        onCancel={() => setShowDisconnectConfirm(false)}
        onConfirm={() => void handleDisconnect()}
      />
    </PageSection>
  );
}
