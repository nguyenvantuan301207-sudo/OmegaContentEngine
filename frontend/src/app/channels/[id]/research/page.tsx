"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import {
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
  type Channel,
  type PrimarySourceStatus,
  type ResearchBrief,
  type ResearchClaim,
  type ResearchConflict,
  type ResearchRequest,
  type ResearchSource,
  type TopicCandidate,
} from "@/lib/api";
import { ChannelContextBar } from "@/components/ChannelContextBar";
import {
  Alert,
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
  EntityList,
  EntityListButton,
  TechnicalDetails,
  WorkflowStatusSummary,
  WorkflowTabs,
  statusTone,
} from "@/components/workflow/WorkflowPrimitives";
import { useOperatorContext } from "@/lib/operator-context";

type ResearchView = "overview" | "brief" | "claims" | "conflicts";

export default function ResearchEnginePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: channelId } = use(params);
  const { setSelectedChannelId } = useOperatorContext();
  const [channel, setChannel] = useState<Channel | null>(null);
  const [requests, setRequests] = useState<ResearchRequest[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [topics, setTopics] = useState<TopicCandidate[]>([]);
  const [sources, setSources] = useState<ResearchSource[]>([]);
  const [claims, setClaims] = useState<ResearchClaim[]>([]);
  const [conflicts, setConflicts] = useState<ResearchConflict[]>([]);
  const [brief, setBrief] = useState<ResearchBrief | null>(null);
  const [view, setView] = useState<ResearchView>("overview");
  const [loading, setLoading] = useState(true);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [topicId, setTopicId] = useState("");
  const [question, setQuestion] = useState("");
  const [scope, setScope] = useState("");
  const [sourceTitle, setSourceTitle] = useState("");
  const [publisher, setPublisher] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [excerpt, setExcerpt] = useState("");
  const [primaryStatus, setPrimaryStatus] =
    useState<PrimarySourceStatus>("UNKNOWN");

  const loadDetails = useCallback(
    async (request: ResearchRequest) => {
      setDetailsLoading(true);
      setDetailError(null);
      const results = await Promise.allSettled([
        listResearchSources(channelId, request.id),
        listResearchClaims(channelId, request.id),
        listResearchConflicts(channelId, request.id),
        getResearchBrief(channelId, request.id),
      ]);
      const [sourceResult, claimResult, conflictResult, briefResult] = results;
      setSources(sourceResult.status === "fulfilled" ? sourceResult.value : []);
      setClaims(claimResult.status === "fulfilled" ? claimResult.value : []);
      setConflicts(
        conflictResult.status === "fulfilled" ? conflictResult.value : [],
      );
      setBrief(briefResult.status === "fulfilled" ? briefResult.value : null);
      const failures = [sourceResult, claimResult, conflictResult].filter(
        (result) => result.status === "rejected",
      );
      if (briefResult.status === "rejected" && request.status === "SUCCEEDED")
        failures.push(briefResult);
      if (failures.length)
        setDetailError(
          `${failures.length} research detail request${failures.length === 1 ? "" : "s"} failed. Refresh to retry.`,
        );
      setDetailsLoading(false);
    },
    [channelId],
  );

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    setSelectedChannelId(channelId);
    try {
      const [channelData, requestData, candidateData] = await Promise.all([
        getChannel(channelId),
        listResearchRequests(channelId),
        listCandidates(channelId),
      ]);
      const eligible = candidateData.filter((candidate) =>
        ["EVALUATED", "RECOMMENDED", "SELECTED"].includes(candidate.status),
      );
      setChannel(channelData);
      setRequests(requestData);
      setTopics(eligible);
      const selected =
        requestData.find((request) => request.id === selectedId) ||
        requestData[0];
      setSelectedId(selected?.id || null);
      if (selected) await loadDetails(selected);
      else {
        setSources([]);
        setClaims([]);
        setConflicts([]);
        setBrief(null);
      }
      if (!topicId && eligible[0]) setTopicId(eligible[0].id);
    } catch (reason: unknown) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Failed to load research workflow",
      );
    } finally {
      setLoading(false);
    }
  }, [channelId, loadDetails, selectedId, setSelectedChannelId, topicId]);

  useEffect(() => {
    void loadData();
  }, [loadData]);
  const selected =
    requests.find((request) => request.id === selectedId) || null;
  const archived = channel?.state === "ARCHIVED";

  async function chooseRequest(request: ResearchRequest) {
    setSelectedId(request.id);
    await loadDetails(request);
  }
  async function perform(
    action: () => Promise<unknown>,
    fallback: string,
    refresh = true,
  ) {
    setBusy(true);
    setError(null);
    try {
      await action();
      if (refresh) await loadData();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : fallback);
    } finally {
      setBusy(false);
    }
  }
  async function createRequest(event: React.FormEvent) {
    event.preventDefault();
    if (!topicId) return;
    await perform(async () => {
      const created = await createResearchRequest(channelId, {
        topic_candidate_id: topicId,
        research_question: question.trim() || undefined,
        scope: scope.trim() || undefined,
      });
      setCreateOpen(false);
      setQuestion("");
      setScope("");
      setSelectedId(created.id);
    }, "Failed to create research request");
  }
  async function addSource(event: React.FormEvent) {
    event.preventDefault();
    if (
      !selected ||
      !sourceTitle.trim() ||
      !publisher.trim() ||
      !excerpt.trim()
    )
      return;
    await perform(
      async () => {
        await addResearchSource(channelId, selected.id, {
          title: sourceTitle.trim(),
          publisher: publisher.trim(),
          url: sourceUrl.trim() || undefined,
          content_excerpt: excerpt.trim(),
          primary_source_status: primaryStatus,
        });
        setSourceOpen(false);
        setSourceTitle("");
        setPublisher("");
        setSourceUrl("");
        setExcerpt("");
        setPrimaryStatus("UNKNOWN");
        await loadDetails(selected);
      },
      "Failed to add research source",
      false,
    );
  }
  async function runResearch() {
    if (!selected) return;
    await perform(async () => {
      const result = await runResearchPipeline(channelId, selected.id);
      setBrief(result);
      setView("brief");
    }, "Research pipeline failed");
  }

  const status = selected?.status || "EMPTY";
  const outcome = brief?.outcome || selected?.outcome || null;
  return (
    <div className="workflow-page ui-page-stack">
      <ChannelContextBar currentTab="research" />
      <PageHeader
        eyebrow="Channel workflow · Step 2"
        title="Research evidence"
        description="Build a traceable evidence base, surface conflicts, and produce a decision-ready research brief."
        actions={
          <>
            <button
              className="btn btn-primary"
              type="button"
              disabled={archived || topics.length === 0}
              onClick={() => setCreateOpen(true)}
            >
              New request
            </button>
            <button
              className="btn btn-secondary"
              type="button"
              onClick={() => void loadData()}
            >
              Refresh
            </button>
          </>
        }
      />
      {archived ? (
        <Alert tone="warning" title="Channel archived">
          Research mutations are disabled until the channel is active.
        </Alert>
      ) : null}
      {error ? (
        <ErrorState
          title="Research workflow unavailable"
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
          { label: "Request", value: status, status },
          {
            label: "Outcome",
            value: outcome || "Not available",
            status: outcome,
          },
          { label: "Sources", value: sources.length },
          {
            label: "Verified claims",
            value: claims.filter((claim) => claim.is_verified).length,
          },
          {
            label: "Open conflicts",
            value: conflicts.filter((conflict) => conflict.status === "OPEN")
              .length,
          },
        ]}
      />
      {loading ? (
        <LoadingState
          title="Loading research workflow"
          description="Retrieving requests and eligible topics."
        />
      ) : requests.length === 0 ? (
        <EmptyState
          title="No research requests"
          description={
            topics.length
              ? "Create a request from an evaluated topic."
              : "Evaluate and select a topic before beginning research."
          }
          action={
            topics.length ? (
              <button
                className="btn btn-primary"
                type="button"
                onClick={() => setCreateOpen(true)}
              >
                Create research request
              </button>
            ) : (
              <Link
                className="btn btn-primary"
                href={`/channels/${channelId}/topics`}
              >
                Open topics
              </Link>
            )
          }
        />
      ) : (
        <div className="workflow-layout">
          <EntityList
            title="Research requests"
            description={`${requests.length} request${requests.length === 1 ? "" : "s"}`}
          >
            {requests.map((request) => (
              <EntityListButton
                key={request.id}
                selected={request.id === selectedId}
                title={
                  request.research_question ||
                  `Request ${request.id.slice(0, 8)}`
                }
                status={request.status}
                meta={new Date(request.created_at).toLocaleDateString()}
                onClick={() => void chooseRequest(request)}
              />
            ))}
          </EntityList>
          <div className="workflow-detail">
            {selected ? (
              <>
                <PageSection
                  title={selected.research_question || "Research request"}
                  description={
                    selected.scope || "No explicit research scope was supplied."
                  }
                  actions={
                    <div className="ui-inline-actions">
                      <button
                        className="btn btn-secondary btn-sm"
                        type="button"
                        disabled={archived}
                        onClick={() => setSourceOpen(true)}
                      >
                        Add source
                      </button>
                      <button
                        className="btn btn-primary btn-sm"
                        type="button"
                        disabled={busy || archived || sources.length === 0}
                        onClick={() => void runResearch()}
                      >
                        {busy ? "Working…" : "Run research"}
                      </button>
                    </div>
                  }
                >
                  <TechnicalDetails data={selected} />
                </PageSection>
                {detailError ? (
                  <ErrorState
                    title="Some research details failed"
                    description={detailError}
                    action={
                      <button
                        className="btn btn-secondary btn-sm"
                        type="button"
                        onClick={() => void loadDetails(selected)}
                      >
                        Retry details
                      </button>
                    }
                  />
                ) : null}
                <WorkflowTabs
                  label="Research details"
                  active={view}
                  onChange={setView}
                  tabs={[
                    { id: "overview", label: "Sources", count: sources.length },
                    { id: "brief", label: "Brief", count: brief ? 1 : 0 },
                    { id: "claims", label: "Claims", count: claims.length },
                    {
                      id: "conflicts",
                      label: "Conflicts",
                      count: conflicts.length,
                    },
                  ]}
                />
                {detailsLoading ? (
                  <LoadingState title="Loading request details" />
                ) : view === "overview" ? (
                  <PageSection
                    title="Sources and independence"
                    description="Source quality and independence clusters are shown directly; excerpts remain readable."
                  >
                    {sources.length === 0 ? (
                      <EmptyState
                        title="No research sources"
                        description="Add at least one source before running the research pipeline."
                      />
                    ) : (
                      <div className="workflow-card-grid">
                        {sources.map((source) => (
                          <article className="workflow-card" key={source.id}>
                            <div className="workflow-card-header">
                              <h3>{source.title}</h3>
                              <StatusBadge
                                tone={statusTone(source.primary_source_status)}
                              >
                                {source.primary_source_status}
                              </StatusBadge>
                            </div>
                            <p className="workflow-copy">
                              {source.content_excerpt}
                            </p>
                            <div className="workflow-data-list">
                              <div className="workflow-data-row">
                                <span>Publisher</span>
                                <strong>{source.publisher}</strong>
                              </div>
                              <div className="workflow-data-row">
                                <span>Quality</span>
                                <strong>
                                  {source.quality_score.toFixed(2)}
                                </strong>
                              </div>
                              <div className="workflow-data-row">
                                <span>Independence cluster</span>
                                <strong>
                                  {source.independence_cluster_id ||
                                    "Not assigned"}
                                </strong>
                              </div>
                            </div>
                            {source.url ? (
                              <a
                                href={source.url}
                                target="_blank"
                                rel="noreferrer"
                              >
                                Open source
                              </a>
                            ) : null}
                            <TechnicalDetails data={source} />
                          </article>
                        ))}
                      </div>
                    )}
                  </PageSection>
                ) : view === "brief" ? (
                  <PageSection
                    title="Research brief"
                    description="The pipeline outcome is explicit; partial or insufficient evidence is not presented as success."
                  >
                    {brief ? (
                      <article className="workflow-card">
                        <div className="workflow-card-header">
                          <div>
                            <h3>{brief.title}</h3>
                            <p className="workflow-copy">{brief.summary}</p>
                          </div>
                          <StatusBadge tone={statusTone(brief.outcome)}>
                            {brief.outcome}
                          </StatusBadge>
                        </div>
                        <WorkflowStatusSummary
                          metrics={[
                            {
                              label: "Confidence",
                              value: `${Math.round(brief.overall_confidence * 100)}%`,
                            },
                            {
                              label: "Verified",
                              value: brief.verified_claims.length,
                            },
                            {
                              label: "Uncertain",
                              value: brief.uncertain_claims.length,
                            },
                            {
                              label: "Contradictions",
                              value: brief.contradictions.length,
                            },
                          ]}
                        />
                        <div>
                          <h3>Key facts</h3>
                          {brief.key_facts.length ? (
                            <ul className="ui-check-list">
                              {brief.key_facts.map((fact) => (
                                <li key={fact}>{fact}</li>
                              ))}
                            </ul>
                          ) : (
                            <p className="workflow-copy">
                              No key facts recorded.
                            </p>
                          )}
                        </div>
                        {brief.open_questions.length ? (
                          <Alert tone="warning" title="Open questions">
                            <ul>
                              {brief.open_questions.map((item) => (
                                <li key={item}>{item}</li>
                              ))}
                            </ul>
                          </Alert>
                        ) : null}
                        <TechnicalDetails data={brief} />
                      </article>
                    ) : (
                      <EmptyState
                        title="No research brief"
                        description={
                          selected.status === "FAILED"
                            ? "The research request failed before producing a brief."
                            : selected.status === "SUCCEEDED"
                              ? "The request completed but its brief could not be loaded."
                              : "Run the research pipeline after adding sources."
                        }
                      />
                    )}
                  </PageSection>
                ) : view === "claims" ? (
                  <PageSection
                    title="Claims"
                    description="Verification, confidence, and source agreement are visible without opening raw payloads."
                  >
                    {claims.length === 0 ? (
                      <EmptyState title="No claims extracted" />
                    ) : (
                      <div className="workflow-card-grid">
                        {claims.map((claim) => (
                          <article className="workflow-card" key={claim.id}>
                            <div className="workflow-card-header">
                              <h3>{claim.claim_text}</h3>
                              <StatusBadge
                                tone={
                                  claim.is_verified
                                    ? "success"
                                    : statusTone(claim.confidence_band)
                                }
                              >
                                {claim.is_verified
                                  ? "VERIFIED"
                                  : claim.confidence_band}
                              </StatusBadge>
                            </div>
                            <div className="workflow-data-list">
                              <div className="workflow-data-row">
                                <span>Independent sources</span>
                                <strong>
                                  {claim.independent_sources_count}
                                </strong>
                              </div>
                              <div className="workflow-data-row">
                                <span>Supports / contradicts</span>
                                <strong>
                                  {claim.supporting_sources_count} /{" "}
                                  {claim.contradicting_sources_count}
                                </strong>
                              </div>
                              <div className="workflow-data-row">
                                <span>Confidence</span>
                                <strong>
                                  {Math.round(claim.confidence_score * 100)}%
                                </strong>
                              </div>
                            </div>
                            <TechnicalDetails data={claim} />
                          </article>
                        ))}
                      </div>
                    )}
                  </PageSection>
                ) : (
                  <PageSection
                    title="Evidence conflicts"
                    description="Open contradictions remain prominent until explicitly resolved or dismissed."
                  >
                    {conflicts.length === 0 ? (
                      <EmptyState
                        title="No conflicts detected"
                        description="No contradictory evidence is recorded for this request."
                      />
                    ) : (
                      <div className="workflow-card-grid">
                        {conflicts.map((conflict) => (
                          <article
                            className={`workflow-card ${conflict.severity === "HIGH" ? "workflow-error-card" : ""}`}
                            key={conflict.id}
                          >
                            <div className="workflow-card-header">
                              <h3>{conflict.conflict_type}</h3>
                              <StatusBadge
                                tone={
                                  conflict.status === "OPEN"
                                    ? "danger"
                                    : "neutral"
                                }
                              >
                                {conflict.severity} · {conflict.status}
                              </StatusBadge>
                            </div>
                            <p className="workflow-copy">
                              {conflict.description}
                            </p>
                            <TechnicalDetails data={conflict} />
                          </article>
                        ))}
                      </div>
                    )}
                  </PageSection>
                )}
              </>
            ) : null}
          </div>
        </div>
      )}
      <Dialog
        open={createOpen}
        title="Create research request"
        description="Choose an evaluated topic and define the operator research goal."
        onClose={() => setCreateOpen(false)}
        actions={
          <>
            <button
              className="btn btn-secondary"
              type="button"
              onClick={() => setCreateOpen(false)}
            >
              Cancel
            </button>
            <button
              className="btn btn-primary"
              type="submit"
              form="research-request-form"
              disabled={busy || !topicId}
            >
              Create request
            </button>
          </>
        }
      >
        <form
          id="research-request-form"
          className="workflow-dialog-form"
          onSubmit={createRequest}
        >
          <FormField id="research-topic" label="Topic" required>
            <select
              className="select"
              value={topicId}
              onChange={(event) => setTopicId(event.target.value)}
            >
              {topics.map((topic) => (
                <option value={topic.id} key={topic.id}>
                  {topic.title}
                </option>
              ))}
            </select>
          </FormField>
          <FormField id="research-question" label="Research question">
            <input
              className="input"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
            />
          </FormField>
          <FormField id="research-scope" label="Scope">
            <textarea
              className="input"
              value={scope}
              onChange={(event) => setScope(event.target.value)}
            />
          </FormField>
        </form>
      </Dialog>
      <Dialog
        open={sourceOpen}
        title="Add research source"
        description="Store an operator-provided source excerpt without masking its provenance."
        onClose={() => setSourceOpen(false)}
        actions={
          <>
            <button
              className="btn btn-secondary"
              type="button"
              onClick={() => setSourceOpen(false)}
            >
              Cancel
            </button>
            <button
              className="btn btn-primary"
              type="submit"
              form="research-source-form"
              disabled={busy}
            >
              Add source
            </button>
          </>
        }
      >
        <form
          id="research-source-form"
          className="workflow-dialog-form"
          onSubmit={addSource}
        >
          <FormField id="source-title" label="Title" required>
            <input
              className="input"
              value={sourceTitle}
              onChange={(event) => setSourceTitle(event.target.value)}
            />
          </FormField>
          <FormField id="source-publisher" label="Publisher" required>
            <input
              className="input"
              value={publisher}
              onChange={(event) => setPublisher(event.target.value)}
            />
          </FormField>
          <FormField id="source-url" label="URL">
            <input
              className="input"
              type="url"
              value={sourceUrl}
              onChange={(event) => setSourceUrl(event.target.value)}
            />
          </FormField>
          <FormField id="source-primary" label="Primary source status">
            <select
              className="select"
              value={primaryStatus}
              onChange={(event) =>
                setPrimaryStatus(event.target.value as PrimarySourceStatus)
              }
            >
              <option value="UNKNOWN">Unknown</option>
              <option value="CLAIMED">Claimed</option>
              <option value="CONFIRMED">Confirmed</option>
            </select>
          </FormField>
          <FormField id="source-excerpt" label="Source excerpt" required>
            <textarea
              className="input"
              value={excerpt}
              onChange={(event) => setExcerpt(event.target.value)}
            />
          </FormField>
        </form>
      </Dialog>
    </div>
  );
}
