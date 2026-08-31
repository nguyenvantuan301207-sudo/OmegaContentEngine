"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Channel,
  ChannelDNARevision,
  activateChannel,
  archiveChannel,
  getChannel,
  getChannelDNARevisions,
  pauseChannel,
  updateChannelDNA,
} from "@/lib/api";

export default function ChannelDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const resolvedParams = use(params);
  const channelId = resolvedParams.id;

  const [channel, setChannel] = useState<Channel | null>(null);
  const [revisions, setRevisions] = useState<ChannelDNARevision[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  // Edit DNA State
  const [isEditingDNA, setIsEditingDNA] = useState(false);
  const [dnaJson, setDnaJson] = useState("");
  const [changeReason, setChangeReason] = useState("");
  const [dnaError, setDnaError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const [chanData, revsData] = await Promise.all([
        getChannel(channelId),
        getChannelDNARevisions(channelId),
      ]);
      setChannel(chanData);
      setRevisions(revsData);
      setDnaJson(JSON.stringify(chanData.dna, null, 2));
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load channel");
    } finally {
      setLoading(false);
    }
  }, [channelId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  async function handleAction(
    action: "activate" | "pause" | "archive"
  ) {
    try {
      setActionLoading(true);
      let updated: Channel;
      if (action === "activate") updated = await activateChannel(channelId);
      else if (action === "pause") updated = await pauseChannel(channelId);
      else updated = await archiveChannel(channelId);

      setChannel(updated);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : `Action ${action} failed`);
    } finally {
      setActionLoading(false);
    }
  }

  async function handleSaveDNA(e: React.FormEvent) {
    e.preventDefault();
    if (!channel) return;
    if (!changeReason.trim() || changeReason.trim().length < 3) {
      setDnaError("A change reason of at least 3 characters is mandatory.");
      return;
    }

    try {
      setDnaError(null);
      const parsedDna = JSON.parse(dnaJson);
      await updateChannelDNA(channelId, parsedDna, changeReason.trim());
      setIsEditingDNA(false);
      setChangeReason("");
      await loadData();
    } catch (err: unknown) {
      setDnaError(err instanceof Error ? err.message : "Invalid DNA format");
    }
  }

  const getStatusBadge = (state: string) => {
    switch (state) {
      case "ACTIVE":
        return "badge-active";
      case "PAUSED":
        return "badge-paused";
      case "ARCHIVED":
        return "badge-failed";
      case "DRAFT":
      default:
        return "badge-draft";
    }
  };

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: "5rem 0", color: "var(--text-muted)", fontSize: "0.9rem" }}>
        Loading Channel workspace...
      </div>
    );
  }

  if (error && !channel) {
    return (
      <div>
        <div style={{ marginBottom: "1rem" }}>
          <Link href="/channels" style={{ fontSize: "0.78rem", color: "var(--accent-secondary)" }}>
            ← Back to Channels
          </Link>
        </div>
        <div style={{ padding: "1.25rem", background: "var(--status-danger-bg)", border: "1px solid var(--status-danger-border)", borderRadius: "var(--radius-sm)", color: "var(--status-danger)", fontSize: "0.85rem" }}>
          {error}
        </div>
      </div>
    );
  }

  if (!channel) return null;

  return (
    <div>
      {/* Header & Breadcrumb */}
      <div className="page-header">
        <div>
          <div style={{ marginBottom: "0.5rem" }}>
            <Link
              href="/channels"
              style={{ fontSize: "0.78rem", color: "var(--accent-secondary)", textDecoration: "none" }}
            >
              ← Back to Channels
            </Link>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
            <h1 className="page-title">{channel.name}</h1>
            <span className={`badge ${getStatusBadge(channel.state)}`}>
              {channel.state}
            </span>
            <span className="badge badge-draft text-mono">
              {channel.platform}
            </span>
          </div>
          <p className="page-subtitle text-mono">
            Slug: /{channel.slug} • ID: {channel.id}
          </p>
        </div>

        {/* Lifecycle Actions */}
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center" }}>
          {(channel.state === "DRAFT" || channel.state === "PAUSED") && (
            <button
              onClick={() => handleAction("activate")}
              disabled={actionLoading}
              className="btn btn-success btn-sm"
            >
              Activate Channel
            </button>
          )}

          {channel.state === "ACTIVE" && (
            <button
              onClick={() => handleAction("pause")}
              disabled={actionLoading}
              className="btn btn-secondary btn-sm"
            >
              Pause Channel
            </button>
          )}

          {channel.state !== "ARCHIVED" && (
            <button
              onClick={() => handleAction("archive")}
              disabled={actionLoading}
              className="btn btn-danger btn-sm"
            >
              Archive
            </button>
          )}

          {channel.state !== "ARCHIVED" && (
            <button
              onClick={() => {
                setIsEditingDNA(!isEditingDNA);
                setDnaJson(JSON.stringify(channel.dna, null, 2));
              }}
              className="btn btn-primary btn-sm"
            >
              {isEditingDNA ? "Close Editor" : "✎ Edit DNA"}
            </button>
          )}
        </div>
      </div>

      {/* Global Error Banner */}
      {error && (
        <div style={{ padding: "1rem", background: "var(--status-danger-bg)", border: "1px solid var(--status-danger-border)", borderRadius: "var(--radius-sm)", color: "var(--status-danger)", marginBottom: "1.5rem", fontSize: "0.85rem" }}>
          {error}
        </div>
      )}

      {/* Quick Navigation Links */}
      <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1.5rem", flexWrap: "wrap" }}>
        <Link href={`/channels/${channel.id}/topics`} className="btn btn-secondary btn-sm">
          💡 Topics
        </Link>
        <Link href={`/channels/${channel.id}/research`} className="btn btn-secondary btn-sm">
          🔬 Research
        </Link>
        <Link href={`/channels/${channel.id}/content`} className="btn btn-secondary btn-sm">
          ✍️ Content
        </Link>
        <Link href={`/channels/${channel.id}/production`} className="btn btn-secondary btn-sm">
          🎬 Production
        </Link>
        <Link href={`/missions/new?channel_id=${channel.id}`} className="btn btn-primary btn-sm">
          + Launch Mission
        </Link>
      </div>

      {/* DNA Editor Modal / Drawer */}
      {isEditingDNA && (
        <div className="card" style={{ marginBottom: "2rem", borderColor: "var(--border-accent)" }}>
          <div className="card-header">
            <h3 className="card-title">Update Channel DNA (Creates New Revision)</h3>
            <span className="badge badge-ready">
              Active: v{revisions[0]?.version || 1}
            </span>
          </div>

          {dnaError && (
            <div style={{ padding: "0.75rem", background: "var(--status-danger-bg)", border: "1px solid var(--status-danger-border)", borderRadius: "var(--radius-sm)", color: "var(--status-danger)", marginBottom: "1rem", fontSize: "0.82rem" }}>
              {dnaError}
            </div>
          )}

          <form onSubmit={handleSaveDNA}>
            <div className="form-group">
              <label className="form-label">
                Change Reason (Mandatory rationale for audit trail) *
              </label>
              <input
                type="text"
                required
                value={changeReason}
                onChange={(e) => setChangeReason(e.target.value)}
                placeholder="e.g. Updating content pillars to include Deep Dives and adjusting audience age range"
                className="input"
                style={{ width: "100%" }}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Channel DNA JSON Configuration</label>
              <textarea
                rows={12}
                value={dnaJson}
                onChange={(e) => setDnaJson(e.target.value)}
                className="text-mono"
                style={{ fontSize: "0.78rem", color: "var(--accent-secondary)" }}
              />
            </div>

            <div className="form-actions">
              <button
                type="button"
                onClick={() => setIsEditingDNA(false)}
                className="btn btn-secondary"
              >
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn-primary"
              >
                Save & Create Revision v{(revisions[0]?.version || 1) + 1}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Structured DNA Cards Grid */}
      <div className="grid grid-cols-3" style={{ marginBottom: "2rem" }}>
        {/* Localization & Routing */}
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">Localization & Routing</h3>
            <span className="badge badge-draft">{channel.platform}</span>
          </div>
          <div className="card-body" style={{ fontSize: "0.85rem" }}>
            <div className="flex-between">
              <span className="text-muted">Primary Language:</span>
              <strong className="text-mono">{channel.primary_language}</strong>
            </div>
            <div className="flex-between">
              <span className="text-muted">Target Region:</span>
              <strong className="text-mono">{channel.target_region}</strong>
            </div>
            <div className="flex-between">
              <span className="text-muted">Timezone:</span>
              <strong className="text-mono">{channel.timezone}</strong>
            </div>
            <div className="flex-between">
              <span className="text-muted">Platform Channel ID:</span>
              <span className="text-mono text-muted">
                {channel.platform_channel_id || "Unlinked"}
              </span>
            </div>
          </div>
        </div>

        {/* Audience Profile */}
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">Audience Profile</h3>
            <span className="badge badge-draft">Targeting</span>
          </div>
          <div className="card-body" style={{ fontSize: "0.85rem" }}>
            <div className="flex-between">
              <span className="text-muted">Age Range:</span>
              <span>{channel.dna?.audience?.age_range || "N/A"}</span>
            </div>
            <div className="flex-between">
              <span className="text-muted">Knowledge Level:</span>
              <span>{channel.dna?.audience?.knowledge_level || "ALL_LEVELS"}</span>
            </div>
            <div className="flex-between">
              <span className="text-muted">Interests:</span>
              <span style={{ maxWidth: "160px", textAlign: "right", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {channel.dna?.audience?.interests?.join(", ") || "None"}
              </span>
            </div>
            <div className="flex-between">
              <span className="text-muted">Preferred Length:</span>
              <span>{channel.dna?.audience?.preferred_content_length || "N/A"}</span>
            </div>
          </div>
        </div>

        {/* Brand Voice & Strategy */}
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">Brand Voice & Strategy</h3>
            <span className="badge badge-draft">Editorial</span>
          </div>
          <div className="card-body" style={{ fontSize: "0.85rem" }}>
            <div className="flex-between">
              <span className="text-muted">Niche:</span>
              <strong>{channel.dna?.content_strategy?.niche || "General"}</strong>
            </div>
            <div className="flex-between">
              <span className="text-muted">Pillars:</span>
              <span style={{ maxWidth: "160px", textAlign: "right", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {channel.dna?.content_strategy?.content_pillars?.join(", ") || "None"}
              </span>
            </div>
            <div className="flex-between">
              <span className="text-muted">Tone:</span>
              <span>{channel.dna?.brand_voice?.tone?.join(", ") || "Standard"}</span>
            </div>
            <div className="flex-between">
              <span className="text-muted">Pacing:</span>
              <span>{channel.dna?.brand_voice?.pace || "Normal"}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Historical DNA Revisions Timeline */}
      <div className="card">
        <div className="card-header">
          <h3 className="card-title">Historical DNA Revisions ({revisions.length})</h3>
          <span className="text-mono text-muted" style={{ fontSize: "0.75rem" }}>
            Immutable Audit Trail
          </span>
        </div>

        {revisions.length === 0 ? (
          <p style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>No revisions recorded yet.</p>
        ) : (
          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: "12%" }}>Version</th>
                  <th style={{ width: "45%" }}>Change Reason</th>
                  <th style={{ width: "18%" }}>Actor</th>
                  <th style={{ width: "25%", textAlign: "right" }}>Timestamp</th>
                </tr>
              </thead>
              <tbody>
                {revisions.map((rev, idx) => (
                  <tr key={rev.id}>
                    <td>
                      <span className={`badge ${idx === 0 ? "badge-succeeded" : "badge-draft"}`}>
                        v{rev.version} {idx === 0 && "• ACTIVE"}
                      </span>
                    </td>
                    <td>
                      <span style={{ color: "var(--text-primary)" }}>{rev.change_reason}</span>
                    </td>
                    <td>
                      <span className="text-mono" style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                        {rev.actor}
                      </span>
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <span className="text-mono text-muted" style={{ fontSize: "0.78rem" }}>
                        {new Date(rev.created_at).toLocaleString()}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
