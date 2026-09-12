"use client";

import { useEffect, useState } from "react";
import {
  getChannelHypotheses,
  getChannelKnowledge,
  triggerLearningRefresh,
  type LearningHypothesisSummary,
  type LearningKnowledgeItem,
} from "@/lib/api";
import {
  Alert,
  EmptyState,
  ErrorState,
  LoadingState,
  PageSection,
  StatusBadge,
} from "@/components/ui";
import {
  TechnicalDetails,
  WorkflowStatusSummary,
  statusTone,
} from "@/components/workflow/WorkflowPrimitives";
import { useOperatorContext } from "@/lib/operator-context";

interface Props {
  channelId: string;
  isArchived?: boolean;
}

export function LearningInsightsCard({
  channelId,
  isArchived: archivedProp,
}: Props) {
  const { selectedChannel } = useOperatorContext();
  const archived = archivedProp ?? selectedChannel?.state === "ARCHIVED";
  const [knowledge, setKnowledge] = useState<LearningKnowledgeItem[]>([]);
  const [hypotheses, setHypotheses] = useState<LearningHypothesisSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshQueued, setRefreshQueued] = useState(false);

  useEffect(() => {
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const [items, hypothesisItems] = await Promise.all([
          getChannelKnowledge(channelId),
          getChannelHypotheses(channelId),
        ]);
        setKnowledge(items);
        setHypotheses(hypothesisItems);
      } catch (reason: unknown) {
        setError(
          reason instanceof Error
            ? reason.message
            : "Learning data could not be loaded.",
        );
      } finally {
        setLoading(false);
      }
    }
    if (channelId) void load();
  }, [channelId]);

  async function refresh() {
    if (archived) return;
    setRefreshing(true);
    setRefreshQueued(false);
    setError(null);
    try {
      await triggerLearningRefresh(channelId);
      setRefreshQueued(true);
    } catch (reason: unknown) {
      setError(
        reason instanceof Error ? reason.message : "Learning refresh failed.",
      );
    } finally {
      setRefreshing(false);
    }
  }

  if (loading)
    return (
      <LoadingState
        title="Loading institutional memory"
        description="Retrieving settled knowledge and active hypotheses."
      />
    );
  return (
    <div className="workflow-detail">
      <PageSection
        title="Institutional channel memory"
        description="Observed historical relationships derived from settled multi-window performance evidence."
        actions={
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={refreshing || archived}
            onClick={() => void refresh()}
          >
            {refreshing ? "Requesting…" : "Request learning sweep"}
          </button>
        }
      >
        <Alert tone="warning" title="Observational association">
          Learning evidence does not establish causation.
        </Alert>
        {refreshQueued ? (
          <Alert tone="success">Learning sweep queued successfully.</Alert>
        ) : null}
        {error ? (
          <ErrorState
            title="Learning workflow unavailable"
            description={error}
          />
        ) : null}
        <WorkflowStatusSummary
          metrics={[
            { label: "Knowledge items", value: knowledge.length },
            { label: "Hypotheses", value: hypotheses.length },
            {
              label: "Channel state",
              value: archived ? "ARCHIVED" : "ACTIVE",
              status: archived ? "BLOCKED" : "READY",
            },
          ]}
        />
      </PageSection>
      <PageSection
        title="Established knowledge"
        description="Human-readable claims remain primary; structured evidence is available on demand."
      >
        {knowledge.length === 0 ? (
          <EmptyState
            title="No established knowledge"
            description="More settled content iterations are required before associations can be recorded."
          />
        ) : (
          <div className="workflow-card-grid">
            {knowledge.map((item) => (
              <article className="workflow-card" key={item.knowledge_item_id}>
                <div className="workflow-card-header">
                  <div className="workflow-chip-list">
                    <StatusBadge tone="info">{item.knowledge_type}</StatusBadge>
                    <StatusBadge tone={statusTone(item.confidence_class)}>
                      {item.confidence_class}
                    </StatusBadge>
                  </div>
                  <span className="workflow-id">
                    {item.knowledge_item_id.slice(0, 8)}
                  </span>
                </div>
                <p className="workflow-copy">{item.human_readable_summary}</p>
                <div className="workflow-data-list">
                  <div className="workflow-data-row">
                    <span>Evidence</span>
                    <strong>{item.evidence_type}</strong>
                  </div>
                  <div className="workflow-data-row">
                    <span>Sample size</span>
                    <strong>
                      {item.sample_size_treatment + item.sample_size_control}
                    </strong>
                  </div>
                  <div className="workflow-data-row">
                    <span>Status</span>
                    <strong>{item.current_status}</strong>
                  </div>
                </div>
                <TechnicalDetails data={item} />
              </article>
            ))}
          </div>
        )}
      </PageSection>
      <PageSection
        title="Investigational hypotheses"
        description="Active experiment families and their target outcomes."
      >
        {hypotheses.length === 0 ? (
          <EmptyState title="No active hypotheses" />
        ) : (
          <div className="workflow-card-grid">
            {hypotheses.map((hypothesis) => (
              <article
                className="workflow-card"
                key={hypothesis.hypothesis_family_id}
              >
                <div className="workflow-card-header">
                  <StatusBadge tone={statusTone(hypothesis.current_status)}>
                    {hypothesis.current_status}
                  </StatusBadge>
                  <span className="workflow-entity-meta">
                    v{hypothesis.current_version} ·{" "}
                    {hypothesis.target_evaluation_window}
                  </span>
                </div>
                <h3>{hypothesis.description}</h3>
                <div className="workflow-data-list">
                  <div className="workflow-data-row">
                    <span>Factor</span>
                    <strong>{hypothesis.factor_name}</strong>
                  </div>
                  <div className="workflow-data-row">
                    <span>Target outcome</span>
                    <strong>{hypothesis.target_outcome_metric}</strong>
                  </div>
                </div>
                <TechnicalDetails data={hypothesis} />
              </article>
            ))}
          </div>
        )}
      </PageSection>
    </div>
  );
}
