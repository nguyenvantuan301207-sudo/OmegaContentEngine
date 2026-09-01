"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useOperatorContext } from "@/lib/operator-context";
import {
  PlatformAccount,
  PublishAttempt,
  PublishIntent,
  UploadProgress,
  getPublishAttempt,
  getUploadProgress,
  listPlatformAccounts,
  listPublishIntents,
} from "@/lib/api";
import { PublishingStatusCard } from "@/components/PublishingStatusCard";
import { ChannelContextBar } from "@/components/ChannelContextBar";

export default function PublisherPage() {
  const { selectedChannelId } = useOperatorContext();

  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [intents, setIntents] = useState<PublishIntent[]>([]);
  const [selectedIntent, setSelectedIntent] = useState<PublishIntent | null>(null);
  const [latestAttempt, setLatestAttempt] = useState<PublishAttempt | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const selectIntent = useCallback(async (intent: PublishIntent | null) => {
    setSelectedIntent(intent);
    if (intent) {
      try {
        const attempt = await getPublishAttempt(intent.id).catch(() => null);
        setLatestAttempt(attempt);
        if (attempt) {
          const prog = await getUploadProgress(attempt.id).catch(() => null);
          setUploadProgress(prog);
        } else {
          setUploadProgress(null);
        }
      } catch {
        setLatestAttempt(null);
        setUploadProgress(null);
      }
    } else {
      setLatestAttempt(null);
      setUploadProgress(null);
    }
  }, []);

  const loadData = useCallback(async () => {
    if (!selectedChannelId) return;
    try {
      setLoading(true);
      setError(null);

      const [accs, intentList] = await Promise.all([
        listPlatformAccounts(selectedChannelId).catch(() => []),
        listPublishIntents({ channel_id: selectedChannelId }).catch(() => []),
      ]);

      setAccounts(accs);
      setIntents(intentList);

      // Check URL query param for intent_id
      let targetIntent: PublishIntent | null = null;
      if (typeof window !== "undefined") {
        const params = new URLSearchParams(window.location.search);
        const urlIntentId = params.get("intent_id");
        if (urlIntentId) {
          targetIntent = intentList.find((i) => i.id === urlIntentId) || null;
        }
      }

      const activeIntent = targetIntent || intentList[0] || null;
      await selectIntent(activeIntent);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load publisher data");
    } finally {
      setLoading(false);
    }
  }, [selectedChannelId, selectIntent]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const activeAccount = accounts.find((a) => a.status === "ACTIVE") || accounts[0] || null;

  return (
    <div style={{ maxWidth: "1200px", margin: "0 auto", padding: "1.5rem" }}>
      {/* Universal Channel Context Bar */}
      <ChannelContextBar currentTab="publisher" />

      {/* Page Header */}
      <div className="page-header" style={{ marginBottom: "1.5rem" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.25rem" }}>
            <h1 className="page-title">☁ Publisher Cockpit</h1>
            <span className="badge badge-active">OMEGA-011</span>
          </div>
          <p className="page-subtitle">
            OAuth account management, Guardian readiness gates, chunked resumable upload, and video verification.
          </p>
        </div>
      </div>

      {error && (
        <div
          style={{
            padding: "0.75rem 1rem",
            background: "var(--status-danger-bg)",
            border: "1px solid var(--status-danger-border)",
            borderRadius: "var(--radius-sm)",
            fontSize: "0.82rem",
            color: "var(--status-danger)",
            marginBottom: "1.5rem",
          }}
        >
          {error}
        </div>
      )}

      {/* Main Publishing Status Card */}
      {selectedChannelId && (
        <div style={{ marginBottom: "1.5rem" }}>
          <PublishingStatusCard
            channelId={selectedChannelId}
            account={activeAccount}
            latestIntent={selectedIntent}
            latestAttempt={latestAttempt}
            uploadProgress={uploadProgress}
            onRefresh={loadData}
          />
        </div>
      )}

      {/* Publish Intent History Table */}
      <div className="card" style={{ padding: "1.25rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
          <div>
            <h3 style={{ fontSize: "0.95rem", fontWeight: 700, color: "var(--text-primary)" }}>
              Publication Intent History ({intents.length})
            </h3>
            <p style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
              Select any intent below to inspect Guardian readiness, approval status, and execution details.
            </p>
          </div>
          <button onClick={loadData} disabled={loading} className="btn btn-secondary btn-sm" style={{ fontSize: "0.75rem" }}>
            ↻ Refresh History
          </button>
        </div>

        {intents.length === 0 ? (
          <div
            style={{
              textAlign: "center",
              padding: "2rem 1rem",
              color: "var(--text-muted)",
              fontSize: "0.82rem",
              background: "var(--bg-input)",
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--border-subtle)",
            }}
          >
            No publication intents created for this channel yet. Prepare a video render in Production Workspace to initiate a publish intent.
          </div>
        ) : (
          <div className="table-container">
            <table className="table" style={{ width: "100%" }}>
              <thead>
                <tr>
                  <th>Title & Revision</th>
                  <th>State</th>
                  <th>Privacy</th>
                  <th>Task ID</th>
                  <th>Created</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {intents.map((intent) => {
                  const isSelected = selectedIntent?.id === intent.id;
                  return (
                    <tr
                      key={intent.id}
                      onClick={() => selectIntent(intent)}
                      style={{
                        cursor: "pointer",
                        background: isSelected ? "var(--bg-card-hover)" : undefined,
                      }}
                    >
                      <td>
                        <div style={{ fontWeight: 600, fontSize: "0.85rem", color: "var(--text-primary)" }}>
                          {intent.title} {isSelected && <span style={{ color: "var(--accent-secondary)", fontSize: "0.72rem" }}>★ Active</span>}
                        </div>
                        <div className="text-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
                          v{intent.revision_number} • Media: {intent.media_artifact_id.substring(0, 8)}...
                        </div>
                      </td>
                      <td>
                        <span className={`badge ${intent.state === "PUBLISHED" ? "badge-success" : intent.state === "FAILED" ? "badge-failed" : "badge-active"}`}>
                          {intent.state}
                        </span>
                      </td>
                      <td>
                        <span className="badge badge-neutral text-mono" style={{ fontSize: "0.7rem" }}>
                          {intent.requested_privacy_status}
                        </span>
                      </td>
                      <td>
                        <span className="text-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
                          {intent.task_id.substring(0, 8)}...
                        </span>
                      </td>
                      <td>
                        <span className="text-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
                          {new Date(intent.created_at).toLocaleDateString()}
                        </span>
                      </td>
                      <td>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            selectIntent(intent);
                          }}
                          className={`btn btn-sm ${isSelected ? "btn-primary" : "btn-secondary"}`}
                          style={{ fontSize: "0.72rem", padding: "0.2rem 0.5rem" }}
                        >
                          {isSelected ? "Inspecting" : "Select"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
