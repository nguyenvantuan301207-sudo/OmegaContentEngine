"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { createChannel } from "@/lib/api";

export default function NewChannelPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [primaryLanguage, setPrimaryLanguage] = useState("en");
  const [targetRegion, setTargetRegion] = useState("US");
  const [timezone, setTimezone] = useState("UTC");
  const [niche, setNiche] = useState("AI & Automation");
  const [pillars, setPillars] = useState("Industry News, Tool Breakdowns, Deep Dives");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setError("Channel name is required.");
      return;
    }

    try {
      setLoading(true);
      setError(null);

      const parsedPillars = pillars
        .split(",")
        .map((p) => p.trim())
        .filter(Boolean);

      const channel = await createChannel({
        name: name.trim(),
        slug: slug.trim() || undefined,
        description: description.trim() || undefined,
        platform: "YOUTUBE",
        primary_language: primaryLanguage.trim(),
        target_region: targetRegion.trim(),
        timezone: timezone.trim(),
        dna: {
          content_strategy: {
            niche: niche.trim(),
            subniches: [],
            content_pillars: parsedPillars.length > 0 ? parsedPillars : ["Overview", "Case Studies"],
            preferred_formats: ["EXPLAINER", "NEWS_ROUNDUP", "DEEP_DIVE"],
            default_duration_min_seconds: 300,
            default_duration_max_seconds: 900,
            evergreen_ratio: 0.7,
          },
        },
      });

      router.push(`/channels/${channel.id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create channel");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      {/* Header */}
      <div className="page-header" style={{ maxWidth: "840px", margin: "0 auto 2rem auto" }}>
        <div>
          <div style={{ marginBottom: "0.5rem" }}>
            <Link
              href="/channels"
              style={{ fontSize: "0.78rem", color: "var(--accent-secondary)", textDecoration: "none" }}
            >
              ← Back to Channels
            </Link>
          </div>
          <h1 className="page-title">Create Operating Channel</h1>
          <p className="page-subtitle">
            Initialize a new autonomous channel workspace and its default DNA profile.
          </p>
        </div>
      </div>

      <div className="form-card">
        {error && (
          <div style={{ padding: "1rem", background: "var(--status-danger-bg)", border: "1px solid var(--status-danger-border)", borderRadius: "var(--radius-sm)", color: "var(--status-danger)", marginBottom: "1.5rem", fontSize: "0.85rem" }}>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit}>
          {/* Identity Section */}
          <div style={{ marginBottom: "1.5rem" }}>
            <div className="section-title" style={{ marginBottom: "1rem" }}>
              1. Identity & Routing
            </div>

            <div className="form-group">
              <label className="form-label">Channel Name *</label>
              <input
                type="text"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. NextGen AI Digest"
                className="input"
                style={{ width: "100%" }}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Slug (Optional - auto-generated if empty)</label>
              <input
                type="text"
                value={slug}
                onChange={(e) => setSlug(e.target.value.toLowerCase())}
                placeholder="e.g. nextgen-ai-digest"
                className="input text-mono"
                style={{ width: "100%" }}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Description (Optional)</label>
              <textarea
                rows={2}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Brief summary of channel editorial purpose and audience..."
              />
            </div>
          </div>

          {/* Localization Section */}
          <div style={{ padding: "1.5rem 0", borderTop: "1px solid var(--border-subtle)", borderBottom: "1px solid var(--border-subtle)", marginBottom: "1.5rem" }}>
            <div className="section-title" style={{ marginBottom: "1rem" }}>
              2. Target Market & Localization
            </div>

            <div className="grid grid-cols-3" style={{ gap: "1rem" }}>
              <div className="form-group" style={{ marginBottom: 0 }}>
                <label className="form-label">Primary Language</label>
                <input
                  type="text"
                  required
                  value={primaryLanguage}
                  onChange={(e) => setPrimaryLanguage(e.target.value)}
                  placeholder="en, vi, ja"
                  className="input text-mono"
                  style={{ width: "100%" }}
                />
              </div>

              <div className="form-group" style={{ marginBottom: 0 }}>
                <label className="form-label">Target Region</label>
                <input
                  type="text"
                  required
                  value={targetRegion}
                  onChange={(e) => setTargetRegion(e.target.value.toUpperCase())}
                  placeholder="US, VN, JP"
                  className="input text-mono"
                  style={{ width: "100%" }}
                />
              </div>

              <div className="form-group" style={{ marginBottom: 0 }}>
                <label className="form-label">Timezone</label>
                <input
                  type="text"
                  required
                  value={timezone}
                  onChange={(e) => setTimezone(e.target.value)}
                  placeholder="UTC, America/New_York"
                  className="input text-mono"
                  style={{ width: "100%" }}
                />
              </div>
            </div>
          </div>

          {/* Content Strategy Foundation */}
          <div style={{ marginBottom: "1.5rem" }}>
            <div className="section-title" style={{ marginBottom: "1rem" }}>
              3. Content Strategy Foundation
            </div>

            <div className="form-group">
              <label className="form-label">Content Niche *</label>
              <input
                type="text"
                required
                value={niche}
                onChange={(e) => setNiche(e.target.value)}
                placeholder="e.g. AI & Machine Learning"
                className="input"
                style={{ width: "100%" }}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Content Pillars (Comma-separated)</label>
              <input
                type="text"
                required
                value={pillars}
                onChange={(e) => setPillars(e.target.value)}
                placeholder="News, Tutorials, Deep Dives, Case Studies"
                className="input"
                style={{ width: "100%" }}
              />
              <span className="form-helper">Core editorial topics used to guide automated topic discovery.</span>
            </div>
          </div>

          {/* Form Actions */}
          <div className="form-actions">
            <Link href="/channels" className="btn btn-secondary">
              Cancel
            </Link>
            <button
              type="submit"
              disabled={loading}
              className="btn btn-primary"
            >
              {loading ? "Creating..." : "Create Operating Channel"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
