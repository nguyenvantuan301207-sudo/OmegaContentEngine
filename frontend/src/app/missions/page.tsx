"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { getMissions, type Mission } from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";
import { Alert, MissionCard } from "@/components/ui";

export default function MissionsPage() {
  const { mode, channels } = useOperatorContext();
  const [missions, setMissions] = useState<Mission[]>([]);
  const [search, setSearch] = useState("");
  const [channelFilter, setChannelFilter] = useState<string>("ALL");
  const [stateFilter, setStateFilter] = useState("ALL");
  const [autonomyFilter, setAutonomyFilter] = useState("ALL");
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const pageSize = 24;

  const loadMissions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const limit = 100;
      const offset = mode === "DEVELOPMENT" ? page * pageSize : 0;
      const records = await getMissions(limit, offset);
      setMissions(records);
    } catch (requestError: unknown) {
      setError(requestError instanceof Error ? requestError.message : "Failed to load missions.");
    } finally {
      setLoading(false);
    }
  }, [mode, page]);

  useEffect(() => {
    void loadMissions();
  }, [loadMissions]);

  const channelMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const ch of channels) {
      map.set(ch.id, ch.name);
    }
    return map;
  }, [channels]);

  const filteredMissions = useMemo(() => {
    return missions.filter((mission) => {
      if (channelFilter !== "ALL" && mission.channel_id !== channelFilter) return false;
      if (stateFilter !== "ALL" && mission.state !== stateFilter) return false;
      if (autonomyFilter !== "ALL" && mission.autonomy_level !== autonomyFilter) return false;
      if (!search.trim()) return true;
      const query = search.toLowerCase();
      return (
        mission.title.toLowerCase().includes(query) ||
        mission.objective.toLowerCase().includes(query) ||
        mission.id.toLowerCase().includes(query)
      );
    });
  }, [autonomyFilter, channelFilter, missions, search, stateFilter]);

  return (
    <div className="ui-page-stack" style={{ maxWidth: "1400px", margin: "0 auto" }}>
      {/* HEADER matching prototype */}
      <div className="page-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "16px", marginBottom: "16px" }}>
        <div>
          <div className="title" style={{ fontSize: "24px", fontWeight: 800, letterSpacing: "-0.03em" }}>
            Missions
          </div>
          <div className="sub" style={{ color: "var(--muted)", fontSize: "13px", marginTop: "4px" }}>
            Autonomous workflow execution, multi-stage pipelines, QA verification, and lineage tracking.
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <button type="button" className="btn" onClick={() => void loadMissions()} disabled={loading} style={{ fontSize: "12px", padding: "8px 12px" }}>
            Refresh
          </button>
          <Link href="/missions/new" className="btn primary" style={{ fontSize: "12px", padding: "8px 14px", fontWeight: 700 }}>
            ＋ New Mission
          </Link>
        </div>
      </div>

      {mode === "DEVELOPMENT" && (
        <Alert tone="warning" title="Development data visible">
          The registry includes development and test records.
        </Alert>
      )}

      {error && (
        <Alert tone="danger" title="Missions unavailable" actions={<button type="button" className="btn btn-secondary btn-sm" onClick={() => void loadMissions()}>Retry</button>}>
          {error}
        </Alert>
      )}

      {/* FILTER BAR */}
      <div
        className="ui-filter-bar"
        role="search"
        style={{
          marginBottom: "14px",
          background: "#0f151e",
          border: "1px solid var(--line)",
          borderRadius: "10px",
          padding: "10px 14px",
          display: "flex",
          gap: "10px",
          flexWrap: "wrap",
        }}
      >
        <div className="ui-filter-field ui-filter-grow" style={{ minWidth: "220px" }}>
          <label htmlFor="mission-search" style={{ fontSize: "10px", textTransform: "uppercase", color: "var(--muted)", letterSpacing: "0.08em" }}>
            Search Missions
          </label>
          <input
            id="mission-search"
            className="input"
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search by title, objective or ID..."
            style={{ background: "#111823", border: "1px solid var(--line)" }}
          />
        </div>

        <div className="ui-filter-field" style={{ minWidth: "160px" }}>
          <label htmlFor="mission-channel" style={{ fontSize: "10px", textTransform: "uppercase", color: "var(--muted)", letterSpacing: "0.08em" }}>
            Channel
          </label>
          <select
            id="mission-channel"
            className="select"
            value={channelFilter}
            onChange={(event) => setChannelFilter(event.target.value)}
            style={{ background: "#111823", border: "1px solid var(--line)" }}
          >
            <option value="ALL">All Channels ({missions.length})</option>
            {channels.map((ch) => {
              const count = missions.filter((m) => m.channel_id === ch.id).length;
              return (
                <option key={ch.id} value={ch.id}>
                  {ch.name} ({count})
                </option>
              );
            })}
          </select>
        </div>

        <div className="ui-filter-field">
          <label htmlFor="mission-state" style={{ fontSize: "10px", textTransform: "uppercase", color: "var(--muted)", letterSpacing: "0.08em" }}>
            State
          </label>
          <select
            id="mission-state"
            className="select"
            value={stateFilter}
            onChange={(event) => setStateFilter(event.target.value)}
            style={{ background: "#111823", border: "1px solid var(--line)" }}
          >
            <option value="ALL">All states</option>
            {["READY", "RUNNING", "WAITING_APPROVAL", "PAUSED", "SUCCEEDED", "FAILED", "CANCELLED"].map((state) => (
              <option key={state} value={state}>{state}</option>
            ))}
          </select>
        </div>

        <div className="ui-filter-field">
          <label htmlFor="mission-autonomy-filter" style={{ fontSize: "10px", textTransform: "uppercase", color: "var(--muted)", letterSpacing: "0.08em" }}>
            Autonomy
          </label>
          <select
            id="mission-autonomy-filter"
            className="select"
            value={autonomyFilter}
            onChange={(event) => setAutonomyFilter(event.target.value)}
            style={{ background: "#111823", border: "1px solid var(--line)" }}
          >
            <option value="ALL">All levels</option>
            {["MANUAL", "ASSISTED", "SUPERVISED", "AUTONOMOUS", "STRATEGIC_AUTONOMOUS"].map((level) => (
              <option key={level} value={level}>{level.replaceAll("_", " ")}</option>
            ))}
          </select>
        </div>
      </div>

      {/* MISSIONS GRID MATCHING PROTOTYPE .missions (3 COLUMNS) */}
      {loading ? (
        <div style={{ padding: "40px", textAlign: "center", color: "var(--muted)" }}>Loading missions...</div>
      ) : filteredMissions.length === 0 ? (
        <div
          className="card"
          style={{
            padding: "36px 20px",
            textAlign: "center",
            maxWidth: "480px",
            margin: "20px auto",
            border: "1px solid var(--line)",
            borderRadius: "var(--radius)",
          }}
        >
          <div style={{ fontSize: "28px", marginBottom: "8px" }}>🎯</div>
          <h3 style={{ fontSize: "15px", fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>No missions found</h3>
          <p style={{ fontSize: "12px", color: "var(--muted)", margin: "6px 0 16px" }}>
            {search || channelFilter !== "ALL" || stateFilter !== "ALL"
              ? "No mission matches your active filters."
              : "No mission records currently exist for this workspace."}
          </p>
          <Link href="/missions/new" className="btn primary btn-sm" style={{ fontWeight: 700 }}>
            ＋ Create Mission
          </Link>
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))",
            gap: "14px",
          }}
        >
          {filteredMissions.map((mission) => (
            <MissionCard
              key={mission.id}
              mission={mission}
              channelName={mission.channel_id ? channelMap.get(mission.channel_id) : undefined}
            />
          ))}
        </div>
      )}

      {mode === "DEVELOPMENT" && !loading && !error && missions.length >= pageSize && (
        <nav className="ui-pagination" aria-label="Mission pages" style={{ marginTop: "16px" }}>
          <button type="button" className="btn btn-secondary btn-sm" disabled={page === 0} onClick={() => setPage((current) => Math.max(0, current - 1))}>Previous</button>
          <span>Page {page + 1}</span>
          <button type="button" className="btn btn-secondary btn-sm" disabled={missions.length < pageSize} onClick={() => setPage((current) => current + 1)}>Next</button>
        </nav>
      )}
    </div>
  );
}
