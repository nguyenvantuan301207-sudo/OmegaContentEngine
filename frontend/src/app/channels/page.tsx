"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { getChannelCount, getChannels, type Channel } from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";
import { Alert, EmptyState, LoadingState, StatusBadge } from "@/components/ui";
import { statusTone } from "@/lib/presentation";
import { classifyChannel } from "@/lib/channel-classification";

export default function ChannelsPage() {
  const {
    mode,
    selectedChannelId,
    setSelectedChannelId,
    showInternalChannels,
    setShowInternalChannels,
  } = useOperatorContext();

  const [channels, setChannels] = useState<Channel[]>([]);
  const [selectedChannel, setSelectedChannel] = useState<Channel | null>(null);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [status, setStatus] = useState("ALL");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(24);
  const [totalCount, setTotalCount] = useState(0);
  const [lifecycleCounts, setLifecycleCounts] = useState<{
    persisted: number;
    active: number;
    draft: number;
    archived: number;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let isMounted = true;
    void Promise.all([
      getChannelCount(undefined),
      getChannelCount("ACTIVE"),
      getChannelCount("DRAFT"),
      getChannelCount("ARCHIVED"),
    ]).then(([persisted, active, draft, archived]) => {
      if (isMounted) {
        setLifecycleCounts({ persisted, active, draft, archived });
      }
    }).catch(() => {});
    return () => { isMounted = false; };
  }, []);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timeout);
  }, [search]);

  useEffect(() => {
    const params = new URLSearchParams();
    if (debouncedSearch) params.set("q", debouncedSearch);
    if (status !== "ALL" && mode === "DEVELOPMENT") params.set("status", status);
    if (page > 1) params.set("page", String(page));
    if (pageSize !== 24) params.set("limit", String(pageSize));
    window.history.replaceState(null, "", params.size ? `/channels?${params}` : "/channels");
  }, [debouncedSearch, mode, page, pageSize, status]);

  const loadChannels = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const state = mode === "OPERATOR" ? "ACTIVE" : status === "ALL" ? undefined : status;
      const offset = (page - 1) * pageSize;
      const [records, count] = await Promise.all([
        getChannels(state, undefined, pageSize, offset, debouncedSearch || undefined),
        getChannelCount(state, undefined, debouncedSearch || undefined),
      ]);
      setChannels(records);
      setTotalCount(count);

      if (records.length > 0) {
        setSelectedChannel((current) => {
          if (current && records.some((r) => r.id === current.id)) {
            return records.find((r) => r.id === current.id) || current;
          }
          if (selectedChannelId) {
            const match = records.find((r) => r.id === selectedChannelId);
            if (match) return match;
          }
          return records[0];
        });
      } else {
        setSelectedChannel(null);
      }
    } catch (requestError: unknown) {
      setError(requestError instanceof Error ? requestError.message : "Failed to load channels.");
    } finally {
      setLoading(false);
    }
  }, [debouncedSearch, mode, page, pageSize, selectedChannelId, status]);

  useEffect(() => {
    void loadChannels();
  }, [loadChannels]);

  const handleSelect = (channel: Channel) => {
    setSelectedChannel(channel);
    void setSelectedChannelId(channel.id);
  };

  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));

  // Classify all loaded channels
  const classifiedChannels = useMemo(() => {
    return channels.map((ch) => ({
      channel: ch,
      provenance: classifyChannel(ch),
    }));
  }, [channels]);

  // Filter based on product visibility policy:
  // Normal mode (showInternalChannels === false): show REAL_USER + UNKNOWN only
  // (Keep selectedChannel addressable even if internal)
  // Internal mode (showInternalChannels === true): show all
  const displayChannels = useMemo(() => {
    return classifiedChannels.filter(({ channel, provenance }) => {
      if (showInternalChannels) return true;
      if (channel.id === selectedChannelId) return true;
      return !provenance.isInternal;
    });
  }, [classifiedChannels, showInternalChannels, selectedChannelId]);

  const hiddenInternalCount = classifiedChannels.length - displayChannels.length;
  const selectedProvenance = selectedChannel ? classifyChannel(selectedChannel) : null;

  return (
    <div className="ui-page-stack" style={{ maxWidth: "1400px", margin: "0 auto" }}>
      {/* HEADER */}
      <div className="page-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "16px", marginBottom: "16px", flexWrap: "wrap" }}>
        <div>
          <div className="title" style={{ fontSize: "24px", fontWeight: 800, letterSpacing: "-0.03em" }}>
            Channels
          </div>
          <div className="sub" style={{ color: "var(--muted)", fontSize: "13px", marginTop: "4px" }}>
            Channel workspace registry, DNA strategy, platform settings, and publishing policies.
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
          {/* INTERNAL VISIBILITY TOGGLE */}
          <div style={{ display: "inline-flex", alignItems: "center", gap: "8px", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: "8px", padding: "6px 12px" }}>
            <span style={{ fontSize: "12px", color: "var(--text-secondary)", fontWeight: 500 }}>
              Show internal / test
            </span>
            <button
              type="button"
              className={`switch ${showInternalChannels ? "on" : ""}`}
              aria-pressed={showInternalChannels}
              onClick={() => setShowInternalChannels(!showInternalChannels)}
              aria-label="Toggle internal channels visibility"
            />
          </div>

          <button type="button" className="btn" onClick={() => void loadChannels()} disabled={loading} style={{ fontSize: "12px", padding: "8px 12px" }}>
            Refresh
          </button>
          <Link href="/channels/new" className="btn primary" style={{ fontSize: "12px", padding: "8px 14px", fontWeight: 700 }}>
            ＋ Create Channel
          </Link>
        </div>
      </div>

      {showInternalChannels && (
        <Alert tone="info" title="Internal / Test Mode Active">
          Internal test fixtures, historical canaries, and diagnostic channels are visible with provenance badges.
        </Alert>
      )}

      {error && (
        <Alert tone="danger" title="Channels unavailable" actions={<button type="button" className="btn btn-secondary btn-sm" onClick={() => void loadChannels()}>Retry</button>}>
          {error}
        </Alert>
      )}

      {/* FILTER BAR */}
      <div className="ui-filter-bar" role="search" style={{ marginBottom: "14px", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: "10px", padding: "10px 14px" }}>
        <div className="ui-filter-field ui-filter-grow">
          <label htmlFor="channel-search" style={{ fontSize: "10px", textTransform: "uppercase", color: "var(--muted)", letterSpacing: "0.08em" }}>Search Channels</label>
          <input
            id="channel-search"
            className="input"
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search by name, slug or platform..."
            style={{ background: "var(--bg-input)", border: "1px solid var(--line)", color: "var(--text)" }}
          />
        </div>
        {(mode === "DEVELOPMENT" || showInternalChannels) && (
          <div className="ui-filter-field">
            <label htmlFor="channel-status" style={{ fontSize: "10px", textTransform: "uppercase", color: "var(--muted)", letterSpacing: "0.08em" }}>Status</label>
            <select id="channel-status" className="select" value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}>
              <option value="ALL">All states</option>
              <option value="ACTIVE">Active</option>
              <option value="DRAFT">Draft</option>
              <option value="PAUSED">Paused</option>
              <option value="ARCHIVED">Archived</option>
            </select>
          </div>
        )}
        <div className="ui-filter-field">
          <label htmlFor="channel-page-size" style={{ fontSize: "10px", textTransform: "uppercase", color: "var(--muted)", letterSpacing: "0.08em" }}>Rows</label>
          <select id="channel-page-size" className="select" value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1); }}>
            <option value={24}>24</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </div>
      </div>

      {loading ? (
        <LoadingState title="Loading channels" />
      ) : displayChannels.length === 0 ? (
        <EmptyState
          title={channels.length > 0 ? "No product channels in this view" : "No channels found"}
          description={
            channels.length > 0
              ? `${channels.length} internal/test channels are present on this page, but hidden in normal mode.`
              : debouncedSearch
              ? "No channel matches the current search and filters."
              : "No channel records are available in this view."
          }
          action={
            channels.length > 0 && !showInternalChannels ? (
              <button type="button" className="btn btn-secondary" onClick={() => setShowInternalChannels(true)}>
                Show internal/test channels
              </button>
            ) : debouncedSearch ? (
              <button type="button" className="btn btn-secondary" onClick={() => setSearch("")}>
                Clear search
              </button>
            ) : (
              <Link href="/channels/new" className="btn btn-primary">
                Create channel
              </Link>
            )
          }
        />
      ) : (
        /* HYBRID PRODUCT WORKSPACE BROWSER (LEFT LIST + RIGHT DETAIL) */
        <div className="channels-browser-grid">
          {/* LEFT: COMPACT REGISTRY LIST */}
          <div className="channel-registry-list">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "4px 8px", fontSize: "11px", color: "var(--muted)", flexWrap: "wrap", gap: "6px" }}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
                <span>
                  <strong>{displayChannels.length} shown</strong>
                  {hiddenInternalCount > 0 ? ` · ${hiddenInternalCount} internal hidden` : ""}
                  {` · ${totalCount.toLocaleString()} ${mode === "OPERATOR" ? "active" : "persisted"}`}
                </span>
                {lifecycleCounts && (
                  <span
                    title={`Authoritative lifecycle counts:\n• ${lifecycleCounts.persisted.toLocaleString()} persisted\n• ${lifecycleCounts.active.toLocaleString()} active\n• ${lifecycleCounts.draft.toLocaleString()} draft\n• ${lifecycleCounts.archived.toLocaleString()} archived`}
                    style={{
                      cursor: "help",
                      fontSize: "10px",
                      color: "var(--muted)",
                      padding: "0 5px",
                      borderRadius: "4px",
                      border: "1px solid var(--line)",
                      background: "var(--bg-tertiary)",
                      userSelect: "none",
                    }}
                    aria-label="Lifecycle breakdown info"
                  >
                    ℹ {lifecycleCounts.persisted.toLocaleString()} persisted
                  </span>
                )}
              </span>
              <span>Select to inspect</span>
            </div>

            {displayChannels.map(({ channel: ch, provenance }) => {
              const isSelected = selectedChannel?.id === ch.id;
              return (
                <div
                  key={ch.id}
                  className={`channel-registry-row${isSelected ? " selected" : ""}`}
                  onClick={() => handleSelect(ch)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") handleSelect(ch); }}
                >
                  <div className="channel-row-left">
                    <div className="channel-avatar-mark">
                      {ch.name.slice(0, 2).toUpperCase()}
                    </div>
                    <div className="channel-row-meta">
                      <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                        <strong className="channel-row-name">{ch.name}</strong>
                        {provenance.isInternal && (
                          <StatusBadge tone={provenance.tone === "purple" ? "info" : provenance.tone}>{provenance.badgeLabel}</StatusBadge>
                        )}
                        {!provenance.isInternal && provenance.classification === "UNKNOWN" && (
                          <StatusBadge tone="neutral">UNCLASSIFIED</StatusBadge>
                        )}
                      </div>
                      <span className="channel-row-sub">
                        {ch.platform} · /{ch.slug}
                      </span>
                    </div>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <StatusBadge tone={statusTone(ch.state)}>{ch.state}</StatusBadge>
                    <span style={{ fontSize: "12px", color: "var(--soft)" }}>›</span>
                  </div>
                </div>
              );
            })}

            {totalPages > 1 && (
              <nav className="ui-pagination" aria-label="Channel pages" style={{ marginTop: "12px" }}>
                <button type="button" className="btn btn-secondary btn-sm" disabled={page === 1} onClick={() => setPage((current) => Math.max(1, current - 1))}>Previous</button>
                <span style={{ fontSize: "12px" }}>Page {page} of {totalPages}</span>
                <button type="button" className="btn btn-secondary btn-sm" disabled={page >= totalPages} onClick={() => setPage((current) => Math.min(totalPages, current + 1))}>Next</button>
              </nav>
            )}
          </div>

          {/* RIGHT: SELECTED CHANNEL WORKSPACE SUMMARY */}
          {selectedChannel ? (
            <div className="channel-detail-card">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px", borderBottom: "1px solid var(--line)", paddingBottom: "14px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                  <div
                    className="channel-avatar-mark"
                    style={{
                      width: "44px",
                      height: "44px",
                      fontSize: "16px",
                      background: "linear-gradient(135deg, var(--accent), #5147df)",
                      color: "#fff",
                    }}
                  >
                    {selectedChannel.name.slice(0, 2).toUpperCase()}
                  </div>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                      <h2 style={{ fontSize: "18px", fontWeight: 800, margin: 0, color: "var(--text)" }}>
                        {selectedChannel.name}
                      </h2>
                      {selectedProvenance?.isInternal && (
                        <StatusBadge tone={selectedProvenance.tone === "purple" ? "info" : selectedProvenance.tone}>{selectedProvenance.badgeLabel}</StatusBadge>
                      )}
                    </div>
                    <span className="small muted">
                      {selectedChannel.platform} · {selectedChannel.primary_language}-{selectedChannel.target_region}
                    </span>
                  </div>
                </div>
                <StatusBadge tone={statusTone(selectedChannel.state)}>
                  {selectedChannel.state}
                </StatusBadge>
              </div>

              {selectedProvenance?.isInternal && (
                <div style={{ marginTop: "12px" }}>
                  <Alert tone="info" title={`Internal Provenance: ${selectedProvenance.label}`}>
                    {selectedProvenance.evidence}
                  </Alert>
                </div>
              )}

              <div style={{ marginTop: "16px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                <div style={{ background: "var(--bg-tertiary)", border: "1px solid var(--line)", borderRadius: "8px", padding: "10px 12px" }}>
                  <div style={{ fontSize: "10px", color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                    Default Language
                  </div>
                  <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--text)", marginTop: "3px" }}>
                    {selectedChannel.primary_language || "English"}
                  </div>
                </div>
                <div style={{ background: "var(--bg-tertiary)", border: "1px solid var(--line)", borderRadius: "8px", padding: "10px 12px" }}>
                  <div style={{ fontSize: "10px", color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                    Timezone
                  </div>
                  <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--text)", marginTop: "3px" }}>
                    {selectedChannel.timezone || "UTC"}
                  </div>
                </div>
              </div>

              <div style={{ marginTop: "12px" }}>
                <div style={{ fontSize: "10px", color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: "6px" }}>
                  Channel DNA &amp; Strategy
                </div>
                <div style={{ background: "var(--bg-tertiary)", border: "1px solid var(--line)", borderRadius: "8px", padding: "12px", fontSize: "12px", color: "var(--text-secondary)", lineHeight: 1.5 }}>
                  {selectedChannel.dna?.content_strategy?.niche || selectedChannel.description || "Evidence-first, concise, modern narrative tone."}
                </div>
              </div>

              {/* PUBLISHING CONFIGURATION */}
              <div style={{ marginTop: "14px", borderTop: "1px solid var(--line)", paddingTop: "12px" }}>
                <div style={{ fontSize: "11px", fontWeight: 700, color: "var(--text)", marginBottom: "8px" }}>
                  Publishing Configuration
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "7px 0", borderBottom: "1px solid var(--line)", fontSize: "12px", color: "var(--muted)" }}>
                  <span>Target Region</span>
                  <span style={{ color: "var(--text)", fontWeight: 600 }}>{selectedChannel.target_region || "US"}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "7px 0", borderBottom: "1px solid var(--line)", fontSize: "12px", color: "var(--muted)" }}>
                  <span>Slug Path</span>
                  <span className="text-mono" style={{ color: "var(--text)", fontSize: "11px" }}>/{selectedChannel.slug}</span>
                </div>
              </div>

              {/* ACTION BUTTONS */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", marginTop: "18px" }}>
                <Link
                  href={`/channels/${selectedChannel.id}`}
                  className="btn primary"
                  style={{ textAlign: "center", fontWeight: 700, fontSize: "13px" }}
                >
                  Open Workspace
                </Link>
                <Link
                  href={`/channels/${selectedChannel.id}/production`}
                  className="btn"
                  style={{ textAlign: "center", fontSize: "13px" }}
                >
                  Production Studio
                </Link>
              </div>

              {/* SECONDARY DISCLOSURE TECHNICAL ID & LINEAGE */}
              <details style={{ marginTop: "14px", fontSize: "11px", color: "var(--soft)" }}>
                <summary style={{ cursor: "pointer" }}>Technical Details &amp; Lineage</summary>
                <div style={{ marginTop: "6px", background: "var(--bg-input)", color: "var(--text)", padding: "8px 10px", borderRadius: "6px", fontSize: "11px", border: "1px solid var(--line)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "4px" }}>
                    <span className="muted">Classification Enum:</span>
                    <code className="text-mono" style={{ fontSize: "10px" }}>{selectedProvenance?.classification}</code>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span className="muted">Channel ID:</span>
                    <code className="text-mono" style={{ fontSize: "10px" }}>{selectedChannel.id}</code>
                  </div>
                </div>
              </details>
            </div>
          ) : (
            <div className="card" style={{ padding: "32px 20px", textAlign: "center", color: "var(--muted)" }}>
              Select a channel from the registry to view workspace details.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
