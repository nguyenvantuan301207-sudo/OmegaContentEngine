"use client";

import React, { useEffect, useState } from "react";
import {
  LearningKnowledgeItem,
  LearningHypothesisSummary,
  getChannelKnowledge,
  getChannelHypotheses,
  triggerLearningRefresh,
} from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";

interface Props {
  channelId: string;
  isArchived?: boolean;
}

export function LearningInsightsCard({ channelId, isArchived: propIsArchived }: Props) {
  const { selectedChannel } = useOperatorContext();
  const isArchived = propIsArchived ?? (selectedChannel?.state === "ARCHIVED");

  const [knowledge, setKnowledge] = useState<LearningKnowledgeItem[]>([]);
  const [hypotheses, setHypotheses] = useState<LearningHypothesisSummary[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [refreshing, setRefreshing] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshSuccess, setRefreshSuccess] = useState<boolean>(false);

  useEffect(() => {
    async function loadData() {
      try {
        setLoading(true);
        setError(null);
        const [kList, hList] = await Promise.all([
          getChannelKnowledge(channelId),
          getChannelHypotheses(channelId),
        ]);
        setKnowledge(kList);
        setHypotheses(hList);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Failed to load learning data");
      } finally {
        setLoading(false);
      }
    }
    if (channelId) {
      loadData();
    }
  }, [channelId]);

  const handleRefresh = async () => {
    if (isArchived) return;
    try {
      setRefreshing(true);
      setRefreshSuccess(false);
      await triggerLearningRefresh(channelId);
      setRefreshSuccess(true);
      setTimeout(() => setRefreshSuccess(false), 4000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Refresh request failed");
    } finally {
      setRefreshing(false);
    }
  };

  const getConfidenceBadgeClass = (conf: string) => {
    switch (conf) {
      case "VERY_HIGH":
      case "HIGH":
        return "badge-success";
      case "MODERATE":
        return "badge-active";
      case "LOW":
        return "badge-warning";
      default:
        return "badge-draft";
    }
  };

  const getStatusBadgeClass = (status: string) => {
    switch (status) {
      case "SUPPORTED":
      case "ACTIVE":
        return "badge-success";
      case "WEAKENED":
        return "badge-warning";
      case "CONTRADICTED":
        return "badge-failed";
      case "INCONCLUSIVE":
        return "badge-draft";
      default:
        return "badge-neutral";
    }
  };

  if (loading) {
    return (
      <div className="card" style={{ padding: "2rem", textAlign: "center", color: "var(--text-muted)", fontSize: "0.85rem" }}>
        Loading Institutional Memory & Hypotheses...
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      {/* Header card with prominent banner */}
      <div className="card" style={{ padding: "1.25rem" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: "1rem",
            borderBottom: "1px solid var(--border-subtle)",
            paddingBottom: "0.85rem",
            marginBottom: "1rem",
          }}
        >
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
              <h2 style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--text-primary)" }}>
                Institutional Channel Memory
              </h2>
              <span className="badge badge-warning" style={{ fontSize: "0.7rem", padding: "0.2rem 0.5rem", letterSpacing: "0.04em" }}>
                OBSERVATIONAL ASSOCIATION — NOT PROVEN CAUSATION
              </span>
            </div>
            <p style={{ fontSize: "0.8rem", color: "var(--text-muted)", marginTop: "0.25rem" }}>
              Factual historical relationships derived from settled multi-window performance evidence.
            </p>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            {refreshSuccess && (
              <span style={{ fontSize: "0.75rem", color: "var(--status-success)", fontWeight: 600 }}>
                ✓ Sweep Triggered
              </span>
            )}
            <button
              onClick={handleRefresh}
              disabled={refreshing || isArchived}
              title={isArchived ? "Activate this channel before requesting learning sweeps." : "Request Learning Sweep"}
              className="btn btn-primary btn-sm"
              style={{ fontSize: "0.8rem", display: "flex", alignItems: "center", gap: "0.4rem" }}
            >
              <span>↻</span> {refreshing ? "Triggering..." : "Request Learning Sweep"}
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
              fontSize: "0.8rem",
              color: "var(--status-danger)",
              marginBottom: "1rem",
            }}
          >
            {error}
          </div>
        )}

        {/* Section 1: Knowledge Claims */}
        <div style={{ marginBottom: "1.5rem" }}>
          <h3 style={{ fontSize: "0.95rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "0.75rem" }}>
            Established Channel Knowledge ({knowledge.length})
          </h3>
          {knowledge.length === 0 ? (
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
              No solidified knowledge items extracted yet. Run more content iterations to establish historical associations.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
              {knowledge.map((item) => (
                <div
                  key={item.knowledge_item_id}
                  style={{
                    padding: "0.85rem 1rem",
                    background: "var(--bg-input)",
                    borderRadius: "var(--radius-sm)",
                    border: "1px solid var(--border-subtle)",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "flex-start",
                    gap: "1rem",
                  }}
                >
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.35rem" }}>
                      <span className="badge badge-active" style={{ fontSize: "0.7rem" }}>
                        {item.knowledge_type}
                      </span>
                      <span className={`badge ${getConfidenceBadgeClass(item.confidence_class)}`} style={{ fontSize: "0.7rem" }}>
                        {item.confidence_class}
                      </span>
                      <span className="badge badge-neutral text-mono" style={{ fontSize: "0.7rem" }}>
                        {item.evidence_type}
                      </span>
                    </div>
                    <p style={{ fontSize: "0.85rem", color: "var(--text-primary)", fontWeight: 500 }}>
                      {item.human_readable_summary}
                    </p>
                  </div>
                  <div className="text-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)", whiteSpace: "nowrap" }}>
                    ID: {item.knowledge_item_id.substring(0, 8)}...
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Section 2: Active Hypotheses */}
        <div>
          <h3 style={{ fontSize: "0.95rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "0.75rem" }}>
            Investigational Hypotheses ({hypotheses.length})
          </h3>
          {hypotheses.length === 0 ? (
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
              No active hypothesis families found for this channel.
            </div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: "0.75rem" }}>
              {hypotheses.map((h) => (
                <div
                  key={h.hypothesis_family_id}
                  style={{
                    padding: "0.85rem 1rem",
                    background: "var(--bg-input)",
                    borderRadius: "var(--radius-sm)",
                    border: "1px solid var(--border-subtle)",
                    display: "flex",
                    flexDirection: "column",
                    justifyContent: "space-between",
                    gap: "0.6rem",
                  }}
                >
                  <div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.35rem" }}>
                      <span className={`badge ${getStatusBadgeClass(h.current_status)}`}>
                        {h.current_status}
                      </span>
                      <span className="text-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
                        v{h.current_version} • {h.target_evaluation_window || "7d"}
                      </span>
                    </div>
                    <p style={{ fontSize: "0.85rem", color: "var(--text-primary)", fontWeight: 600, lineHeight: 1.4 }}>
                      {h.description}
                    </p>
                  </div>

                  <div
                    style={{
                      paddingTop: "0.5rem",
                      borderTop: "1px solid var(--border-subtle)",
                      display: "flex",
                      justifyContent: "space-between",
                      fontSize: "0.75rem",
                      color: "var(--text-muted)",
                    }}
                  >
                    <span>
                      Factor: <strong style={{ color: "var(--accent-secondary)" }}>{h.factor_name}</strong>
                    </span>
                    <span>
                      Target: <strong style={{ color: "var(--status-success)" }}>{h.target_outcome_metric}</strong>
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
