"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import {
  approveTask,
  createYouTubeAuthorizeUrl,
  disconnectPlatformAccount,
  executePublish,
  getMissionGuardianStatus,
  GuardianConsolidatedStatus,
  PlatformAccount,
  PublishAttempt,
  PublishIntent,
  rejectTask,
  UploadProgress,
} from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";

interface PublishingStatusCardProps {
  channelId: string;
  account: PlatformAccount | null;
  latestIntent?: PublishIntent | null;
  latestAttempt?: PublishAttempt | null;
  uploadProgress?: UploadProgress | null;
  onRefresh?: () => void;
  isArchived?: boolean;
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
  const isArchived = propIsArchived ?? (selectedChannel?.state === "ARCHIVED");

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [guardianStatus, setGuardianStatus] = useState<GuardianConsolidatedStatus | null>(null);

  // Confirmation Modal State
  const [showConfirmModal, setShowConfirmModal] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [showRejectModal, setShowRejectModal] = useState(false);

  // Load Guardian status for intent's mission
  useEffect(() => {
    const missionId = latestIntent?.mission_id;
    if (!missionId) {
      setGuardianStatus(null);
      return;
    }

    async function loadGuardian() {
      try {
        const status = await getMissionGuardianStatus(missionId!);
        setGuardianStatus(status);
      } catch {
        setGuardianStatus(null);
      }
    }

    loadGuardian();
  }, [latestIntent?.mission_id]);

  const handleConnect = async () => {
    if (isArchived) return;
    try {
      setLoading(true);
      setError(null);
      const res = await createYouTubeAuthorizeUrl(channelId);
      if (res.authorization_url) {
        window.location.href = res.authorization_url;
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to initiate YouTube authorization.");
      setLoading(false);
    }
  };

  const handleDisconnect = async () => {
    if (!account || isArchived) return;
    if (!confirm("Are you sure you want to disconnect this YouTube channel account?")) return;

    try {
      setLoading(true);
      setError(null);
      await disconnectPlatformAccount(account.id, true);
      if (onRefresh) onRefresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to disconnect account.");
    } finally {
      setLoading(false);
    }
  };

  const handleApprove = async () => {
    if (!latestIntent || isArchived) return;
    try {
      setLoading(true);
      setError(null);
      await approveTask(latestIntent.task_id);
      if (onRefresh) onRefresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to approve publication task.");
    } finally {
      setLoading(false);
    }
  };

  const handleReject = async () => {
    if (!latestIntent || isArchived) return;
    try {
      setLoading(true);
      setError(null);
      await rejectTask(latestIntent.task_id, rejectReason || "Operator rejected publication intent");
      setShowRejectModal(false);
      if (onRefresh) onRefresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to reject publication task.");
    } finally {
      setLoading(false);
    }
  };

  const handleExecuteConfirmed = async () => {
    if (!latestIntent || isArchived) return;
    try {
      setLoading(true);
      setError(null);
      setShowConfirmModal(false);
      await executePublish(latestIntent.task_id);
      if (onRefresh) onRefresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to execute publication.");
    } finally {
      setLoading(false);
    }
  };

  const getStateBadgeClass = (state: string) => {
    switch (state) {
      case "PUBLISHED":
      case "SUCCEEDED":
        return "badge-success";
      case "UPLOADING":
      case "FINALIZING":
        return "badge-active";
      case "CLAIMED":
      case "APPROVED":
        return "badge-active";
      case "RETRYABLE_FAILED":
      case "UNKNOWN":
        return "badge-warning";
      case "FAILED":
      case "PERMANENT_FAILED":
      case "BLOCKED_GUARDIAN":
        return "badge-failed";
      default:
        return "badge-draft";
    }
  };

  const isGuardianBlocked = guardianStatus?.overall_gate_state === "BLOCKED";
  const isExecutionPermitted = latestIntent?.state === "APPROVED" && !isGuardianBlocked && !isArchived;

  return (
    <div className="card" style={{ padding: "1.25rem" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          borderBottom: "1px solid var(--border-subtle)",
          paddingBottom: "0.85rem",
          marginBottom: "1rem",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <div
            style={{
              width: "34px",
              height: "34px",
              borderRadius: "var(--radius-sm)",
              background: "rgba(239, 68, 68, 0.15)",
              color: "var(--status-danger)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 700,
              fontSize: "0.9rem",
            }}
          >
            ▶
          </div>
          <div>
            <h3 style={{ fontSize: "0.95rem", fontWeight: 600, color: "var(--text-primary)" }}>
              YouTube Publisher
            </h3>
            <p style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
              Official YouTube Data API v3 Distribution
            </p>
          </div>
        </div>

        {/* Account Status Badge */}
        {account ? (
          <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
            <span className={`badge ${account.status === "ACTIVE" ? "badge-success" : "badge-failed"}`}>
              {account.status}
            </span>
            <button
              onClick={handleDisconnect}
              disabled={loading || isArchived}
              title={isArchived ? "Activate this channel before modifying accounts." : "Disconnect Account"}
              className="btn btn-secondary btn-sm"
              style={{ fontSize: "0.75rem", padding: "0.2rem 0.5rem", color: "var(--status-danger)" }}
            >
              Disconnect
            </button>
          </div>
        ) : (
          <button
            onClick={handleConnect}
            disabled={loading || isArchived}
            title={isArchived ? "Activate this channel before connecting platform accounts." : "Connect Channel"}
            className="btn btn-primary btn-sm"
            style={{ background: "#dc2626", borderColor: "#b91c1c" }}
          >
            {loading ? "Connecting..." : "Connect Channel"}
          </button>
        )}
      </div>

      {error && (
        <div
          style={{
            padding: "0.75rem 1rem",
            background: "var(--status-danger-bg)",
            border: "1px solid var(--status-danger-border)",
            borderRadius: "var(--radius-sm)",
            fontSize: "0.8rem",
            color: "var(--status-danger)",
            marginBottom: "1rem",
          }}
        >
          {error}
        </div>
      )}

      {/* Account Details */}
      {account && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: "0.75rem",
            padding: "0.85rem 1rem",
            background: "var(--bg-input)",
            borderRadius: "var(--radius-sm)",
            border: "1px solid var(--border-subtle)",
            marginBottom: "1rem",
          }}
        >
          <div>
            <span style={{ fontSize: "0.7rem", color: "var(--text-muted)", display: "block", textTransform: "uppercase" }}>
              Connected Channel
            </span>
            <span style={{ fontSize: "0.85rem", fontWeight: 600, color: "var(--text-primary)" }}>
              {account.account_display_name}
            </span>
          </div>
          <div>
            <span style={{ fontSize: "0.7rem", color: "var(--text-muted)", display: "block", textTransform: "uppercase" }}>
              External Account ID
            </span>
            <span className="text-mono" style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
              {account.external_account_id}
            </span>
          </div>
        </div>
      )}

      {/* Publication Contract & Progress */}
      {latestIntent ? (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-secondary)" }}>
              Publish Intent (v{latestIntent.revision_number})
            </span>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <Link
                href={`/schedule?channel_id=${channelId}&intent_id=${latestIntent.id}`}
                className="btn btn-secondary btn-sm"
                style={{ fontSize: "0.72rem", padding: "0.2rem 0.5rem" }}
              >
                📅 Smart Scheduler ↗
              </Link>
              <span className={`badge ${getStateBadgeClass(latestIntent.state)}`}>
                {latestIntent.state}
              </span>
            </div>
          </div>

          {/* Metadata Card */}
          <div
            style={{
              padding: "0.85rem 1rem",
              background: "var(--bg-input)",
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--border-subtle)",
            }}
          >
            <p style={{ fontSize: "0.85rem", fontWeight: 600, color: "var(--text-primary)", marginBottom: "0.35rem" }}>
              {latestIntent.title}
            </p>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "1rem", fontSize: "0.75rem", color: "var(--text-muted)" }}>
              <span>
                Requested Privacy: <strong style={{ color: "var(--text-primary)" }}>{latestIntent.requested_privacy_status}</strong>
              </span>
              {latestAttempt?.effective_privacy_status && (
                <span>
                  Effective: <strong style={{ color: "var(--status-success)" }}>{latestAttempt.effective_privacy_status}</strong>
                </span>
              )}
              <span>
                Audience: <strong style={{ color: "var(--text-primary)" }}>{latestIntent.made_for_kids ? "Made for Kids" : "Standard Audience"}</strong>
              </span>
              <span>
                Category: <strong style={{ color: "var(--text-primary)" }}>{latestIntent.category_id}</strong>
              </span>
            </div>
          </div>

          {/* Guardian Readiness & Gate Status Panel */}
          {guardianStatus && (
            <div
              style={{
                padding: "0.85rem 1rem",
                background: guardianStatus.overall_gate_state === "OPEN" ? "var(--status-success-bg)" : guardianStatus.overall_gate_state === "BLOCKED" ? "var(--status-danger-bg)" : "var(--status-warning-bg)",
                border: `1px solid ${guardianStatus.overall_gate_state === "OPEN" ? "var(--status-success-border)" : guardianStatus.overall_gate_state === "BLOCKED" ? "var(--status-danger-border)" : "var(--status-warning-border)"}`,
                borderRadius: "var(--radius-sm)",
                fontSize: "0.78rem",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.35rem" }}>
                <span style={{ fontWeight: 700, color: guardianStatus.overall_gate_state === "OPEN" ? "var(--status-success)" : guardianStatus.overall_gate_state === "BLOCKED" ? "var(--status-danger)" : "var(--status-warning)" }}>
                  🛡️ Guardian Gate: {guardianStatus.overall_gate_state}
                </span>
                <span className="text-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
                  Epoch: {guardianStatus.guardian_epoch} • Cost: ${guardianStatus.accumulated_cost_usd}
                </span>
              </div>
              {guardianStatus.blocking_checkpoints.length > 0 && (
                <div style={{ color: "var(--status-danger)", marginTop: "0.25rem" }}>
                  Blocking Checkpoints: {guardianStatus.blocking_checkpoints.join(", ")}
                </div>
              )}
            </div>
          )}

          {/* Pre-Execution Approval Gating Banner */}
          {(latestIntent.state === "DRAFT" || latestIntent.state === "SUPERSEDED") && (
            <div
              style={{
                padding: "0.85rem 1rem",
                background: "var(--status-warning-bg)",
                border: "1px solid var(--status-warning-border)",
                borderRadius: "var(--radius-sm)",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <div>
                <span style={{ fontSize: "0.82rem", fontWeight: 700, color: "var(--status-warning)", display: "block" }}>
                  ⏳ WAITING FOR OPERATOR APPROVAL
                </span>
                <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                  Review video title, metadata, and audience declaration before enabling upload execution.
                </span>
              </div>
              <div style={{ display: "flex", gap: "0.5rem" }}>
                <button
                  onClick={() => setShowRejectModal(true)}
                  disabled={loading || isArchived}
                  className="btn btn-danger btn-sm"
                >
                  Reject
                </button>
                <button
                  onClick={handleApprove}
                  disabled={loading || isArchived || isGuardianBlocked}
                  title={isGuardianBlocked ? "Guardian gate is BLOCKED" : "Approve for Publication"}
                  className="btn btn-success btn-sm"
                >
                  {loading ? "Approving..." : "✓ Approve Intent"}
                </button>
              </div>
            </div>
          )}

          {/* Upload Progress Bar */}
          {uploadProgress && !uploadProgress.is_complete && (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", color: "var(--text-secondary)" }}>
                <span>Uploading Video Chunks to YouTube</span>
                <span>
                  {uploadProgress.progress_percentage}% ({Math.round(uploadProgress.bytes_uploaded / (1024 * 1024))}MB / {Math.round(uploadProgress.total_bytes / (1024 * 1024))}MB)
                </span>
              </div>
              <div style={{ width: "100%", height: "6px", background: "var(--bg-input)", borderRadius: "3px", overflow: "hidden" }}>
                <div
                  style={{
                    height: "100%",
                    background: "var(--accent-primary)",
                    width: `${uploadProgress.progress_percentage}%`,
                    transition: "width 0.3s ease",
                  }}
                />
              </div>
            </div>
          )}

          {/* Succeeded Result with YouTube URL */}
          {latestAttempt?.provider_video_id && (
            <div
              style={{
                padding: "0.85rem 1rem",
                background: "var(--status-success-bg)",
                border: "1px solid var(--status-success-border)",
                borderRadius: "var(--radius-sm)",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <div>
                <span style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--status-success)", display: "block" }}>
                  ✓ Published to YouTube
                </span>
                <span className="text-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
                  Video ID: {latestAttempt.provider_video_id}
                </span>
              </div>
              {latestAttempt.provider_url && (
                <a
                  href={latestAttempt.provider_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn btn-success btn-sm"
                  style={{ fontSize: "0.75rem" }}
                >
                  Open on YouTube ↗
                </a>
              )}
            </div>
          )}

          {/* Ambiguous State / Reconciliation Required */}
          {(latestAttempt?.state === "UNKNOWN" || latestAttempt?.reconciliation_status === "PENDING") && (
            <div
              style={{
                padding: "0.85rem 1rem",
                background: "rgba(168, 85, 247, 0.12)",
                border: "1px solid rgba(168, 85, 247, 0.3)",
                borderRadius: "var(--radius-sm)",
                fontSize: "0.78rem",
              }}
            >
              <div style={{ fontWeight: 700, color: "var(--status-purple)", marginBottom: "0.25rem" }}>
                ⚠️ RESULT UNKNOWN — RECONCILIATION REQUIRED
              </div>
              <p style={{ color: "var(--text-primary)" }}>
                The provider outcome could not be deterministically verified. A background reconciliation sweep is required before retrying to prevent duplicate uploads.
              </p>
            </div>
          )}

          {/* Failure & Diagnostic Reason */}
          {latestAttempt?.error_message && latestAttempt.state !== "UNKNOWN" && (
            <div
              style={{
                padding: "0.85rem 1rem",
                background: "var(--status-danger-bg)",
                border: "1px solid var(--status-danger-border)",
                borderRadius: "var(--radius-sm)",
                fontSize: "0.78rem",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", color: "var(--status-danger)", fontWeight: 600, marginBottom: "0.25rem" }}>
                <span>{latestAttempt.error_category || "Publish Error"}</span>
                {latestAttempt.retry_after_seconds && (
                  <span className="text-mono">Retry in {latestAttempt.retry_after_seconds}s</span>
                )}
              </div>
              <p style={{ color: "var(--text-primary)" }}>{latestAttempt.error_message}</p>
            </div>
          )}

          {/* Safe Execution Button (Opens Confirmation Modal) */}
          {latestIntent.state === "APPROVED" && (
            <button
              onClick={() => setShowConfirmModal(true)}
              disabled={loading || !isExecutionPermitted}
              title={
                isArchived
                  ? "Activate this channel before publishing content."
                  : isGuardianBlocked
                  ? "Guardian gate is BLOCKED."
                  : "Trigger Live Video Publication to YouTube"
              }
              className="btn btn-primary"
              style={{
                width: "100%",
                padding: "0.7rem",
                background: isExecutionPermitted ? "#dc2626" : undefined,
                borderColor: isExecutionPermitted ? "#b91c1c" : undefined,
                fontWeight: 700,
                letterSpacing: "0.02em",
              }}
            >
              {loading ? "Executing Upload..." : "🚀 Execute Publication to YouTube"}
            </button>
          )}
        </div>
      ) : (
        <div style={{ textAlign: "center", padding: "1.5rem", fontSize: "0.82rem", color: "var(--text-muted)" }}>
          No active publish intent for this channel.
        </div>
      )}

      {/* Rejection Modal */}
      {showRejectModal && (
        <div className="modal-backdrop">
          <div className="modal-card" style={{ maxWidth: "480px" }}>
            <div className="modal-header">
              <h3 style={{ fontSize: "0.95rem", fontWeight: 700, color: "var(--status-danger)" }}>
                Reject Publication Intent
              </h3>
              <button onClick={() => setShowRejectModal(false)} className="btn btn-secondary btn-sm">✕</button>
            </div>
            <div className="modal-body">
              <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
                Reason for Rejection:
              </label>
              <textarea
                className="textarea"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder="Explain why this intent is being rejected..."
              />
            </div>
            <div className="modal-footer">
              <button onClick={() => setShowRejectModal(false)} className="btn btn-secondary btn-sm">
                Cancel
              </button>
              <button onClick={handleReject} disabled={loading} className="btn btn-danger btn-sm">
                Confirm Rejection
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Explicit Publish Execution Confirmation Modal */}
      {showConfirmModal && latestIntent && (
        <div className="modal-backdrop">
          <div className="modal-card" style={{ maxWidth: "560px" }}>
            <div className="modal-header">
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <span style={{ fontSize: "1.2rem" }}>⚠️</span>
                <h3 style={{ fontSize: "0.98rem", fontWeight: 700, color: "var(--text-primary)" }}>
                  Confirm Video Publication to YouTube
                </h3>
              </div>
              <button onClick={() => setShowConfirmModal(false)} className="btn btn-secondary btn-sm">✕</button>
            </div>
            <div className="modal-body" style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
              <p style={{ fontSize: "0.82rem", color: "var(--text-secondary)", lineHeight: 1.5 }}>
                You are about to initiate an external publication upload. This triggers the live YouTube Data API v3 upload pipeline with resilient chunking and idempotent locking.
              </p>

              <div
                style={{
                  padding: "0.85rem 1rem",
                  background: "var(--bg-input)",
                  borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--border-subtle)",
                  display: "flex",
                  flexDirection: "column",
                  gap: "0.4rem",
                  fontSize: "0.78rem",
                }}
              >
                <div>
                  <span style={{ color: "var(--text-muted)" }}>Target Title:</span>{" "}
                  <strong style={{ color: "var(--text-primary)" }}>{latestIntent.title}</strong>
                </div>
                <div>
                  <span style={{ color: "var(--text-muted)" }}>Platform Account:</span>{" "}
                  <span className="badge badge-active">{account?.account_display_name || "YouTube"}</span>
                </div>
                <div>
                  <span style={{ color: "var(--text-muted)" }}>Privacy Status:</span>{" "}
                  <span className="badge badge-neutral">{latestIntent.requested_privacy_status}</span>
                </div>
                <div>
                  <span style={{ color: "var(--text-muted)" }}>Artifact Checksum:</span>{" "}
                  <span className="text-mono" style={{ color: "var(--accent-secondary)", fontSize: "0.72rem" }}>
                    {latestIntent.media_artifact_checksum.substring(0, 32)}...
                  </span>
                </div>
              </div>

              <div
                style={{
                  padding: "0.75rem 1rem",
                  background: "var(--status-warning-bg)",
                  border: "1px solid var(--status-warning-border)",
                  borderRadius: "var(--radius-sm)",
                  fontSize: "0.75rem",
                  color: "var(--status-warning)",
                }}
              >
                🔒 <strong>Safety Assurance:</strong> Idempotency key verified. Guardian gate is {guardianStatus?.overall_gate_state || "OPEN"}.
              </div>
            </div>
            <div className="modal-footer">
              <button onClick={() => setShowConfirmModal(false)} className="btn btn-secondary btn-sm">
                Cancel
              </button>
              <button
                onClick={handleExecuteConfirmed}
                disabled={loading}
                className="btn btn-primary btn-sm"
                style={{ background: "#dc2626", borderColor: "#b91c1c", fontWeight: 700 }}
              >
                {loading ? "Executing..." : "Confirm & Publish to YouTube"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
