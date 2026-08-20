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
    <div className="min-h-screen bg-slate-950 text-slate-100 p-8">
      <div className="max-w-2xl mx-auto space-y-6">
        <div>
          <Link
            href="/channels"
            className="text-xs text-indigo-400 hover:text-indigo-300 transition-colors"
          >
            ← Back to Channels
          </Link>
          <h1 className="text-2xl font-bold text-slate-100 mt-2">
            Create Operating Channel
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Initialize a new autonomous channel workspace and its default DNA profile.
          </p>
        </div>

        {error && (
          <div className="p-4 bg-rose-500/10 border border-rose-500/30 rounded-xl text-rose-400 text-sm">
            {error}
          </div>
        )}

        <form
          onSubmit={handleSubmit}
          className="space-y-6 bg-slate-900/60 border border-slate-800 rounded-2xl p-6"
        >
          {/* Identity */}
          <div className="space-y-4">
            <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
              1. Identity & Routing
            </h2>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Channel Name *
              </label>
              <input
                type="text"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. NextGen AI Digest"
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Slug (Optional - auto-generated if empty)
              </label>
              <input
                type="text"
                value={slug}
                onChange={(e) => setSlug(e.target.value.toLowerCase())}
                placeholder="e.g. nextgen-ai-digest"
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm font-mono text-slate-100 focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Description
              </label>
              <textarea
                rows={2}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Brief summary of channel purpose..."
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-indigo-500"
              />
            </div>
          </div>

          {/* Localization */}
          <div className="space-y-4 pt-4 border-t border-slate-800">
            <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
              2. Target Market & Localization
            </h2>

            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Primary Language
                </label>
                <input
                  type="text"
                  required
                  value={primaryLanguage}
                  onChange={(e) => setPrimaryLanguage(e.target.value)}
                  placeholder="en, vi, en-US"
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm font-mono text-slate-100 focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Target Region
                </label>
                <input
                  type="text"
                  required
                  value={targetRegion}
                  onChange={(e) => setTargetRegion(e.target.value.toUpperCase())}
                  placeholder="US, VN"
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm font-mono text-slate-100 focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1">
                  Timezone
                </label>
                <input
                  type="text"
                  required
                  value={timezone}
                  onChange={(e) => setTimezone(e.target.value)}
                  placeholder="UTC, America/New_York"
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm font-mono text-slate-100 focus:outline-none focus:border-indigo-500"
                />
              </div>
            </div>
          </div>

          {/* Initial Strategy */}
          <div className="space-y-4 pt-4 border-t border-slate-800">
            <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
              3. Content Strategy Foundation
            </h2>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Content Niche
              </label>
              <input
                type="text"
                required
                value={niche}
                onChange={(e) => setNiche(e.target.value)}
                placeholder="e.g. AI & Machine Learning"
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Content Pillars (comma-separated)
              </label>
              <input
                type="text"
                required
                value={pillars}
                onChange={(e) => setPillars(e.target.value)}
                placeholder="News, Tutorials, Deep Dives"
                className="w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-indigo-500"
              />
            </div>
          </div>

          {/* Submit Buttons */}
          <div className="pt-6 border-t border-slate-800 flex items-center justify-end space-x-3">
            <Link
              href="/channels"
              className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 text-sm font-medium rounded-lg transition-colors"
            >
              Cancel
            </Link>
            <button
              type="submit"
              disabled={loading}
              className="px-5 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-sm font-medium rounded-lg shadow-lg shadow-indigo-600/30 transition-all"
            >
              {loading ? "Creating..." : "Create Channel"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
