"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { getMissions, Mission } from "@/lib/api";
import { useOperatorContext, CANARY_CHANNEL_ID } from "@/lib/operator-context";

export default function MissionsPage() {
  const { mode } = useOperatorContext();
  const [missions, setMissions] = useState<Mission[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [stateFilter, setStateFilter] = useState("ALL");
  const [autonomyFilter, setAutonomyFilter] = useState("ALL");
  const [page, setPage] = useState(0);
  const pageSize = 25;

  const fetchMissions = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      // In Operator Mode: we fetch the latest batch and filter for the active canary channel
      // In Development Mode: we fetch the paginated records
      const fetchLimit = mode === "OPERATOR" ? 100 : pageSize;
      const fetchOffset = mode === "OPERATOR" ? 0 : page * pageSize;

      const data = await getMissions(fetchLimit, fetchOffset);

      if (mode === "OPERATOR") {
        const canaryMissions = data.filter((m) => m.channel_id === CANARY_CHANNEL_ID);
        setMissions(canaryMissions);
      } else {
        setMissions(data);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load missions");
    } finally {
      setLoading(false);
    }
  }, [mode, page]);

  useEffect(() => {
    fetchMissions();
  }, [fetchMissions]);

  const filteredMissions = missions.filter((m) => {
    if (stateFilter !== "ALL" && m.state !== stateFilter) return false;
    if (autonomyFilter !== "ALL" && m.autonomy_level !== autonomyFilter) return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      return (
        m.title.toLowerCase().includes(q) ||
        m.objective.toLowerCase().includes(q) ||
        m.id.toLowerCase().includes(q)
      );
    }
    return true;
  });

  const getBadgeClass = (state: string) => {
    switch (state) {
      case "SUCCEEDED":
        return "badge-succeeded";
      case "RUNNING":
        return "badge-running";
      case "READY":
        return "badge-ready";
      case "WAITING_APPROVAL":
        return "badge-waiting";
      case "FAILED":
        return "badge-failed";
      case "PAUSED":
        return "badge-paused";
      case "DRAFT":
      default:
        return "badge-draft";
    }
  };

  return (
    <div>
      {/* Page Header */}
      <div className="page-header">
        <div>
          <h1 className="page-title">Missions Orchestration Registry</h1>
          <p className="page-subtitle">
            Autonomous DAG planning, execution lifecycle tracking, and publisher handoffs.
          </p>
        </div>
        <div style={{ display: "flex", gap: "0.75rem" }}>
          <button
            type="button"
            onClick={fetchMissions}
            className="btn btn-secondary"
          >
            ↻ Refresh
          </button>
          <Link href="/missions/new" className="btn btn-primary">
            + Create Mission
          </Link>
        </div>
      </div>

      {/* Dev Mode Notice Banner */}
      {mode === "DEVELOPMENT" && (
        <div className="banner-dev-mode">
          <div>
            <strong>DEVELOPMENT MODE ACTIVE:</strong> Showing all database test missions (Page {page + 1}, {pageSize} items/page). Automated smoke runs and test fixtures are listed.
          </div>
          <span className="badge badge-warning">RAW FIXTURES</span>
        </div>
      )}

      {/* Filter and Search Bar */}
      <div className="filter-bar">
        <div className="search-input-wrapper">
          <span className="search-icon">🔍</span>
          <input
            type="text"
            className="input"
            placeholder="Search missions by title, objective, or ID..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        <select
          className="select"
          value={stateFilter}
          onChange={(e) => setStateFilter(e.target.value)}
        >
          <option value="ALL">All States</option>
          <option value="DRAFT">DRAFT</option>
          <option value="READY">READY</option>
          <option value="RUNNING">RUNNING</option>
          <option value="WAITING_APPROVAL">WAITING_APPROVAL</option>
          <option value="SUCCEEDED">SUCCEEDED</option>
          <option value="FAILED">FAILED</option>
          <option value="PAUSED">PAUSED</option>
        </select>

        <select
          className="select"
          value={autonomyFilter}
          onChange={(e) => setAutonomyFilter(e.target.value)}
        >
          <option value="ALL">All Autonomy</option>
          <option value="SUPERVISED">SUPERVISED</option>
          <option value="AUTONOMOUS">AUTONOMOUS</option>
          <option value="MANUAL">MANUAL</option>
        </select>
      </div>

      {/* Error Alert */}
      {error && (
        <div style={{ padding: "1rem", background: "var(--status-danger-bg)", border: "1px solid var(--status-danger-border)", borderRadius: "var(--radius-sm)", color: "var(--status-danger)", marginBottom: "1.5rem", fontSize: "0.85rem" }}>
          {error}
        </div>
      )}

      {/* Missions Table / List */}
      {loading ? (
        <div style={{ textAlign: "center", padding: "4rem 0", color: "var(--text-muted)", fontSize: "0.9rem" }}>
          Loading missions registry...
        </div>
      ) : filteredMissions.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">⚡</div>
          <h3>No Missions Found</h3>
          <p>
            {mode === "OPERATOR"
              ? "No operational missions match your filters for the Canary Channel. Switch to Development Mode to view test missions or launch Canary Mission #2."
              : "No development missions match the current query."}
          </p>
          <Link href="/missions/new" className="btn btn-primary">
            Create First Mission
          </Link>
        </div>
      ) : (
        <div className="table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: "32%" }}>Mission / Objective</th>
                <th style={{ width: "16%" }}>Channel</th>
                <th style={{ width: "14%" }}>State</th>
                <th style={{ width: "12%" }}>Autonomy</th>
                <th style={{ width: "14%" }}>Created</th>
                <th style={{ width: "12%", textAlign: "right" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredMissions.map((m) => {
                const isCanary = m.channel_id === CANARY_CHANNEL_ID;
                return (
                  <tr key={m.id}>
                    <td>
                      <div style={{ fontWeight: 600, color: "var(--text-primary)", marginBottom: "0.2rem" }}>
                        {m.title}
                      </div>
                      <div style={{ fontSize: "0.78rem", color: "var(--text-secondary)", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                        {m.objective}
                      </div>
                    </td>
                    <td>
                      {isCanary ? (
                        <span className="badge badge-canary">DmYTB Canary</span>
                      ) : m.channel_id ? (
                        <span className="text-mono text-muted" style={{ fontSize: "0.75rem" }}>
                          {m.channel_id.slice(0, 8)}...
                        </span>
                      ) : (
                        <span className="text-muted" style={{ fontSize: "0.75rem" }}>Standalone</span>
                      )}
                    </td>
                    <td>
                      <span className={`badge ${getBadgeClass(m.state)}`}>
                        {m.state}
                      </span>
                    </td>
                    <td>
                      <span className="text-mono" style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                        {m.autonomy_level}
                      </span>
                    </td>
                    <td>
                      <span className="text-mono text-muted" style={{ fontSize: "0.78rem" }}>
                        {new Date(m.created_at).toLocaleString(undefined, {
                          month: "short",
                          day: "numeric",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <Link
                        href={`/missions/${m.id}`}
                        className="btn btn-secondary btn-sm"
                      >
                        Inspect DAG →
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Development Mode Pagination Controls */}
      {mode === "DEVELOPMENT" && (
        <div className="pagination-controls">
          <span>
            Page {page + 1} ({filteredMissions.length} records shown)
          </span>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button
              type="button"
              disabled={page === 0 || loading}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              className="btn btn-secondary btn-sm"
            >
              ← Previous
            </button>
            <button
              type="button"
              disabled={filteredMissions.length < pageSize || loading}
              onClick={() => setPage((p) => p + 1)}
              className="btn btn-secondary btn-sm"
            >
              Next →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
