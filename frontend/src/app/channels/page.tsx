"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Channel,
  getChannel,
  getChannelCount,
  getChannels,
  listPlatformAccounts,
  PlatformAccount,
} from "@/lib/api";
import { CANARY_CHANNEL_ID, useOperatorContext } from "@/lib/operator-context";

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

  // Search & Filter State
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");

  // Pagination State (1-indexed)
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(24);
  const [totalCount, setTotalCount] = useState(0);
  const [goToPageInput, setGoToPageInput] = useState("");

  // Initialize from URL parameters on first mount
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const qParam = params.get("q");
    const statusParam = params.get("status");
    const pageParam = params.get("page");
    const limitParam = params.get("limit");

    if (qParam) {
      setSearchQuery(qParam);
      setDebouncedSearch(qParam);
    }
    if (statusParam) {
      setStatusFilter(statusParam);
    }
    if (pageParam) {
      const parsedPage = parseInt(pageParam, 10);
      if (!isNaN(parsedPage) && parsedPage >= 1) {
        setPage(parsedPage);
      }
    }
    if (limitParam) {
      const parsedLimit = parseInt(limitParam, 10);
      if ([24, 50, 100].includes(parsedLimit)) {
        setPageSize(parsedLimit);
      }
    }
  }, []);

  // Debounce search query changes
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(searchQuery);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Sync state with URL query parameters
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams();
    if (debouncedSearch.trim()) params.set("q", debouncedSearch.trim());
    if (statusFilter !== "ALL") params.set("status", statusFilter);
    if (page > 1) params.set("page", String(page));
    if (pageSize !== 24) params.set("limit", String(pageSize));

    const newUrl = params.toString() ? `/channels?${params.toString()}` : "/channels";
    window.history.replaceState(null, "", newUrl);
  }, [debouncedSearch, statusFilter, page, pageSize]);

  // Reset page to 1 when search or status filter or pageSize changes
  const handleSearchChange = (val: string) => {
    setSearchQuery(val);
    setPage(1);
  };

  const handleStatusFilterChange = (val: string) => {
    setStatusFilter(val);
    setPage(1);
  };

  const handlePageSizeChange = (val: number) => {
    setPageSize(val);
    setPage(1);
  };

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      if (mode === "OPERATOR") {
        // In Operator Mode: Fetch Canary Channel
        const [canaryChan, canaryAccounts] = await Promise.all([
          getChannel(CANARY_CHANNEL_ID).catch(() => null),
          listPlatformAccounts(CANARY_CHANNEL_ID).catch(() => []),
        ]);

        const items: ChannelWithAccount[] = [];
        if (canaryChan) {
          const activeAccount =
            canaryAccounts.find((a) => a.status === "ACTIVE") || canaryAccounts[0] || null;
          items.push({
            channel: canaryChan,
            account: activeAccount,
            isCanary: true,
          });
        }

        setChannelsData(items);
        setTotalCount(items.length);
      } else {
        // In Development Mode: Server-side search & pagination
        const stateParam = statusFilter !== "ALL" ? statusFilter : undefined;
        const offset = (page - 1) * pageSize;

        const [apiChannels, count] = await Promise.all([
          getChannels(stateParam, undefined, pageSize, offset, debouncedSearch),
          getChannelCount(stateParam, undefined, debouncedSearch),
        ]);

        const enhanced: ChannelWithAccount[] = apiChannels.map((c) => ({
          channel: c,
          account: null,
          isCanary: c.id === CANARY_CHANNEL_ID,
        }));

        setChannelsData(enhanced);
        setTotalCount(count);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load channels");
    } finally {
      setLoading(false);
    }
  }, [mode, page, pageSize, statusFilter, debouncedSearch]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));

  // Compute pagination numbered range with compact window & ellipsis
  const getPaginationPages = (): (number | "...")[] => {
    if (totalPages <= 7) {
      return Array.from({ length: totalPages }, (_, i) => i + 1);
    }
    if (page <= 4) {
      return [1, 2, 3, 4, 5, "...", totalPages];
    }
    if (page >= totalPages - 3) {
      return [1, "...", totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages];
    }
    return [1, "...", page - 1, page, page + 1, "...", totalPages];
  };

  const handleGoToPage = (e: React.FormEvent) => {
    e.preventDefault();
    const target = parseInt(goToPageInput, 10);
    if (!isNaN(target) && target >= 1 && target <= totalPages) {
      setPage(target);
      setGoToPageInput("");
    }
  };

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

  const startRecord = totalCount === 0 ? 0 : (page - 1) * pageSize + 1;
  const endRecord = Math.min(page * pageSize, totalCount);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
      {/* Page Header */}
      <div className="page-header">
        <div>
          <h1 className="page-title">Channel Fleet Workspaces</h1>
          <p className="page-subtitle">
            Autonomous channel identities, target audience profiles, brand voice, and DNA revisions.
          </p>
        </div>
        <div style={{ display: "flex", gap: "0.75rem", alignItems: "center" }}>
          <button
            type="button"
            onClick={() => loadData()}
            className="btn btn-secondary"
            disabled={loading}
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
            <strong>DEVELOPMENT MODE ACTIVE:</strong> Showing fleet database records with global server-side search across all pages.
          </div>
          <span className="badge badge-warning">RAW FIXTURES</span>
        </div>
      )}

      {/* Filter and Search Bar */}
      <div className="filter-bar" style={{ display: "flex", gap: "1rem", flexWrap: "wrap", alignItems: "center" }}>
        <div className="search-input-wrapper" style={{ flex: 1, minWidth: "260px" }}>
          <span className="search-icon">🔍</span>
          <input
            type="text"
            className="input"
            placeholder="Search all channels by name, slug, or UUID across fleet..."
            value={searchQuery}
            onChange={(e) => handleSearchChange(e.target.value)}
          />
          {searchQuery && (
            <button
              onClick={() => handleSearchChange("")}
              className="btn btn-secondary btn-sm"
              style={{
                position: "absolute",
                right: "8px",
                top: "50%",
                transform: "translateY(-50%)",
                padding: "0.15rem 0.4rem",
                fontSize: "0.75rem",
              }}
              title="Clear search"
            >
              ✕
            </button>
          )}
        </div>

        {mode === "DEVELOPMENT" && (
          <div style={{ display: "flex", gap: "0.75rem", alignItems: "center", flexWrap: "wrap" }}>
            <select
              className="select"
              value={statusFilter}
              onChange={(e) => handleStatusFilterChange(e.target.value)}
              style={{ minWidth: "130px" }}
            >
              <option value="ALL">All States</option>
              <option value="ACTIVE">ACTIVE</option>
              <option value="DRAFT">DRAFT</option>
              <option value="PAUSED">PAUSED</option>
              <option value="ARCHIVED">ARCHIVED</option>
            </select>

            <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
              <span style={{ fontSize: "0.78rem", color: "var(--text-muted)" }}>Per page:</span>
              <select
                className="select"
                value={pageSize}
                onChange={(e) => handlePageSizeChange(parseInt(e.target.value, 10))}
                style={{ fontSize: "0.8rem", padding: "0.35rem 0.6rem" }}
              >
                <option value={24}>24</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
              </select>
            </div>
          </div>
        )}
      </div>

      {/* Result Metrics Bar */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "0.82rem", color: "var(--text-secondary)" }}>
        <div>
          {loading ? (
            <span>Searching fleet...</span>
          ) : (
            <span>
              Showing <strong>{startRecord}–{endRecord}</strong> of <strong>{totalCount}</strong> channels
              {debouncedSearch && <span> matching &ldquo;{debouncedSearch}&rdquo;</span>}
              {statusFilter !== "ALL" && <span> (Status: {statusFilter})</span>}
            </span>
          )}
        </div>
        {mode === "DEVELOPMENT" && totalPages > 1 && (
          <div style={{ color: "var(--text-muted)", fontSize: "0.78rem" }}>
            Page {page} of {totalPages}
          </div>
        )}
      </div>

      {/* Error Alert */}
      {error && (
        <div
          style={{
            padding: "1rem",
            background: "var(--status-danger-bg)",
            border: "1px solid var(--status-danger-border)",
            borderRadius: "var(--radius-sm)",
            color: "var(--status-danger)",
            fontSize: "0.85rem",
          }}
        >
          {error}
        </div>
      )}

      {/* Main Channel Cards Grid */}
      {loading ? (
        <div style={{ textAlign: "center", padding: "4rem 0", color: "var(--text-muted)", fontSize: "0.9rem" }}>
          Loading channel workspaces...
        </div>
      ) : channelsData.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">⊞</div>
          <h3>No Channels Found</h3>
          <p>
            {debouncedSearch
              ? `No channels found matching "${debouncedSearch}"${statusFilter !== "ALL" ? ` with status ${statusFilter}` : ""}.`
              : mode === "OPERATOR"
              ? "No operational channels match your filter criteria. Switch to Development Mode to view test fixtures or create a new channel."
              : "No development channels found for this query."}
          </p>
          {debouncedSearch && (
            <button
              onClick={() => handleSearchChange("")}
              className="btn btn-secondary"
              style={{ marginTop: "0.5rem" }}
            >
              Clear Search Query
            </button>
          )}
        </div>
      ) : (
        <div className="grid-cards">
          {channelsData.map(({ channel: c, account, isCanary }) => (
            <div key={c.id} className="card" style={{ display: "flex", flexDirection: "column" }}>
              {/* Card Top */}
              <div className="card-header">
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                  <span style={{ fontSize: "1.25rem" }}>
                    {c.platform === "YOUTUBE" ? "▶" : "◈"}
                  </span>
                  <span
                    className="text-secondary"
                    style={{
                      fontSize: "0.75rem",
                      fontWeight: 700,
                      textTransform: "uppercase",
                      letterSpacing: "0.06em",
                    }}
                  >
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
                <h3
                  style={{
                    fontSize: "1.15rem",
                    fontWeight: 700,
                    color: "var(--text-primary)",
                    marginBottom: "0.25rem",
                  }}
                >
                  {c.name}
                </h3>
                <div className="text-mono text-muted" style={{ fontSize: "0.75rem" }}>
                  slug: /{c.slug}
                </div>
              </div>

              {/* Metadata Fields */}
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "0.45rem",
                  fontSize: "0.82rem",
                  background: "var(--bg-secondary)",
                  padding: "0.75rem",
                  borderRadius: "var(--radius-sm)",
                  marginBottom: "1rem",
                }}
              >
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
              <div
                style={{
                  marginTop: "auto",
                  display: "flex",
                  gap: "0.5rem",
                  paddingTop: "0.75rem",
                  borderTop: "1px solid var(--border-subtle)",
                }}
              >
                <Link
                  href={`/channels/${c.id}`}
                  className="btn btn-secondary btn-sm"
                  style={{ flex: 1, textAlign: "center" }}
                >
                  Workspace →
                </Link>
                <Link
                  href={`/missions/new?channel_id=${c.id}`}
                  className="btn btn-primary btn-sm"
                  style={{ flex: 1, textAlign: "center" }}
                >
                  + Mission
                </Link>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Enhanced Pagination Controls */}
      {mode === "DEVELOPMENT" && totalPages > 1 && (
        <div
          className="card"
          style={{
            padding: "0.85rem 1.25rem",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: "1rem",
          }}
        >
          {/* Left: First & Prev Buttons */}
          <div style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}>
            <button
              type="button"
              disabled={page === 1 || loading}
              onClick={() => setPage(1)}
              className="btn btn-secondary btn-sm"
              style={{ padding: "0.3rem 0.6rem", fontSize: "0.78rem" }}
              title="First Page"
            >
              « First
            </button>
            <button
              type="button"
              disabled={page === 1 || loading}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="btn btn-secondary btn-sm"
              style={{ padding: "0.3rem 0.6rem", fontSize: "0.78rem" }}
              title="Previous Page"
            >
              ‹ Prev
            </button>
          </div>

          {/* Center: Numbered Page Window */}
          <div style={{ display: "flex", alignItems: "center", gap: "0.3rem", flexWrap: "wrap" }}>
            {getPaginationPages().map((pageNum, idx) => {
              if (pageNum === "...") {
                return (
                  <span
                    key={`ellipsis-${idx}`}
                    style={{
                      padding: "0.3rem 0.5rem",
                      color: "var(--text-muted)",
                      fontSize: "0.85rem",
                    }}
                  >
                    …
                  </span>
                );
              }
              const isActive = page === pageNum;
              return (
                <button
                  key={`page-${pageNum}`}
                  type="button"
                  onClick={() => setPage(pageNum as number)}
                  disabled={loading}
                  className={`btn btn-sm ${isActive ? "btn-primary" : "btn-secondary"}`}
                  style={{
                    padding: "0.3rem 0.65rem",
                    fontSize: "0.8rem",
                    minWidth: "32px",
                    fontWeight: isActive ? 700 : 500,
                  }}
                >
                  {pageNum}
                </button>
              );
            })}
          </div>

          {/* Right: Next & Last Buttons + Go to page Input */}
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}>
              <button
                type="button"
                disabled={page >= totalPages || loading}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                className="btn btn-secondary btn-sm"
                style={{ padding: "0.3rem 0.6rem", fontSize: "0.78rem" }}
                title="Next Page"
              >
                Next ›
              </button>
              <button
                type="button"
                disabled={page >= totalPages || loading}
                onClick={() => setPage(totalPages)}
                className="btn btn-secondary btn-sm"
                style={{ padding: "0.3rem 0.6rem", fontSize: "0.78rem" }}
                title="Last Page"
              >
                Last »
              </button>
            </div>

            {/* Direct Jump Input */}
            <form onSubmit={handleGoToPage} style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}>
              <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>Go to:</span>
              <input
                type="number"
                min={1}
                max={totalPages}
                placeholder={String(page)}
                value={goToPageInput}
                onChange={(e) => setGoToPageInput(e.target.value)}
                className="input"
                style={{ width: "52px", padding: "0.25rem 0.4rem", fontSize: "0.78rem", textAlign: "center" }}
              />
              <button
                type="submit"
                disabled={!goToPageInput || loading}
                className="btn btn-secondary btn-sm"
                style={{ padding: "0.25rem 0.5rem", fontSize: "0.75rem" }}
              >
                Go
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
