"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createChannel } from "@/lib/api";
import { Alert, FormField, PageHeader, PageSection } from "@/components/ui";

export default function NewChannelPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [primaryLanguage, setPrimaryLanguage] = useState("");
  const [targetRegion, setTargetRegion] = useState("");
  const [timezone, setTimezone] = useState("");
  const [niche, setNiche] = useState("");
  const [pillars, setPillars] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const parsedPillars = pillars.split(",").map((pillar) => pillar.trim()).filter(Boolean);
    if (!name.trim() || !primaryLanguage.trim() || !targetRegion.trim() || !timezone.trim() || !niche.trim() || parsedPillars.length === 0) {
      setError("Complete every required field and provide at least one content pillar.");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const channel = await createChannel({
        name: name.trim(),
        slug: slug.trim() || undefined,
        description: description.trim() || undefined,
        platform: "YOUTUBE",
        primary_language: primaryLanguage.trim(),
        target_region: targetRegion.trim().toUpperCase(),
        timezone: timezone.trim(),
        dna: {
          content_strategy: {
            niche: niche.trim(),
            subniches: [],
            content_pillars: parsedPillars,
            preferred_formats: ["EXPLAINER", "NEWS_ROUNDUP", "DEEP_DIVE"],
            default_duration_min_seconds: 300,
            default_duration_max_seconds: 900,
            evergreen_ratio: 0.7,
          },
        },
      });
      router.push(`/channels/${channel.id}`);
    } catch (requestError: unknown) {
      setError(requestError instanceof Error ? requestError.message : "Failed to create channel.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="ui-page-stack ui-form-page">
      <PageHeader
        eyebrow="Channels"
        title="Create channel"
        description="Create an operating workspace and its initial content-strategy DNA."
        actions={<Link href="/channels" className="btn btn-secondary">Back to channels</Link>}
      />

      {error && <Alert tone="danger" title="Channel could not be created">{error}</Alert>}

      <form className="ui-form-shell" onSubmit={handleSubmit} noValidate>
        <PageSection title="Identity" description="How operators and downstream workflows identify this channel.">
          <FormField id="channel-name" label="Channel name" required>
            <input className="input" type="text" required value={name} onChange={(event) => setName(event.target.value)} autoComplete="organization" />
          </FormField>
          <FormField id="channel-slug" label="Slug" description="Optional. The service generates one when left empty.">
            <input className="input text-mono" type="text" value={slug} onChange={(event) => setSlug(event.target.value.toLowerCase())} placeholder="channel-slug" />
          </FormField>
          <FormField id="channel-description" label="Description">
            <textarea rows={3} value={description} onChange={(event) => setDescription(event.target.value)} />
          </FormField>
        </PageSection>

        <PageSection title="Market and locale" description="These values control scheduling and audience localization.">
          <div className="ui-form-grid ui-form-grid-3">
            <FormField id="channel-language" label="Primary language" description="Use a language code such as en or vi." required>
              <input className="input text-mono" required value={primaryLanguage} onChange={(event) => setPrimaryLanguage(event.target.value)} />
            </FormField>
            <FormField id="channel-region" label="Target region" description="Use a region code such as US or VN." required>
              <input className="input text-mono" required value={targetRegion} onChange={(event) => setTargetRegion(event.target.value.toUpperCase())} />
            </FormField>
            <FormField id="channel-timezone" label="Timezone" description="Use an IANA timezone or UTC." required>
              <input className="input text-mono" required value={timezone} onChange={(event) => setTimezone(event.target.value)} placeholder="UTC" />
            </FormField>
          </div>
        </PageSection>

        <PageSection title="Content strategy" description="Provide the initial editorial scope used by topic discovery.">
          <FormField id="channel-niche" label="Content niche" required>
            <input className="input" required value={niche} onChange={(event) => setNiche(event.target.value)} />
          </FormField>
          <FormField id="channel-pillars" label="Content pillars" description="Comma-separated editorial pillars. At least one is required." required>
            <input className="input" required value={pillars} onChange={(event) => setPillars(event.target.value)} />
          </FormField>
        </PageSection>

        <div className="ui-form-actions">
          <Link href="/channels" className="btn btn-secondary">Cancel</Link>
          <button type="submit" className="btn btn-primary" disabled={submitting}>
            {submitting ? "Creating channel…" : "Create channel"}
          </button>
        </div>
      </form>
    </div>
  );
}
