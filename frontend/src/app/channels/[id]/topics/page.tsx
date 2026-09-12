"use client";

import { use, useCallback, useEffect, useMemo, useState } from "react";
import {
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
  type Channel,
  type TopicCandidate,
  type TopicMemory,
} from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";
import { ChannelContextBar } from "@/components/ChannelContextBar";
import {
  Alert,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorState,
  FormField,
  LoadingState,
  PageHeader,
  PageSection,
  StatusBadge,
} from "@/components/ui";
import {
  TechnicalDetails,
  WorkflowStatusSummary,
  WorkflowTabs,
  statusTone,
} from "@/components/workflow/WorkflowPrimitives";

type TopicView = "recommendations" | "candidates" | "memory";

function CandidateCard({
  candidate,
  busy,
  archived,
  onEvaluate,
  onSelect,
  onReject,
  onArchive,
}: {
  candidate: TopicCandidate;
  busy: boolean;
  archived: boolean;
  onEvaluate: () => void;
  onSelect: () => void;
  onReject: () => void;
  onArchive: () => void;
}) {
  return (
    <article className="workflow-card">
      <div className="workflow-card-header">
        <div>
          <h3>{candidate.title}</h3>
          <p className="workflow-copy">
            {candidate.summary || "No candidate summary supplied."}
          </p>
        </div>
        <StatusBadge tone={statusTone(candidate.status)}>
          {candidate.status}
        </StatusBadge>
      </div>
      <div className="workflow-chip-list" aria-label="Candidate classification">
        <span className="workflow-chip">
          {candidate.source_type.replaceAll("_", " ")}
        </span>
        <span className="workflow-chip">
          {candidate.duplicate_status.replaceAll("_", " ")}
        </span>
        {candidate.keywords.slice(0, 4).map((keyword) => (
          <span className="workflow-chip" key={keyword}>
            {keyword}
          </span>
        ))}
      </div>
      {candidate.final_score !== null ? (
        <div className="workflow-score">
          <label>
            <span>Final score</span>
            <strong>{candidate.final_score.toFixed(1)}</strong>
          </label>
          <meter min="0" max="100" value={candidate.final_score}>
            {candidate.final_score}
          </meter>
        </div>
      ) : (
        <Alert tone="warning" title="Not evaluated">
          Score this candidate before selecting it.
        </Alert>
      )}
      {candidate.reasons.length ? (
        <ul className="ui-check-list">
          {candidate.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : null}
      <div className="workflow-action-bar">
        <div className="ui-inline-actions">
          {candidate.status === "DISCOVERED" ? (
            <button
              className="btn btn-secondary btn-sm"
              type="button"
              disabled={busy || archived}
              onClick={onEvaluate}
            >
              Evaluate
            </button>
          ) : null}
          {["EVALUATED", "RECOMMENDED"].includes(candidate.status) ? (
            <button
              className="btn btn-primary btn-sm"
              type="button"
              disabled={busy || archived}
              onClick={onSelect}
            >
              Select topic
            </button>
          ) : null}
          {!["REJECTED", "ARCHIVED"].includes(candidate.status) ? (
            <button
              className="btn btn-secondary btn-sm"
              type="button"
              disabled={busy || archived}
              onClick={onReject}
            >
              Reject
            </button>
          ) : null}
          {candidate.status !== "ARCHIVED" ? (
            <button
              className="btn btn-secondary btn-sm"
              type="button"
              disabled={busy || archived}
              onClick={onArchive}
            >
              Archive
            </button>
          ) : null}
        </div>
      </div>
      <TechnicalDetails
        label="Scoring and metadata"
        data={{
          id: candidate.id,
          score_breakdown: candidate.score_breakdown,
          similarity_score: candidate.similarity_score,
          angles: candidate.angles,
          metadata: candidate.metadata,
        }}
      />
    </article>
  );
}

export default function ChannelTopicsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: channelId } = use(params);
  const { setSelectedChannelId } = useOperatorContext();
  const [channel, setChannel] = useState<Channel | null>(null);
  const [view, setView] = useState<TopicView>("recommendations");
  const [recommendations, setRecommendations] = useState<TopicCandidate[]>([]);
  const [candidates, setCandidates] = useState<TopicCandidate[]>([]);
  const [memories, setMemories] = useState<TopicMemory[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showIngest, setShowIngest] = useState(false);
  const [rejectCandidate, setRejectCandidate] = useState<TopicCandidate | null>(
    null,
  );
  const [archiveCandidate, setArchiveCandidate] =
    useState<TopicCandidate | null>(null);
  const [rejectReason, setRejectReason] = useState("Off-strategy");
  const [newTitle, setNewTitle] = useState("");
  const [newSummary, setNewSummary] = useState("");
  const [newKeywords, setNewKeywords] = useState("");
  const [newAngle, setNewAngle] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    setSelectedChannelId(channelId);
    try {
      const [channelData, recommendationData, candidateData, memoryData] =
        await Promise.all([
          getChannel(channelId),
          getTopicRecommendations(channelId, 50, 20),
          getTopicCandidates(channelId),
          getTopicMemory(channelId, search || undefined),
        ]);
      setChannel(channelData);
      setRecommendations(recommendationData);
      setCandidates(candidateData);
      setMemories(memoryData);
    } catch (reason: unknown) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Failed to load topic intelligence data",
      );
    } finally {
      setLoading(false);
    }
  }, [channelId, search, setSelectedChannelId]);

  useEffect(() => {
    void loadData();
  }, [loadData]);
  const isArchived = channel?.state === "ARCHIVED";
  const selectedCount = candidates.filter(
    (candidate) => candidate.status === "SELECTED",
  ).length;
  const unevaluatedCount = candidates.filter(
    (candidate) => candidate.status === "DISCOVERED",
  ).length;
  const visibleCandidates = useMemo(
    () => (view === "recommendations" ? recommendations : candidates),
    [candidates, recommendations, view],
  );

  async function runAction(action: () => Promise<unknown>, fallback: string) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await loadData();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : fallback);
    } finally {
      setBusy(false);
    }
  }

  async function handleIngest(event: React.FormEvent) {
    event.preventDefault();
    if (!newTitle.trim()) {
      setFormError("Topic title is required.");
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      await createTopicCandidate(channelId, {
        title: newTitle.trim(),
        summary: newSummary.trim() || undefined,
        keywords: newKeywords
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
        angles: newAngle.trim()
          ? [{ angle: newAngle.trim(), hook: "Primary hook" }]
          : [],
      });
      setShowIngest(false);
      setNewTitle("");
      setNewSummary("");
      setNewKeywords("");
      setNewAngle("");
      await loadData();
    } catch (reason: unknown) {
      setFormError(
        reason instanceof Error ? reason.message : "Failed to ingest candidate",
      );
    } finally {
      setBusy(false);
    }
  }

  async function handleReject(event: React.FormEvent) {
    event.preventDefault();
    if (!rejectCandidate || !rejectReason.trim()) return;
    const candidate = rejectCandidate;
    setRejectCandidate(null);
    await runAction(
      () => rejectTopicCandidate(channelId, candidate.id, rejectReason.trim()),
      "Failed to reject candidate",
    );
  }

  return (
    <div className="workflow-page ui-page-stack">
      <ChannelContextBar currentTab="topics" />
      <PageHeader
        eyebrow="Channel workflow · Step 1"
        title="Topic intelligence"
        description="Evaluate ideas against channel fit, novelty, and topic memory before selecting the next production direction."
        actions={
          <>
            <button
              className="btn btn-primary"
              type="button"
              disabled={isArchived}
              onClick={() => setShowIngest(true)}
            >
              Add candidate
            </button>
            <button
              className="btn btn-secondary"
              type="button"
              disabled={busy || isArchived || unevaluatedCount === 0}
              onClick={() =>
                void runAction(
                  () => evaluateTopicBatch(channelId),
                  "Batch evaluation failed",
                )
              }
            >
              Evaluate discovered ({unevaluatedCount})
            </button>
          </>
        }
      />
      {isArchived ? (
        <Alert tone="warning" title="Channel archived">
          Topic actions are disabled until the channel is active.
        </Alert>
      ) : null}
      {error ? (
        <ErrorState
          title="Topic workflow unavailable"
          description={error}
          action={
            <button
              className="btn btn-secondary btn-sm"
              type="button"
              onClick={() => void loadData()}
            >
              Retry
            </button>
          }
        />
      ) : null}
      <WorkflowStatusSummary
        metrics={[
          { label: "Candidates", value: candidates.length },
          { label: "Recommended", value: recommendations.length },
          { label: "Selected", value: selectedCount },
          { label: "Memory records", value: memories.length },
        ]}
      />
      <WorkflowTabs
        label="Topic views"
        active={view}
        onChange={setView}
        tabs={[
          {
            id: "recommendations",
            label: "Recommendations",
            count: recommendations.length,
          },
          {
            id: "candidates",
            label: "All candidates",
            count: candidates.length,
          },
          { id: "memory", label: "Topic memory", count: memories.length },
        ]}
      />
      {loading ? (
        <LoadingState
          title="Loading topic intelligence"
          description="Retrieving candidates, recommendations, and channel memory."
        />
      ) : view === "memory" ? (
        <PageSection
          title="Topic memory"
          description="Historical discovery and production frequency informs duplicate and fatigue decisions."
          actions={
            <div className="ui-filter-field">
              <label htmlFor="topic-memory-search">Search memory</label>
              <input
                id="topic-memory-search"
                className="input"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Topic or keyword"
              />
            </div>
          }
        >
          {memories.length === 0 ? (
            <EmptyState
              title="No topic memory found"
              description={
                search
                  ? "No history matches this search."
                  : "Memory appears after topic activity is recorded."
              }
            />
          ) : (
            <div className="workflow-card-grid">
              {memories.map((memory) => (
                <article className="workflow-card" key={memory.id}>
                  <div className="workflow-card-header">
                    <h3>{memory.canonical_topic}</h3>
                    <StatusBadge>{memory.times_produced} produced</StatusBadge>
                  </div>
                  <div className="workflow-data-list">
                    <div className="workflow-data-row">
                      <span>Discovered</span>
                      <strong>{memory.times_discovered}</strong>
                    </div>
                    <div className="workflow-data-row">
                      <span>Selected</span>
                      <strong>{memory.times_selected}</strong>
                    </div>
                    <div className="workflow-data-row">
                      <span>Rejected</span>
                      <strong>{memory.times_rejected}</strong>
                    </div>
                    <div className="workflow-data-row">
                      <span>Last seen</span>
                      <strong>
                        {new Date(memory.last_seen_at).toLocaleDateString()}
                      </strong>
                    </div>
                  </div>
                  <TechnicalDetails data={memory} />
                </article>
              ))}
            </div>
          )}
        </PageSection>
      ) : visibleCandidates.length === 0 ? (
        <EmptyState
          title={
            view === "recommendations"
              ? "No recommendations meet the threshold"
              : "No topic candidates yet"
          }
          description={
            view === "recommendations"
              ? "Evaluate discovered topics or inspect all candidates."
              : "Add a candidate to begin topic evaluation."
          }
        />
      ) : (
        <PageSection
          title={
            view === "recommendations"
              ? "Ranked recommendations"
              : "Candidate inventory"
          }
          description="Actions remain explicit; scores and rationale are primary, while raw metadata stays secondary."
        >
          <div className="workflow-card-grid">
            {visibleCandidates.map((candidate) => (
              <CandidateCard
                key={candidate.id}
                candidate={candidate}
                busy={busy}
                archived={Boolean(isArchived)}
                onEvaluate={() =>
                  void runAction(
                    () => evaluateTopicCandidate(channelId, candidate.id),
                    "Failed to evaluate candidate",
                  )
                }
                onSelect={() =>
                  void runAction(
                    () => selectTopicCandidate(channelId, candidate.id),
                    "Failed to select candidate",
                  )
                }
                onReject={() => {
                  setRejectReason("Off-strategy");
                  setRejectCandidate(candidate);
                }}
                onArchive={() => setArchiveCandidate(candidate)}
              />
            ))}
          </div>
        </PageSection>
      )}
      <Dialog
        open={showIngest}
        title="Add topic candidate"
        description="Add an operator-sourced topic for deterministic scoring."
        onClose={() => setShowIngest(false)}
        actions={
          <>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setShowIngest(false)}
            >
              Cancel
            </button>
            <button
              type="submit"
              form="topic-ingest-form"
              className="btn btn-primary"
              disabled={busy}
            >
              {busy ? "Adding…" : "Add candidate"}
            </button>
          </>
        }
      >
        <form
          id="topic-ingest-form"
          className="workflow-dialog-form"
          onSubmit={handleIngest}
        >
          {formError ? <Alert tone="danger">{formError}</Alert> : null}
          <FormField id="topic-title" label="Title" required>
            <input
              className="input"
              value={newTitle}
              onChange={(event) => setNewTitle(event.target.value)}
            />
          </FormField>
          <FormField id="topic-summary" label="Summary">
            <textarea
              className="input"
              value={newSummary}
              onChange={(event) => setNewSummary(event.target.value)}
            />
          </FormField>
          <FormField
            id="topic-keywords"
            label="Keywords"
            description="Comma-separated"
          >
            <input
              className="input"
              value={newKeywords}
              onChange={(event) => setNewKeywords(event.target.value)}
            />
          </FormField>
          <FormField id="topic-angle" label="Initial angle">
            <input
              className="input"
              value={newAngle}
              onChange={(event) => setNewAngle(event.target.value)}
            />
          </FormField>
        </form>
      </Dialog>
      <Dialog
        open={Boolean(rejectCandidate)}
        title="Reject topic candidate"
        description={
          rejectCandidate
            ? `Record why “${rejectCandidate.title}” should not proceed.`
            : undefined
        }
        onClose={() => setRejectCandidate(null)}
        actions={
          <>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setRejectCandidate(null)}
            >
              Cancel
            </button>
            <button
              type="submit"
              form="topic-reject-form"
              className="btn btn-danger"
              disabled={busy || !rejectReason.trim()}
            >
              Reject candidate
            </button>
          </>
        }
      >
        <form id="topic-reject-form" onSubmit={handleReject}>
          <FormField id="topic-reject-reason" label="Reason" required>
            <textarea
              className="input"
              value={rejectReason}
              onChange={(event) => setRejectReason(event.target.value)}
            />
          </FormField>
        </form>
      </Dialog>
      <ConfirmDialog
        open={Boolean(archiveCandidate)}
        title="Archive topic candidate"
        description={
          archiveCandidate
            ? `Archive “${archiveCandidate.title}”? It will leave the active workflow.`
            : "Archive this candidate?"
        }
        confirmLabel="Archive"
        busy={busy}
        onCancel={() => setArchiveCandidate(null)}
        onConfirm={() => {
          if (!archiveCandidate) return;
          const candidate = archiveCandidate;
          setArchiveCandidate(null);
          void runAction(
            () => archiveTopicCandidate(channelId, candidate.id),
            "Failed to archive candidate",
          );
        }}
      />
    </div>
  );
}
