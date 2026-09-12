"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  listProductionRequests,
  listMediaArtifacts,
  listProductionAssets,
  listNarrationSegments,
  listSubtitleCues,
  type ProductionRequest,
} from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";
import { Alert } from "@/components/ui";

type AssetCategory = "ALL" | "VIDEO_ARTIFACT" | "SCENE_ASSET" | "NARRATION" | "SUBTITLE";

interface UnifiedAssetItem {
  id: string;
  category: "VIDEO_ARTIFACT" | "SCENE_ASSET" | "NARRATION" | "SUBTITLE";
  title: string;
  subtitle: string;
  channelId: string;
  channelName: string;
  requestId: string;
  version?: number;
  formatOrType: string;
  fileSizeBytes?: number;
  durationMs?: number | null;
  storageUri?: string;
  contentHash?: string;
  createdAt: string;
  streamUrl?: string;
  extraText?: string;
}

export default function AssetsPage() {
  const { channels, channelsLoading } = useOperatorContext();
  const [assets, setAssets] = useState<UnifiedAssetItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [channelFilter, setChannelFilter] = useState<string>("ALL");
  const [categoryFilter, setCategoryFilter] = useState<AssetCategory>("ALL");
  const [search, setSearch] = useState("");
  const [previewAssetId, setPreviewAssetId] = useState<string | null>(null);

  const loadAssets = useCallback(async () => {
    if (channels.length === 0) {
      setAssets([]);
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const targetChannels =
        channelFilter !== "ALL"
          ? channels.filter((c) => c.id === channelFilter)
          : channels;

      const unifiedList: UnifiedAssetItem[] = [];

      for (const ch of targetChannels) {
        let requests: ProductionRequest[] = [];
        try {
          requests = await listProductionRequests(ch.id);
        } catch {
          continue;
        }

        // Limit requests queried per channel to avoid overwhelming requests
        for (const req of requests.slice(0, 10)) {
          // 1. Media Artifacts (rendered video files)
          try {
            const artifacts = await listMediaArtifacts(ch.id, req.id);
            for (const art of artifacts) {
              unifiedList.push({
                id: art.id,
                category: "VIDEO_ARTIFACT",
                title: `Render Artifact v${art.version} (${art.artifact_type.toUpperCase()})`,
                subtitle: `${art.mime_type}${art.width && art.height ? ` · ${art.width}×${art.height}` : ""} · Request ${req.id.slice(0, 8)}`,
                channelId: ch.id,
                channelName: ch.name,
                requestId: req.id,
                version: art.version,
                formatOrType: art.artifact_type || "video",
                fileSizeBytes: art.file_size_bytes || undefined,
                durationMs: art.duration_ms,
                storageUri: art.storage_uri,
                contentHash: art.content_hash,
                createdAt: art.created_at,
                streamUrl: `/api/v1/channels/${ch.id}/production/${req.id}/artifacts/${art.id}/stream`,
              });
            }
          } catch {
            // Artifacts optional
          }

          // 2. Production Assets (scenes, broll, images)
          try {
            const prodAssets = await listProductionAssets(ch.id, req.id);
            for (const pa of prodAssets) {
              unifiedList.push({
                id: pa.id,
                category: "SCENE_ASSET",
                title: `${pa.asset_type || "Scene Asset"}`,
                subtitle: `${pa.provider_type || "Asset"} · ${pa.mime_type || ""} · Request ${req.id.slice(0, 8)}`,
                channelId: ch.id,
                channelName: ch.name,
                requestId: req.id,
                formatOrType: pa.asset_type || "Asset",
                storageUri: pa.storage_uri,
                contentHash: pa.content_hash,
                createdAt: pa.created_at,
                durationMs: pa.duration_ms,
              });
            }
          } catch {
            // Assets optional
          }

          // 3. Narration Segments
          try {
            const narration = await listNarrationSegments(ch.id, req.id);
            for (const narr of narration) {
              unifiedList.push({
                id: narr.id,
                category: "NARRATION",
                title: `Narration Segment (${(narr.duration_ms / 1000).toFixed(1)}s)`,
                subtitle: `Scene ${narr.scene_id.slice(0, 8)} · Request ${req.id.slice(0, 8)}`,
                channelId: ch.id,
                channelName: ch.name,
                requestId: req.id,
                formatOrType: "audio",
                durationMs: narr.duration_ms,
                createdAt: narr.created_at,
                extraText: narr.text,
              });
            }
          } catch {
            // Narration optional
          }

          // 4. Subtitle Cues
          try {
            const cues = await listSubtitleCues(ch.id, req.id);
            if (cues.length > 0) {
              // Group cues by request into a master subtitle asset
              const firstCue = cues[0];
              unifiedList.push({
                id: `subtitles-${req.id}`,
                category: "SUBTITLE",
                title: `Subtitle Track (${cues.length} cues)`,
                subtitle: `${cues[0].text.slice(0, 45)}… · Request ${req.id.slice(0, 8)}`,
                channelId: ch.id,
                channelName: ch.name,
                requestId: req.id,
                formatOrType: "subtitles",
                createdAt: firstCue.created_at,
                extraText: cues.map((c) => `[${c.start_ms}ms]: ${c.text}`).slice(0, 10).join("\n"),
              });
            }
          } catch {
            // Subtitles optional
          }
        }
      }

      // Sort newest first
      unifiedList.sort(
        (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
      );

      setAssets(unifiedList);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load asset library.");
    } finally {
      setLoading(false);
    }
  }, [channelFilter, channels]);

  useEffect(() => {
    void loadAssets();
  }, [loadAssets]);

  const filteredAssets = useMemo(() => {
    return assets.filter((item) => {
      if (categoryFilter !== "ALL" && item.category !== categoryFilter) return false;
      if (search.trim()) {
        const query = search.toLowerCase();
        const matches =
          item.title.toLowerCase().includes(query) ||
          item.subtitle.toLowerCase().includes(query) ||
          item.channelName.toLowerCase().includes(query) ||
          item.formatOrType.toLowerCase().includes(query) ||
          (item.extraText && item.extraText.toLowerCase().includes(query));
        if (!matches) return false;
      }
      return true;
    });
  }, [assets, categoryFilter, search]);

  const videoArtifactCount = assets.filter((a) => a.category === "VIDEO_ARTIFACT").length;
  const sceneAssetCount = assets.filter((a) => a.category === "SCENE_ASSET").length;
  const narrationCount = assets.filter((a) => a.category === "NARRATION").length;
  const subtitleCount = assets.filter((a) => a.category === "SUBTITLE").length;

  return (
    <div className="ui-page-stack" style={{ maxWidth: "1400px", margin: "0 auto" }}>
      {/* PAGE HEADER */}
      <div
        className="page-head"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: "16px",
          marginBottom: "16px",
        }}
      >
        <div>
          <div className="title" style={{ fontSize: "24px", fontWeight: 800, letterSpacing: "-0.03em" }}>
            Asset Library
          </div>
          <div className="sub" style={{ color: "var(--muted)", fontSize: "13px", marginTop: "4px" }}>
            Authoritative media repository. Rendered videos, scene b-roll, audio masters, and subtitles with full provenance.
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <button
            type="button"
            className="btn"
            onClick={() => void loadAssets()}
            disabled={loading}
            style={{ fontSize: "12px", padding: "8px 12px" }}
          >
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <Alert
          tone="danger"
          title="Asset library error"
          actions={
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => void loadAssets()}
            >
              Retry
            </button>
          }
        >
          {error}
        </Alert>
      )}

      {/* METRICS ROW */}
      <div className="dash-metrics-grid" aria-label="Asset Inventory">
        <div className="dash-metric-card">
          <div className="metric-k">Video Artifacts</div>
          <div className="metric-v">{videoArtifactCount}</div>
          <div className="metric-sub" style={{ color: "var(--accent)" }}>
            Rendered final outputs
          </div>
        </div>

        <div className="dash-metric-card">
          <div className="metric-k">Scene Assets</div>
          <div className="metric-v">{sceneAssetCount}</div>
          <div className="metric-sub">B-roll, imagery & visuals</div>
        </div>

        <div className="dash-metric-card">
          <div className="metric-k">Narration Masters</div>
          <div className="metric-v">{narrationCount}</div>
          <div className="metric-sub">Synthesized audio segments</div>
        </div>

        <div className="dash-metric-card">
          <div className="metric-k">Subtitle Tracks</div>
          <div className="metric-v">{subtitleCount}</div>
          <div className="metric-sub">Time-aligned captions</div>
        </div>
      </div>

      {/* FILTER BAR */}
      <div
        className="ui-filter-bar"
        role="search"
        aria-label="Filter asset repository"
        style={{
          display: "flex",
          gap: "12px",
          alignItems: "center",
          flexWrap: "wrap",
          padding: "10px 14px",
          background: "#111823",
          border: "1px solid var(--line)",
          borderRadius: "var(--radius)",
          margin: "14px 0",
        }}
      >
        <div style={{ flex: 1, minWidth: "220px" }}>
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by asset title, format, text..."
            className="input"
            style={{
              width: "100%",
              background: "#0f151e",
              border: "1px solid var(--line)",
              padding: "7px 10px",
              fontSize: "12px",
              borderRadius: "8px",
              color: "var(--text-primary)",
            }}
          />
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <label htmlFor="assets-channel-filter" style={{ fontSize: "11px", color: "var(--muted)" }}>
            Channel:
          </label>
          <select
            id="assets-channel-filter"
            className="select"
            value={channelFilter}
            onChange={(e) => setChannelFilter(e.target.value)}
            style={{
              background: "#0f151e",
              border: "1px solid var(--line)",
              padding: "6px 10px",
              fontSize: "12px",
              borderRadius: "8px",
              color: "var(--text)",
            }}
            disabled={channelsLoading}
          >
            <option value="ALL">All channels ({channels.length})</option>
            {channels.map((ch) => (
              <option key={ch.id} value={ch.id}>
                {ch.name}
              </option>
            ))}
          </select>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <label htmlFor="assets-category-filter" style={{ fontSize: "11px", color: "var(--muted)" }}>
            Category:
          </label>
          <select
            id="assets-category-filter"
            className="select"
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value as AssetCategory)}
            style={{
              background: "#0f151e",
              border: "1px solid var(--line)",
              padding: "6px 10px",
              fontSize: "12px",
              borderRadius: "8px",
              color: "var(--text)",
            }}
          >
            <option value="ALL">All categories ({assets.length})</option>
            <option value="VIDEO_ARTIFACT">Video Artifacts ({videoArtifactCount})</option>
            <option value="SCENE_ASSET">Scene Assets ({sceneAssetCount})</option>
            <option value="NARRATION">Narration ({narrationCount})</option>
            <option value="SUBTITLE">Subtitles ({subtitleCount})</option>
          </select>
        </div>
      </div>

      {/* ASSET GRID */}
      {loading ? (
        <div style={{ padding: "48px", textAlign: "center", color: "var(--muted)" }}>
          Loading asset repository from authoritative store...
        </div>
      ) : filteredAssets.length === 0 ? (
        <div
          className="card pad"
          style={{
            textAlign: "center",
            padding: "48px 20px",
            border: "1px solid var(--line)",
            borderRadius: "var(--radius)",
          }}
        >
          <div style={{ fontSize: "32px", marginBottom: "10px" }}>▧</div>
          <h3 style={{ fontSize: "16px", fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
            No assets found
          </h3>
          <p style={{ fontSize: "13px", color: "var(--muted)", margin: "6px 0 16px" }}>
            {search || channelFilter !== "ALL" || categoryFilter !== "ALL"
              ? "No media asset matches your active filters."
              : "No media assets or artifacts are currently persisted."}
          </p>
          <Link href="/production" className="btn primary" style={{ fontWeight: 700 }}>
            Go to Production Hub
          </Link>
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
            gap: "14px",
          }}
        >
          {filteredAssets.map((asset) => {
            const isSelected = previewAssetId === asset.id;
            const categoryBadge =
              asset.category === "VIDEO_ARTIFACT"
                ? "VIDEO"
                : asset.category === "SCENE_ASSET"
                ? "ASSET"
                : asset.category === "NARRATION"
                ? "AUDIO"
                : "CAPTION";

            return (
              <div
                key={asset.id}
                className="card pad"
                style={{
                  border: isSelected ? "1px solid var(--accent)" : "1px solid var(--line)",
                  borderRadius: "var(--radius)",
                  background: isSelected ? "rgba(124, 92, 255, 0.05)" : "#111823",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                }}
              >
                <div>
                  {/* THUMBNAIL / PREVIEW BOX */}
                  <div
                    className="story-thumb"
                    style={{
                      height: "110px",
                      borderRadius: "8px",
                      background:
                        asset.category === "VIDEO_ARTIFACT"
                          ? "linear-gradient(135deg, #1e293b, #0f172a)"
                          : asset.category === "NARRATION"
                          ? "linear-gradient(135deg, #1e3a5f, #0f1e33)"
                          : "linear-gradient(145deg, #283348, #131a24)",
                      display: "grid",
                      placeItems: "center",
                      color: "#94a3b8",
                      marginBottom: "10px",
                      position: "relative",
                      overflow: "hidden",
                    }}
                  >
                    {asset.category === "VIDEO_ARTIFACT" && asset.streamUrl && isSelected ? (
                      <video
                        controls
                        src={asset.streamUrl}
                        style={{ width: "100%", height: "100%", objectFit: "contain" }}
                      />
                    ) : (
                      <div style={{ textAlign: "center" }}>
                        <span style={{ fontSize: "24px", display: "block" }}>
                          {asset.category === "VIDEO_ARTIFACT"
                            ? "▶"
                            : asset.category === "NARRATION"
                            ? "🎙"
                            : asset.category === "SUBTITLE"
                            ? "💬"
                            : "▧"}
                        </span>
                        <span
                          style={{
                            fontSize: "10px",
                            fontWeight: 700,
                            letterSpacing: "0.08em",
                            color: "var(--soft)",
                            textTransform: "uppercase",
                          }}
                        >
                          {categoryBadge}
                        </span>
                      </div>
                    )}
                  </div>

                  <div className="between" style={{ marginBottom: "6px" }}>
                    <span className="badge info" style={{ fontSize: "10px" }}>
                      {categoryBadge}
                    </span>
                    <span className="small muted" style={{ fontSize: "11px" }}>
                      {asset.channelName}
                    </span>
                  </div>

                  <b style={{ fontSize: "13px", color: "var(--text-primary)", display: "block", marginBottom: "4px" }}>
                    {asset.title}
                  </b>
                  <div className="small muted" style={{ fontSize: "11px", marginBottom: "8px" }}>
                    {asset.subtitle}
                  </div>

                  {asset.extraText && (
                    <div
                      className="box text"
                      style={{
                        fontSize: "11px",
                        lineHeight: 1.4,
                        maxHeight: "70px",
                        overflowY: "auto",
                        background: "#0a0f16",
                        padding: "6px 8px",
                        borderRadius: "6px",
                        marginBottom: "8px",
                        color: "#cbd5e1",
                      }}
                    >
                      {asset.extraText}
                    </div>
                  )}

                  {asset.fileSizeBytes && (
                    <div className="small muted" style={{ fontSize: "10px", marginBottom: "4px" }}>
                      File size: {(asset.fileSizeBytes / (1024 * 1024)).toFixed(2)} MB
                    </div>
                  )}
                </div>

                <div style={{ marginTop: "12px", borderTop: "1px solid var(--line)", paddingTop: "8px" }}>
                  <div className="between" style={{ alignItems: "center" }}>
                    {asset.streamUrl ? (
                      <button
                        type="button"
                        className={`btn btn-sm ${isSelected ? "primary" : ""}`}
                        onClick={() => setPreviewAssetId(isSelected ? null : asset.id)}
                        style={{ fontSize: "11px", padding: "4px 8px" }}
                      >
                        {isSelected ? "Close Preview" : "Preview"}
                      </button>
                    ) : (
                      <span className="small muted" style={{ fontSize: "10px" }}>
                        {new Date(asset.createdAt).toLocaleDateString()}
                      </span>
                    )}

                    <Link
                      href={`/channels/${asset.channelId}/production`}
                      className="btn btn-secondary btn-sm"
                      style={{ fontSize: "11px", padding: "4px 8px" }}
                    >
                      Source Studio →
                    </Link>
                  </div>

                  {/* SECONDARY DISCLOSURE FOR TECHNICAL PROVENANCE */}
                  <details className="product-identity" style={{ marginTop: "8px" }}>
                    <summary className="small muted" style={{ fontSize: "10px", cursor: "pointer" }}>
                      Technical Provenance
                    </summary>
                    <div
                      style={{
                        fontSize: "10px",
                        color: "var(--soft)",
                        fontFamily: "var(--font-mono)",
                        marginTop: "4px",
                        wordBreak: "break-all",
                      }}
                    >
                      <div>ID: {asset.id}</div>
                      {asset.storageUri && <div>URI: {asset.storageUri}</div>}
                      {asset.contentHash && <div>SHA256: {asset.contentHash.slice(0, 24)}…</div>}
                    </div>
                  </details>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
