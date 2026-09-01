"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import {
  Channel,
  MediaArtifact,
  PlatformAccount,
  PrivacyStatus,
  ProductionRequest,
  PublishIntent,
  createPublishIntent,
  getMissions,
  getMissionTasks,
  listPlatformAccounts,
  Task,
  Mission,
} from "@/lib/api";

interface PublishPreparationModalProps {
  channel: Channel;
  productionRequest: ProductionRequest;
  artifact: MediaArtifact;
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: (intent: PublishIntent) => void;
}

export function PublishPreparationModal({
  channel,
  productionRequest,
  artifact,
  isOpen,
  onClose,
  onSuccess,
}: PublishPreparationModalProps) {
  const isArchived = channel.state === "ARCHIVED";

  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [selectedAccountId, setSelectedAccountId] = useState<string>("");
  const [missions, setMissions] = useState<Mission[]>([]);
  const [selectedMissionId, setSelectedMissionId] = useState<string>("");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string>("");

  const [title, setTitle] = useState<string>("");
  const [description, setDescription] = useState<string>("");
  const [tagsInput, setTagsInput] = useState<string>("");
  const [privacy, setPrivacy] = useState<PrivacyStatus>("PRIVATE");
  const [categoryId, setCategoryId] = useState<string>("28"); // Science & Technology
  const [madeForKids, setMadeForKids] = useState<boolean>(false);

  const [loading, setLoading] = useState<boolean>(true);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [createdIntent, setCreatedIntent] = useState<PublishIntent | null>(null);

  // Initialize and load accounts + missions
  useEffect(() => {
    if (!isOpen) return;

    async function init() {
      try {
        setLoading(true);
        setError(null);
        setCreatedIntent(null);

        // Pre-populate default title & description
        setTitle(`Episode: ${channel.name} Video`);
        setDescription(`Official release from ${channel.name}.\n\nProduced with OMEGA Content Engine.`);
        setTagsInput("omega, video, technology");

        // 1. Fetch connected platform accounts
        const accts = await listPlatformAccounts(channel.id);
        const activeAccts = accts.filter((a) => a.status === "ACTIVE");
        setAccounts(activeAccts);
        if (activeAccts.length > 0) {
          setSelectedAccountId(activeAccts[0].id);
        }

        // 2. Fetch missions for this channel to obtain canonical mission_id & task_id
        const allMissions = await getMissions();
        const mList = allMissions.filter((m) => m.channel_id === channel.id);
        setMissions(mList);
        if (mList.length > 0) {
          const firstMission = mList[0];
          setSelectedMissionId(firstMission.id);
          const tList = await getMissionTasks(firstMission.id);
          setTasks(tList);
          if (tList.length > 0) {
            setSelectedTaskId(tList[0].id);
          }
        }
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Failed to load publishing prerequisites.");
      } finally {
        setLoading(false);
      }
    }

    init();
  }, [isOpen, channel.id, channel.name]);

  // When selected mission changes, load its tasks
  const handleMissionChange = async (mId: string) => {
    setSelectedMissionId(mId);
    try {
      const tList = await getMissionTasks(mId);
      setTasks(tList);
      if (tList.length > 0) {
        setSelectedTaskId(tList[0].id);
      } else {
        setSelectedTaskId("");
      }
    } catch {
      setTasks([]);
      setSelectedTaskId("");
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isArchived) return;

    if (!selectedAccountId) {
      setError("Please connect and select an active platform account.");
      return;
    }
    if (!selectedMissionId || !selectedTaskId) {
      setError("A valid Mission and Task are required to create a Publish Intent.");
      return;
    }
    if (!title.trim()) {
      setError("Video title is mandatory.");
      return;
    }

    const tags = tagsInput
      .split(",")
      .map((t) => t.trim())
      .filter((t) => t.length > 0);

    try {
      setSubmitting(true);
      setError(null);

      const payload = {
        mission_id: selectedMissionId,
        task_id: selectedTaskId,
        channel_id: channel.id,
        platform_account_id: selectedAccountId,
        media_artifact_id: artifact.id,
        media_artifact_checksum: artifact.content_hash,
        channel_dna_revision_id: productionRequest.channel_dna_revision_id || null,
        title: title.trim(),
        description: description.trim(),
        tags,
        requested_privacy_status: privacy,
        category_id: categoryId,
        made_for_kids: madeForKids,
      };

      const intent = await createPublishIntent(payload);
      setCreatedIntent(intent);
      if (onSuccess) onSuccess(intent);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create publish intent.");
    } finally {
      setSubmitting(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="modal-backdrop">
      <div className="modal-card" style={{ maxWidth: "680px" }}>
        {/* Modal Header */}
        <div className="modal-header">
          <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
            <span style={{ fontSize: "1.2rem" }}>🚀</span>
            <div>
              <h3 style={{ fontSize: "1rem", fontWeight: 700, color: "var(--text-primary)" }}>
                Prepare Video Publication
              </h3>
              <p style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                Construct an approved PublishIntent snapshot for {channel.name}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="btn btn-secondary btn-sm"
            style={{ padding: "0.2rem 0.55rem" }}
          >
            ✕
          </button>
        </div>

        {/* Modal Body */}
        <div className="modal-body">
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

          {/* Success Result View */}
          {createdIntent ? (
            <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
              <div
                style={{
                  padding: "1rem 1.25rem",
                  background: "var(--status-success-bg)",
                  border: "1px solid var(--status-success-border)",
                  borderRadius: "var(--radius-sm)",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.5rem" }}>
                  <span style={{ color: "var(--status-success)", fontWeight: 700, fontSize: "0.95rem" }}>
                    ✓ Publish Intent Created Successfully
                  </span>
                  <span className="badge badge-success" style={{ fontSize: "0.7rem" }}>
                    {createdIntent.state}
                  </span>
                </div>
                <p style={{ fontSize: "0.8rem", color: "var(--text-primary)", marginBottom: "0.5rem" }}>
                  <strong>Title:</strong> {createdIntent.title}
                </p>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem", fontSize: "0.75rem", color: "var(--text-secondary)" }}>
                  <div>
                    <span style={{ color: "var(--text-muted)" }}>Privacy: </span>
                    <span className="badge badge-neutral" style={{ fontSize: "0.68rem" }}>{createdIntent.requested_privacy_status}</span>
                  </div>
                  <div>
                    <span style={{ color: "var(--text-muted)" }}>Revision: </span>
                    <span className="text-mono">v{createdIntent.revision_number}</span>
                  </div>
                  <div style={{ gridColumn: "span 2" }}>
                    <span style={{ color: "var(--text-muted)" }}>Intent Checksum: </span>
                    <span className="text-mono" style={{ fontSize: "0.7rem", color: "var(--accent-secondary)" }}>
                      {createdIntent.intent_checksum.slice(0, 24)}...
                    </span>
                  </div>
                </div>
              </div>

              {/* Next Steps Routing Buttons */}
              <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
                <span style={{ fontSize: "0.78rem", fontWeight: 600, color: "var(--text-secondary)", textTransform: "uppercase" }}>
                  Next Workflow Stages:
                </span>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" }}>
                  <Link
                    href={`/schedule?channel_id=${channel.id}&intent_id=${createdIntent.id}`}
                    className="btn btn-secondary"
                    style={{ fontSize: "0.82rem", padding: "0.6rem", textAlign: "center" }}
                  >
                    📅 Smart Scheduler →
                  </Link>
                  <Link
                    href={`/publisher?channel_id=${channel.id}&intent_id=${createdIntent.id}`}
                    className="btn btn-primary"
                    style={{ fontSize: "0.82rem", padding: "0.6rem", textAlign: "center" }}
                  >
                    🚀 Publisher Cockpit →
                  </Link>
                </div>
              </div>
            </div>
          ) : loading ? (
            <div style={{ textAlign: "center", padding: "2rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
              Loading publishing context and platform accounts...
            </div>
          ) : (
            <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
              {/* Media Artifact Summary Panel */}
              <div
                style={{
                  padding: "0.85rem 1rem",
                  background: "var(--bg-input)",
                  borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--border-subtle)",
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: "0.5rem",
                  fontSize: "0.78rem",
                }}
              >
                <div>
                  <span style={{ color: "var(--text-muted)" }}>Target Artifact:</span>{" "}
                  <span className="text-mono" style={{ color: "var(--text-primary)", fontWeight: 600 }}>
                    v{artifact.version} ({artifact.width}x{artifact.height})
                  </span>
                </div>
                <div>
                  <span style={{ color: "var(--text-muted)" }}>Duration:</span>{" "}
                  <span className="text-mono" style={{ color: "var(--status-success)" }}>
                    {artifact.duration_ms ? (artifact.duration_ms / 1000).toFixed(1) : "0"}s
                  </span>
                </div>
                <div style={{ gridColumn: "span 2" }}>
                  <span style={{ color: "var(--text-muted)" }}>SHA-256 Hash:</span>{" "}
                  <span className="text-mono" style={{ color: "var(--accent-secondary)", fontSize: "0.72rem" }}>
                    {artifact.content_hash}
                  </span>
                </div>
              </div>

              {/* Platform Account Selector */}
              <div>
                <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
                  Connected YouTube Account *
                </label>
                {accounts.length === 0 ? (
                  <div
                    style={{
                      padding: "0.75rem 1rem",
                      background: "var(--status-warning-bg)",
                      border: "1px solid var(--status-warning-border)",
                      borderRadius: "var(--radius-sm)",
                      fontSize: "0.78rem",
                      color: "var(--status-warning)",
                    }}
                  >
                    ⚠️ No active YouTube account connected for this channel.{" "}
                    <Link href="/publisher" style={{ textDecoration: "underline", fontWeight: 600 }}>
                      Connect YouTube Account in Publisher Cockpit →
                    </Link>
                  </div>
                ) : (
                  <select
                    className="select"
                    style={{ width: "100%" }}
                    value={selectedAccountId}
                    onChange={(e) => setSelectedAccountId(e.target.value)}
                    required
                  >
                    {accounts.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.account_display_name} ({a.platform} • {a.external_account_id})
                      </option>
                    ))}
                  </select>
                )}
              </div>

              {/* Mission & Task Linkage */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" }}>
                <div>
                  <label style={{ display: "block", fontSize: "0.75rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "0.3rem" }}>
                    Associated Mission
                  </label>
                  <select
                    className="select"
                    style={{ width: "100%", fontSize: "0.78rem" }}
                    value={selectedMissionId}
                    onChange={(e) => handleMissionChange(e.target.value)}
                  >
                    {missions.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.id.slice(0, 8)}... ({m.state})
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label style={{ display: "block", fontSize: "0.75rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "0.3rem" }}>
                    Associated Task
                  </label>
                  <select
                    className="select"
                    style={{ width: "100%", fontSize: "0.78rem" }}
                    value={selectedTaskId}
                    onChange={(e) => setSelectedTaskId(e.target.value)}
                  >
                    {tasks.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.task_type} ({t.id.slice(0, 8)}...)
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Title Field */}
              <div>
                <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
                  Publication Video Title *
                </label>
                <input
                  type="text"
                  className="input"
                  style={{ width: "100%" }}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  maxLength={255}
                  placeholder="Enter compelling, policy-compliant title..."
                  required
                />
              </div>

              {/* Description Field */}
              <div>
                <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
                  Video Description
                </label>
                <textarea
                  className="textarea"
                  style={{ width: "100%", minHeight: "75px" }}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  maxLength={5000}
                  placeholder="Enter description, timestamps, and attribution links..."
                />
              </div>

              {/* Tags Field */}
              <div>
                <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "0.35rem" }}>
                  Tags (Comma-separated)
                </label>
                <input
                  type="text"
                  className="input"
                  style={{ width: "100%" }}
                  value={tagsInput}
                  onChange={(e) => setTagsInput(e.target.value)}
                  placeholder="e.g. technology, tutorial, deepdive"
                />
              </div>

              {/* Privacy, Category, and COPPA Compliance */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "0.75rem" }}>
                <div>
                  <label style={{ display: "block", fontSize: "0.75rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "0.3rem" }}>
                    Privacy Status
                  </label>
                  <select
                    className="select"
                    style={{ width: "100%", fontSize: "0.8rem" }}
                    value={privacy}
                    onChange={(e) => setPrivacy(e.target.value as PrivacyStatus)}
                  >
                    <option value="PRIVATE">PRIVATE (Safe Default)</option>
                    <option value="UNLISTED">UNLISTED</option>
                    <option value="PUBLIC">PUBLIC</option>
                  </select>
                </div>
                <div>
                  <label style={{ display: "block", fontSize: "0.75rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "0.3rem" }}>
                    YouTube Category
                  </label>
                  <select
                    className="select"
                    style={{ width: "100%", fontSize: "0.8rem" }}
                    value={categoryId}
                    onChange={(e) => setCategoryId(e.target.value)}
                  >
                    <option value="28">28 — Science & Tech</option>
                    <option value="27">27 — Education</option>
                    <option value="22">22 — People & Blogs</option>
                    <option value="24">24 — Entertainment</option>
                  </select>
                </div>
                <div>
                  <label style={{ display: "block", fontSize: "0.75rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "0.3rem" }}>
                    Audience (COPPA)
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.78rem", color: "var(--text-primary)", marginTop: "0.4rem" }}>
                    <input
                      type="checkbox"
                      checked={madeForKids}
                      onChange={(e) => setMadeForKids(e.target.checked)}
                    />
                    Made for Kids
                  </label>
                </div>
              </div>

              {/* Submit Action */}
              <div className="modal-footer" style={{ padding: "1rem 0 0 0", borderTop: "1px solid var(--border-subtle)", marginTop: "0.5rem" }}>
                <button type="button" onClick={onClose} className="btn btn-secondary btn-sm">
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submitting || accounts.length === 0 || isArchived}
                  title={isArchived ? "Activate this channel before preparing publication." : "Construct Publish Intent Snapshot"}
                  className="btn btn-primary btn-sm"
                >
                  {submitting ? "Constructing Intent..." : "🚀 Construct Publish Intent"}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
