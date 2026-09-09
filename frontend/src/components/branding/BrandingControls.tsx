"use client";

import { useState } from "react";
import {
    BrandAssetReference,
    BrandAssetRole,
    BrandPackage,
    ChannelBugPolicy,
    getBrandAssetPreviewUrl,
} from "@/lib/api";

interface StatusItem {
    label: string;
    active: boolean;
    activeText?: string;
    inactiveText?: string;
}

export function BrandingStatusSummary({ brandPackage, dirty }: { brandPackage: BrandPackage; dirty: boolean }) {
    const items: StatusItem[] = [
        { label: "Logo", active: Boolean(brandPackage.logo_asset), activeText: "Configured", inactiveText: "Missing" },
        { label: "Intro", active: Boolean(brandPackage.intro_asset), activeText: "Configured", inactiveText: "Missing" },
        { label: "Outro", active: Boolean(brandPackage.outro_asset), activeText: "Configured", inactiveText: "Missing" },
        { label: "Channel Bug", active: brandPackage.long_form.channel_bug_enabled },
        { label: "Micro Intro", active: brandPackage.long_form.micro_intro_enabled },
        { label: "Branded Outro", active: brandPackage.long_form.branded_outro_enabled },
    ];

    return (
        <section className="card branding-summary" aria-labelledby="branding-status-title">
            <div className="card-header">
                <div>
                    <h2 id="branding-status-title" className="card-title">Branding Status</h2>
                    <p className="branding-section-copy">Current asset readiness and long-form runtime policy.</p>
                </div>
                <span className={`badge ${dirty ? "badge-running" : "badge-succeeded"}`}>
                    {dirty ? "Unsaved Changes" : "Saved"}
                </span>
            </div>
            <div className="branding-status-grid">
                {items.map((item) => (
                    <div className="branding-status-item" key={item.label}>
                        <span>{item.label}</span>
                        <strong className={item.active ? "branding-positive" : "branding-muted"}>
                            <span className={`status-dot ${item.active ? "healthy" : "loading"}`} />
                            {item.active ? item.activeText || "Enabled" : item.inactiveText || "Disabled"}
                        </strong>
                    </div>
                ))}
            </div>
        </section>
    );
}

function displayAssetName(reference: string): string {
    const normalized = reference.replace(/\\/g, "/");
    return normalized.split("/").filter(Boolean).pop() || "Managed asset";
}

function BrandMediaPreview({ channelId, role, asset }: { channelId: string; role: BrandAssetRole; asset: BrandAssetReference }) {
    const [failed, setFailed] = useState(false);
    const assetName = displayAssetName(asset.reference);
    const previewUrl = getBrandAssetPreviewUrl(channelId, role, assetName);

    if (failed) {
        return (
            <div className="branding-preview branding-preview-failed" role="status">
                <span>Preview unavailable</span>
                <small>The managed asset metadata remains available below.</small>
            </div>
        );
    }

    return (
        <div className="branding-preview">
            {role === "logo" ? (
                // eslint-disable-next-line @next/next/no-img-element -- authenticated API media is not compatible with Next image optimization.
                <img src={previewUrl} alt="Configured channel logo" onError={() => setFailed(true)} />
            ) : (
                <video src={previewUrl} controls preload="metadata" onError={() => setFailed(true)}>
                    Your browser does not support native video preview.
                </video>
            )}
        </div>
    );
}

export function BrandAssetCard({
    channelId,
    role,
    title,
    description,
    asset,
}: {
    channelId: string;
    role: BrandAssetRole;
    title: string;
    description: string;
    asset: BrandAssetReference | null;
}) {
    return (
        <article className="card branding-asset-card">
            <div className="card-header">
                <div>
                    <h3 className="card-title">{title}</h3>
                    <p className="branding-section-copy">{description}</p>
                </div>
                <span className={`badge ${asset ? "badge-succeeded" : "badge-draft"}`}>
                    {asset ? "Valid" : "Not configured"}
                </span>
            </div>

            {asset ? (
                <div className="card-body">
                    <BrandMediaPreview channelId={channelId} role={role} asset={asset} />
                    <dl className="branding-metadata">
                        <div><dt>Managed asset</dt><dd title={displayAssetName(asset.reference)}>{displayAssetName(asset.reference)}</dd></div>
                        <div><dt>MIME</dt><dd>{asset.mime_type}</dd></div>
                        <div><dt>SHA</dt><dd className="text-mono">{asset.content_hash.slice(0, 12)}…</dd></div>
                        <div><dt>Dimensions</dt><dd>{asset.width && asset.height ? `${asset.width} × ${asset.height}` : "Not reported"}</dd></div>
                        {role !== "logo" && <div><dt>Duration</dt><dd>{asset.duration_seconds != null ? `${asset.duration_seconds.toFixed(2)}s` : "Not reported"}</dd></div>}
                        {asset.variant_name && <div><dt>Variant</dt><dd>{asset.variant_name}</dd></div>}
                    </dl>
                </div>
            ) : (
                <div className="empty-state branding-asset-empty">
                    <div className="empty-state-icon" aria-hidden="true">{role === "logo" ? "◇" : "▷"}</div>
                    <h3>No {title.toLowerCase()} configured</h3>
                    <p>Add a validated managed asset in the upcoming asset workflow.</p>
                </div>
            )}

            <div className="card-footer branding-asset-actions">
                <span className="form-helper">Asset mutations arrive in Phase 2D.</span>
                <div className="flex-row gap-2">
                    <button className="btn btn-secondary btn-sm" disabled title="Available in Phase 2D">Replace</button>
                    <button className="btn btn-danger btn-sm" disabled title="Available in Phase 2D">Remove</button>
                </div>
            </div>
        </article>
    );
}

function ToggleControl({ label, description, checked, onChange, disabled }: { label: string; description: string; checked: boolean; onChange: (checked: boolean) => void; disabled?: boolean }) {
    return (
        <label className="branding-toggle-row">
            <span>
                <strong>{label}</strong>
                <small>{description}</small>
            </span>
            <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} disabled={disabled} />
        </label>
    );
}

export function RuntimeBrandingCard({ brandPackage, onChange }: { brandPackage: BrandPackage; onChange: (next: BrandPackage) => void }) {
    const updateLongForm = (field: "micro_intro_enabled" | "channel_bug_enabled" | "branded_outro_enabled", value: boolean) => {
        onChange({ ...brandPackage, long_form: { ...brandPackage.long_form, [field]: value } });
    };

    return (
        <section className="card" aria-labelledby="runtime-branding-title">
            <div className="card-header">
                <div>
                    <h2 id="runtime-branding-title" className="card-title">Runtime Branding</h2>
                    <p className="branding-section-copy">Choose which brand elements long-form production may schedule.</p>
                </div>
                <span className="badge badge-ready">Long Form</span>
            </div>
            <div className="card-body">
                <ToggleControl label="Micro Intro" description="Allow the configured intro after the opening hook." checked={brandPackage.long_form.micro_intro_enabled} onChange={(value) => updateLongForm("micro_intro_enabled", value)} disabled={!brandPackage.intro_asset} />
                <ToggleControl label="Channel Bug Runtime Policy" description="Allow long-form production to include a channel bug. Placement-level enablement below must also be on." checked={brandPackage.long_form.channel_bug_enabled} onChange={(value) => updateLongForm("channel_bug_enabled", value)} disabled={!brandPackage.logo_asset} />
                <ToggleControl label="Branded Outro" description="Allow the configured outro at the end of long-form content." checked={brandPackage.long_form.branded_outro_enabled} onChange={(value) => updateLongForm("branded_outro_enabled", value)} disabled={!brandPackage.outro_asset} />
                <div className="branding-info-callout">
                    <strong>Two independent channel bug gates</strong>
                    <span><b>Runtime policy</b> decides whether long-form production may use the bug. <b>Placement enablement</b> activates the configured overlay itself. Both must be enabled for rendering.</span>
                </div>
            </div>
        </section>
    );
}

function RangeField({ label, value, min, max, step, suffix, error, onChange }: { label: string; value: number; min: number; max: number; step: number; suffix: string; error?: string; onChange: (value: number) => void }) {
    return (
        <div className="branding-range-field">
            <div className="flex-between">
                <label className="form-label">{label}</label>
                <output className="branding-range-output">{Math.round(value * 100)}{suffix}</output>
            </div>
            <div className="branding-range-inputs">
                <input type="range" min={min} max={max} step={step} value={value} onChange={(event) => onChange(Number(event.target.value))} />
                <input type="number" min={min} max={max} step={step} value={value} onChange={(event) => onChange(Number(event.target.value))} aria-label={`${label} numeric value`} />
            </div>
            {error && <span className="branding-field-error">{error}</span>}
        </div>
    );
}

export function LogoPlacementCard({ policy, onChange, errors }: { policy: ChannelBugPolicy; onChange: (next: ChannelBugPolicy) => void; errors: Record<string, string> }) {
    const update = <K extends keyof ChannelBugPolicy>(field: K, value: ChannelBugPolicy[K]) => onChange({ ...policy, [field]: value });

    return (
        <section className="card" aria-labelledby="logo-placement-title">
            <div className="card-header">
                <div>
                    <h2 id="logo-placement-title" className="card-title">Logo Placement</h2>
                    <p className="branding-section-copy">Renderer-independent overlay placement and visibility policy.</p>
                </div>
                <span className={`badge ${policy.enabled ? "badge-succeeded" : "badge-draft"}`}>{policy.enabled ? "Placement enabled" : "Placement disabled"}</span>
            </div>
            <div className="card-body">
                <ToggleControl label="Placement Enablement" description="Enable this overlay configuration. This is separate from long-form runtime policy." checked={policy.enabled} onChange={(value) => update("enabled", value)} />
                <div className="branding-control-grid">
                    <div className="form-group">
                        <label className="form-label" htmlFor="bug-position">Position</label>
                        <select id="bug-position" value={policy.position} onChange={(event) => update("position", event.target.value as ChannelBugPolicy["position"])}>
                            <option value="TOP_LEFT">Top left</option>
                            <option value="TOP_RIGHT">Top right</option>
                            <option value="BOTTOM_LEFT">Bottom left</option>
                            <option value="BOTTOM_RIGHT">Bottom right</option>
                        </select>
                    </div>
                    <div className="form-group">
                        <label className="form-label" htmlFor="bug-timing">Timing policy</label>
                        <select id="bug-timing" value={policy.timing_policy} onChange={(event) => update("timing_policy", event.target.value as ChannelBugPolicy["timing_policy"])}>
                            <option value="ALWAYS">Always</option>
                            <option value="AFTER_INTRO">After intro</option>
                            <option value="MAIN_CONTENT_ONLY">Main content only</option>
                        </select>
                    </div>
                </div>
                <RangeField label="Scale" value={policy.scale} min={0.01} max={1} step={0.01} suffix="%" error={errors.scale} onChange={(value) => update("scale", value)} />
                <RangeField label="Opacity" value={policy.opacity} min={0} max={1} step={0.01} suffix="%" error={errors.opacity} onChange={(value) => update("opacity", value)} />
                <RangeField label="Safe Margin X" value={policy.safe_margin_x} min={0} max={0.25} step={0.01} suffix="%" error={errors.safe_margin_x} onChange={(value) => update("safe_margin_x", value)} />
                <RangeField label="Safe Margin Y" value={policy.safe_margin_y} min={0} max={0.25} step={0.01} suffix="%" error={errors.safe_margin_y} onChange={(value) => update("safe_margin_y", value)} />
                <ToggleControl label="Subtitle-safe placement" description="Keep logo placement clear of subtitle-safe rendering regions." checked={policy.subtitle_safe} onChange={(value) => update("subtitle_safe", value)} />
            </div>
        </section>
    );
}

export function BrandingSaveBar({ dirty, saving, changeReason, invalid, success, error, onReasonChange, onReset, onSave }: { dirty: boolean; saving: boolean; changeReason: string; invalid: boolean; success: string | null; error: string | null; onReasonChange: (value: string) => void; onReset: () => void; onSave: () => void }) {
    const reasonInvalid = dirty && changeReason.trim().length < 3;
    return (
        <aside className="branding-save-bar" aria-label="Branding save controls">
            <div className="branding-save-state">
                <span className={`status-dot ${dirty ? "warning" : "healthy"}`} />
                <span><strong>{dirty ? "Unsaved branding changes" : "Branding is up to date"}</strong><small>Saving creates a revisioned complete Channel DNA snapshot.</small></span>
            </div>
            <div className="branding-save-reason">
                <label className="form-label" htmlFor="branding-change-reason">Change reason *</label>
                <input id="branding-change-reason" value={changeReason} onChange={(event) => onReasonChange(event.target.value)} placeholder="Describe why this branding policy changed" disabled={!dirty || saving} />
                {reasonInvalid && <span className="branding-field-error">Enter at least 3 characters for the audit trail.</span>}
                {invalid && <span className="branding-field-error">Resolve invalid field values before saving.</span>}
                {success && <span className="branding-inline-success">{success}</span>}
                {error && <span className="branding-field-error">{error}</span>}
            </div>
            <div className="branding-save-actions">
                <button type="button" className="btn btn-secondary" onClick={onReset} disabled={!dirty || saving}>Reset</button>
                <button type="button" className="btn btn-primary" onClick={onSave} disabled={!dirty || reasonInvalid || invalid || saving}>{saving ? "Saving…" : "Save Branding"}</button>
            </div>
        </aside>
    );
}
