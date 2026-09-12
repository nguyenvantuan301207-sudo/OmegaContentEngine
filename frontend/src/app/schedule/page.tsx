"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useOperatorContext } from "@/lib/operator-context";
import { SchedulerTimelineCard } from "@/components/SchedulerTimelineCard";
import {
  getChannelScheduleTimeline,
  type ChannelTimelineResponse,
} from "@/lib/api";
import { LoadingState } from "@/components/ui";

export default function SchedulePage() {
  const { selectedChannel, selectedChannelId, setSelectedChannelId, channels, channelsLoading } = useOperatorContext();
  const [timeline, setTimeline] = useState<ChannelTimelineResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const loadTimeline = useCallback(async (channelId: string) => {
    setLoading(true);
    try {
      const data = await getChannelScheduleTimeline(channelId);
      setTimeline(data);
    } catch {
      setTimeline(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedChannelId) {
      void loadTimeline(selectedChannelId);
    }
  }, [loadTimeline, selectedChannelId]);

  // Derived summary values
  const capacityLimit = timeline?.capacity_limit_today ?? "—";
  const capacityUsed = timeline?.capacity_used_today ?? 0;
  const activeReservations = timeline?.reservations?.filter((r) => r.state === "ACTIVE" || r.state === "DISPATCHING") ?? [];
  const nextReservation = timeline?.reservations?.[0]
    ? new Date(timeline.reservations[0].scheduled_start_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : "None queued";

  return (
    <div className="ui-page-stack" style={{ maxWidth: "1400px", margin: "0 auto" }}>
      {/* HEADER matching prototype */}
      <div className="page-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "16px", marginBottom: "16px" }}>
        <div>
          <div className="title" style={{ fontSize: "24px", fontWeight: 800, letterSpacing: "-0.03em" }}>
            Publishing Schedule
          </div>
          <div className="sub" style={{ color: "var(--muted)", fontSize: "13px", marginTop: "4px" }}>
            Capacity reservations, dispatch windows, and automated publication slots.
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          {/* COMPACT CHANNEL SELECTOR CONTROL */}
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span className="small muted" style={{ fontSize: "11px" }}>Channel:</span>
            <select
              id="schedule-channel-selector"
              value={selectedChannelId}
              onChange={(e) => void setSelectedChannelId(e.target.value)}
              className="select"
              style={{
                background: "#0f151e",
                border: "1px solid var(--line)",
                padding: "6px 10px",
                fontSize: "12px",
                borderRadius: "8px",
                color: "var(--text)",
                maxWidth: "200px",
              }}
              disabled={channelsLoading || channels.length === 0}
            >
              {channels.map((ch) => (
                <option key={ch.id} value={ch.id}>
                  {ch.name} ({ch.platform})
                </option>
              ))}
            </select>
          </div>

          <Link
            href={selectedChannelId ? `/publisher?channel_id=${selectedChannelId}` : "/publisher"}
            className="btn primary"
            style={{ fontSize: "12px", padding: "7px 13px", fontWeight: 700 }}
          >
            Open Publisher
          </Link>
        </div>
      </div>

      {/* SUMMARY ROW: REAL SCHEDULER VALUES */}
      <div className="schedule-summary-metrics" aria-label="Schedule Summary Metrics">
        <div className="dash-metric-card">
          <div className="metric-k">Today&apos;s Capacity</div>
          <div className="metric-v">{capacityLimit}</div>
          <div className="metric-sub">Configured channel limit</div>
        </div>

        <div className="dash-metric-card">
          <div className="metric-k">Slots Used</div>
          <div className="metric-v">{capacityUsed}</div>
          <div className="metric-sub" style={{ color: capacityUsed > 0 ? "var(--warning)" : "var(--soft)" }}>
            {capacityUsed > 0 ? `${capacityUsed} published today` : "No slots consumed today"}
          </div>
        </div>

        <div className="dash-metric-card">
          <div className="metric-k">Upcoming Reservations</div>
          <div className="metric-v">{activeReservations.length}</div>
          <div className="metric-sub" style={{ color: activeReservations.length > 0 ? "var(--accent)" : "var(--soft)" }}>
            {activeReservations.length > 0 ? `${activeReservations.length} active or dispatching` : "Ready for next booking"}
          </div>
        </div>

        <div className="dash-metric-card">
          <div className="metric-k">Next Reservation</div>
          <div className="metric-v" style={{ fontSize: "20px" }}>{nextReservation}</div>
          <div className="metric-sub">
            Timezone: {timeline?.timezone || selectedChannel?.timezone || "UTC"}
          </div>
        </div>
      </div>

      {/* MAIN: SCHEDULE / TIMELINE CARD */}
      <div className="card" style={{ overflow: "hidden", border: "1px solid var(--line)" }}>
        {channelsLoading || loading ? (
          <div style={{ padding: "40px 20px" }}>
            <LoadingState title="Loading publishing schedule" />
          </div>
        ) : selectedChannelId ? (
          <SchedulerTimelineCard channelId={selectedChannelId} />
        ) : (
          <div style={{ padding: "40px 20px", textAlign: "center", color: "var(--muted)" }}>
            <p style={{ fontSize: "14px", margin: 0 }}>Select a channel above to view its publishing schedule.</p>
          </div>
        )}
      </div>
    </div>
  );
}
