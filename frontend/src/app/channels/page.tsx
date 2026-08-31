"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Channel, getChannels, getChannel, listPlatformAccounts, PlatformAccount } from "@/lib/api";
import { useOperatorContext, CANARY_CHANNEL_ID } from "@/lib/operator-context";

interface ChannelWithAccount {
  channel: Channel;
  account: PlatformAccount | null;
  isCanary: boolean;
}

export default function ChannelsPage() {
  const { mode } = useOperatorContext();
  const [channelsData, setChannelsData] = useState<ChannelWithAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [page, setPage] = useState(0);
  const pageSize = 24;

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      if (mode === "OPERATOR") {
        // In Operator Mode: Authoritatively fetch the known Canary Channel and its connected platform account
        const [canaryChan, canaryAccounts] = await Promise.all([
          getChannel(CANARY_CHANNEL_ID).catch(() => null),
          listPlatformAccounts(CANARY_CHANNEL_ID).catch(() => []),
        ]);

        const items: ChannelWithAccount[] = [];
        if (canaryChan) {
          const activeAccount = canaryAccounts.find((a) => a.status === "ACTIVE") || canaryAccounts[0] || null;
          items.push({
            channel: canaryChan,
            account: activeAccount,
            isCanary: true,
          });
        }

        setChannelsData(items);
      } else {
        // In Development Mode: Fetch paginated records from database with safety limit
        const apiChannels = await getChannels(
          statusFilter !== "ALL" ? statusFilter : undefined,
          undefined,
          pageSize,
          page * pageSize
        );

        // Enhance with accounts where possible
        const enhanced: ChannelWithAccount[] = apiChannels.map((c) => ({
          channel: c,
          account: null,
          isCanary: c.id === CANARY_CHANNEL_ID,
        }));

        setChannelsData(enhanced);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load channels");
    } finally {
      setLoading(false);
    }
  }, [mode, page, statusFilter]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const filteredChannels = channelsData.filter((item) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      item.channel.name.toLowerCase().includes(q) ||
      item.channel.slug.toLowerCase().includes(q) ||
      item.channel.id.toLowerCase().includes(q)
    );
  });

  const getBadgeClass = (state: string) => {
    switch (state) {
      case "ACTIVE":
        return "badge-active";
      case "PAUSED":
        return "badge-paused";
      case "ARCHIVED":
        return "badge-failed";
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
          <h1 className="page-title">Channel Fleet Workspaces</h1>
          <p className="page-subtitle">
            Autonomous channel identities, target audience profiles, brand voice, and DNA revisions.
          </p>
        </div>
        <div style={{ display: "flex", gap: "0.75rem" }}>
          <button
            type="button"
            onClick={() => loadData()}
            className="btn btn-secondary"
          >
            ↻ Refresh
          </button>
          <Link href="/channels/new" className="btn btn-primary">
            + New Channel
          </Link>
        </div>
      </div>

      {/* Dev Mode Notice Banner */}
      {mode === "DEVELOPMENT" && (
        <div className="banner-dev-mode">
          <div>
            <strong>DEVELOPMENT MODE ACTIVE:</strong> Showing raw database fixtures (Page {page + 1}, {pageSize} items/page). Automated smoke/test records are visible.
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
            placeholder="Search channels by name, slug, or ID..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        {mode === "DEVELOPMENT" && (
          <select
            className="select"
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setPage(0);
            }}
          >
            <option value="ALL">All States</option>
            <option value="ACTIVE">ACTIVE</option>
            <option value="DRAFT">DRAFT</option>
            <option value="PAUSED">PAUSED</option>
            <option value="ARCHIVED">ARCHIVED</option>
          </select>
        )}
      </div>

      {/* Error Alert */}
      {error && (
        <div style={{ padding: "1rem", background: "var(--status-danger-bg)", border: "1px solid var(--status-danger-border)", borderRadius: "var(--radius-sm)", color: "var(--status-danger)", marginBottom: "1.5rem", fontSize: "0.85rem" }}>
          {error}
        </div>
      )}

      {/* Main Channel Cards Grid */}
      {loading ? (
        <div style={{ textAlign: "center", padding: "4rem 0", color: "var(--text-muted)", fontSize: "0.9rem" }}>
          Loading channel workspaces...
        </div>
      ) : filteredChannels.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">⊞</div>
          <h3>No Channels Found</h3>
          <p>
            {mode === "OPERATOR"
              ? "No operational channels match your filter criteria. Switch to Development Mode to view test fixtures or create a new channel."
              : "No development channels found for this query."}
          </p>
          <Link href="/channels/new" className="btn btn-primary">
            Create Channel
          </Link>
        </div>
      ) : (
        <div className="grid-cards">
          {filteredChannels.map(({ channel: c, account, isCanary }) => (
            <div key={c.id} className="card" style={{ display: "flex", flexDirection: "column" }}>
              {/* Card Top */}
              <div className="card-header">
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                  <span style={{ fontSize: "1.25rem" }}>
                    {c.platform === "YOUTUBE" ? "▶" : "◈"}
                  </span>
                  <span className="text-secondary" style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em" }}>
                    {c.platform}
                  </span>
                </div>
                <div style={{ display: "flex", gap: "0.4rem" }}>
                  {isCanary && <span className="badge badge-canary">CANARY FLEET</span>}
                  <span className={`badge ${getBadgeClass(c.state)}`}>{c.state}</span>
                </div>
              </div>

              {/* Channel Identity */}
              <div style={{ marginBottom: "1rem" }}>
                <h3 style={{ fontSize: "1.15rem", fontWeight: 700, color: "var(--text-primary)", marginBottom: "0.25rem" }}>
                  {c.name}
                </h3>
                <div className="text-mono text-muted" style={{ fontSize: "0.75rem" }}>
                  slug: {c.slug}
                </div>
              </div>

              {/* Metadata Fields */}
              <div style={{ display: "flex", flexDirection: "column", gap: "0.45rem", fontSize: "0.82rem", background: "var(--bg-secondary)", padding: "0.75rem", borderRadius: "var(--radius-sm)", marginBottom: "1rem" }}>
                <div className="flex-between">
                  <span className="text-muted">Niche:</span>
                  <span style={{ fontWeight: 600 }}>{c.dna?.content_strategy?.niche || "General"}</span>
                </div>
                <div className="flex-between">
                  <span className="text-muted">Language:</span>
                  <span className="text-mono">{c.primary_language || "en"}</span>
                </div>
                <div className="flex-between">
                  <span className="text-muted">Connected Account:</span>
                  {account ? (
                    <span style={{ color: "var(--status-success)", fontWeight: 600 }}>
                      ● {account.account_display_name} ({account.status})
                    </span>
                  ) : isCanary ? (
                    <span style={{ color: "var(--status-success)", fontWeight: 600 }}>
                      ● DmYTB (ACTIVE)
                    </span>
                  ) : (
                    <span className="text-muted">Not Connected</span>
                  )}
                </div>
                <div className="flex-between">
                  <span className="text-muted">Created:</span>
                  <span className="text-mono text-muted">
                    {new Date(c.created_at).toLocaleDateString()}
                  </span>
                </div>
              </div>

              {/* Card Footer Actions */}
              <div style={{ marginTop: "auto", display: "flex", gap: "0.5rem", paddingTop: "0.75rem", borderTop: "1px solid var(--border-subtle)" }}>
                <Link
                  href={`/channels/${c.id}`}
                  className="btn btn-secondary btn-sm"
                  style={{ flex: 1 }}
                >
                  Workspace →
                </Link>
                <Link
                  href={`/missions/new?channel_id=${c.id}`}
                  className="btn btn-primary btn-sm"
                  style={{ flex: 1 }}
                >
                  + Mission
                </Link>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Development Mode Pagination */}
      {mode === "DEVELOPMENT" && (
        <div className="pagination-controls">
          <span>
            Page {page + 1} ({filteredChannels.length} records shown)
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
              disabled={filteredChannels.length < pageSize || loading}
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
