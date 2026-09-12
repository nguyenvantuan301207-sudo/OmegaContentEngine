"use client";

import { use, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  approveTask,
  cancelMission,
  getMission,
  getMissionDecisions,
  getMissionTasks,
  pauseMission,
  planMission,
  rejectTask,
  resumeMission,
  startMission,
  type DecisionLog,
  type Mission,
  type Task,
} from "@/lib/api";
import { GuardianPanel } from "@/components/guardian/GuardianPanel";
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
  type StatusTone,
} from "@/components/ui";
import { WorkflowProgress, type WorkflowProgressStep } from "@/components/workflow/WorkflowPrimitives";

function stateTone(state: string): StatusTone {
  if (state === "SUCCEEDED") return "success";
  if (["READY", "QUEUED", "RUNNING"].includes(state)) return "info";
  if (["WAITING_APPROVAL", "PAUSED"].includes(state)) return "warning";
  if (["FAILED", "BLOCKED", "CANCELLED"].includes(state)) return "danger";
  return "neutral";
}

function taskDependencies(task: Task): string[] {
  const candidate = task.input?.depends_on ?? task.input?.dependency_ids;
  return Array.isArray(candidate) ? candidate.filter((value): value is string => typeof value === "string") : [];
}

export default function MissionDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: missionId } = use(params);
  const [mission, setMission] = useState<Mission | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [decisions, setDecisions] = useState<DecisionLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [approvalTask, setApprovalTask] = useState<Task | null>(null);
  const [rejectTaskRecord, setRejectTaskRecord] = useState<Task | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  const loadData = useCallback(async () => {
    try {
      const [missionRecord, taskRecords, decisionRecords] = await Promise.all([
        getMission(missionId),
        getMissionTasks(missionId),
        getMissionDecisions(missionId),
      ]);
      setMission(missionRecord);
      setTasks(taskRecords);
      setDecisions(decisionRecords);
      setError(null);
    } catch (requestError: unknown) {
      setError(requestError instanceof Error ? requestError.message : "Failed to load mission.");
    } finally {
      setLoading(false);
    }
  }, [missionId]);

  useEffect(() => {
    void loadData();
    const interval = window.setInterval(() => void loadData(), 3000);
    return () => window.clearInterval(interval);
  }, [loadData]);

  const runAction = async (action: () => Promise<unknown>) => {
    setActionLoading(true);
    setError(null);
    try {
      await action();
      await loadData();
    } catch (requestError: unknown) {
      setError(requestError instanceof Error ? requestError.message : "Mission action failed.");
    } finally {
      setActionLoading(false);
    }
  };

  const executionIds = useMemo(() => [...new Set(tasks.map((task) => task.execution_id).filter((id): id is string => Boolean(id)))], [tasks]);
  const completedTasks = tasks.filter((task) => task.state === "SUCCEEDED").length;
  const taskProgress: WorkflowProgressStep[] = tasks.map((task) => ({
    label: task.task_type.replaceAll("_", " "),
    state: task.state === "SUCCEEDED"
      ? "complete"
      : ["FAILED", "BLOCKED", "CANCELLED"].includes(task.state)
        ? "failed"
        : ["RUNNING", "QUEUED", "WAITING_APPROVAL"].includes(task.state)
          ? "current"
          : "pending",
    detail: task.state.replaceAll("_", " "),
  }));

  if (loading && !mission) return <LoadingState title="Loading mission" description={missionId} />;
  if (!mission) {
    return (
      <ErrorState
        title="Mission unavailable"
        description={error || "The mission does not exist or could not be loaded."}
        action={<Link href="/missions" className="btn btn-secondary">Back to missions</Link>}
      />
    );
  }

  return (
    <div className="workflow-page ui-page-stack mission-workspace">
      <PageHeader
        eyebrow="Mission detail"
        title={mission.title}
        description={mission.objective}
        actions={
          <>
            <Link href="/missions" className="btn btn-secondary">Back</Link>
            {mission.state === "DRAFT" && <button type="button" className="btn btn-primary" disabled={actionLoading} onClick={() => void runAction(() => planMission(mission.id))}>Plan workflow</button>}
            {mission.state === "READY" && <button type="button" className="btn btn-primary" disabled={actionLoading} onClick={() => void runAction(() => startMission(mission.id))}>Start execution</button>}
            {mission.state === "RUNNING" && <button type="button" className="btn btn-secondary" disabled={actionLoading} onClick={() => void runAction(() => pauseMission(mission.id))}>Pause</button>}
            {mission.state === "PAUSED" && <button type="button" className="btn btn-primary" disabled={actionLoading} onClick={() => void runAction(() => resumeMission(mission.id))}>Resume</button>}
            {["READY", "RUNNING", "PAUSED"].includes(mission.state) && <button type="button" className="btn btn-danger" disabled={actionLoading} onClick={() => setCancelOpen(true)}>Cancel mission</button>}
          </>
        }
      />

      {error && <Alert tone="danger" title="Mission operation failed">{error}</Alert>}

      <section className="ui-mission-summary" aria-label="Mission summary">
        <div><span>State</span><StatusBadge tone={stateTone(mission.state)}>{mission.state}</StatusBadge></div>
        <div><span>Tasks</span><strong>{completedTasks} of {tasks.length} succeeded</strong></div>
        <div><span>Autonomy</span><strong>{mission.autonomy_level.replaceAll("_", " ")}</strong></div>
        <div><span>Priority</span><strong>{mission.priority}</strong></div>
        <div><span>Created</span><time dateTime={mission.created_at}>{new Date(mission.created_at).toLocaleString()}</time></div>
        <div><span>Updated</span><time dateTime={mission.updated_at}>{new Date(mission.updated_at).toLocaleString()}</time></div>
      </section>

      {taskProgress.length ? <WorkflowProgress label="Mission stage progression" steps={taskProgress} /> : null}

      {mission.description && <Alert title="Mission context">{mission.description}</Alert>}

      <PageSection title="Safety and approvals" description="Guardian checks and operator controls for this mission.">
        <GuardianPanel missionId={mission.id} isPaused={mission.state === "PAUSED"} onStateChanged={loadData} />
      </PageSection>

      <div className="ui-mission-layout">
        <PageSection
          title="Workflow"
          description={executionIds.length ? `Execution ${executionIds.join(", ")} · refreshing every 3 seconds` : "No execution has been associated with these tasks."}
        >
          {tasks.length === 0 ? (
            <EmptyState title="No tasks planned" description="Plan the mission to create its persisted workflow." />
          ) : (
            <ol className="ui-workflow" aria-label="Mission task progression">
              {tasks.map((task, index) => {
                const dependencies = taskDependencies(task);
                return (
                  <li className="ui-workflow-item" key={task.id}>
                    <div className="ui-workflow-marker" aria-hidden="true">{index + 1}</div>
                    <article className="card ui-task-card">
                      <header className="ui-task-header">
                        <div>
                          <span className="ui-eyebrow">{task.task_type.replaceAll("_", " ")}</span>
                          <h3>{task.title}</h3>
                        </div>
                        <StatusBadge tone={stateTone(task.state)}>{task.state}</StatusBadge>
                      </header>
                      {task.description && <p className="ui-card-copy">{task.description}</p>}
                      {dependencies.length > 0 && <p className="ui-task-dependencies"><strong>Depends on:</strong> {dependencies.join(", ")}</p>}
                      {task.state === "WAITING_APPROVAL" && (
                        <Alert
                          tone="warning"
                          title="Operator approval required"
                          actions={
                            <>
                              <button type="button" className="btn btn-secondary btn-sm" disabled={actionLoading} onClick={() => { setRejectReason(""); setRejectTaskRecord(task); }}>Reject</button>
                              <button type="button" className="btn btn-primary btn-sm" disabled={actionLoading} onClick={() => setApprovalTask(task)}>Approve</button>
                            </>
                          }
                        >
                          Review the task evidence before continuing the workflow.
                        </Alert>
                      )}
                      {task.error && <Alert tone="danger" title="Task failure">{task.error}</Alert>}
                      <div className="ui-task-meta">
                        <span className="text-mono">{task.id}</span>
                        <span>Retries {task.retry_count}/{task.max_retries}</span>
                        {task.started_at && <time dateTime={task.started_at}>Started {new Date(task.started_at).toLocaleString()}</time>}
                        {task.completed_at && <time dateTime={task.completed_at}>Completed {new Date(task.completed_at).toLocaleString()}</time>}
                      </div>
                      {(task.input || task.output) && (
                        <details className="ui-technical-details">
                          <summary>Technical details</summary>
                          {task.input && <div><strong>Input</strong><pre>{JSON.stringify(task.input, null, 2)}</pre></div>}
                          {task.output && <div><strong>Output</strong><pre>{JSON.stringify(task.output, null, 2)}</pre></div>}
                        </details>
                      )}
                    </article>
                  </li>
                );
              })}
            </ol>
          )}
        </PageSection>

        <PageSection title="Decision history" description="Persisted operator and system decisions.">
          {decisions.length === 0 ? (
            <EmptyState title="No decisions recorded" />
          ) : (
            <ol className="ui-decision-list">
              {decisions.map((decision) => (
                <li key={decision.id} className="card">
                  <div className="ui-task-header">
                    <strong>{decision.decision}</strong>
                    <StatusBadge>{decision.decision_type}</StatusBadge>
                  </div>
                  {decision.reason && <p className="ui-card-copy">{decision.reason}</p>}
                  <span className="ui-table-subtitle">{decision.actor} · <time dateTime={decision.created_at}>{new Date(decision.created_at).toLocaleString()}</time></span>
                </li>
              ))}
            </ol>
          )}
        </PageSection>
      </div>

      <ConfirmDialog
        open={cancelOpen}
        title="Cancel mission?"
        description="Cancellation stops further mission progression and cannot be undone from this view."
        confirmLabel="Cancel mission"
        destructive
        busy={actionLoading}
        onCancel={() => setCancelOpen(false)}
        onConfirm={() => {
          setCancelOpen(false);
          void runAction(() => cancelMission(mission.id));
        }}
      />

      <ConfirmDialog
        open={Boolean(approvalTask)}
        title="Approve task?"
        description={approvalTask ? `Approve “${approvalTask.title}” and allow the workflow to continue.` : ""}
        confirmLabel="Approve task"
        busy={actionLoading}
        onCancel={() => setApprovalTask(null)}
        onConfirm={() => {
          const task = approvalTask;
          setApprovalTask(null);
          if (task) void runAction(() => approveTask(task.id));
        }}
      />

      <Dialog
        open={Boolean(rejectTaskRecord)}
        title="Reject task"
        description={rejectTaskRecord ? `Record a reason for rejecting “${rejectTaskRecord.title}”.` : undefined}
        onClose={() => setRejectTaskRecord(null)}
        actions={
          <>
            <button type="button" className="btn btn-secondary" onClick={() => setRejectTaskRecord(null)} disabled={actionLoading}>Cancel</button>
            <button
              type="button"
              className="btn btn-danger"
              disabled={actionLoading || !rejectReason.trim()}
              onClick={() => {
                const task = rejectTaskRecord;
                setRejectTaskRecord(null);
                if (task) void runAction(() => rejectTask(task.id, rejectReason.trim()));
              }}
            >
              Reject task
            </button>
          </>
        }
      >
        <FormField id="task-rejection-reason" label="Rejection reason" required>
          <textarea rows={4} value={rejectReason} onChange={(event) => setRejectReason(event.target.value)} />
        </FormField>
      </Dialog>
    </div>
  );
}
