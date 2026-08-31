"use client";

import { use, useCallback, useEffect, useState } from "react";
import {
  Channel,
  TopicCandidate,
  TopicMemory,
  archiveTopicCandidate,
  createTopicCandidate,
  evaluateTopicBatch,
  evaluateTopicCandidate,
  getChannel,
  getTopicCandidates,
  getTopicMemory,
  getTopicRecommendations,
  rejectTopicCandidate,
  selectTopicCandidate,
} from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";
import { ChannelContextBar } from "@/components/ChannelContextBar";

export default function ChannelTopicsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const resolvedParams = use(params);
  const channelId = resolvedParams.id;
  const { setSelectedChannelId } = useOperatorContext();

  const [channel, setChannel] = useState<Channel | null>(null);
  const [activeTab, setActiveTab] = useState<"recommendations" | "candidates" | "memory">("recommendations");
  const [recommendations, setRecommendations] = useState<TopicCandidate[]>([]);
  const [candidates, setCandidates] = useState<TopicCandidate[]>([]);
  const [memories, setMemories] = useState<TopicMemory[]>([]);
  const [searchMemory, setSearchMemory] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  // Ingestion Modal State
  const [showIngestModal, setShowIngestModal] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newSummary, setNewSummary] = useState("");
  const [newKeywords, setNewKeywords] = useState("");
  const [newAngle, setNewAngle] = useState("");
  const [ingestError, setIngestError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setSelectedChannelId(channelId);
      const [chanData, recsData, candsData, memsData] = await Promise.all([
        getChannel(channelId).catch(() => null),
        getTopicRecommendations(channelId, 50.0, 20).catch(() => []),
        getTopicCandidates(channelId).catch(() => []),
        getTopicMemory(channelId, searchMemory || undefined).catch(() => []),
      ]);
      setChannel(chanData);
      setRecommendations(recsData);
      setCandidates(candsData);
      setMemories(memsData);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load topic intelligence data");
    } finally {
      setLoading(false);
    }
  }, [channelId, searchMemory, setSelectedChannelId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  async function handleIngest(e: React.FormEvent) {
    e.preventDefault();
    if (!newTitle.trim()) {
      setIngestError("Topic title is required.");
      return;
    }

    try {
      setActionLoading(true);
      setIngestError(null);

      const parsedKeywords = newKeywords
        .split(",")
        .map((k) => k.trim())
        .filter(Boolean);

      const angles = newAngle.trim()
        ? [{ angle: newAngle.trim(), hook: "Primary hook" }]
        : [];

      await createTopicCandidate(channelId, {
        title: newTitle.trim(),
        summary: newSummary.trim() || undefined,
        keywords: parsedKeywords,
        angles,
      });

      setShowIngestModal(false);
      setNewTitle("");
      setNewSummary("");
      setNewKeywords("");
      setNewAngle("");
      await loadData();
    } catch (err: unknown) {
      setIngestError(err instanceof Error ? err.message : "Failed to ingest candidate");
    } finally {
      setActionLoading(false);
    }
  }

  async function handleEvaluateSingle(candidateId: string) {
    try {
      setActionLoading(true);
      await evaluateTopicCandidate(channelId, candidateId);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to evaluate candidate");
    } finally {
      setActionLoading(false);
    }
  }

  async function handleEvaluateBatch() {
    try {
      setActionLoading(true);
      await evaluateTopicBatch(channelId);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to execute batch evaluation");
    } finally {
      setActionLoading(false);
    }
  }

  async function handleSelect(candidateId: string) {
    try {
      setActionLoading(true);
      await selectTopicCandidate(channelId, candidateId);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to select candidate");
    } finally {
      setActionLoading(false);
    }
  }

  async function handleReject(candidateId: string) {
    const reason = prompt("Please enter a reason for rejecting this topic candidate:", "Off-strategy");
    if (!reason) return;

    try {
      setActionLoading(true);
      await rejectTopicCandidate(channelId, candidateId, reason);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to reject candidate");
    } finally {
      setActionLoading(false);
    }
  }

  async function handleArchive(candidateId: string) {
    try {
      setActionLoading(true);
      await archiveTopicCandidate(channelId, candidateId);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to archive candidate");
    } finally {
      setActionLoading(false);
    }
  }

  const getStatusBadgeClass = (st: string) => {
    switch (st) {
      case "SELECTED":
        return "badge-success";
      case "RECOMMENDED":
        return "badge-active";
      case "EVALUATED":
        return "badge-purple";
      case "REJECTED":
        return "badge-failed";
      case "ARCHIVED":
        return "badge-draft";
      case "DISCOVERED":
      default:
        return "badge-warning";
    }
  };

  const getDuplicateBadgeClass = (dup?: string | null) => {
    switch (dup) {
      case "UNIQUE":
        return "badge-success";
      case "SIMILAR":
        return "badge-warning";
      case "EXACT_DUPLICATE":
        return "badge-failed";
      default:
        return "badge-neutral";
    }
  };

  const isArchived = channel?.state === "ARCHIVED";

  return (
    <div style={{ maxWidth: "1200px", margin: "0 auto", padding: "1.5rem" }}>
      {/* Channel Context Bar with Pipeline Tabs */}
      <ChannelContextBar currentTab="topics" />

      {/* Header */}
      <div className="page-header" style={{ marginBottom: "1.5rem" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.25rem" }}>
            <h1 className="page-title">💡 Topic Intelligence Engine</h1>
            <span className="badge badge-active">OMEGA-003</span>
          </div>
          <p className="page-subtitle">
            Candidate scoring, anti-fatigue memory, content gap analysis, and deterministic topic ranking.
          </p>
        </div>

        <div style={{ display: "flex", gap: "0.5rem" }}>
          <button
            onClick={handleEvaluateBatch}
            disabled={actionLoading || isArchived}
            title={isArchived ? "Activate this channel before evaluating topics." : "Evaluate All Discovered Topics"}
            className="btn btn-primary btn-sm"
            style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}
          >
            <span>⚡</span> Evaluate All Discovered
          </button>
          <button
            onClick={() => setShowIngestModal(true)}
            disabled={isArchived}
            title={isArchived ? "Activate this channel before ingesting candidate topics." : "Ingest Candidate Topic"}
            className="btn btn-secondary btn-sm"
            style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}
          >
            <span>+</span> Ingest Candidate
          </button>
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
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span>{error}</span>
          <button onClick={() => setError(null)} className="btn btn-secondary btn-sm" style={{ padding: "0.15rem 0.45rem", fontSize: "0.72rem" }}>
            ✕
          </button>
        </div>
      )}

      {loading && !channel && (
        <div className="card" style={{ padding: "3rem", textAlign: "center", color: "var(--text-muted)", fontSize: "0.9rem" }}>
          Loading Topic Intelligence Engine...
        </div>
      )}

      {/* Tabs */}
      <div className="card" style={{ padding: "1.25rem" }}>
        <div className="tab-group" style={{ marginBottom: "1.25rem" }}>
          <button
            onClick={() => setActiveTab("recommendations")}
            className={`tab-item ${activeTab === "recommendations" ? "active" : ""}`}
          >
            🎯 Top Recommendations ({recommendations.length})
          </button>
          <button
            onClick={() => setActiveTab("candidates")}
            className={`tab-item ${activeTab === "candidates" ? "active" : ""}`}
          >
            📥 All Candidates ({candidates.length})
          </button>
          <button
            onClick={() => setActiveTab("memory")}
            className={`tab-item ${activeTab === "memory" ? "active" : ""}`}
          >
            🧠 Topic Memory ({memories.length})
          </button>
        </div>

        {/* TAB 1: RECOMMENDATIONS */}
        {activeTab === "recommendations" && (
          <div>
            {recommendations.length === 0 ? (
              <div
                style={{
                  textAlign: "center",
                  padding: "3rem 1.5rem",
                  color: "var(--text-muted)",
                  background: "var(--bg-input)",
                  borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--border-subtle)",
                }}
              >
                <p style={{ fontSize: "0.95rem", marginBottom: "0.5rem" }}>No evaluated topic recommendations available.</p>
                <p style={{ fontSize: "0.82rem", marginBottom: "1rem" }}>
                  Ingest or discover raw candidate topics, then trigger batch evaluation to score and rank them against channel DNA.
                </p>
                <button
                  onClick={handleEvaluateBatch}
                  disabled={actionLoading || isArchived}
                  title={isArchived ? "Activate this channel before evaluating topics." : "Evaluate Pending Candidates"}
                  className="btn btn-primary btn-sm"
                >
                  ⚡ Evaluate Pending Candidates Now
                </button>
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                {recommendations.map((rec, idx) => (
                  <div
                    key={rec.id}
                    style={{
                      padding: "1.25rem",
                      background: "var(--bg-input)",
                      borderRadius: "var(--radius-sm)",
                      border: "1px solid var(--border-subtle)",
                      display: "flex",
                      flexDirection: "column",
                      gap: "0.75rem",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "1rem" }}>
                      <div>
                        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.35rem" }}>
                          <span className="badge badge-active font-mono" style={{ fontSize: "0.72rem" }}>
                            Rank #{idx + 1}
                          </span>
                          <span className={`badge ${getDuplicateBadgeClass(rec.duplicate_status)}`}>
                            {rec.duplicate_status}
                          </span>
                          <span className={`badge ${getStatusBadgeClass(rec.status)}`}>
                            {rec.status}
                          </span>
                        </div>
                        <h2 style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--text-primary)" }}>{rec.title}</h2>
                        {rec.summary && (
                          <p style={{ fontSize: "0.82rem", color: "var(--text-muted)", marginTop: "0.25rem", lineHeight: 1.4 }}>
                            {rec.summary}
                          </p>
                        )}
                      </div>

                      <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
                        <div style={{ textAlign: "right" }}>
                          <div className="text-mono" style={{ fontSize: "1.6rem", fontWeight: 800, color: "var(--accent-secondary)" }}>
                            {rec.final_score?.toFixed(1)}
                          </div>
                          <span style={{ fontSize: "0.68rem", textTransform: "uppercase", color: "var(--text-muted)", letterSpacing: "0.06em" }}>
                            Final Score
                          </span>
                        </div>

                        {rec.status !== "SELECTED" && (
                          <button
                            onClick={() => handleSelect(rec.id)}
                            disabled={actionLoading || isArchived}
                            title={isArchived ? "Activate this channel before selecting topics." : "Select Topic"}
                            className="btn btn-primary btn-sm"
                          >
                            Select Topic
                          </button>
                        )}

                        {rec.status !== "REJECTED" && (
                          <button
                            onClick={() => handleReject(rec.id)}
                            disabled={actionLoading || isArchived}
                            title={isArchived ? "Activate this channel before rejecting topics." : "Reject Topic"}
                            className="btn btn-secondary btn-sm"
                            style={{ color: "var(--status-danger)" }}
                          >
                            Reject
                          </button>
                        )}
                      </div>
                    </div>

                    {/* Operational Reasons */}
                    {rec.reasons && rec.reasons.length > 0 && (
                      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.4rem", paddingTop: "0.5rem", borderTop: "1px solid var(--border-subtle)" }}>
                        {rec.reasons.map((r, i) => (
                          <span key={i} className="badge badge-neutral text-mono" style={{ fontSize: "0.7rem" }}>
                            ✓ {r}
                          </span>
                        ))}
                      </div>
                    )}

                    {/* Score Breakdown Bar */}
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
                        gap: "0.5rem",
                        paddingTop: "0.5rem",
                        borderTop: "1px solid var(--border-subtle)",
                        fontSize: "0.72rem",
                      }}
                    >
                      <div>
                        <span style={{ color: "var(--text-muted)", display: "block" }}>Audience Fit:</span>
                        <strong style={{ color: "var(--text-primary)" }}>{rec.score_breakdown?.audience_fit}</strong>
                      </div>
                      <div>
                        <span style={{ color: "var(--text-muted)", display: "block" }}>Strategy Fit:</span>
                        <strong style={{ color: "var(--text-primary)" }}>{rec.score_breakdown?.strategic_fit}</strong>
                      </div>
                      <div>
                        <span style={{ color: "var(--text-muted)", display: "block" }}>Trend Score:</span>
                        <strong style={{ color: "var(--text-primary)" }}>{rec.score_breakdown?.trend}</strong>
                      </div>
                      <div>
                        <span style={{ color: "var(--text-muted)", display: "block" }}>Novelty:</span>
                        <strong style={{ color: "var(--text-primary)" }}>{rec.score_breakdown?.novelty}</strong>
                      </div>
                      <div>
                        <span style={{ color: "var(--text-muted)", display: "block" }}>Content Gap:</span>
                        <strong style={{ color: "var(--text-primary)" }}>{rec.score_breakdown?.content_gap}</strong>
                      </div>
                      <div>
                        <span style={{ color: "var(--text-muted)", display: "block" }}>Hist. Perf:</span>
                        <strong style={{ color: "var(--text-primary)" }}>{rec.score_breakdown?.historical_performance}</strong>
                      </div>
                      <div>
                        <span style={{ color: "var(--text-muted)", display: "block" }}>Feasibility:</span>
                        <strong style={{ color: "var(--text-primary)" }}>{rec.score_breakdown?.cost_efficiency}</strong>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* TAB 2: ALL CANDIDATES */}
        {activeTab === "candidates" && (
          <div>
            <div className="table-container">
              <table className="table">
                <thead>
                  <tr>
                    <th>Title & Summary</th>
                    <th>Status</th>
                    <th>Duplicate Check</th>
                    <th>Score</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {candidates.length === 0 ? (
                    <tr>
                      <td colSpan={5} style={{ textAlign: "center", padding: "2rem", color: "var(--text-muted)" }}>
                        No topic candidates found for this channel.
                      </td>
                    </tr>
                  ) : (
                    candidates.map((cand) => (
                      <tr key={cand.id}>
                        <td>
                          <div style={{ fontWeight: 600, fontSize: "0.85rem", color: "var(--text-primary)" }}>{cand.title}</div>
                          {cand.summary && (
                            <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.2rem" }}>
                              {cand.summary.substring(0, 90)}...
                            </div>
                          )}
                          <div className="text-mono" style={{ fontSize: "0.68rem", color: "var(--text-muted)", marginTop: "0.2rem" }}>
                            Source: {cand.source_type}
                          </div>
                        </td>
                        <td>
                          <span className={`badge ${getStatusBadgeClass(cand.status)}`}>
                            {cand.status}
                          </span>
                        </td>
                        <td>
                          <span className={`badge ${getDuplicateBadgeClass(cand.duplicate_status)}`}>
                            {cand.duplicate_status || "UNKNOWN"}
                          </span>
                        </td>
                        <td>
                          <span className="text-mono" style={{ fontSize: "0.85rem", fontWeight: 700, color: cand.final_score ? "var(--accent-secondary)" : "var(--text-muted)" }}>
                            {cand.final_score !== null && cand.final_score !== undefined ? cand.final_score.toFixed(1) : "—"}
                          </span>
                        </td>
                        <td>
                          <div style={{ display: "flex", gap: "0.35rem" }}>
                            {cand.status === "DISCOVERED" && (
                              <button
                                onClick={() => handleEvaluateSingle(cand.id)}
                                disabled={actionLoading || isArchived}
                                title={isArchived ? "Activate this channel before evaluating topics." : "Evaluate Candidate"}
                                className="btn btn-primary btn-sm"
                                style={{ fontSize: "0.72rem", padding: "0.2rem 0.45rem" }}
                              >
                                Evaluate
                              </button>
                            )}
                            {cand.status === "EVALUATED" && (
                              <button
                                onClick={() => handleSelect(cand.id)}
                                disabled={actionLoading || isArchived}
                                title={isArchived ? "Activate this channel before selecting topics." : "Select Topic"}
                                className="btn btn-primary btn-sm"
                                style={{ fontSize: "0.72rem", padding: "0.2rem 0.45rem" }}
                              >
                                Select
                              </button>
                            )}
                            <button
                              onClick={() => handleArchive(cand.id)}
                              disabled={actionLoading || isArchived}
                              title={isArchived ? "Channel is already archived" : "Archive Candidate"}
                              className="btn btn-secondary btn-sm"
                              style={{ fontSize: "0.72rem", padding: "0.2rem 0.45rem" }}
                            >
                              Archive
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* TAB 3: TOPIC MEMORY */}
        {activeTab === "memory" && (
          <div>
            <div style={{ display: "flex", gap: "0.75rem", marginBottom: "1rem" }}>
              <input
                type="text"
                value={searchMemory}
                onChange={(e) => setSearchMemory(e.target.value)}
                placeholder="Search channel topic memory by keyword..."
                className="form-input"
                style={{ flex: 1 }}
              />
            </div>

            {memories.length === 0 ? (
              <div
                style={{
                  textAlign: "center",
                  padding: "2.5rem 1rem",
                  color: "var(--text-muted)",
                  background: "var(--bg-input)",
                  borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--border-subtle)",
                  fontSize: "0.85rem",
                }}
              >
                No historical topic memory records found. Memory automatically populates as topics are produced and published.
              </div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: "0.75rem" }}>
                {memories.map((m) => (
                  <div
                    key={m.id}
                    style={{
                      padding: "0.85rem 1rem",
                      background: "var(--bg-input)",
                      borderRadius: "var(--radius-sm)",
                      border: "1px solid var(--border-subtle)",
                      display: "flex",
                      flexDirection: "column",
                      gap: "0.5rem",
                    }}
                  >
                    <div style={{ fontWeight: 600, fontSize: "0.88rem", color: "var(--text-primary)" }}>
                      {m.canonical_topic}
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", color: "var(--text-muted)" }}>
                      <span>Discovered: <strong style={{ color: "var(--text-primary)" }}>{m.times_discovered}</strong></span>
                      <span>Selected: <strong style={{ color: "var(--accent-secondary)" }}>{m.times_selected}</strong></span>
                      <span>Produced: <strong style={{ color: "var(--status-success)" }}>{m.times_produced}</strong></span>
                    </div>
                    <div className="text-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
                      Last Seen: {m.last_seen_at ? new Date(m.last_seen_at).toLocaleDateString() : "Never"}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Modal: Ingest Candidate */}
      {showIngestModal && (
        <div className="modal-backdrop">
          <div className="modal-card" style={{ maxWidth: "520px" }}>
            <div className="modal-header">
              <h3 style={{ fontSize: "1rem", fontWeight: 700, color: "var(--text-primary)" }}>
                Ingest Topic Candidate
              </h3>
              <button onClick={() => setShowIngestModal(false)} className="btn btn-secondary btn-sm" style={{ padding: "0.2rem 0.5rem" }}>
                ✕
              </button>
            </div>

            <form onSubmit={handleIngest}>
              <div className="modal-body" style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
                {ingestError && (
                  <div style={{ padding: "0.5rem 0.75rem", background: "var(--status-danger-bg)", color: "var(--status-danger)", borderRadius: "var(--radius-sm)", fontSize: "0.78rem" }}>
                    {ingestError}
                  </div>
                )}

                <div className="form-group">
                  <label className="form-label">Topic Title *</label>
                  <input
                    type="text"
                    value={newTitle}
                    onChange={(e) => setNewTitle(e.target.value)}
                    placeholder="e.g. 5 FastAPI Performance Traps & How to Fix Them"
                    className="form-input"
                    required
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">Summary / Core Premise</label>
                  <textarea
                    value={newSummary}
                    onChange={(e) => setNewSummary(e.target.value)}
                    placeholder="Brief explanation of the angle and key audience takeaway..."
                    className="form-textarea"
                    rows={3}
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">Keywords (comma-separated)</label>
                  <input
                    type="text"
                    value={newKeywords}
                    onChange={(e) => setNewKeywords(e.target.value)}
                    placeholder="fastapi, python, concurrency, async, database"
                    className="form-input"
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">Primary Hook / Angle</label>
                  <input
                    type="text"
                    value={newAngle}
                    onChange={(e) => setNewAngle(e.target.value)}
                    placeholder="e.g. Why sync blocking calls destroy FastAPI throughput"
                    className="form-input"
                  />
                </div>
              </div>

              <div className="modal-footer" style={{ display: "flex", justifyContent: "flex-end", gap: "0.5rem" }}>
                <button
                  type="button"
                  onClick={() => setShowIngestModal(false)}
                  className="btn btn-secondary btn-sm"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={actionLoading || !newTitle.trim()}
                  className="btn btn-primary btn-sm"
                >
                  {actionLoading ? "Ingesting..." : "Ingest Candidate"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
