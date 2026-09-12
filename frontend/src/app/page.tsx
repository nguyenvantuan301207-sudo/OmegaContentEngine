"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  getHealth, getMissions, getSystemInfo, getSystemStatus,
  listProductionRequests, listPublishIntents,
  type Mission, type ProductionRequest, type PublishIntent,
  type SystemInfo, type SystemStatus,
} from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";
import { StatusBadge } from "@/components/ui";
import { statusTone } from "@/lib/presentation";

export default function Home() {
  const { selectedChannel, selectedChannelId, channels } = useOperatorContext();
  const [health, setHealth] = useState<string | null>(null);
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [missions, setMissions] = useState<Mission[]>([]);
  const [productionRequests, setProductionRequests] = useState<ProductionRequest[]>([]);
  const [publishIntents, setPublishIntents] = useState<PublishIntent[]>([]);

  const fetchStatus = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [healthResponse, infoResponse, statusResponse, missionRecords, productionRecords, publishRecords] = await Promise.all([
        getHealth(),
        getSystemInfo(),
        getSystemStatus(),
        getMissions(100, 0),
        selectedChannelId ? listProductionRequests(selectedChannelId) : Promise.resolve([]),
        selectedChannelId ? listPublishIntents({ channel_id: selectedChannelId }) : Promise.resolve([]),
      ]);
      setHealth(healthResponse.status);
      setSystemInfo(infoResponse);
      setSystemStatus(statusResponse);
      setMissions(missionRecords);
      setProductionRequests(productionRecords);
      setPublishIntents(publishRecords);
    } catch (requestError: unknown) {
      setHealth("error");
      setError(requestError instanceof Error ? requestError.message : "Workspace data is unavailable.");
    } finally {
      setLoading(false);
    }
  }, [selectedChannelId]);

  useEffect(() => {
    void fetchStatus();
    const interval = window.setInterval(() => void fetchStatus(), 20000);
    return () => window.clearInterval(interval);
  }, [fetchStatus]);

  const channelMissions = useMemo(
    () => (selectedChannelId ? missions.filter((m) => m.channel_id === selectedChannelId) : []),
    [missions, selectedChannelId],
  );

  const displayMissions = useMemo(
    () => (channelMissions.length > 0 ? channelMissions : missions),
    [channelMissions, missions],
  );

  // Derivable KPIs matching prototype values
  const activeMissions = displayMissions.filter((m) =>
    ["READY", "RUNNING", "PAUSED"].includes(m.state),
  );
  const runningProduction = productionRequests.filter((r) => r.status === "RUNNING");
  const needsReview = displayMissions.filter((m) => m.state === "PAUSED");
  const readyToPublish = publishIntents.filter((i) => i.state === "APPROVED");

  const channelMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const ch of channels) {
      map.set(ch.id, ch.name);
    }
    return map;
  }, [channels]);

  return (
    <div className="ui-page-stack product-home" style={{ maxWidth: "1400px", margin: "0 auto" }}>
      {/* HEADER matching prototype */}
      <div className="page-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "16px", marginBottom: "16px" }}>
        <div>
          <div className="title" style={{ fontSize: "24px", fontWeight: 800, letterSpacing: "-0.03em" }}>
            OMEGA Home
          </div>
          <div className="sub" style={{ color: "var(--muted)", fontSize: "13px", marginTop: "4px" }}>
            {selectedChannel
              ? `Operational workspace for ${selectedChannel.name} · ${selectedChannel.platform}`
              : "Unified workspace for content generation, production studio, and publishing."}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <button
            type="button"
            onClick={() => void fetchStatus()}
            className="btn"
            disabled={loading}
            style={{ fontSize: "12px", padding: "8px 12px" }}
          >
            Refresh
          </button>
          <Link
            href="/missions/new"
            className="btn primary"
            style={{ fontSize: "12px", padding: "8px 14px", fontWeight: 700 }}
          >
            ＋ New Mission
          </Link>
        </div>
      </div>

      {error ? (
        <div className="alert alert-danger" style={{ marginBottom: "16px" }}>
          <span><strong>Workspace unavailable:</strong> {error}</span>
          <button type="button" onClick={() => void fetchStatus()} className="btn btn-secondary btn-sm">Retry</button>
        </div>
      ) : null}

      {/* ROW 1: REAL KPI CARDS matching prototype .metrics */}
      <div className="dash-metrics-grid" aria-label="Key Performance Indicators">
        <div className="dash-metric-card">
          <div className="metric-k">Active Missions</div>
          <div className="metric-v">{activeMissions.length || missions.filter((m) => ["READY", "RUNNING", "PAUSED"].includes(m.state)).length}</div>
          <div className="metric-sub" style={{ color: "var(--success)" }}>
            {activeMissions.length ? `${activeMissions.length} in channel queue` : `${missions.filter((m) => ["READY", "RUNNING", "PAUSED"].includes(m.state)).length} across workspace`}
          </div>
        </div>

        <div className="dash-metric-card">
          <div className="metric-k">Rendering</div>
          <div className="metric-v">
            {runningProduction.length || missions.filter((m) => m.state === "RUNNING").length}
          </div>
          <div className="metric-sub">
            {runningProduction.length ? `${runningProduction.length} active jobs` : `${missions.filter((m) => m.state === "RUNNING").length} running pipeline`}
          </div>
        </div>

        <div className="dash-metric-card">
          <div className="metric-k">Needs Review</div>
          <div className="metric-v">{needsReview.length || missions.filter((m) => m.state === "PAUSED").length}</div>
          <div className="metric-sub" style={{ color: (needsReview.length || missions.filter((m) => m.state === "PAUSED").length) ? "var(--warning)" : "var(--soft)" }}>
            {(needsReview.length || missions.filter((m) => m.state === "PAUSED").length) ? `${needsReview.length || missions.filter((m) => m.state === "PAUSED").length} awaiting operator` : "All approvals cleared"}
          </div>
        </div>

        <div className="dash-metric-card">
          <div className="metric-k">Ready to Publish</div>
          <div className="metric-v">
            {readyToPublish.length || displayMissions.filter((m) => m.state === "SUCCEEDED").length || missions.filter((m) => m.state === "SUCCEEDED").length}
          </div>
          <div className="metric-sub" style={{ color: "var(--success)" }}>
            {readyToPublish.length ? `${readyToPublish.length} intents approved` : `${displayMissions.filter((m) => m.state === "SUCCEEDED").length || missions.filter((m) => m.state === "SUCCEEDED").length} succeeded`}
          </div>
        </div>
      </div>

      {/* ROW 2: PROTOTYPE QUICK ACTIONS */}
      <div className="dash-quick-actions" aria-label="Quick Actions">
        <Link href="/missions/new" className="dash-quick-btn">
          <b>＋ New Mission</b>
          <span>Topic → production workflow</span>
        </Link>
        <Link
          href={selectedChannelId ? `/channels/${selectedChannelId}/production` : "/channels"}
          className="dash-quick-btn"
        >
          <b>▶ Continue Production</b>
          <span>{selectedChannel ? `Resume ${selectedChannel.name}` : "Choose active channel"}</span>
        </Link>
        <Link href="/publisher" className="dash-quick-btn">
          <b>✓ Review QA</b>
          <span>{needsReview.length ? `${needsReview.length} waiting reviews` : "Inspect compliance"}</span>
        </Link>
        <Link href="/publisher" className="dash-quick-btn">
          <b>↑ Publisher</b>
          <span>{readyToPublish.length ? `${readyToPublish.length} ready intents` : "Publishing queue"}</span>
        </Link>
      </div>

      {/* ROW 3: TODAY'S PRODUCTION (65%) + SMART ACTIVITY (35%) */}
      <div className="dash-main-grid">
        {/* LEFT CARD (~65%): TODAY'S PRODUCTION */}
        <div className="card" style={{ overflow: "hidden" }}>
          <div
            style={{
              padding: "14px 16px",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              borderBottom: "1px solid var(--line)",
            }}
          >
            <div>
              <strong style={{ fontSize: "14px", color: "#fff" }}>Today&apos;s Production</strong>
              <span className="small muted" style={{ display: "block", fontSize: "11px", marginTop: "2px" }}>
                {channelMissions.length > 0 ? `Active work for ${selectedChannel?.name}` : "Live queue & workspace progression"}
              </span>
            </div>
            <Link href="/missions" className="btn btn-secondary btn-sm" style={{ fontSize: "11px", padding: "4px 9px" }}>
              View all
            </Link>
          </div>

          <div className="v2-table-wrapper">
            {displayMissions.length > 0 ? (
              <table className="v2-table">
                <thead>
                  <tr>
                    <th>Mission</th>
                    <th>Channel</th>
                    <th>Progress</th>
                    <th>Status</th>
                    <th style={{ textAlign: "right" }}>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {displayMissions.slice(0, 6).map((mission) => {
                    const isSuccess = mission.state === "SUCCEEDED";
                    const isRunning = mission.state === "RUNNING";
                    const progressWidth = isSuccess ? "100%" : isRunning ? "65%" : mission.state === "PAUSED" ? "85%" : "25%";
                    const cName = mission.channel_id ? channelMap.get(mission.channel_id) || "Channel" : "Unassigned";

                    return (
                      <tr key={mission.id}>
                        <td>
                          <Link href={`/missions/${mission.id}`} style={{ fontWeight: 600, color: "#eef3fb" }}>
                            {mission.title}
                          </Link>
                          <div className="small muted" style={{ fontSize: "11px" }}>
                            {mission.autonomy_level.replaceAll("_", " ")} · P{mission.priority}
                          </div>
                        </td>
                        <td style={{ fontSize: "12px", color: "var(--muted)" }}>
                          {cName}
                        </td>
                        <td>
                          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                            <div className="v2-progress">
                              <span style={{ width: progressWidth }} />
                            </div>
                            <span style={{ fontSize: "10px", color: "var(--soft)" }}>{progressWidth}</span>
                          </div>
                        </td>
                        <td>
                          <StatusBadge tone={statusTone(mission.state)}>{mission.state}</StatusBadge>
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <Link
                            href={mission.channel_id ? `/channels/${mission.channel_id}/production` : `/missions/${mission.id}`}
                            className="btn btn-secondary btn-sm"
                            style={{ fontSize: "11px", padding: "4px 8px" }}
                          >
                            Open
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : (
              <div style={{ padding: "32px 16px", textAlign: "center", color: "var(--muted)" }}>
                <p style={{ fontSize: "13px", margin: 0 }}>No active missions for this workspace view.</p>
                <Link href="/missions/new" className="btn btn-secondary btn-sm" style={{ marginTop: "12px", display: "inline-block" }}>
                  Create a mission
                </Link>
              </div>
            )}
          </div>
        </div>

        {/* RIGHT CARD (~35%): SMART ACTIVITY & NEEDS ATTENTION */}
        <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
          <div className="card">
            <div
              style={{
                padding: "14px 16px",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                borderBottom: "1px solid var(--line)",
              }}
            >
              <div>
                <strong style={{ fontSize: "14px", color: "#fff" }}>Smart Activity</strong>
                <span className="small muted" style={{ display: "block", fontSize: "11px", marginTop: "2px" }}>
                  Signals &amp; attention items
                </span>
              </div>
              <span className="cmd-category-tag">Contextual</span>
            </div>

            <div className="v2-activity-feed">
              {needsReview.length > 0 && (
                <div className="v2-activity-item">
                  <span className="v2-activity-dot" style={{ background: "var(--warning)" }} />
                  <div>
                    <b style={{ fontSize: "12px", color: "var(--warning)" }}>Needs Operator Review</b>
                    <div className="small muted" style={{ fontSize: "11px" }}>
                      {needsReview[0].title} · waiting approval
                    </div>
                  </div>
                </div>
              )}

              {displayMissions.slice(0, 4).map((m) => {
                const tone = statusTone(m.state);
                const dotColor = tone === "success" ? "var(--success)" : tone === "warning" ? "var(--warning)" : tone === "danger" ? "var(--danger)" : "var(--accent)";
                return (
                  <div key={m.id} className="v2-activity-item">
                    <span className="v2-activity-dot" style={{ background: dotColor }} />
                    <div>
                      <b style={{ fontSize: "12px", color: "#eef3fb" }}>
                        {m.state === "SUCCEEDED" ? "Mission succeeded" : m.state === "RUNNING" ? "Pipeline running" : `State: ${m.state}`}
                      </b>
                      <div className="small muted" style={{ fontSize: "11px" }}>
                        {m.title} · <time dateTime={m.updated_at}>{new Date(m.updated_at).toLocaleTimeString()}</time>
                      </div>
                    </div>
                  </div>
                );
              })}

              {displayMissions.length === 0 && (
                <div style={{ padding: "16px 0", textAlign: "center", color: "var(--muted)", fontSize: "12px" }}>
                  No recent activity events recorded.
                </div>
              )}
            </div>
          </div>

          {/* COMPACT WORKSPACE & SYSTEM STATUS STRIP */}
          <div
            className="card"
            style={{
              padding: "12px 14px",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "10px",
              background: "#0d131c",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "8px", minWidth: 0 }}>
              <span className="dot" style={{ background: health === "ok" ? "var(--success)" : "var(--warning)" }} />
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: "11px", fontWeight: 700, color: "#cbd5e1" }}>
                  {systemInfo?.app || "OMEGA"} {systemInfo?.version || "Engine"} · {health === "ok" ? "All services online" : "System alert"}
                </div>
                <div style={{ fontSize: "10px", color: "var(--soft)", textOverflow: "ellipsis", overflow: "hidden", whiteSpace: "nowrap" }}>
                  Postgres {systemStatus?.checks?.postgres?.latency_ms ?? "—"}ms · Redis {systemStatus?.checks?.redis?.latency_ms ?? "—"}ms
                </div>
              </div>
            </div>
            {selectedChannel && (
              <Link
                href={`/channels/${selectedChannel.id}`}
                className="pill"
                style={{ fontSize: "11px", padding: "4px 8px", flexShrink: 0 }}
              >
                {selectedChannel.name}
              </Link>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
