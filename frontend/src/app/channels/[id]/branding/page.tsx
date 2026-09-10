"use client";

import { use, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { BrandAssetReference, BrandAssetRole, Channel, BrandPackage, getChannel, updateChannelDNA } from "@/lib/api";
import { ChannelContextBar } from "@/components/ChannelContextBar";
import {
    BrandAssetCard,
    BrandingSaveBar,
    BrandingStatusSummary,
    LogoPlacementCard,
    RuntimeBrandingCard,
} from "@/components/branding/BrandingControls";
import { useOperatorContext } from "@/lib/operator-context";

const EMPTY_BRAND_PACKAGE: BrandPackage = {
    logo_asset: null,
    logo_variant: null,
    intro_asset: null,
    outro_asset: null,
    channel_bug: {
        enabled: false,
        logo_variant: null,
        position: "TOP_RIGHT",
        safe_margin_x: 0.03,
        safe_margin_y: 0.03,
        scale: 0.1,
        opacity: 0.7,
        timing_policy: "MAIN_CONTENT_ONLY",
        subtitle_safe: true,
    },
    tagline: null,
    sonic_logo: null,
    end_screen_layout: {
        enabled: false,
        layout: "LOGO_ONLY",
        show_tagline: false,
        show_subscribe: false,
        next_video_slots: 0,
    },
    long_form: {
        hook_before_intro: true,
        micro_intro_enabled: false,
        channel_bug_enabled: false,
        branded_outro_enabled: false,
        end_screen_enabled: false,
    },
    short_form: {
        inherit_long_form_micro_intro: false,
        lightweight_logo_motif_enabled: false,
        short_ending_enabled: false,
    },
};

function cloneBrandPackage(brandPackage: BrandPackage | null): BrandPackage {
    return structuredClone(brandPackage || EMPTY_BRAND_PACKAGE);
}

function validateBrandPackage(brandPackage: BrandPackage): Record<string, string> {
    const errors: Record<string, string> = {};
    const { channel_bug: bug } = brandPackage;
    if (!Number.isFinite(bug.scale) || bug.scale <= 0 || bug.scale > 1) errors.scale = "Scale must be greater than 0 and no more than 1.";
    if (!Number.isFinite(bug.opacity) || bug.opacity < 0 || bug.opacity > 1) errors.opacity = "Opacity must be between 0 and 1.";
    if (!Number.isFinite(bug.safe_margin_x) || bug.safe_margin_x < 0 || bug.safe_margin_x > 0.25) errors.safe_margin_x = "Safe margin X must be between 0 and 0.25.";
    if (!Number.isFinite(bug.safe_margin_y) || bug.safe_margin_y < 0 || bug.safe_margin_y > 0.25) errors.safe_margin_y = "Safe margin Y must be between 0 and 0.25.";
    if (brandPackage.long_form.micro_intro_enabled && !brandPackage.intro_asset) errors.micro_intro = "Micro Intro requires a configured intro asset.";
    if (brandPackage.long_form.branded_outro_enabled && !brandPackage.outro_asset) errors.branded_outro = "Branded Outro requires a configured outro asset.";
    if (brandPackage.long_form.channel_bug_enabled && !brandPackage.logo_asset) errors.channel_bug_runtime = "Channel Bug Runtime Policy requires a configured logo asset.";
    if (bug.enabled && !brandPackage.logo_asset) errors.channel_bug = "Channel Bug Placement Enablement requires a configured logo asset.";
    return errors;
}

export default function BrandManagementPage({ params }: { params: Promise<{ id: string }> }) {
    const { id: channelId } = use(params);
    const { setSelectedChannelId } = useOperatorContext();
    const [channel, setChannel] = useState<Channel | null>(null);
    const [savedPackage, setSavedPackage] = useState<BrandPackage | null>(null);
    const [draftPackage, setDraftPackage] = useState<BrandPackage | null>(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [saveError, setSaveError] = useState<string | null>(null);
    const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
    const [changeReason, setChangeReason] = useState("");

    const loadBranding = useCallback(async () => {
        try {
            setLoading(true);
            setLoadError(null);
            setSelectedChannelId(channelId);
            const loadedChannel = await getChannel(channelId);
            const brandPackage = cloneBrandPackage(loadedChannel.dna.brand_package);
            setChannel(loadedChannel);
            setSavedPackage(brandPackage);
            setDraftPackage(cloneBrandPackage(brandPackage));
        } catch (error: unknown) {
            setLoadError(error instanceof Error ? error.message : "Failed to load channel branding.");
        } finally {
            setLoading(false);
        }
    }, [channelId, setSelectedChannelId]);

    useEffect(() => {
        void loadBranding();
    }, [loadBranding]);

    const dirty = useMemo(
        () => Boolean(savedPackage && draftPackage && JSON.stringify(savedPackage) !== JSON.stringify(draftPackage)),
        [draftPackage, savedPackage],
    );
    const validationErrors = useMemo(() => draftPackage ? validateBrandPackage(draftPackage) : {}, [draftPackage]);
    const invalid = Object.keys(validationErrors).length > 0;

    const handleDraftChange = (next: BrandPackage) => {
        setDraftPackage(next);
        setSaveError(null);
        setSaveSuccess(null);
    };

    const handleAssetChange = (role: BrandAssetRole, asset: BrandAssetReference | null) => {
        if (!draftPackage) return;
        const next = cloneBrandPackage(draftPackage);
        if (role === "logo") {
            next.logo_asset = asset;
            if (!asset) {
                next.logo_variant = null;
                next.channel_bug.enabled = false;
                next.long_form.channel_bug_enabled = false;
            }
        } else if (role === "intro") {
            next.intro_asset = asset;
            if (!asset) next.long_form.micro_intro_enabled = false;
        } else {
            next.outro_asset = asset;
            if (!asset) next.long_form.branded_outro_enabled = false;
        }
        handleDraftChange(next);
    };

    const handleReset = () => {
        if (!savedPackage) return;
        setDraftPackage(cloneBrandPackage(savedPackage));
        setChangeReason("");
        setSaveError(null);
        setSaveSuccess(null);
    };

    const handleSave = async () => {
        if (!channel || !draftPackage || !dirty || invalid || saving || changeReason.trim().length < 3) return;
        try {
            setSaving(true);
            setSaveError(null);
            setSaveSuccess(null);
            const nextDna = { ...channel.dna, brand_package: draftPackage };
            const savedDna = await updateChannelDNA(channelId, nextDna, changeReason.trim());
            const nextPackage = cloneBrandPackage(savedDna.brand_package);
            setChannel({ ...channel, dna: savedDna });
            setSavedPackage(nextPackage);
            setDraftPackage(cloneBrandPackage(nextPackage));
            setChangeReason("");
            setSaveSuccess("Branding saved as a new Channel DNA revision.");
        } catch (error: unknown) {
            setSaveError(error instanceof Error ? error.message : "Failed to save branding.");
        } finally {
            setSaving(false);
        }
    };

    if (loading) {
        return <div className="branding-loading card" role="status">Loading channel branding…</div>;
    }

    if (loadError || !channel || !draftPackage) {
        return (
            <div>
                <Link href="/channels" className="branding-back-link">← Back to Channels</Link>
                <div className="branding-error-banner" role="alert">
                    <strong>Branding could not be loaded.</strong>
                    <span>{loadError || "The channel returned no branding configuration."}</span>
                    <button className="btn btn-secondary btn-sm" onClick={() => void loadBranding()}>Retry</button>
                </div>
            </div>
        );
    }

    return (
        <div className="branding-page">
            <ChannelContextBar currentTab="branding" />

            <header className="page-header branding-page-header">
                <div>
                    <Link href={`/channels/${channel.id}`} className="branding-back-link">← Channel DNA</Link>
                    <div className="branding-title-row">
                        <span className="branding-title-icon" aria-hidden="true">Ω</span>
                        <div>
                            <h1 className="page-title">Branding</h1>
                            <p className="page-subtitle">Manage channel identity, intro/outro assets, logo behavior, and runtime branding policies.</p>
                        </div>
                    </div>
                </div>
                <div className="branding-channel-pill">
                    <span>Current channel</span>
                    <strong>{channel.name}</strong>
                    <small className="text-mono">/{channel.slug}</small>
                </div>
            </header>

            <BrandingStatusSummary brandPackage={draftPackage} dirty={dirty} />

            <div className="section-header">
                <div>
                    <h2 className="section-title">Brand Assets</h2>
                    <p className="branding-section-copy">Upload validated candidates, then save Channel DNA separately to activate them.</p>
                </div>
                <span className="badge badge-running">Mutation workflow</span>
            </div>
            <div className="branding-assets-grid">
                <BrandAssetCard channelId={channelId} role="logo" title="Logo" description="Primary visual identity and bug source." asset={draftPackage.logo_asset} savedAsset={savedPackage?.logo_asset || null} onAssetChange={(asset) => handleAssetChange("logo", asset)} />
                <BrandAssetCard channelId={channelId} role="intro" title="Intro" description="Short branded sequence after the hook." asset={draftPackage.intro_asset} savedAsset={savedPackage?.intro_asset || null} onAssetChange={(asset) => handleAssetChange("intro", asset)} />
                <BrandAssetCard channelId={channelId} role="outro" title="Outro" description="Branded closing sequence for long form." asset={draftPackage.outro_asset} savedAsset={savedPackage?.outro_asset || null} onAssetChange={(asset) => handleAssetChange("outro", asset)} />
            </div>

            <div className="branding-policy-grid">
                <RuntimeBrandingCard brandPackage={draftPackage} onChange={handleDraftChange} />
                <LogoPlacementCard policy={draftPackage.channel_bug} hasLogo={Boolean(draftPackage.logo_asset)} errors={validationErrors} onChange={(channelBug) => handleDraftChange({ ...draftPackage, channel_bug: channelBug })} />
            </div>

            {(validationErrors.micro_intro || validationErrors.branded_outro || validationErrors.channel_bug_runtime || validationErrors.channel_bug) && (
                <div className="branding-error-banner" role="alert">
                    <strong>Resolve branding dependencies before saving.</strong>
                    {validationErrors.micro_intro && <span>{validationErrors.micro_intro}</span>}
                    {validationErrors.branded_outro && <span>{validationErrors.branded_outro}</span>}
                    {validationErrors.channel_bug_runtime && <span>{validationErrors.channel_bug_runtime}</span>}
                    {validationErrors.channel_bug && <span>{validationErrors.channel_bug}</span>}
                </div>
            )}

            <BrandingSaveBar dirty={dirty} saving={saving} changeReason={changeReason} invalid={invalid} success={saveSuccess} error={saveError} onReasonChange={setChangeReason} onReset={handleReset} onSave={() => void handleSave()} />
        </div>
    );
}
