"use client";

import { useCallback, useEffect, useState } from "react";
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

  const getStateBadge = (state: ReservationState) => {
    switch (state) {
      case "ACTIVE":
        return (
          <span className="px-2 py-0.5 text-xs font-semibold rounded bg-emerald-950 text-emerald-400 border border-emerald-800">
            ACTIVE (Scheduled)
          </span>
        );
      case "DISPATCHING":
        return (
          <span className="px-2 py-0.5 text-xs font-semibold rounded bg-blue-950 text-blue-400 border border-blue-800 animate-pulse">
            DISPATCHING
          </span>
        );
      case "CONSUMED":
        return (
          <span className="px-2 py-0.5 text-xs font-semibold rounded bg-purple-950 text-purple-400 border border-purple-800">
            CONSUMED
          </span>
        );
      case "RELEASED":
        return (
          <span className="px-2 py-0.5 text-xs font-semibold rounded bg-zinc-800 text-zinc-400 border border-zinc-700">
            RELEASED
          </span>
        );
      case "EXPIRED":
        return (
          <span className="px-2 py-0.5 text-xs font-semibold rounded bg-amber-950 text-amber-400 border border-amber-800">
            EXPIRED
          </span>
        );
      case "CANCELLED":
        return (
          <span className="px-2 py-0.5 text-xs font-semibold rounded bg-rose-950 text-rose-400 border border-rose-800">
            CANCELLED
          </span>
        );
      default:
        return (
          <span className="px-2 py-0.5 text-xs font-semibold rounded bg-zinc-800 text-zinc-400">
            {state}
          </span>
        );
    }
  };

  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5 shadow-lg text-zinc-100">
      <div className="flex items-center justify-between border-b border-zinc-800 pb-3 mb-4">
        <div>
          <h2 className="text-lg font-bold text-zinc-100 flex items-center gap-2">
            <span>📅</span> Smart Scheduler Timeline
          </h2>
          <p className="text-xs text-zinc-400 mt-0.5">
            Channel Target Timezone:{" "}
            <span className="font-mono text-zinc-300 font-medium">
              {timeline?.timezone || "UTC"}
            </span>
          </p>
        </div>

        {timeline && (
          <div className="text-right">
            <span className="text-xs text-zinc-400 block">Today&apos;s Throughput</span>
            <span className="text-sm font-semibold text-zinc-200">
              {timeline.capacity_used_today} / {timeline.capacity_limit_today} slots
            </span>
          </div>
        )}
      </div>

      {loading && (
        <div className="py-8 text-center text-zinc-500 text-sm">
          Loading schedule timeline...
        </div>
      )}

      {error && (
        <div className="py-3 px-4 bg-rose-950/50 border border-rose-800 rounded-lg text-rose-300 text-xs mb-4">
          {error}
        </div>
      )}

      {!loading && !error && timeline && (
        <div className="space-y-3">
          {timeline.reservations.length === 0 ? (
            <div className="py-6 text-center text-zinc-500 text-xs bg-zinc-950/50 rounded-lg border border-zinc-800/50">
              No active or upcoming reservations scheduled for this channel.
            </div>
          ) : (
            <div className="divide-y divide-zinc-800/60">
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
                    className="py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-3"
                  >
                    <div className="space-y-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        {getStateBadge(res.state)}
                        <span className="text-xs font-mono px-2 py-0.5 rounded bg-zinc-800 text-zinc-300">
                          {res.workload_category}
                        </span>
                        <span className="text-xs text-zinc-400">
                          Priority:{" "}
                          <span className="font-semibold text-zinc-200">
                            {res.priority_score}
                          </span>
                        </span>
                      </div>

                      <div className="text-xs text-zinc-300">
                        <span className="font-medium text-emerald-400">{localStr}</span>{" "}
                        <span className="text-zinc-500 font-mono text-[11px]">({utcStr})</span>
                      </div>

                      <div className="text-[11px] text-zinc-500 font-mono">
                        ID: {res.id.substring(0, 8)}... | Target: {res.target_id.substring(0, 8)}...
                      </div>
                    </div>

                    {(res.state === "ACTIVE" || res.state === "DISPATCHING") && (
                      <button
                        onClick={() => handleRelease(res.id)}
                        disabled={releasingId === res.id}
                        className="self-start sm:self-center px-3 py-1 text-xs font-medium bg-zinc-800 hover:bg-rose-950 hover:text-rose-300 hover:border-rose-800 text-zinc-300 border border-zinc-700 rounded transition disabled:opacity-50"
                      >
                        {releasingId === res.id ? "Releasing..." : "Release Slot"}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          <div className="pt-3 border-t border-zinc-800/80 flex justify-end">
            <button
              onClick={loadTimeline}
              disabled={loading}
              className="text-xs text-zinc-400 hover:text-zinc-200 transition"
            >
              ↻ Refresh Timeline
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
