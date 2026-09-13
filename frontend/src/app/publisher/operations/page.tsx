"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  getActivePublications,
  getPublishCalendar,
  getPublicationDetail,
  getPublisherDeadLetters,
  getPublisherHistory,
  getPublisherManualHolds,
  getPublisherOperationsOverview,
  getPublisherRetries,
  reconcilePublisherManualHold,
  requeuePublisherDeadLetter,
  authorizePublisherSessionResume,
  type ActivePublicationItem,
  type DeadLetterItem,
  type ManualHoldItem,
  type PublicationDetailResponse,
  type PublicationHistoryItem,
  type PublishCalendarItem,
  type PublisherOperationsOverview,
  type RetryQueueItem,
} from "@/lib/api";
import {
  calculateProgress,
  formatBytes,
  formatDuration,
  getAttemptStateBadge,
  getHoldSourceBadge,
} from "@/lib/publisher-operations-helpers";
import { useOperatorContext } from "@/lib/operator-context";
import { ChannelContextBar } from "@/components/ChannelContextBar";
import { Alert, Dialog, EmptyState, LoadingState, PageHeader, PageSection, StatusBadge } from "@/components/ui";

type OperationsTab = "overview" | "calendar" | "active" | "recovery" | "history";
type RecoverySubTab = "holds" | "retries" | "dead_letters";

export default function PublisherOperationsPage() {
  const { selectedChannelId, selectedChannel } = useOperatorContext();

  const [activeTab, setActiveTab] = useState<OperationsTab>("overview");
  const [recoverySubTab, setRecoverySubTab] = useState<RecoverySubTab>("holds");

  // Overview metrics
  const [overview, setOverview] = useState<PublisherOperationsOverview | null>(null);

  // Tab datasets
  const [calendarItems, setCalendarItems] = useState<PublishCalendarItem[]>([]);
  const [activeItems, setActiveItems] = useState<ActivePublicationItem[]>([]);
  const [holdItems, setHoldItems] = useState<ManualHoldItem[]>([]);
  const [retryItems, setRetryItems] = useState<RetryQueueItem[]>([]);
  const [deadLetterItems, setDeadLetterItems] = useState<DeadLetterItem[]>([]);
  const [historyItems, setHistoryItems] = useState<PublicationHistoryItem[]>([]);

  // History filters
  const [historySearch, setHistorySearch] = useState("");
  const [historyStateFilter, setHistoryStateFilter] = useState<string>("");

  // Detail Drawer State
  const [selectedIntentId, setSelectedIntentId] = useState<string | null>(null);
  const [detailData, setDetailData] = useState<PublicationDetailResponse | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  // Operator Action Dialog States
  const [reconcileTarget, setReconcileTarget] = useState<ManualHoldItem | null>(null);
  const [requeueTarget, setRequeueTarget] = useState<DeadLetterItem | null>(null);
  const [resumeTarget, setResumeTarget] = useState<{ attemptId: string; intentId: string } | null>(null);

  const [operatorActor, setOperatorActor] = useState("OPERATOR_CONSOLE");
  const [operatorReason, setOperatorReason] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const [actionSuccessMessage, setActionSuccessMessage] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Load Overview & Active Data
  const loadOperationsData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const channelParam = selectedChannelId || undefined;

      const [
        overviewRes,
        calendarRes,
        activeRes,
        holdsRes,
        retriesRes,
        deadLettersRes,
        historyRes,
      ] = await Promise.all([
        getPublisherOperationsOverview(),
        getPublishCalendar({ channel_id: channelParam, limit: 50 }),
        getActivePublications({ channel_id: channelParam, limit: 50 }),
        getPublisherManualHolds({ channel_id: channelParam, limit: 50 }),
        getPublisherRetries({ channel_id: channelParam, limit: 50 }),
        getPublisherDeadLetters({ channel_id: channelParam, limit: 50 }),
        getPublisherHistory({
          channel_id: channelParam,
          search: historySearch || undefined,
          state: historyStateFilter || undefined,
          limit: 50,
        }),
      ]);

      setOverview(overviewRes);
      setCalendarItems(calendarRes.items);
      setActiveItems(activeRes.items);
      setHoldItems(holdsRes.items);
      setRetryItems(retriesRes.items);
      setDeadLetterItems(deadLettersRes.items);
      setHistoryItems(historyRes.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load publisher operations data.");
    } finally {
      setLoading(false);
    }
  }, [selectedChannelId, historySearch, historyStateFilter]);

  useEffect(() => {
    void loadOperationsData();
  }, [loadOperationsData]);

  // Load publication detail drawer
  const inspectPublication = async (intentId: string) => {
    setSelectedIntentId(intentId);
    setLoadingDetail(true);
    try {
      const data = await getPublicationDetail(intentId);
      setDetailData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to inspect publication detail.");
    } finally {
      setLoadingDetail(false);
    }
  };

  // ── Operator Actions ──

  const handleExecuteReconciliation = async () => {
    if (!reconcileTarget) return;
    if (!operatorActor.trim() || !operatorReason.trim()) {
      alert("Both Operator Actor and Reason are strictly required for external reconciliation.");
      return;
    }
    setActionBusy(true);
    try {
      const result = await reconcilePublisherManualHold(reconcileTarget.attempt_id, {
        actor: operatorActor.trim(),
        reason: operatorReason.trim(),
      });
      setActionSuccessMessage(
        `Reconciliation completed for attempt ${result.attempt_id}. New status: ${result.reconciliation_status}.`
      );
      setReconcileTarget(null);
      setOperatorReason("");
      await loadOperationsData();
      if (selectedIntentId) {
        await inspectPublication(selectedIntentId);
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : "Reconciliation operation failed.");
    } finally {
      setActionBusy(false);
    }
  };

  const handleExecuteRequeue = async () => {
    if (!requeueTarget) return;
    if (!operatorActor.trim() || !operatorReason.trim()) {
      alert("Both Operator Actor and Reason are strictly required to requeue dead letters.");
      return;
    }
    setActionBusy(true);
    try {
      const result = await requeuePublisherDeadLetter(requeueTarget.handoff_id, {
        actor: operatorActor.trim(),
        reason: operatorReason.trim(),
      });
      setActionSuccessMessage(`Handoff ${result.handoff_id} requeued to status ${result.status}.`);
      setRequeueTarget(null);
      setOperatorReason("");
      await loadOperationsData();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Dead letter requeue operation failed.");
    } finally {
      setActionBusy(false);
    }
  };

  const handleAuthorizeResume = async () => {
    if (!resumeTarget) return;
    if (!operatorActor.trim() || !operatorReason.trim()) {
      alert("Both Operator Actor and Reason are strictly required to authorize session resume.");
      return;
    }
    setActionBusy(true);
    try {
      const result = await authorizePublisherSessionResume(resumeTarget.attemptId, {
        actor: operatorActor.trim(),
        reason: operatorReason.trim(),
      });
      setActionSuccessMessage(
        `Resume authorized for attempt ${result.attempt_id} at provider offset ${formatBytes(result.provider_offset)} of ${formatBytes(result.total_bytes)}.`
      );
      setResumeTarget(null);
      setOperatorReason("");
      await loadOperationsData();
      if (selectedIntentId) {
        await inspectPublication(selectedIntentId);
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : "Resume authorization failed.");
    } finally {
      setActionBusy(false);
    }
  };

  return (
    <div className="ui-page-stack">
      <ChannelContextBar currentTab="publisher" />

      <PageHeader
        eyebrow="Operations & Observability"
        title="Publisher Fleet Operations"
        description={
          selectedChannel
            ? `Offline operational monitoring, queues, and recovery controls for ${selectedChannel.name}.`
            : "Fleet-wide offline operational monitoring, publish calendar, queues, and recovery controls."
        }
        actions={
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            <Link href="/publisher" className="btn btn-secondary">
              ← Publish Studio
            </Link>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => void loadOperationsData()}
              disabled={loading}
            >
              Refresh Fleet
            </button>
          </div>
        }
      />

      {error && (
        <Alert
          tone="danger"
          title="Operations Query Error"
          actions={
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => void loadOperationsData()}>
              Retry
            </button>
          }
        >
          {error}
        </Alert>
      )}

      {actionSuccessMessage && (
        <Alert tone="success" title="Operation Executed">
          {actionSuccessMessage}
        </Alert>
      )}

      {/* Fleet Configuration & Telemetry Bar */}
      {overview && (
        <div className="ui-panel-grid" style={{ marginBottom: "1.5rem" }}>
          <div className="ui-card" style={{ padding: "1rem" }}>
            <div style={{ fontSize: "0.75rem", textTransform: "uppercase", color: "var(--color-text-muted)" }}>
              Fleet Configuration
            </div>
            <div style={{ fontWeight: 600, marginTop: "0.25rem", fontSize: "1rem" }}>
              Queue: <span style={{ color: "var(--color-primary-text)" }}>{overview.publisher_queue_name}</span>
            </div>
            <div style={{ fontSize: "0.85rem", color: "var(--color-text-muted)", marginTop: "0.25rem" }}>
              Worker Role: {overview.publisher_worker_role} · Concurrency: {overview.configured_concurrency} · Prefetch:{" "}
              {overview.prefetch_multiplier}
            </div>
          </div>

          <div className="ui-card" style={{ padding: "1rem" }}>
            <div style={{ fontSize: "0.75rem", textTransform: "uppercase", color: "var(--color-text-muted)" }}>
              Publish Calendar
            </div>
            <div style={{ display: "flex", gap: "1rem", marginTop: "0.25rem" }}>
              <div>
                <span style={{ fontSize: "1.25rem", fontWeight: 700 }}>{overview.scheduled_upcoming}</span>
                <span style={{ fontSize: "0.8rem", color: "var(--color-text-muted)", marginLeft: "0.25rem" }}>
                  Upcoming
                </span>
              </div>
              <div>
                <span
                  style={{
                    fontSize: "1.25rem",
                    fontWeight: 700,
                    color: overview.scheduled_due > 0 ? "var(--color-warning-text)" : "inherit",
                  }}
                >
                  {overview.scheduled_due}
                </span>
                <span style={{ fontSize: "0.8rem", color: "var(--color-text-muted)", marginLeft: "0.25rem" }}>
                  Due Now
                </span>
              </div>
            </div>
          </div>

          <div className="ui-card" style={{ padding: "1rem" }}>
            <div style={{ fontSize: "0.75rem", textTransform: "uppercase", color: "var(--color-text-muted)" }}>
              Active In-Flight
            </div>
            <div style={{ display: "flex", gap: "0.75rem", marginTop: "0.25rem" }}>
              <div>
                <span style={{ fontSize: "1.25rem", fontWeight: 700 }}>{overview.publish_uploading}</span>
                <span style={{ fontSize: "0.8rem", color: "var(--color-text-muted)", marginLeft: "0.25rem" }}>
                  Uploading
                </span>
              </div>
              <div>
                <span style={{ fontSize: "1.25rem", fontWeight: 700 }}>{overview.publish_finalizing}</span>
                <span style={{ fontSize: "0.8rem", color: "var(--color-text-muted)", marginLeft: "0.25rem" }}>
                  Finalizing
                </span>
              </div>
              <div>
                <span
                  style={{
                    fontSize: "1.25rem",
                    fontWeight: 700,
                    color: overview.publish_unknown > 0 ? "var(--color-danger-text)" : "inherit",
                  }}
                >
                  {overview.publish_unknown}
                </span>
                <span style={{ fontSize: "0.8rem", color: "var(--color-text-muted)", marginLeft: "0.25rem" }}>
                  Unknown
                </span>
              </div>
            </div>
          </div>

          <div
            className="ui-card"
            style={{
              padding: "1rem",
              borderColor: overview.recovery_manual_hold_count > 0 ? "var(--color-danger-border)" : undefined,
            }}
          >
            <div style={{ fontSize: "0.75rem", textTransform: "uppercase", color: "var(--color-text-muted)" }}>
              Holds & Recovery
            </div>
            <div style={{ display: "flex", gap: "1rem", marginTop: "0.25rem" }}>
              <div>
                <span
                  style={{
                    fontSize: "1.25rem",
                    fontWeight: 700,
                    color: overview.recovery_manual_hold_count > 0 ? "var(--color-danger-text)" : "inherit",
                  }}
                >
                  {overview.recovery_manual_hold_count}
                </span>
                <span style={{ fontSize: "0.8rem", color: "var(--color-text-muted)", marginLeft: "0.25rem" }}>
                  Recovery Hold
                </span>
              </div>
              <div>
                <span style={{ fontSize: "1.25rem", fontWeight: 700 }}>{overview.schedule_manual_hold_count}</span>
                <span style={{ fontSize: "0.8rem", color: "var(--color-text-muted)", marginLeft: "0.25rem" }}>
                  Schedule Hold
                </span>
              </div>
            </div>
          </div>

          <div className="ui-card" style={{ padding: "1rem" }}>
            <div style={{ fontSize: "0.75rem", textTransform: "uppercase", color: "var(--color-text-muted)" }}>
              Recent Window ({overview.recent_window_hours}h)
            </div>
            <div style={{ display: "flex", gap: "1rem", marginTop: "0.25rem" }}>
              <div>
                <span style={{ fontSize: "1.25rem", fontWeight: 700, color: "var(--color-success-text)" }}>
                  {overview.publish_succeeded_recent}
                </span>
                <span style={{ fontSize: "0.8rem", color: "var(--color-text-muted)", marginLeft: "0.25rem" }}>
                  Succeeded
                </span>
              </div>
              <div>
                <span
                  style={{
                    fontSize: "1.25rem",
                    fontWeight: 700,
                    color: overview.publish_failed_recent > 0 ? "var(--color-danger-text)" : "inherit",
                  }}
                >
                  {overview.publish_failed_recent}
                </span>
                <span style={{ fontSize: "0.8rem", color: "var(--color-text-muted)", marginLeft: "0.25rem" }}>
                  Failed
                </span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Main Tab Navigation */}
      <div className="ui-subnav" style={{ display: "flex", gap: "0.5rem", borderBottom: "1px solid var(--color-border)", marginBottom: "1rem" }}>
        <button
          type="button"
          className={`ui-subnav-link ${activeTab === "overview" ? "is-active" : ""}`}
          onClick={() => setActiveTab("overview")}
        >
          Overview & Queues
        </button>
        <button
          type="button"
          className={`ui-subnav-link ${activeTab === "calendar" ? "is-active" : ""}`}
          onClick={() => setActiveTab("calendar")}
        >
          Publish Calendar ({calendarItems.length})
        </button>
        <button
          type="button"
          className={`ui-subnav-link ${activeTab === "active" ? "is-active" : ""}`}
          onClick={() => setActiveTab("active")}
        >
          Active In-Flight ({activeItems.length})
        </button>
        <button
          type="button"
          className={`ui-subnav-link ${activeTab === "recovery" ? "is-active" : ""}`}
          onClick={() => setActiveTab("recovery")}
        >
          Recovery & Holds ({holdItems.length + retryItems.length + deadLetterItems.length})
        </button>
        <button
          type="button"
          className={`ui-subnav-link ${activeTab === "history" ? "is-active" : ""}`}
          onClick={() => setActiveTab("history")}
        >
          Publication History ({historyItems.length})
        </button>
      </div>

      {loading && <LoadingState title="Refreshing operations data..." />}

      {/* ── TAB 1: OVERVIEW ── */}
      {!loading && activeTab === "overview" && (
        <div className="ui-page-stack">
          <PageSection
            title="In-Flight Publisher Fleet"
            description="Active background jobs executing against the omega-publisher queue."
          >
            {activeItems.length === 0 ? (
              <EmptyState title="No active publishers running" description="All queues idle or awaiting due schedule dispatch." />
            ) : (
              <div className="table-container ui-table-card">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Title / Intent</th>
                      <th>Channel</th>
                      <th>Attempt State</th>
                      <th>Upload Progress</th>
                      <th>Started</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activeItems.map((item) => {
                      const badge = getAttemptStateBadge(item.attempt_state);
                      const prog = calculateProgress(item.bytes_uploaded, item.total_bytes);
                      return (
                        <tr key={item.attempt_id}>
                          <td>
                            <div style={{ fontWeight: 600 }}>{item.title}</div>
                            <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                              Attempt #{item.attempt_number} · Intent: {item.publish_intent_id.slice(0, 8)}…
                            </div>
                          </td>
                          <td>{item.channel_name || item.channel_id.slice(0, 8)}</td>
                          <td>
                            <StatusBadge tone={badge.tone}>{badge.label}</StatusBadge>
                          </td>
                          <td style={{ minWidth: "160px" }}>
                            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", marginBottom: "0.25rem" }}>
                              <span>{prog.percentage}%</span>
                              <span>{formatBytes(item.bytes_uploaded)}</span>
                            </div>
                            <div style={{ height: "6px", backgroundColor: "var(--color-surface-hover)", borderRadius: "3px", overflow: "hidden" }}>
                              <div
                                style={{
                                  height: "100%",
                                  width: `${prog.percentage}%`,
                                  backgroundColor: "var(--color-primary)",
                                  transition: "width 0.3s ease",
                                }}
                              />
                            </div>
                          </td>
                          <td style={{ fontSize: "0.85rem" }}>{new Date(item.started_at).toLocaleTimeString()}</td>
                          <td>
                            <button
                              type="button"
                              className="btn btn-secondary btn-sm"
                              onClick={() => void inspectPublication(item.publish_intent_id)}
                            >
                              Inspect
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </PageSection>

          {/* Recovery Queue Snapshot */}
          <PageSection
            title="Recovery Actions Required"
            description="Items blocked by ambiguous outcomes, exhausted retries, or rate limit holds."
          >
            <div className="ui-panel-grid">
              <div className="ui-card" style={{ padding: "1.25rem" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <h4 style={{ margin: 0 }}>Manual Holds</h4>
                  <span className="badge">{holdItems.length}</span>
                </div>
                <p style={{ fontSize: "0.85rem", color: "var(--color-text-muted)", margin: "0.5rem 0 1rem" }}>
                  Ambiguous uploads requiring explicit operator reconciliation.
                </p>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => {
                    setActiveTab("recovery");
                    setRecoverySubTab("holds");
                  }}
                >
                  Manage Holds ({holdItems.length})
                </button>
              </div>

              <div className="ui-card" style={{ padding: "1.25rem" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <h4 style={{ margin: 0 }}>Retry Queue</h4>
                  <span className="badge">{retryItems.length}</span>
                </div>
                <p style={{ fontSize: "0.85rem", color: "var(--color-text-muted)", margin: "0.5rem 0 1rem" }}>
                  Outbox handoffs with scheduled backoff retry times.
                </p>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => {
                    setActiveTab("recovery");
                    setRecoverySubTab("retries");
                  }}
                >
                  View Retries ({retryItems.length})
                </button>
              </div>

              <div className="ui-card" style={{ padding: "1.25rem" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <h4 style={{ margin: 0 }}>Dead Letters</h4>
                  <span className="badge">{deadLetterItems.length}</span>
                </div>
                <p style={{ fontSize: "0.85rem", color: "var(--color-text-muted)", margin: "0.5rem 0 1rem" }}>
                  Handoffs that exhausted maximum attempts and need operator decision.
                </p>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => {
                    setActiveTab("recovery");
                    setRecoverySubTab("dead_letters");
                  }}
                >
                  Inspect Dead Letters ({deadLetterItems.length})
                </button>
              </div>
            </div>
          </PageSection>
        </div>
      )}

      {/* ── TAB 2: CALENDAR ── */}
      {!loading && activeTab === "calendar" && (
        <PageSection
          title="Publish Calendar Schedule"
          description="Authoritative OMEGA-010 schedule reservations linked to approved PublishIntents."
        >
          {calendarItems.length === 0 ? (
            <EmptyState title="No scheduled publications found" description="Schedule an approved publish intent from the Publish Studio." />
          ) : (
            <div className="table-container ui-table-card">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Title / Publish Intent</th>
                    <th>Channel</th>
                    <th>Scheduled Slot</th>
                    <th>State</th>
                    <th>Priority</th>
                    <th>Due Status</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {calendarItems.map((item) => (
                    <tr key={item.reservation_id}>
                      <td>
                        <div style={{ fontWeight: 600 }}>{item.title}</div>
                        <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                          Intent: {item.publish_intent_id.slice(0, 8)}… · Res: {item.reservation_id.slice(0, 8)}…
                        </div>
                      </td>
                      <td>{item.channel_name || item.channel_id.slice(0, 8)}</td>
                      <td style={{ fontSize: "0.85rem" }}>
                        <div>{new Date(item.scheduled_start_at).toLocaleString()}</div>
                        <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                          to {new Date(item.scheduled_end_at).toLocaleTimeString()}
                        </div>
                      </td>
                      <td>
                        <StatusBadge tone={item.reservation_state === "ACTIVE" ? "info" : "neutral"}>
                          {item.reservation_state}
                        </StatusBadge>
                      </td>
                      <td>{item.priority_score.toFixed(1)}</td>
                      <td>
                        {item.is_due ? (
                          <StatusBadge tone="warning">DUE DISPATCH</StatusBadge>
                        ) : (
                          <StatusBadge tone="neutral">Upcoming</StatusBadge>
                        )}
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => void inspectPublication(item.publish_intent_id)}
                        >
                          Inspect
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </PageSection>
      )}

      {/* ── TAB 3: ACTIVE IN-FLIGHT ── */}
      {!loading && activeTab === "active" && (
        <PageSection
          title="Active In-Flight Publishing Jobs"
          description="Publisher worker tasks currently running against external upload sessions."
        >
          {activeItems.length === 0 ? (
            <EmptyState title="No active publications" description="No publisher jobs are currently running." />
          ) : (
            <div className="table-container ui-table-card">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Channel</th>
                    <th>Platform</th>
                    <th>State</th>
                    <th>Progress</th>
                    <th>Started</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {activeItems.map((item) => {
                    const badge = getAttemptStateBadge(item.attempt_state);
                    const prog = calculateProgress(item.bytes_uploaded, item.total_bytes);
                    return (
                      <tr key={item.attempt_id}>
                        <td>
                          <div style={{ fontWeight: 600 }}>{item.title}</div>
                          <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                            Attempt #{item.attempt_number} · ID: {item.attempt_id.slice(0, 8)}…
                          </div>
                        </td>
                        <td>{item.channel_name || item.channel_id.slice(0, 8)}</td>
                        <td>{item.platform}</td>
                        <td>
                          <StatusBadge tone={badge.tone}>{badge.label}</StatusBadge>
                        </td>
                        <td style={{ minWidth: "160px" }}>
                          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", marginBottom: "0.25rem" }}>
                            <span>{prog.percentage}%</span>
                            <span>{prog.label}</span>
                          </div>
                          <div style={{ height: "6px", backgroundColor: "var(--color-surface-hover)", borderRadius: "3px", overflow: "hidden" }}>
                            <div
                              style={{
                                height: "100%",
                                width: `${prog.percentage}%`,
                                backgroundColor: "var(--color-primary)",
                                transition: "width 0.3s ease",
                              }}
                            />
                          </div>
                        </td>
                        <td style={{ fontSize: "0.85rem" }}>{new Date(item.started_at).toLocaleTimeString()}</td>
                        <td>
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            onClick={() => void inspectPublication(item.publish_intent_id)}
                          >
                            Inspect
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </PageSection>
      )}

      {/* ── TAB 4: RECOVERY & HOLDS ── */}
      {!loading && activeTab === "recovery" && (
        <div className="ui-page-stack">
          <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem" }}>
            <button
              type="button"
              className={`btn btn-sm ${recoverySubTab === "holds" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setRecoverySubTab("holds")}
            >
              Manual Holds ({holdItems.length})
            </button>
            <button
              type="button"
              className={`btn btn-sm ${recoverySubTab === "retries" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setRecoverySubTab("retries")}
            >
              Retry Queue ({retryItems.length})
            </button>
            <button
              type="button"
              className={`btn btn-sm ${recoverySubTab === "dead_letters" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setRecoverySubTab("dead_letters")}
            >
              Dead Letters ({deadLetterItems.length})
            </button>
          </div>

          {/* Subtab: Manual Holds */}
          {recoverySubTab === "holds" && (
            <PageSection
              title="Provider Recovery Manual Holds"
              description="Ambiguous publishing outcomes quarantined to prevent duplicate uploads. Requires explicit operator reconciliation."
            >
              {holdItems.length === 0 ? (
                <EmptyState title="No manual holds active" description="All publishing attempts resolved with clean definitive outcomes." />
              ) : (
                <div className="table-container ui-table-card">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Title / Intent</th>
                        <th>Channel</th>
                        <th>Hold Source</th>
                        <th>Age</th>
                        <th>Last Error (Sanitized)</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {holdItems.map((item) => {
                        const holdBadge = getHoldSourceBadge(item.hold_source);
                        return (
                          <tr key={item.attempt_id}>
                            <td>
                              <div style={{ fontWeight: 600 }}>{item.title}</div>
                              <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                                Attempt #{item.attempt_number} · Attempt ID: {item.attempt_id.slice(0, 8)}…
                              </div>
                            </td>
                            <td>{item.channel_name || item.channel_id.slice(0, 8)}</td>
                            <td>
                              <StatusBadge tone={holdBadge.tone}>{holdBadge.label}</StatusBadge>
                            </td>
                            <td style={{ fontSize: "0.85rem" }}>{formatDuration(item.age_seconds)}</td>
                            <td style={{ maxWidth: "300px", fontSize: "0.8rem", color: "var(--color-text-muted)" }}>
                              {item.last_sanitized_error || "Outcome ambiguous; verification required"}
                            </td>
                            <td>
                              <div style={{ display: "flex", gap: "0.5rem" }}>
                                <button
                                  type="button"
                                  className="btn btn-danger btn-sm"
                                  onClick={() => setReconcileTarget(item)}
                                >
                                  Reconcile Provider
                                </button>
                                <button
                                  type="button"
                                  className="btn btn-secondary btn-sm"
                                  onClick={() => void inspectPublication(item.publish_intent_id)}
                                >
                                  Inspect
                                </button>
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </PageSection>
          )}

          {/* Subtab: Retries */}
          {recoverySubTab === "retries" && (
            <PageSection
              title="Scheduler Handoff Retry Queue"
              description="Outbox queue items with configured backoff schedules awaiting worker pickup."
            >
              {retryItems.length === 0 ? (
                <EmptyState title="No pending retries" description="Retry queue is clear." />
              ) : (
                <div className="table-container ui-table-card">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Title / Intent</th>
                        <th>Channel</th>
                        <th>Status</th>
                        <th>Attempts</th>
                        <th>Next Retry</th>
                        <th>Last Error (Sanitized)</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {retryItems.map((item) => (
                        <tr key={item.handoff_id}>
                          <td>
                            <div style={{ fontWeight: 600 }}>{item.title}</div>
                            <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                              Handoff: {item.handoff_id.slice(0, 8)}…
                            </div>
                          </td>
                          <td>{item.channel_name || item.channel_id.slice(0, 8)}</td>
                          <td>
                            <StatusBadge tone={item.status === "CLAIMED" ? "info" : "neutral"}>
                              {item.status}
                            </StatusBadge>
                          </td>
                          <td>
                            {item.attempt_count} / {item.max_attempts}
                          </td>
                          <td style={{ fontSize: "0.85rem" }}>
                            {item.next_retry_time ? new Date(item.next_retry_time).toLocaleTimeString() : "Immediate"}
                          </td>
                          <td style={{ maxWidth: "300px", fontSize: "0.8rem", color: "var(--color-text-muted)" }}>
                            {item.last_sanitized_error || "-"}
                          </td>
                          <td>
                            <button
                              type="button"
                              className="btn btn-secondary btn-sm"
                              onClick={() => void inspectPublication(item.publish_intent_id)}
                            >
                              Inspect
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </PageSection>
          )}

          {/* Subtab: Dead Letters */}
          {recoverySubTab === "dead_letters" && (
            <PageSection
              title="Dead Letter Handoffs"
              description="Outbox records that reached max attempts without success. Operators may inspect and perform controlled requeue."
            >
              {deadLetterItems.length === 0 ? (
                <EmptyState title="No dead letters" description="All handoffs succeeded or remain within active retry limits." />
              ) : (
                <div className="table-container ui-table-card">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Title / Intent</th>
                        <th>Channel</th>
                        <th>Attempts</th>
                        <th>Created</th>
                        <th>Last Error (Sanitized)</th>
                        <th>Eligibility</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {deadLetterItems.map((item) => (
                        <tr key={item.handoff_id}>
                          <td>
                            <div style={{ fontWeight: 600 }}>{item.title}</div>
                            <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                              Handoff: {item.handoff_id.slice(0, 8)}…
                            </div>
                          </td>
                          <td>{item.channel_name || item.channel_id.slice(0, 8)}</td>
                          <td>
                            {item.attempt_count} / {item.max_attempts}
                          </td>
                          <td style={{ fontSize: "0.85rem" }}>{new Date(item.created_at).toLocaleString()}</td>
                          <td style={{ maxWidth: "300px", fontSize: "0.8rem", color: "var(--color-text-muted)" }}>
                            {item.last_sanitized_error || "Max attempts exceeded"}
                          </td>
                          <td>
                            {item.can_requeue ? (
                              <StatusBadge tone="success">Requeue Allowed</StatusBadge>
                            ) : (
                              <StatusBadge tone="danger">Blocked: {item.requeue_blocked_reason}</StatusBadge>
                            )}
                          </td>
                          <td>
                            <div style={{ display: "flex", gap: "0.5rem" }}>
                              <button
                                type="button"
                                className="btn btn-primary btn-sm"
                                disabled={!item.can_requeue}
                                onClick={() => setRequeueTarget(item)}
                              >
                                Controlled Requeue
                              </button>
                              <button
                                type="button"
                                className="btn btn-secondary btn-sm"
                                onClick={() => void inspectPublication(item.publish_intent_id)}
                              >
                                Inspect
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </PageSection>
          )}
        </div>
      )}

      {/* ── TAB 5: PUBLICATION HISTORY ── */}
      {!loading && activeTab === "history" && (
        <PageSection
          title="Publication History"
          description="Audit trail of completed, failed, and historical publishing attempts."
        >
          {/* History Filters */}
          <div style={{ display: "flex", gap: "1rem", marginBottom: "1rem", flexWrap: "wrap" }}>
            <input
              type="text"
              className="input"
              placeholder="Search by title or video ID…"
              value={historySearch}
              onChange={(e) => setHistorySearch(e.target.value)}
              style={{ maxWidth: "300px" }}
            />
            <select
              className="input"
              value={historyStateFilter}
              onChange={(e) => setHistoryStateFilter(e.target.value)}
              style={{ maxWidth: "200px" }}
            >
              <option value="">All States</option>
              <option value="SUCCEEDED">SUCCEEDED</option>
              <option value="PERMANENT_FAILED">PERMANENT_FAILED</option>
              <option value="RETRYABLE_FAILED">RETRYABLE_FAILED</option>
              <option value="UNKNOWN">UNKNOWN</option>
            </select>
          </div>

          {historyItems.length === 0 ? (
            <EmptyState title="No history found" description="No publication attempts match the current criteria." />
          ) : (
            <div className="table-container ui-table-card">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Channel</th>
                    <th>State</th>
                    <th>Provider Video ID</th>
                    <th>Duration</th>
                    <th>Completed At</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {historyItems.map((item) => {
                    const badge = getAttemptStateBadge(item.state);
                    return (
                      <tr key={item.attempt_id}>
                        <td>
                          <div style={{ fontWeight: 600 }}>{item.title}</div>
                          <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                            Attempt #{item.attempt_number} · Intent: {item.publish_intent_id.slice(0, 8)}…
                          </div>
                        </td>
                        <td>{item.channel_name || item.channel_id.slice(0, 8)}</td>
                        <td>
                          <StatusBadge tone={badge.tone}>{badge.label}</StatusBadge>
                        </td>
                        <td>
                          {item.provider_video_id ? (
                            <a
                              href={item.provider_url || `https://youtu.be/${item.provider_video_id}`}
                              target="_blank"
                              rel="noreferrer"
                              style={{ color: "var(--color-primary-text)", textDecoration: "underline" }}
                            >
                              {item.provider_video_id}
                            </a>
                          ) : (
                            <span style={{ color: "var(--color-text-muted)" }}>-</span>
                          )}
                        </td>
                        <td style={{ fontSize: "0.85rem" }}>{formatDuration(item.duration_seconds)}</td>
                        <td style={{ fontSize: "0.85rem" }}>
                          {item.completed_at ? new Date(item.completed_at).toLocaleString() : "-"}
                        </td>
                        <td>
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            onClick={() => void inspectPublication(item.publish_intent_id)}
                          >
                            Inspect
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </PageSection>
      )}

      {/* ── MODAL: EXPLICIT PROVIDER RECONCILIATION ── */}
      {reconcileTarget && (
        <Dialog
          open={Boolean(reconcileTarget)}
          title="Explicit External Provider Reconciliation"
          description="Manually query the external provider to verify delivery of an ambiguous upload attempt."
          onClose={() => setReconcileTarget(null)}
          actions={
            <>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setReconcileTarget(null)}
                disabled={actionBusy}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={() => void handleExecuteReconciliation()}
                disabled={actionBusy}
              >
                {actionBusy ? "Reconciling..." : "Execute Provider Reconciliation"}
              </button>
            </>
          }
        >
          <div className="ui-page-stack" style={{ gap: "1rem" }}>
            <Alert tone="warning" title="EXTERNAL NETWORK OPERATION">
              This action will initiate an outbound API call to the video provider to query the status of the
              underlying resumable upload session. It must never run automatically.
            </Alert>

            <div>
              <div style={{ fontSize: "0.85rem", fontWeight: 600 }}>Target Publication:</div>
              <div style={{ fontSize: "0.95rem" }}>{reconcileTarget.title}</div>
              <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                Attempt ID: {reconcileTarget.attempt_id}
              </div>
            </div>

            <div>
              <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, marginBottom: "0.25rem" }}>
                Operator Username / Actor ID: *
              </label>
              <input
                type="text"
                className="input"
                value={operatorActor}
                onChange={(e) => setOperatorActor(e.target.value)}
                placeholder="e.g. OperatorJane"
                disabled={actionBusy}
              />
            </div>

            <div>
              <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, marginBottom: "0.25rem" }}>
                Operational Reason / Justification: *
              </label>
              <textarea
                className="input"
                rows={3}
                value={operatorReason}
                onChange={(e) => setOperatorReason(e.target.value)}
                placeholder="Explain why manual reconciliation is required..."
                disabled={actionBusy}
              />
            </div>
          </div>
        </Dialog>
      )}

      {/* ── MODAL: DEAD LETTER REQUEUE ── */}
      {requeueTarget && (
        <Dialog
          open={Boolean(requeueTarget)}
          title="Controlled Dead Letter Requeue"
          description="Requeue an exhausted outbox handoff record back into PENDING state."
          onClose={() => setRequeueTarget(null)}
          actions={
            <>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setRequeueTarget(null)}
                disabled={actionBusy}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void handleExecuteRequeue()}
                disabled={actionBusy}
              >
                {actionBusy ? "Requeueing..." : "Requeue Handoff"}
              </button>
            </>
          }
        >
          <div className="ui-page-stack" style={{ gap: "1rem" }}>
            <p style={{ fontSize: "0.9rem" }}>
              Requeueing resets the handoff attempt count to 0 and clears the dead letter state. The scheduler
              dispatch will become eligible for immediate execution.
            </p>

            <div>
              <div style={{ fontSize: "0.85rem", fontWeight: 600 }}>Target Publication:</div>
              <div style={{ fontSize: "0.95rem" }}>{requeueTarget.title}</div>
              <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                Handoff ID: {requeueTarget.handoff_id}
              </div>
            </div>

            <div>
              <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, marginBottom: "0.25rem" }}>
                Operator Actor: *
              </label>
              <input
                type="text"
                className="input"
                value={operatorActor}
                onChange={(e) => setOperatorActor(e.target.value)}
                disabled={actionBusy}
              />
            </div>

            <div>
              <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, marginBottom: "0.25rem" }}>
                Approval Justification / Reason: *
              </label>
              <textarea
                className="input"
                rows={3}
                value={operatorReason}
                onChange={(e) => setOperatorReason(e.target.value)}
                placeholder="Reason for approving retry after dead-letter exhaustion..."
                disabled={actionBusy}
              />
            </div>
          </div>
        </Dialog>
      )}

      {/* ── MODAL: AUTHORIZE RESUME ── */}
      {resumeTarget && (
        <Dialog
          open={Boolean(resumeTarget)}
          title="Authorize Existing-Session Resume"
          description="Authorize resuming an existing resumable upload session at verified provider offset."
          onClose={() => setResumeTarget(null)}
          actions={
            <>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setResumeTarget(null)}
                disabled={actionBusy}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void handleAuthorizeResume()}
                disabled={actionBusy}
              >
                {actionBusy ? "Authorizing..." : "Authorize Existing-Session Resume"}
              </button>
            </>
          }
        >
          <div className="ui-page-stack" style={{ gap: "1rem" }}>
            <p style={{ fontSize: "0.9rem" }}>
              This operation verifies the persisted session and records authorization metadata. It will NOT start
              transmitting bytes or call provider APIs directly.
            </p>

            <div>
              <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, marginBottom: "0.25rem" }}>
                Operator Actor: *
              </label>
              <input
                type="text"
                className="input"
                value={operatorActor}
                onChange={(e) => setOperatorActor(e.target.value)}
                disabled={actionBusy}
              />
            </div>

            <div>
              <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, marginBottom: "0.25rem" }}>
                Operational Reason: *
              </label>
              <textarea
                className="input"
                rows={3}
                value={operatorReason}
                onChange={(e) => setOperatorReason(e.target.value)}
                placeholder="Reason for resuming existing session..."
                disabled={actionBusy}
              />
            </div>
          </div>
        </Dialog>
      )}

      {/* ── DRAWER: PUBLICATION DETAIL ── */}
      {selectedIntentId && (
        <Dialog
          open={Boolean(selectedIntentId)}
          title="Publication Operational Detail"
          description="Detailed projection of intent state, upload sessions, provider video, and recovery eligibility."
          onClose={() => {
            setSelectedIntentId(null);
            setDetailData(null);
          }}
        >
          {loadingDetail ? (
            <LoadingState title="Inspecting publication detail..." />
          ) : !detailData ? (
            <EmptyState title="Publication not found" description="Unable to locate projection details." />
          ) : (
            <div className="ui-page-stack" style={{ gap: "1.25rem" }}>
              {/* Core Attributes */}
              <div>
                <h4 style={{ margin: "0 0 0.5rem 0" }}>{detailData.title}</h4>
                <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", fontSize: "0.85rem" }}>
                  <div>
                    <span style={{ color: "var(--color-text-muted)" }}>Channel: </span>
                    {detailData.channel_name || detailData.channel_id}
                  </div>
                  <div>
                    <span style={{ color: "var(--color-text-muted)" }}>State: </span>
                    <StatusBadge tone={detailData.intent_state === "PUBLISHED" ? "success" : "info"}>
                      {detailData.intent_state}
                    </StatusBadge>
                  </div>
                  <div>
                    <span style={{ color: "var(--color-text-muted)" }}>Privacy: </span>
                    {detailData.requested_privacy_status}
                  </div>
                </div>
              </div>

              {/* Upload Session Info (No session_uri) */}
              {detailData.upload_session && (
                <div className="ui-card" style={{ padding: "1rem" }}>
                  <div style={{ fontSize: "0.8rem", textTransform: "uppercase", color: "var(--color-text-muted)" }}>
                    Persisted Upload Session (Credentials Redacted)
                  </div>
                  <div style={{ marginTop: "0.5rem" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.85rem" }}>
                      <span>
                        Progress: {detailData.upload_session.progress_percentage}% (
                        {formatBytes(detailData.upload_session.bytes_uploaded)} /{" "}
                        {formatBytes(detailData.upload_session.total_bytes)})
                      </span>
                      <span>Chunk Size: {formatBytes(detailData.upload_session.chunk_size_bytes)}</span>
                    </div>
                    <div
                      style={{
                        height: "6px",
                        backgroundColor: "var(--color-surface-hover)",
                        borderRadius: "3px",
                        overflow: "hidden",
                        marginTop: "0.25rem",
                      }}
                    >
                      <div
                        style={{
                          height: "100%",
                          width: `${detailData.upload_session.progress_percentage}%`,
                          backgroundColor: "var(--color-primary)",
                        }}
                      />
                    </div>
                  </div>
                </div>
              )}

              {/* Provider Video */}
              {detailData.provider.video_id && (
                <div className="ui-card" style={{ padding: "1rem" }}>
                  <div style={{ fontSize: "0.8rem", textTransform: "uppercase", color: "var(--color-text-muted)" }}>
                    Provider Video Metadata
                  </div>
                  <div style={{ marginTop: "0.25rem", fontSize: "0.9rem" }}>
                    Video ID: <code>{detailData.provider.video_id}</code>
                  </div>
                  {detailData.provider.url && (
                    <div style={{ marginTop: "0.25rem", fontSize: "0.85rem" }}>
                      URL:{" "}
                      <a
                        href={detailData.provider.url}
                        target="_blank"
                        rel="noreferrer"
                        style={{ color: "var(--color-primary-text)", textDecoration: "underline" }}
                      >
                        {detailData.provider.url}
                      </a>
                    </div>
                  )}
                </div>
              )}

              {/* Recovery Eligibility from Backend */}
              <div className="ui-card" style={{ padding: "1rem" }}>
                <div style={{ fontSize: "0.8rem", textTransform: "uppercase", color: "var(--color-text-muted)" }}>
                  Action Eligibility (Authoritative Backend Contract)
                </div>
                <div style={{ marginTop: "0.5rem" }}>
                  <div style={{ fontSize: "0.85rem", fontWeight: 600, marginBottom: "0.25rem" }}>
                    Allowed Operations:
                  </div>
                  {detailData.recovery_eligibility.allowed_operations.length === 0 ? (
                    <span style={{ fontSize: "0.85rem", color: "var(--color-text-muted)" }}>None</span>
                  ) : (
                    <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                      {detailData.recovery_eligibility.allowed_operations.map((op) => (
                        <StatusBadge key={op} tone="success">
                          {op}
                        </StatusBadge>
                      ))}
                    </div>
                  )}

                  {Object.keys(detailData.recovery_eligibility.blocked_operations).length > 0 && (
                    <div style={{ marginTop: "0.75rem" }}>
                      <div style={{ fontSize: "0.85rem", fontWeight: 600, marginBottom: "0.25rem" }}>
                        Blocked Operations:
                      </div>
                      <ul style={{ margin: 0, paddingLeft: "1.25rem", fontSize: "0.8rem", color: "var(--color-danger-text)" }}>
                        {Object.entries(detailData.recovery_eligibility.blocked_operations).map(([op, reason]) => (
                          <li key={op}>
                            <strong>{op}:</strong> {reason}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>

                {/* Direct Action Buttons in Drawer */}
                <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem" }}>
                  {detailData.recovery_eligibility.allowed_operations.includes(
                    "AUTHORIZE_EXISTING_SESSION_RESUME"
                  ) &&
                    detailData.active_attempt && (
                      <button
                        type="button"
                        className="btn btn-primary btn-sm"
                        onClick={() =>
                          setResumeTarget({
                            attemptId: detailData.active_attempt!.attempt_id,
                            intentId: detailData.intent_id,
                          })
                        }
                      >
                        Authorize existing-session resume
                      </button>
                    )}

                  {detailData.recovery_eligibility.allowed_operations.includes(
                    "EXPLICIT_EXTERNAL_RECONCILIATION"
                  ) &&
                    detailData.active_attempt && (
                      <button
                        type="button"
                        className="btn btn-danger btn-sm"
                        onClick={() =>
                          setReconcileTarget({
                            attempt_id: detailData.active_attempt!.attempt_id,
                            publish_intent_id: detailData.intent_id,
                            attempt_number: detailData.active_attempt!.attempt_number,
                            attempt_state: detailData.active_attempt!.state,
                            error_category: detailData.active_attempt!.error_category,
                            reconciliation_status: detailData.active_attempt!.reconciliation_status,
                            last_sanitized_error: detailData.active_attempt!.last_sanitized_error,
                            started_at: detailData.active_attempt!.started_at,
                            age_seconds: null,
                            title: detailData.title,
                            channel_id: detailData.channel_id,
                            channel_name: detailData.channel_name,
                            hold_source: "RECOVERY",
                          })
                        }
                      >
                        Reconcile Provider
                      </button>
                    )}
                </div>
              </div>

              {/* Attempt History */}
              <div>
                <h5 style={{ margin: "0 0 0.5rem 0" }}>Attempt History</h5>
                <div className="table-container ui-table-card">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>State</th>
                        <th>Started</th>
                        <th>Completed</th>
                        <th>Error (Sanitized)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detailData.attempts_history.map((att) => {
                        const b = getAttemptStateBadge(att.state);
                        return (
                          <tr key={att.attempt_id}>
                            <td>{att.attempt_number}</td>
                            <td>
                              <StatusBadge tone={b.tone}>{b.label}</StatusBadge>
                            </td>
                            <td style={{ fontSize: "0.8rem" }}>{new Date(att.started_at).toLocaleTimeString()}</td>
                            <td style={{ fontSize: "0.8rem" }}>
                              {att.completed_at ? new Date(att.completed_at).toLocaleTimeString() : "-"}
                            </td>
                            <td style={{ fontSize: "0.8rem", color: "var(--color-text-muted)" }}>
                              {att.last_sanitized_error || "-"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </Dialog>
      )}
    </div>
  );
}
