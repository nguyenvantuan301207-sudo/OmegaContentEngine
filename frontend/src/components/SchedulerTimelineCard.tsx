"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  getChannelScheduleTimeline,
  releaseScheduleReservation,
  type ChannelTimelineResponse,
  type ReservationState,
} from "@/lib/api";
import {
  Alert,
  ConfirmDialog,
  EmptyState,
  LoadingState,
  StatusBadge,
  type StatusTone,
} from "@/components/ui";

function reservationTone(state: ReservationState): StatusTone {
  if (state === "ACTIVE" || state === "DISPATCHING") return "info";
  if (state === "CONSUMED") return "success";
  if (state === "EXPIRED") return "warning";
  if (state === "CANCELLED") return "danger";
  return "neutral";
}

export function SchedulerTimelineCard({ channelId }: { channelId: string }) {
  const [timeline, setTimeline] = useState<ChannelTimelineResponse | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [releaseId, setReleaseId] = useState<string | null>(null);
  const [releasing, setReleasing] = useState(false);

  const loadTimeline = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setTimeline(await getChannelScheduleTimeline(channelId));
    } catch (requestError: unknown) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Failed to load schedule.",
      );
    } finally {
      setLoading(false);
    }
  }, [channelId]);

  useEffect(() => {
    if (channelId) void loadTimeline();
  }, [channelId, loadTimeline]);

  const releaseReservation = async () => {
    if (!releaseId) return;
    setReleasing(true);
    setError(null);
    try {
      await releaseScheduleReservation(
        releaseId,
        "Manual release from Channel Timeline",
      );
      setReleaseId(null);
      await loadTimeline();
    } catch (requestError: unknown) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Failed to release reservation.",
      );
    } finally {
      setReleasing(false);
    }
  };

  return (
    <div className="card ui-route-card">
      <header className="ui-card-header">
        <div>
          <h2>Schedule timeline</h2>
          <p>
            Timezone:{" "}
            <span className="text-mono">
              {timeline?.timezone || "Unavailable"}
            </span>
          </p>
        </div>
        <div className="ui-inline-actions">
          {timeline && (
            <span className="ui-card-stat">
              {timeline.capacity_used_today} / {timeline.capacity_limit_today}{" "}
              slots used today
            </span>
          )}
          <Link
            href={`/publisher?channel_id=${channelId}`}
            className="btn btn-secondary btn-sm"
          >
            Open publisher
          </Link>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => void loadTimeline()}
            disabled={loading}
          >
            Refresh
          </button>
        </div>
      </header>

      {error && (
        <Alert tone="danger" title="Schedule operation failed">
          {error}
        </Alert>
      )}
      {loading ? (
        <LoadingState title="Loading schedule" />
      ) : timeline && timeline.reservations.length === 0 ? (
        <EmptyState
          title="No reservations"
          description="No active or upcoming reservation exists for this channel."
        />
      ) : (
        timeline && (
          <ol className="ui-reservation-list">
            {timeline.reservations.map((reservation) => {
              const scheduled = new Date(reservation.scheduled_start_at);
              return (
                <li key={reservation.id} className="ui-reservation-item">
                  <div className="ui-reservation-primary">
                    <div className="ui-inline-actions">
                      <StatusBadge tone={reservationTone(reservation.state)}>
                        {reservation.state}
                      </StatusBadge>
                      <StatusBadge>{reservation.workload_category}</StatusBadge>
                    </div>
                    <strong>
                      {scheduled.toLocaleString(undefined, {
                        timeZone: timeline.timezone || "UTC",
                        dateStyle: "medium",
                        timeStyle: "short",
                      })}
                    </strong>
                    <span className="ui-table-subtitle text-mono">
                      {scheduled.toISOString()} · {reservation.id}
                    </span>
                  </div>
                  <div className="ui-reservation-actions">
                    <span>Priority {reservation.priority_score}</span>
                    {(reservation.state === "ACTIVE" ||
                      reservation.state === "DISPATCHING") && (
                      <button
                        type="button"
                        className="btn btn-danger btn-sm"
                        onClick={() => setReleaseId(reservation.id)}
                      >
                        Release slot
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )
      )}

      <ConfirmDialog
        open={Boolean(releaseId)}
        title="Release scheduled slot?"
        description="The reservation will no longer protect this publishing window."
        confirmLabel="Release slot"
        destructive
        busy={releasing}
        onCancel={() => setReleaseId(null)}
        onConfirm={() => void releaseReservation()}
      />
    </div>
  );
}
