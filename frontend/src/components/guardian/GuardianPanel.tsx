"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getMissionGuardianStatus,
  listGuardianExceptions,
  listMissionGuardianChecks,
  triggerSafeResume,
  type GuardianCheck,
  type GuardianConsolidatedStatus,
  type GuardianException,
} from "@/lib/api";
import { formatCurrencyUsd } from "@/lib/formatters";
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

export function GuardianPanel({
  missionId,
  isPaused,
  onStateChanged,
}: {
  missionId: string;
  isPaused: boolean;
  onStateChanged?: () => void;
}) {
  const [status, setStatus] = useState<GuardianConsolidatedStatus | null>(null);
  const [checks, setChecks] = useState<GuardianCheck[]>([]);
  const [exceptions, setExceptions] = useState<GuardianException[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [nextStatus, nextChecks, nextExceptions] = await Promise.all([
        getMissionGuardianStatus(missionId),
        listMissionGuardianChecks(missionId),
        listGuardianExceptions(true),
      ]);
      setStatus(nextStatus);
      setChecks(nextChecks);
      setExceptions(nextExceptions);
    } catch (reason: unknown) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Guardian data could not be loaded.",
      );
    } finally {
      setLoading(false);
    }
  }, [missionId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function safeResume() {
    setBusy(true);
    setError(null);
    try {
      await triggerSafeResume(missionId);
      await load();
      onStateChanged?.();
    } catch (reason: unknown) {
      setError(
        reason instanceof Error ? reason.message : "Safe resume failed.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (loading && !status)
    return (
      <LoadingState
        title="Loading Guardian controls"
        description="Retrieving invariant checks and active exceptions."
      />
    );
  const gate = status?.overall_gate_state || "UNKNOWN";
  return (
    <PageSection
      title="Guardian control plane"
      description="Deterministic invariant checks protect dispatch, rendering, and external side effects."
      actions={
        isPaused ? (
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={busy}
            onClick={() => void safeResume()}
          >
            {busy ? "Verifying…" : "Safe resume recheck"}
          </button>
        ) : undefined
      }
    >
      {error ? (
        <ErrorState
          title="Guardian status unavailable"
          description={error}
          action={
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => void load()}
            >
              Retry
            </button>
          }
        />
      ) : null}
      {gate === "BLOCKED" ? (
        <Alert tone="danger" title="Guardian blocked">
          One or more invariant checks prevent continuation.
        </Alert>
      ) : gate === "RESTRICTED" ? (
        <Alert tone="warning" title="Guardian restricted">
          Continuation is constrained by active warnings.
        </Alert>
      ) : (
        <Alert tone="success" title="Guardian gates open">
          No blocking invariant is currently reported.
        </Alert>
      )}
      <WorkflowStatusSummary
        metrics={[
          {
            label: "Gate",
            value: gate,
            status: gate === "OPEN" ? "SUCCEEDED" : gate,
          },
          { label: "Epoch", value: `v${status?.guardian_epoch ?? 1}` },
          {
            label: "Budget",
            value: `${formatCurrencyUsd(status?.accumulated_cost_usd, "$0.00")} / ${formatCurrencyUsd(status?.budget_ceiling_usd, "$50.00")}`,
          },
          { label: "Open findings", value: status?.open_findings_count ?? 0 },
          { label: "Active exceptions", value: exceptions.length },
        ]}
      />
      {checks.length === 0 ? (
        <EmptyState
          title="No Guardian checks"
          description="No evaluated invariant checks are available for this mission."
        />
      ) : (
        <div className="ui-table-card table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th>Checkpoint</th>
                <th>Decision</th>
                <th>Rationale</th>
                <th>Timestamp</th>
              </tr>
            </thead>
            <tbody>
              {checks.slice(0, 8).map((check) => {
                const action = check.decision?.action || check.status;
                return (
                  <tr key={check.id}>
                    <td data-label="Checkpoint" className="workflow-id">
                      {check.checkpoint}
                    </td>
                    <td data-label="Decision">
                      <StatusBadge tone={statusTone(String(action))}>
                        {String(action)}
                      </StatusBadge>
                    </td>
                    <td data-label="Rationale">
                      {check.decision?.reason || "Passed invariant"}
                    </td>
                    <td data-label="Timestamp">
                      <time dateTime={check.created_at}>
                        {new Date(check.created_at).toLocaleTimeString()}
                      </time>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <TechnicalDetails data={{ status, exceptions }} />
    </PageSection>
  );
}
