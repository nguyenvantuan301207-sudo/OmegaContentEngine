"use client";

import React, { useState } from "react";
import {
  createYouTubeAuthorizeUrl,
  disconnectPlatformAccount,
  executePublish,
  PlatformAccount,
  PublishAttempt,
  PublishIntent,
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

  const handleExecute = async () => {
    if (!latestIntent || isArchived) return;
    try {
      setLoading(true);
      setError(null);
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
            <span className={`badge ${getStateBadgeClass(latestIntent.state)}`}>
              {latestIntent.state}
            </span>
          </div>

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
                Made for Kids: <strong style={{ color: "var(--text-primary)" }}>{latestIntent.made_for_kids ? "Yes" : "No"}</strong>
              </span>
            </div>
          </div>

          {/* Upload Progress Bar */}
          {uploadProgress && !uploadProgress.is_complete && (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", color: "var(--text-secondary)" }}>
                <span>Uploading Video Chunks</span>
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
                  View on YouTube ↗
                </a>
              )}
            </div>
          )}

          {/* Failure & Diagnostic Reason */}
          {latestAttempt?.error_message && (
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

          {/* Manual Trigger Button for Approved Intent */}
          {latestIntent.state === "APPROVED" && (
            <button
              onClick={handleExecute}
              disabled={loading || isArchived}
              title={isArchived ? "Activate this channel before publishing content." : "Publish Now"}
              className="btn btn-primary"
              style={{ width: "100%", padding: "0.6rem" }}
            >
              {loading ? "Executing Upload..." : "Publish Now"}
            </button>
          )}
        </div>
      ) : (
        <div style={{ textAlign: "center", padding: "1.5rem", fontSize: "0.82rem", color: "var(--text-muted)" }}>
          No active publish intent for this channel.
        </div>
      )}
    </div>
  );
}
