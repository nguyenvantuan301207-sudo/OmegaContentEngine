"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ChannelTimelineResponse,
  ReservationState,
  getChannelScheduleTimeline,
  releaseScheduleReservation,
} from "@/lib/api";

interface SchedulerTimelineCardProps {
  channelId: string;
}

export function SchedulerTimelineCard({ channelId }: SchedulerTimelineCardProps) {
  const [timeline, setTimeline] = useState<ChannelTimelineResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [releasingId, setReleasingId] = useState<string | null>(null);

  const loadTimeline = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getChannelScheduleTimeline(channelId);
      setTimeline(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load scheduler timeline");
    } finally {
      setLoading(false);
    }
  }, [channelId]);

  useEffect(() => {
    if (channelId) {
      loadTimeline();
    }
  }, [channelId, loadTimeline]);

  const handleRelease = async (reservationId: string) => {
    if (!confirm("Are you sure you want to release this scheduled slot?")) {
      return;
    }
    try {
      setReleasingId(reservationId);
      await releaseScheduleReservation(reservationId, "Manual release from Channel Timeline");
      await loadTimeline();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to release reservation");
    } finally {
      setReleasingId(null);
    }
  };

  const getStateBadgeClass = (state: ReservationState) => {
    switch (state) {
      case "ACTIVE":
        return "badge-active";
      case "DISPATCHING":
        return "badge-active";
      case "CONSUMED":
        return "badge-purple";
      case "RELEASED":
        return "badge-draft";
      case "EXPIRED":
        return "badge-warning";
      case "CANCELLED":
        return "badge-failed";
      default:
        return "badge-neutral";
    }
  };

  return (
    <div className="card" style={{ padding: "1.25rem" }}>
      {/* Card Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          borderBottom: "1px solid var(--border-subtle)",
          paddingBottom: "0.85rem",
          marginBottom: "1rem",
        }}
      >
        <div>
          <h2 style={{ fontSize: "1.05rem", fontWeight: 700, color: "var(--text-primary)", display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <span>📅</span> Smart Scheduler Timeline
          </h2>
          <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.2rem" }}>
            Channel Target Timezone:{" "}
            <span className="text-mono" style={{ color: "var(--text-secondary)", fontWeight: 600 }}>
              {timeline?.timezone || "UTC"}
            </span>
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          {timeline && (
            <div style={{ textAlign: "right" }}>
              <span style={{ fontSize: "0.7rem", color: "var(--text-muted)", display: "block", textTransform: "uppercase" }}>
                Today&apos;s Throughput
              </span>
              <span style={{ fontSize: "0.95rem", fontWeight: 700, color: "var(--text-primary)" }}>
                {timeline.capacity_used_today} / {timeline.capacity_limit_today} slots
              </span>
            </div>
          )}
          <Link
            href={`/publisher?channel_id=${channelId}`}
            className="btn btn-secondary btn-sm"
            style={{ fontSize: "0.75rem" }}
          >
            🚀 Publisher Cockpit ↗
          </Link>
        </div>
      </div>

      {loading && (
        <div style={{ textAlign: "center", padding: "2rem", color: "var(--text-muted)", fontSize: "0.85rem" }}>
          Loading schedule timeline...
        </div>
      )}

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

      {!loading && !error && timeline && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
          {timeline.reservations.length === 0 ? (
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
              No active or upcoming reservations scheduled for this channel.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.6rem" }}>
              {timeline.reservations.map((res) => {
                const utcDate = new Date(res.scheduled_start_at);
                const localStr = utcDate.toLocaleString("en-US", {
                  timeZone: timeline.timezone || "UTC",
                  dateStyle: "medium",
                  timeStyle: "short",
                });
                const utcStr = utcDate.toISOString().replace("T", " ").substring(0, 16) + " UTC";

                return (
                  <div
                    key={res.id}
                    style={{
                      padding: "0.85rem 1rem",
                      background: "var(--bg-input)",
                      borderRadius: "var(--radius-sm)",
                      border: "1px solid var(--border-subtle)",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      flexWrap: "wrap",
                      gap: "0.75rem",
                    }}
                  >
                    <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
                        <span className={`badge ${getStateBadgeClass(res.state)}`}>
                          {res.state}
                        </span>
                        <span className="badge badge-neutral text-mono" style={{ fontSize: "0.7rem" }}>
                          {res.workload_category}
                        </span>
                        <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                          Priority: <strong style={{ color: "var(--text-primary)" }}>{res.priority_score}</strong>
                        </span>
                      </div>

                      <div style={{ fontSize: "0.82rem", color: "var(--text-primary)" }}>
                        <span style={{ fontWeight: 600, color: "var(--status-success)" }}>{localStr}</span>{" "}
                        <span className="text-mono" style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
                          ({utcStr})
                        </span>
                      </div>

                      <div className="text-mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
                        ID: {res.id.substring(0, 8)}... • Target: {res.target_id.substring(0, 8)}...
                      </div>
                    </div>

                    {(res.state === "ACTIVE" || res.state === "DISPATCHING") && (
                      <button
                        onClick={() => handleRelease(res.id)}
                        disabled={releasingId === res.id}
                        className="btn btn-secondary btn-sm"
                        style={{ fontSize: "0.75rem", padding: "0.3rem 0.65rem", color: "var(--status-danger)" }}
                      >
                        {releasingId === res.id ? "Releasing..." : "Release Slot"}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          <div style={{ paddingTop: "0.75rem", borderTop: "1px solid var(--border-subtle)", display: "flex", justifyContent: "flex-end" }}>
            <button
              onClick={loadTimeline}
              disabled={loading}
              className="btn btn-secondary btn-sm"
              style={{ fontSize: "0.78rem", display: "flex", alignItems: "center", gap: "0.35rem" }}
            >
              <span>↻</span> Refresh Timeline
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
