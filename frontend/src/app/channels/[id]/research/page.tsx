"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Channel,
  PrimarySourceStatus,
  ResearchBrief,
  ResearchClaim,
  ResearchConflict,
  ResearchRequest,
  ResearchSource,
  TopicCandidate,
  addResearchSource,
  createResearchRequest,
  getChannel,
  getResearchBrief,
  listCandidates,
  listResearchClaims,
  listResearchConflicts,
  listResearchRequests,
  listResearchSources,
  runResearchPipeline,
} from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";
import { ChannelContextBar } from "@/components/ChannelContextBar";

type Tab = "requests" | "brief" | "sources" | "claims" | "conflicts";

export default function ResearchEnginePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const resolvedParams = use(params);
  const channelId = resolvedParams.id;
  const { setSelectedChannelId } = useOperatorContext();

  const [channel, setChannel] = useState<Channel | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("requests");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Core Data
  const [requests, setRequests] = useState<ResearchRequest[]>([]);
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(null);
  const [currentBrief, setCurrentBrief] = useState<ResearchBrief | null>(null);
  const [sources, setSources] = useState<ResearchSource[]>([]);
  const [claims, setClaims] = useState<ResearchClaim[]>([]);
  const [conflicts, setConflicts] = useState<ResearchConflict[]>([]);
  const [eligibleTopics, setEligibleTopics] = useState<TopicCandidate[]>([]);

  // Action states
  const [actionLoading, setActionLoading] = useState(false);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showSourceModal, setShowSourceModal] = useState(false);

  // Form States
  const [selectedTopicId, setSelectedTopicId] = useState("");
  const [researchQuestion, setResearchQuestion] = useState("");
  const [researchScope, setResearchScope] = useState("");

  const [sourceTitle, setSourceTitle] = useState("");
  const [sourcePublisher, setSourcePublisher] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceExcerpt, setSourceExcerpt] = useState("");
  const [sourcePrimaryStatus, setSourcePrimaryStatus] = useState<PrimarySourceStatus>("UNKNOWN");

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setSelectedChannelId(channelId);
      const [chanData, reqsData, topicsData] = await Promise.all([
        getChannel(channelId).catch(() => null),
        listResearchRequests(channelId).catch(() => []),
        listCandidates(channelId).catch(() => []),
      ]);
      setChannel(chanData);
      setRequests(reqsData);

      const eligible = topicsData.filter(
        (t) => t.status === "EVALUATED" || t.status === "RECOMMENDED" || t.status === "SELECTED"
      );
      setEligibleTopics(eligible);
      if (eligible.length > 0 && !selectedTopicId) {
        setSelectedTopicId(eligible[0].id);
      }

      if (reqsData.length > 0 && !selectedRequestId) {
        setSelectedRequestId(reqsData[0].id);
      }
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load research data");
    } finally {
      setLoading(false);
    }
  }, [channelId, selectedRequestId, selectedTopicId, setSelectedChannelId]);

  const loadRequestDetails = useCallback(
    async (reqId: string) => {
      try {
        const [srcs, clms, cnflcts] = await Promise.all([
          listResearchSources(channelId, reqId).catch(() => []),
          listResearchClaims(channelId, reqId).catch(() => []),
          listResearchConflicts(channelId, reqId).catch(() => []),
        ]);
        setSources(srcs);
        setClaims(clms);
        setConflicts(cnflcts);

        try {
          const brief = await getResearchBrief(channelId, reqId);
          setCurrentBrief(brief);
        } catch {
          setCurrentBrief(null);
        }
      } catch {
        // Ignored for partial loads
      }
    },
    [channelId]
  );

  useEffect(() => {
    loadData();
  }, [loadData]);

  useEffect(() => {
    if (selectedRequestId) {
      loadRequestDetails(selectedRequestId);
    }
  }, [selectedRequestId, loadRequestDetails]);

  async function handleCreateRequest(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedTopicId) return;

    try {
      setActionLoading(true);
      const newReq = await createResearchRequest(channelId, {
        topic_candidate_id: selectedTopicId,
        research_question: researchQuestion.trim() || undefined,
        scope: researchScope.trim() || undefined,
      });
      setShowCreateModal(false);
      setResearchQuestion("");
      setResearchScope("");
      setSelectedRequestId(newReq.id);
      await loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create research request");
    } finally {
      setActionLoading(false);
    }
  }

  async function handleAddSource(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedRequestId || !sourceTitle || !sourcePublisher || !sourceExcerpt) return;

    try {
      setActionLoading(true);
      await addResearchSource(channelId, selectedRequestId, {
        title: sourceTitle.trim(),
        publisher: sourcePublisher.trim(),
        url: sourceUrl.trim() || undefined,
        content_excerpt: sourceExcerpt.trim(),
        primary_source_status: sourcePrimaryStatus,
      });
      setShowSourceModal(false);
      setSourceTitle("");
      setSourcePublisher("");
      setSourceUrl("");
      setSourceExcerpt("");
      setSourcePrimaryStatus("UNKNOWN");
      await loadRequestDetails(selectedRequestId);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to add source");
    } finally {
      setActionLoading(false);
    }
  }

  async function handleRunResearch(reqId: string) {
    try {
      setActionLoading(true);
      const brief = await runResearchPipeline(channelId, reqId);
      setCurrentBrief(brief);
      setActiveTab("brief");
      await loadData();
      await loadRequestDetails(reqId);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to execute research pipeline");
    } finally {
      setActionLoading(false);
    }
  }

  const getStatusBadgeClass = (status: string) => {
    switch (status) {
      case "SUCCEEDED":
        return "badge-success";
      case "RUNNING":
        return "badge-active";
      case "PENDING":
        return "badge-warning";
      case "FAILED":
        return "badge-failed";
      default:
        return "badge-draft";
    }
  };

  const getConfidenceBadgeClass = (band: string) => {
    switch (band) {
      case "VERY_HIGH":
      case "HIGH":
        return "badge-success";
      case "MEDIUM":
        return "badge-active";
      case "LOW":
        return "badge-warning";
      default:
        return "badge-neutral";
    }
  };

  const selectedRequest = requests.find((r) => r.id === selectedRequestId);

  const isArchived = channel?.state === "ARCHIVED";

  return (
    <div style={{ maxWidth: "1200px", margin: "0 auto", padding: "1.5rem" }}>
      {/* Channel Context Bar with Pipeline Tabs */}
      <ChannelContextBar currentTab="research" />

      {/* Page Header */}
      <div className="page-header" style={{ marginBottom: "1.5rem" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.25rem" }}>
            <h1 className="page-title">🔬 Research Engine</h1>
            <span className="badge badge-active">OMEGA-005</span>
          </div>
          <p className="page-subtitle">
            Deterministic multi-source evidence extraction, independence scoring, contradiction analysis, and ResearchBriefs.
          </p>
        </div>

        <div style={{ display: "flex", gap: "0.5rem" }}>
          <button
            onClick={() => setShowCreateModal(true)}
            disabled={eligibleTopics.length === 0 || isArchived}
            title={isArchived ? "Activate this channel before creating new research requests." : eligibleTopics.length === 0 ? "Select an evaluated topic first" : "New Research Request"}
            className="btn btn-primary btn-sm"
            style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}
          >
            <span>+</span> New Research Request
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
          Loading Research Engine...
        </div>
      )}

      {/* Main Research Content Workspace */}
      <div style={{ display: "grid", gridTemplateColumns: "300px 1fr", gap: "1.5rem", alignItems: "start" }}>
        {/* Left Column: Research Requests List */}
        <div className="card" style={{ padding: "1.25rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
            <h3 style={{ fontSize: "0.95rem", fontWeight: 700, color: "var(--text-primary)" }}>
              Requests ({requests.length})
            </h3>
            {eligibleTopics.length === 0 && (
              <Link href={`/channels/${channelId}/topics`} className="btn btn-secondary btn-sm" style={{ fontSize: "0.72rem", padding: "0.2rem 0.45rem" }}>
                Evaluate Topics →
              </Link>
            )}
          </div>

          {requests.length === 0 ? (
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
              No research requests created yet. Select an evaluated topic to create a research request.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
              {requests.map((req) => {
                const isSelected = req.id === selectedRequestId;
                return (
                  <div
                    key={req.id}
                    onClick={() => setSelectedRequestId(req.id)}
                    style={{
                      padding: "0.75rem 0.85rem",
                      borderRadius: "var(--radius-sm)",
                      background: isSelected ? "var(--bg-card-hover)" : "var(--bg-input)",
                      border: isSelected ? "1px solid var(--accent-primary)" : "1px solid var(--border-subtle)",
                      cursor: "pointer",
                      transition: "all 0.15s ease",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.35rem" }}>
                      <span className={`badge ${getStatusBadgeClass(req.status)}`} style={{ fontSize: "0.68rem" }}>
                        {req.status}
                      </span>
                      <span className="text-mono" style={{ fontSize: "0.68rem", color: "var(--text-muted)" }}>
                        {new Date(req.created_at).toLocaleDateString()}
                      </span>
                    </div>
                    <div style={{ fontSize: "0.82rem", fontWeight: 600, color: isSelected ? "var(--accent-secondary)" : "var(--text-primary)", lineHeight: 1.3 }}>
                      {req.research_question || `Request ${req.id.slice(0, 8)}`}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Right Column: Active Request Workspace */}
        {selectedRequest ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            {/* Request Summary & Action Toolbar */}
            <div className="card" style={{ padding: "1.25rem" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "1rem", marginBottom: "0.75rem" }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.35rem" }}>
                    <span className={`badge ${getStatusBadgeClass(selectedRequest.status)}`}>
                      {selectedRequest.status}
                    </span>
                    <span className="text-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
                      ID: {selectedRequest.id}
                    </span>
                  </div>
                  <h2 style={{ fontSize: "1.2rem", fontWeight: 700, color: "var(--text-primary)" }}>
                    {selectedRequest.research_question || `Research Request ${selectedRequest.id.slice(0, 8)}`}
                  </h2>
                </div>

                <div style={{ display: "flex", gap: "0.5rem" }}>
                  <button
                    onClick={() => setShowSourceModal(true)}
                    disabled={isArchived}
                    title={isArchived ? "Activate this channel before adding research sources." : "Add Research Source"}
                    className="btn btn-secondary btn-sm"
                  >
                    + Add Source
                  </button>
                  <button
                    onClick={() => handleRunResearch(selectedRequest.id)}
                    disabled={actionLoading || sources.length === 0 || isArchived}
                    title={isArchived ? "Activate this channel before executing research." : sources.length === 0 ? "Add at least one source first" : "Execute Research Pipeline"}
                    className="btn btn-primary btn-sm"
                  >
                    {actionLoading ? "Running Pipeline..." : "⚡ Execute Research Pipeline"}
                  </button>
                </div>
              </div>

              {selectedRequest.research_question && (
                <div style={{ padding: "0.75rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)", fontSize: "0.82rem", color: "var(--text-secondary)" }}>
                  <strong style={{ color: "var(--text-primary)" }}>Core Question:</strong> {selectedRequest.research_question}
                </div>
              )}
            </div>

            {/* Tab Navigation */}
            <div className="card" style={{ padding: "1.25rem" }}>
              <div className="tab-group" style={{ marginBottom: "1.25rem" }}>
                <button
                  onClick={() => setActiveTab("requests")}
                  className={`tab-item ${activeTab === "requests" ? "active" : ""}`}
                >
                  Overview & Sources ({sources.length})
                </button>
                <button
                  onClick={() => setActiveTab("claims")}
                  className={`tab-item ${activeTab === "claims" ? "active" : ""}`}
                >
                  Fact Claims ({claims.length})
                </button>
                <button
                  onClick={() => setActiveTab("conflicts")}
                  className={`tab-item ${activeTab === "conflicts" ? "active" : ""}`}
                >
                  Conflicts ({conflicts.length})
                </button>
                <button
                  onClick={() => setActiveTab("brief")}
                  className={`tab-item ${activeTab === "brief" ? "active" : ""}`}
                >
                  Research Brief {currentBrief ? "✓" : ""}
                </button>
              </div>

              {/* Tab: Overview & Sources */}
              {activeTab === "requests" && (
                <div>
                  <h4 style={{ fontSize: "0.9rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "0.75rem" }}>
                    Sources & Extraction Base ({sources.length})
                  </h4>
                  {sources.length === 0 ? (
                    <div
                      style={{
                        textAlign: "center",
                        padding: "2.5rem 1rem",
                        color: "var(--text-muted)",
                        fontSize: "0.85rem",
                        background: "var(--bg-input)",
                        borderRadius: "var(--radius-sm)",
                        border: "1px solid var(--border-subtle)",
                      }}
                    >
                      <p style={{ marginBottom: "0.75rem" }}>No sources ingested for this research request yet.</p>
                      <button
                        onClick={() => setShowSourceModal(true)}
                        disabled={isArchived}
                        title={isArchived ? "Activate this channel before adding research sources." : "Add First Source Excerpt"}
                        className="btn btn-primary btn-sm"
                      >
                        + Add First Source Excerpt
                      </button>
                    </div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                      {sources.map((src) => (
                        <div
                          key={src.id}
                          style={{
                            padding: "0.85rem 1rem",
                            background: "var(--bg-input)",
                            borderRadius: "var(--radius-sm)",
                            border: "1px solid var(--border-subtle)",
                          }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.35rem" }}>
                            <div style={{ fontWeight: 600, fontSize: "0.9rem", color: "var(--text-primary)" }}>
                              {src.title}
                            </div>
                            <div style={{ display: "flex", gap: "0.4rem" }}>
                              <span className="badge badge-neutral" style={{ fontSize: "0.68rem" }}>
                                {src.publisher}
                              </span>
                              <span className="badge badge-active" style={{ fontSize: "0.68rem" }}>
                                {src.primary_source_status}
                              </span>
                            </div>
                          </div>
                          {src.url && (
                            <a
                              href={src.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              style={{ fontSize: "0.75rem", color: "var(--accent-secondary)", textDecoration: "underline", display: "inline-block", marginBottom: "0.35rem" }}
                            >
                              {src.url} ↗
                            </a>
                          )}
                          <p style={{ fontSize: "0.82rem", color: "var(--text-muted)", lineHeight: 1.4, fontStyle: "italic" }}>
                            &ldquo;{src.content_excerpt}&rdquo;
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Tab: Fact Claims */}
              {activeTab === "claims" && (
                <div>
                  <h4 style={{ fontSize: "0.9rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "0.75rem" }}>
                    Verified Fact Claims ({claims.length})
                  </h4>
                  {claims.length === 0 ? (
                    <div style={{ textAlign: "center", padding: "2rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
                      No claims extracted yet. Run the research pipeline to extract deterministic claims from sources.
                    </div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                      {claims.map((claim) => (
                        <div
                          key={claim.id}
                          style={{
                            padding: "0.85rem 1rem",
                            background: "var(--bg-input)",
                            borderRadius: "var(--radius-sm)",
                            border: "1px solid var(--border-subtle)",
                          }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.35rem" }}>
                            <span style={{ fontSize: "0.88rem", fontWeight: 600, color: "var(--text-primary)" }}>
                              {claim.claim_text}
                            </span>
                            <span className={`badge ${getConfidenceBadgeClass(claim.confidence_band)}`}>
                              {claim.confidence_band} ({Math.round(claim.confidence_score * 100)}%)
                            </span>
                          </div>
                          <div style={{ display: "flex", gap: "1rem", fontSize: "0.75rem", color: "var(--text-muted)" }}>
                            <span>Claim Type: <strong>{claim.claim_type}</strong></span>
                            <span>Verified: <strong>{claim.is_verified ? "Yes" : "No"}</strong></span>
                            <span>Supporting Sources: <strong>{claim.supporting_sources_count}</strong></span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Tab: Conflicts */}
              {activeTab === "conflicts" && (
                <div>
                  <h4 style={{ fontSize: "0.9rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "0.75rem" }}>
                    Contradiction & Conflict Log ({conflicts.length})
                  </h4>
                  {conflicts.length === 0 ? (
                    <div style={{ textAlign: "center", padding: "2rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
                      ✓ No evidentiary contradictions detected among ingested sources.
                    </div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                      {conflicts.map((cnf) => (
                        <div
                          key={cnf.id}
                          style={{
                            padding: "0.85rem 1rem",
                            background: "var(--status-warning-bg)",
                            border: "1px solid var(--status-warning-border)",
                            borderRadius: "var(--radius-sm)",
                          }}
                        >
                          <div style={{ fontWeight: 600, fontSize: "0.85rem", color: "var(--status-warning)", marginBottom: "0.25rem" }}>
                            Conflict: {cnf.conflict_type} (Severity: {cnf.severity})
                          </div>
                          <p style={{ fontSize: "0.82rem", color: "var(--text-primary)" }}>{cnf.description}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Tab: Research Brief */}
              {activeTab === "brief" && (
                <div>
                  {currentBrief ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <div>
                          <span className="badge badge-success" style={{ marginBottom: "0.35rem" }}>
                            Research Brief Ready (v{currentBrief.version})
                          </span>
                          <h3 style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--text-primary)" }}>
                            {currentBrief.title}
                          </h3>
                        </div>
                        {isArchived ? (
                          <button
                            disabled
                            title="Activate this channel before proceeding to content generation."
                            className="btn btn-primary btn-sm"
                          >
                            Proceed to Content Engine →
                          </button>
                        ) : (
                          <Link
                            href={`/channels/${channelId}/content`}
                            className="btn btn-primary btn-sm"
                          >
                            Proceed to Content Engine →
                          </Link>
                        )}
                      </div>

                      <div style={{ padding: "1rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
                        <h4 style={{ fontSize: "0.85rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "0.5rem" }}>
                          Synthesized Core Summary
                        </h4>
                        <div style={{ fontSize: "0.85rem", color: "var(--text-primary)", lineHeight: 1.6 }}>
                          {currentBrief.summary || "No narrative summary generated."}
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div style={{ textAlign: "center", padding: "2.5rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
                      <p style={{ marginBottom: "0.75rem" }}>Research brief not generated yet for this request.</p>
                      <button
                        onClick={() => handleRunResearch(selectedRequest.id)}
                        disabled={actionLoading || sources.length === 0}
                        className="btn btn-primary btn-sm"
                      >
                        Run Research Pipeline to Generate Brief
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="card" style={{ padding: "3rem 1.5rem", textAlign: "center", color: "var(--text-muted)" }}>
            <p style={{ fontSize: "0.95rem", marginBottom: "0.75rem" }}>No research request selected.</p>
            <p style={{ fontSize: "0.82rem" }}>Select a research request on the left or create a new request above.</p>
          </div>
        )}
      </div>

      {/* Modal: Create Research Request */}
      {showCreateModal && (
        <div className="modal-backdrop">
          <div className="modal-card" style={{ maxWidth: "540px" }}>
            <div className="modal-header">
              <h3 style={{ fontSize: "1rem", fontWeight: 700, color: "var(--text-primary)" }}>
                New Research Request
              </h3>
              <button onClick={() => setShowCreateModal(false)} className="btn btn-secondary btn-sm" style={{ padding: "0.2rem 0.5rem" }}>
                ✕
              </button>
            </div>

            <form onSubmit={handleCreateRequest}>
              <div className="modal-body" style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                <div className="form-group">
                  <label className="form-label">Eligible Topic Candidate *</label>
                  <select
                    value={selectedTopicId}
                    onChange={(e) => setSelectedTopicId(e.target.value)}
                    className="form-select"
                    required
                  >
                    {eligibleTopics.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.title} [{t.status}]
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label className="form-label">Research Question</label>
                  <input
                    type="text"
                    value={researchQuestion}
                    onChange={(e) => setResearchQuestion(e.target.value)}
                    placeholder="e.g. What are the top 3 architectural bottlenecks in async python?"
                    className="form-input"
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">Scope & Guidance</label>
                  <textarea
                    value={researchScope}
                    onChange={(e) => setResearchScope(e.target.value)}
                    placeholder="Focus on real production benchmarks, avoid promotional blogs."
                    className="form-textarea"
                    rows={3}
                  />
                </div>
              </div>

              <div className="modal-footer" style={{ display: "flex", justifyContent: "flex-end", gap: "0.5rem" }}>
                <button
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                  className="btn btn-secondary btn-sm"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={actionLoading || !selectedTopicId}
                  className="btn btn-primary btn-sm"
                >
                  {actionLoading ? "Creating..." : "Create Request"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modal: Add Source */}
      {showSourceModal && (
        <div className="modal-backdrop">
          <div className="modal-card" style={{ maxWidth: "560px" }}>
            <div className="modal-header">
              <h3 style={{ fontSize: "1rem", fontWeight: 700, color: "var(--text-primary)" }}>
                Add Research Source
              </h3>
              <button onClick={() => setShowSourceModal(false)} className="btn btn-secondary btn-sm" style={{ padding: "0.2rem 0.5rem" }}>
                ✕
              </button>
            </div>

            <form onSubmit={handleAddSource}>
              <div className="modal-body" style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
                <div className="form-group">
                  <label className="form-label">Source Title *</label>
                  <input
                    type="text"
                    value={sourceTitle}
                    onChange={(e) => setSourceTitle(e.target.value)}
                    placeholder="e.g. FastAPI Async Database Concurrency Benchmarks 2026"
                    className="form-input"
                    required
                  />
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" }}>
                  <div className="form-group">
                    <label className="form-label">Publisher *</label>
                    <input
                      type="text"
                      value={sourcePublisher}
                      onChange={(e) => setSourcePublisher(e.target.value)}
                      placeholder="e.g. Tiangolo / Official Docs"
                      className="form-input"
                      required
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Primary Source Status</label>
                    <select
                      value={sourcePrimaryStatus}
                      onChange={(e) => setSourcePrimaryStatus(e.target.value as PrimarySourceStatus)}
                      className="form-select"
                    >
                      <option value="PRIMARY">PRIMARY (Authoritative)</option>
                      <option value="SECONDARY">SECONDARY (Synthesis)</option>
                      <option value="UNKNOWN">UNKNOWN</option>
                    </select>
                  </div>
                </div>

                <div className="form-group">
                  <label className="form-label">URL (Optional)</label>
                  <input
                    type="url"
                    value={sourceUrl}
                    onChange={(e) => setSourceUrl(e.target.value)}
                    placeholder="https://..."
                    className="form-input"
                  />
                </div>

                <div className="form-group">
                  <label className="form-label">Content Excerpt *</label>
                  <textarea
                    value={sourceExcerpt}
                    onChange={(e) => setSourceExcerpt(e.target.value)}
                    placeholder="Paste the factual excerpt from the source here..."
                    className="form-textarea"
                    rows={4}
                    required
                  />
                </div>
              </div>

              <div className="modal-footer" style={{ display: "flex", justifyContent: "flex-end", gap: "0.5rem" }}>
                <button
                  type="button"
                  onClick={() => setShowSourceModal(false)}
                  className="btn btn-secondary btn-sm"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={actionLoading}
                  className="btn btn-primary btn-sm"
                >
                  {actionLoading ? "Adding..." : "Add Source"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
