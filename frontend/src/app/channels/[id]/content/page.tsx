"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import {
  cancelContentRequest,
  createContentRequest,
  generateContent,
  getChannel,
  getContentIntent,
  getContentOutline,
  getScriptQAResult,
  getScriptVersion,
  listContentHooks,
  listContentRequests,
  listResearchBriefs,
  listScriptVersions,
  listTopics,
  regenerateScript,
  rerunScriptQA,
  selectContentHook,
  type Channel,
  type ContentGenerationRequest,
  type ContentHook,
  type ContentIntent,
  type ContentOutline,
  type ContentQAResult,
  type ContentType,
  type ResearchBriefSummary,
  type ScriptVersion,
  type ScriptVersionSummary,
  type TopicCandidate,
} from "@/lib/api";
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
} from "@/components/ui";
import {
  CitationsPanel,
  ContentQAPanel,
  IntentHooksPanel,
  OutlinePanel,
  ScriptPanel,
} from "@/components/workflow/ContentPanels";
import {
  EntityList,
  EntityListButton,
  TechnicalDetails,
  WorkflowStatusSummary,
  WorkflowTabs,
} from "@/components/workflow/WorkflowPrimitives";
import { useOperatorContext } from "@/lib/operator-context";

type ContentView = "script" | "intent" | "outline" | "citations" | "qa";

export default function ContentEnginePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: channelId } = use(params);
  const { setSelectedChannelId } = useOperatorContext();
  const [channel, setChannel] = useState<Channel | null>(null);
  const [requests, setRequests] = useState<ContentGenerationRequest[]>([]);
  const [selected, setSelected] = useState<ContentGenerationRequest | null>(
    null,
  );
  const [topics, setTopics] = useState<TopicCandidate[]>([]);
  const [briefs, setBriefs] = useState<ResearchBriefSummary[]>([]);
  const [intent, setIntent] = useState<ContentIntent | null>(null);
  const [hooks, setHooks] = useState<ContentHook[]>([]);
  const [outline, setOutline] = useState<ContentOutline | null>(null);
  const [scripts, setScripts] = useState<ScriptVersionSummary[]>([]);
  const [script, setScript] = useState<ScriptVersion | null>(null);
  const [qa, setQa] = useState<ContentQAResult | null>(null);
  const [view, setView] = useState<ContentView>("script");
  const [loading, setLoading] = useState(true);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [confirmAction, setConfirmAction] = useState<
    "regenerate" | "cancel" | null
  >(null);
  const [topicId, setTopicId] = useState("");
  const [briefId, setBriefId] = useState("");
  const [contentType, setContentType] =
    useState<ContentType>("YOUTUBE_LONGFORM");
  const [duration, setDuration] = useState(480);
  const [creativeDirection, setCreativeDirection] = useState("");

  const loadVersion = useCallback(
    async (request: ContentGenerationRequest, version: number) => {
      setDetailsLoading(true);
      setDetailError(null);
      const [scriptResult, qaResult] = await Promise.allSettled([
        getScriptVersion(channelId, request.id, version),
        getScriptQAResult(channelId, request.id, version),
      ]);
      setScript(
        scriptResult.status === "fulfilled" ? scriptResult.value : null,
      );
      setQa(qaResult.status === "fulfilled" ? qaResult.value : null);
      if (scriptResult.status === "rejected" || qaResult.status === "rejected")
        setDetailError(
          "The selected script or its QA result could not be loaded.",
        );
      setDetailsLoading(false);
    },
    [channelId],
  );

  const loadDetails = useCallback(
    async (request: ContentGenerationRequest) => {
      setDetailsLoading(true);
      setDetailError(null);
      const [intentResult, hookResult, outlineResult, scriptResult] =
        await Promise.allSettled([
          getContentIntent(channelId, request.id),
          listContentHooks(channelId, request.id),
          getContentOutline(channelId, request.id),
          listScriptVersions(channelId, request.id),
        ]);
      setIntent(
        intentResult.status === "fulfilled" ? intentResult.value : null,
      );
      setHooks(hookResult.status === "fulfilled" ? hookResult.value : []);
      setOutline(
        outlineResult.status === "fulfilled" ? outlineResult.value : null,
      );
      const scriptList =
        scriptResult.status === "fulfilled" ? scriptResult.value : [];
      setScripts(scriptList);
      const failures = [
        intentResult,
        hookResult,
        outlineResult,
        scriptResult,
      ].filter((result) => result.status === "rejected");
      if (request.status === "SUCCEEDED" && failures.length)
        setDetailError(
          `${failures.length} generated content artifact${failures.length === 1 ? "" : "s"} could not be loaded.`,
        );
      if (scriptList[0]) await loadVersion(request, scriptList[0].version);
      else {
        setScript(null);
        setQa(null);
        setDetailsLoading(false);
      }
    },
    [channelId, loadVersion],
  );

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    setSelectedChannelId(channelId);
    try {
      const [channelData, requestData, topicData] = await Promise.all([
        getChannel(channelId),
        listContentRequests(channelId),
        listTopics(channelId),
      ]);
      const eligible = topicData.filter(
        (topic) => !["ARCHIVED", "DISCOVERED"].includes(topic.status),
      );
      setChannel(channelData);
      setRequests(requestData);
      setTopics(eligible);
      const next = requestData[0] || null;
      setSelected(next);
      if (next) await loadDetails(next);
      else {
        setIntent(null);
        setHooks([]);
        setOutline(null);
        setScripts([]);
        setScript(null);
        setQa(null);
      }
      if (!topicId && eligible[0]) setTopicId(eligible[0].id);
    } catch (reason: unknown) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Failed to load content workflow",
      );
    } finally {
      setLoading(false);
    }
  }, [channelId, loadDetails, setSelectedChannelId, topicId]);

  useEffect(() => {
    void loadData();
  }, [loadData]);
  const archived = channel?.state === "ARCHIVED";

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
  async function choose(request: ContentGenerationRequest) {
    setSelected(request);
    await loadDetails(request);
  }
  async function changeTopic(id: string) {
    setTopicId(id);
    setBriefId("");
    setBriefs([]);
    if (!id) return;
    setBusy(true);
    try {
      const values = await listResearchBriefs(channelId, id);
      setBriefs(values);
      setBriefId(values[0]?.id || "");
    } catch (reason: unknown) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Research briefs could not be loaded",
      );
    } finally {
      setBusy(false);
    }
  }
  async function createRequest(event: React.FormEvent) {
    event.preventDefault();
    if (!topicId || !briefId) return;
    await perform(async () => {
      await createContentRequest(channelId, {
        topic_candidate_id: topicId,
        research_brief_id: briefId,
        content_type: contentType,
        target_duration_seconds: duration,
        creative_direction: creativeDirection.trim() || undefined,
      });
      setCreateOpen(false);
      setCreativeDirection("");
    }, "Failed to create content request");
  }
  async function selectHook(id: string) {
    if (!selected) return;
    await perform(
      async () => {
        await selectContentHook(channelId, selected.id, id);
        setHooks(await listContentHooks(channelId, selected.id));
      },
      "Failed to select hook",
      false,
    );
  }
  async function rerunQA() {
    if (!selected || !script) return;
    await perform(
      async () =>
        setQa(await rerunScriptQA(channelId, selected.id, script.version)),
      "QA run failed",
      false,
    );
  }
  async function executeConfirmedAction() {
    const action = confirmAction;
    setConfirmAction(null);
    if (!selected || !action) return;
    if (action === "regenerate")
      await perform(
        () => regenerateScript(channelId, selected.id),
        "Script regeneration failed",
      );
    else
      await perform(
        () => cancelContentRequest(channelId, selected.id),
        "Failed to cancel content request",
      );
  }

  return (
    <div className="workflow-page ui-page-stack">
      <ChannelContextBar currentTab="content" />
      <PageHeader
        eyebrow="Channel workflow · Step 3"
        title="Content studio"
        description="Turn a sufficient research brief into a traceable, timed script with explicit hooks, revisions, and QA."
        actions={
          <>
        <button
          type="button"
          className="btn btn-primary"
          disabled={archived}
          onClick={() => {
            setCreateOpen(true);
            if (topicId) void changeTopic(topicId);
          }}
            >
              New content request
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => void loadData()}
            >
              Refresh
            </button>
          </>
        }
      />
      {archived ? (
        <Alert tone="warning" title="Channel archived">
          Content generation actions are disabled until the channel is active.
        </Alert>
      ) : null}
      {error ? (
        <ErrorState
          title="Content workflow unavailable"
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
          {
            label: "Request",
            value: selected?.status || "None",
            status: selected?.status,
          },
          {
            label: "Outcome",
            value: selected?.outcome || "Not available",
            status: selected?.outcome,
          },
          {
            label: "Script version",
            value: script ? `v${script.version}` : "None",
          },
          {
            label: "Target",
            value: selected ? `${selected.target_duration_seconds}s` : "—",
          },
          { label: "QA", value: qa?.status || "Not run", status: qa?.status },
        ]}
      />
      {loading ? (
        <LoadingState title="Loading content workflow" />
      ) : requests.length === 0 ? (
        <EmptyState
          title="No content requests"
          description={
            topics.length
              ? "Create a request from a researched topic and brief."
              : "A researched topic is required before content generation."
          }
          action={
            topics.length ? (
            <button
              className="btn btn-primary"
              type="button"
              onClick={() => {
                setCreateOpen(true);
                if (topicId) void changeTopic(topicId);
              }}
              >
                Create content request
              </button>
            ) : (
              <Link
                className="btn btn-primary"
                href={`/channels/${channelId}/research`}
              >
                Open research
              </Link>
            )
          }
        />
      ) : (
        <div className="workflow-layout">
          <EntityList
            title="Content requests"
            description={`${requests.length} request${requests.length === 1 ? "" : "s"}`}
          >
            {requests.map((request) => (
              <EntityListButton
                key={request.id}
                selected={selected?.id === request.id}
                title={`${request.content_type.replaceAll("_", " ")} · ${request.target_duration_seconds}s`}
                status={request.outcome || request.status}
                meta={new Date(request.created_at).toLocaleDateString()}
                onClick={() => void choose(request)}
              />
            ))}
          </EntityList>
          <div className="workflow-detail">
            {selected ? (
              <>
                <PageSection
                  title={
                    script?.title ||
                    `Content request ${selected.id.slice(0, 8)}`
                  }
                  description={`${selected.content_type.replaceAll("_", " ")} · target ${selected.target_duration_seconds} seconds`}
                  actions={
                    <div className="ui-inline-actions">
                      {selected.status === "DRAFT" ? (
                        <button
                          className="btn btn-primary btn-sm"
                          type="button"
                          disabled={busy || archived}
                          onClick={() =>
                            void perform(
                              () => generateContent(channelId, selected.id),
                              "Content generation failed",
                            )
                          }
                        >
                          Generate content
                        </button>
                      ) : null}
                      {selected.status === "SUCCEEDED" ? (
                        <>
                          <button
                            className="btn btn-secondary btn-sm"
                            type="button"
                            disabled={busy || archived}
                            onClick={() => setConfirmAction("regenerate")}
                          >
                            Regenerate
                          </button>
                          <Link
                            className="btn btn-primary btn-sm"
                            href={`/channels/${channelId}/production`}
                          >
                            Proceed to production
                          </Link>
                        </>
                      ) : null}
                      {selected.status === "RUNNING" ? (
                        <button
                          className="btn btn-danger btn-sm"
                          type="button"
                          disabled={busy}
                          onClick={() => setConfirmAction("cancel")}
                        >
                          Cancel request
                        </button>
                      ) : null}
                    </div>
                  }
                >
                  <TechnicalDetails data={selected} />
                </PageSection>
                {detailError ? (
                  <ErrorState
                    title="Generated content details unavailable"
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
                  label="Content details"
                  active={view}
                  onChange={setView}
                  tabs={[
                    { id: "script", label: "Script", count: scripts.length },
                    {
                      id: "intent",
                      label: "Intent & hooks",
                      count: hooks.length,
                    },
                    {
                      id: "outline",
                      label: "Outline",
                      count: outline?.sections.length || 0,
                    },
                    { id: "citations", label: "Citations" },
                    { id: "qa", label: "QA", count: qa?.findings.length || 0 },
                  ]}
                />
                {detailsLoading ? (
                  <LoadingState title="Loading generated content" />
                ) : view === "script" ? (
                  <ScriptPanel
                    script={script}
                    revisions={scripts}
                    busy={busy}
                    onRevision={(version) =>
                      void loadVersion(selected, version)
                    }
                  />
                ) : view === "intent" ? (
                  <IntentHooksPanel
                    intent={intent}
                    hooks={hooks}
                    busy={busy}
                    onSelect={(id) => void selectHook(id)}
                  />
                ) : view === "outline" ? (
                  <OutlinePanel outline={outline} />
                ) : view === "citations" ? (
                  <CitationsPanel script={script} />
                ) : (
                  <ContentQAPanel
                    qa={qa}
                    busy={busy}
                    onRerun={() => void rerunQA()}
                  />
                )}
              </>
            ) : null}
          </div>
        </div>
      )}
      <Dialog
        open={createOpen}
        title="Create content request"
        description="Choose a topic, a research brief, and an honest duration target."
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
              form="content-request-form"
              disabled={busy || !topicId || !briefId}
            >
              Create request
            </button>
          </>
        }
      >
        <form
          id="content-request-form"
          className="workflow-dialog-form"
          onSubmit={createRequest}
        >
          <FormField id="content-topic" label="Topic" required>
            <select
              className="select"
              value={topicId}
              onChange={(event) => void changeTopic(event.target.value)}
            >
              <option value="">Choose a topic</option>
              {topics.map((topic) => (
                <option value={topic.id} key={topic.id}>
                  {topic.title}
                </option>
              ))}
            </select>
          </FormField>
          <FormField
            id="content-brief"
            label="Research brief"
            required
            description={
              topicId && briefs.length === 0
                ? "No brief is currently available for this topic."
                : undefined
            }
          >
            <select
              className="select"
              value={briefId}
              onChange={(event) => setBriefId(event.target.value)}
            >
              <option value="">Choose a brief</option>
              {briefs.map((briefItem) => (
                <option value={briefItem.id} key={briefItem.id}>
                  v{briefItem.version} · {briefItem.outcome} ·{" "}
                  {Math.round(briefItem.overall_confidence * 100)}%
                </option>
              ))}
            </select>
          </FormField>
          <div className="ui-form-grid">
            <FormField id="content-type" label="Format">
              <select
                className="select"
                value={contentType}
                onChange={(event) => {
                  const value = event.target.value as ContentType;
                  setContentType(value);
                  setDuration(value === "YOUTUBE_SHORT" ? 45 : 480);
                }}
              >
                <option value="YOUTUBE_LONGFORM">YouTube longform</option>
                <option value="YOUTUBE_SHORT">YouTube short</option>
              </select>
            </FormField>
            <FormField id="content-duration" label="Target seconds" required>
              <input
                className="input"
                type="number"
                min="15"
                value={duration}
                onChange={(event) => setDuration(Number(event.target.value))}
              />
            </FormField>
          </div>
          <FormField id="content-direction" label="Creative direction">
            <textarea
              className="input"
              value={creativeDirection}
              onChange={(event) => setCreativeDirection(event.target.value)}
            />
          </FormField>
        </form>
      </Dialog>
      <ConfirmDialog
        open={confirmAction === "regenerate"}
        title="Regenerate script"
        description="Create a new script revision while retaining existing version history?"
        confirmLabel="Regenerate"
        busy={busy}
        onCancel={() => setConfirmAction(null)}
        onConfirm={() => void executeConfirmedAction()}
      />
      <ConfirmDialog
        open={confirmAction === "cancel"}
        title="Cancel content request"
        description="Cancel the active content generation request?"
        confirmLabel="Cancel request"
        destructive
        busy={busy}
        onCancel={() => setConfirmAction(null)}
        onConfirm={() => void executeConfirmedAction()}
      />
    </div>
  );
}
