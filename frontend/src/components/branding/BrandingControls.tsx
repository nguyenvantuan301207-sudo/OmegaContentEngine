"use client";

import { ChangeEvent, useRef, useState } from "react";
import {
    BrandAssetApiError,
    BrandAssetReference,
    BrandAssetRole,
    BrandPackage,
    ChannelBugPolicy,
    deleteBrandAsset,
    getBrandAssetPreviewUrl,
    uploadBrandAsset,
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

function formatFileSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function brandAssetErrorMessage(error: unknown, action: "upload" | "delete"): string {
    if (!(error instanceof BrandAssetApiError)) {
        return action === "upload" ? "Upload failed. Check the server and try again." : "Stored asset could not be deleted.";
    }
    const messages: Record<string, string> = {
        INVALID_MEDIA_TYPE: "This file type is not valid for this asset role. Choose a supported file.",
        INVALID_DURATION: "The video duration is outside the allowed range for this role.",
        UNPROBEABLE_MEDIA: "The media could not be inspected. Choose a valid, uncorrupted file.",
        UNSUPPORTED_ROLE: "This asset role is not supported by the server.",
        MISSING_ASSET: "The stored asset no longer exists. Refresh the channel before continuing.",
        ASSET_IN_USE: "This asset is referenced by current or historical branding and cannot be deleted.",
        EMPTY_UPLOAD: "The selected file is empty. Choose another file.",
        MALFORMED_CANONICAL_REFERENCE: "The stored asset reference is invalid. Refresh before continuing.",
        VALIDATION_ERROR: "The server rejected this file. Review the selection and try again.",
        STORAGE_FAILURE: "The server could not store the asset. Try again later.",
        SERVER_ERROR: "The server could not complete the asset request. Try again later.",
        MISSING_CHANNEL: "This channel no longer exists. Return to Channels and refresh.",
    };
    return messages[error.brandCode] || error.detail.message || "The asset request failed.";
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
    savedAsset,
    onAssetChange,
}: {
    channelId: string;
    role: BrandAssetRole;
    title: string;
    description: string;
    asset: BrandAssetReference | null;
    savedAsset: BrandAssetReference | null;
    onAssetChange: (asset: BrandAssetReference | null) => void;
}) {
    const inputRef = useRef<HTMLInputElement>(null);
    const [selectedFile, setSelectedFile] = useState<File | null>(null);
    const [uploading, setUploading] = useState(false);
    const [deleting, setDeleting] = useState(false);
    const [message, setMessage] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const persisted = Boolean(asset && savedAsset && asset.reference === savedAsset.reference);
    const pending = Boolean(asset && (!savedAsset || asset.reference !== savedAsset.reference));
    const safelyDeletable = Boolean(asset && !persisted);
    const accept = role === "logo" ? "image/*,.png,.jpg,.jpeg,.webp" : "video/*,.mp4,.mov,.webm";

    const handleSelection = (event: ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0] || null;
        setSelectedFile(file);
        setMessage(file ? "Selected locally. Upload for authoritative server validation." : null);
        setError(null);
    };

    const handleUpload = async () => {
        if (!selectedFile || uploading) return;
        try {
            setUploading(true);
            setError(null);
            setMessage(null);
            const result = await uploadBrandAsset(channelId, role, selectedFile);
            onAssetChange(result.asset);
            setSelectedFile(null);
            if (inputRef.current) inputRef.current.value = "";
            setMessage("Uploaded and stored. Save Branding to activate this asset in Channel DNA.");
        } catch (uploadError: unknown) {
            setError(brandAssetErrorMessage(uploadError, "upload"));
        } finally {
            setUploading(false);
        }
    };

    const handleDetach = () => {
        onAssetChange(null);
        setSelectedFile(null);
        if (inputRef.current) inputRef.current.value = "";
        setError(null);
        setMessage("Removed from the current branding draft only. Storage and history are unchanged until explicitly deleted where permitted.");
    };

    const handleDelete = async () => {
        if (!asset || !safelyDeletable || deleting) return;
        try {
            setDeleting(true);
            setError(null);
            await deleteBrandAsset(channelId, role, displayAssetName(asset.reference));
            onAssetChange(savedAsset ?? null);
            setMessage(savedAsset
                ? "Unused candidate deleted. The persisted branding asset has been restored to this draft."
                : "Unused candidate deleted. This branding role remains unconfigured.");
        } catch (deleteError: unknown) {
            setError(brandAssetErrorMessage(deleteError, "delete"));
        } finally {
            setDeleting(false);
        }
    };

    return (
        <article className="card branding-asset-card">
            <div className="card-header">
                <div>
                    <h3 className="card-title">{title}</h3>
                    <p className="branding-section-copy">{description}</p>
                </div>
                <span className={`badge ${pending ? "badge-running" : asset ? "badge-succeeded" : "badge-draft"}`}>
                    {pending ? "Pending unsaved change" : persisted ? "Active in current DNA" : asset ? "Stored" : savedAsset ? "Pending detach" : "Not configured"}
                </span>
            </div>

            {asset ? (
                <div className="card-body">
                    <BrandMediaPreview key={asset.reference} channelId={channelId} role={role} asset={asset} />
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
                    <h3>No {title.toLowerCase()} active in this draft</h3>
                    <p>Select a file below. The backend remains authoritative for media validation.</p>
                </div>
            )}

            <div className="branding-upload-panel">
                <label className="form-label" htmlFor={`brand-${role}-file`}>{asset ? `Select replacement ${title.toLowerCase()}` : `Select ${title.toLowerCase()} file`}</label>
                <input ref={inputRef} id={`brand-${role}-file`} type="file" accept={accept} onChange={handleSelection} disabled={uploading || deleting} />
                {selectedFile && <div className="branding-selected-file"><strong>{selectedFile.name}</strong><span>{formatFileSize(selectedFile.size)} · {selectedFile.type || "type not reported"}</span></div>}
                <button type="button" className="btn btn-primary btn-sm" onClick={() => void handleUpload()} disabled={!selectedFile || uploading || deleting}>{uploading ? "Uploading…" : "Upload for validation"}</button>
                <small>Uploading stores a candidate only. It does not save Channel DNA.</small>
                {message && <div className="branding-inline-message" role="status">{message}</div>}
                {error && <div className="branding-asset-error" role="alert">{error}</div>}
            </div>

            <div className="card-footer branding-asset-actions">
                <span className="form-helper">Detach changes current branding only; historical assets remain protected.</span>
                <div className="flex-row gap-2">
                    {asset && <button type="button" className="btn btn-secondary btn-sm" onClick={handleDetach} disabled={uploading || deleting}>Remove from branding</button>}
                    {safelyDeletable && <button type="button" className="btn btn-danger btn-sm" onClick={() => void handleDelete()} disabled={uploading || deleting}>{deleting ? "Deleting…" : "Delete unused candidate"}</button>}
                </div>
            </div>
        </article>
    );
}

function ToggleControl({ label, description, checked, onChange, disabled, disabledReason }: { label: string; description: string; checked: boolean; onChange: (checked: boolean) => void; disabled?: boolean; disabledReason?: string }) {
    return (
        <label className={`branding-toggle-row${disabled ? " disabled" : ""}`}>
            <span>
                <strong>{label}</strong>
                <small>{description}</small>
                {disabled && disabledReason && <small className="branding-disabled-reason">{disabledReason}</small>}
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
                <ToggleControl label="Micro Intro" description="Permit long-form production to schedule the configured intro after the opening hook." checked={brandPackage.long_form.micro_intro_enabled} onChange={(value) => updateLongForm("micro_intro_enabled", value)} disabled={!brandPackage.intro_asset} disabledReason="Upload or restore an intro asset to enable this runtime permission." />
                <ToggleControl label="Channel Bug Runtime Policy" description="Permit long-form production to use a channel bug. This does not activate the overlay placement." checked={brandPackage.long_form.channel_bug_enabled} onChange={(value) => updateLongForm("channel_bug_enabled", value)} disabled={!brandPackage.logo_asset} disabledReason="Upload or restore a logo asset to enable this runtime permission." />
                <ToggleControl label="Branded Outro" description="Permit long-form production to schedule the configured branded outro." checked={brandPackage.long_form.branded_outro_enabled} onChange={(value) => updateLongForm("branded_outro_enabled", value)} disabled={!brandPackage.outro_asset} disabledReason="Upload or restore an outro asset to enable this runtime permission." />
                <div className="branding-info-callout">
                    <strong>Two independent channel bug gates</strong>
                    <span><b>Runtime policy</b> decides whether long-form production may use the bug. <b>Placement enablement</b> activates the configured overlay itself. Both must be enabled for rendering.</span>
                </div>
            </div>
        </section>
    );
}

function RangeField({ label, helper, value, min, max, step, suffix, error, onChange }: { label: string; helper: string; value: number; min: number; max: number; step: number; suffix: string; error?: string; onChange: (value: number) => void }) {
    const update = (raw: string) => {
        const parsed = Number(raw);
        if (!Number.isFinite(parsed)) return;
        const decimals = String(step).split(".")[1]?.length || 0;
        const clamped = Math.min(max, Math.max(min, parsed));
        onChange(Number(clamped.toFixed(decimals)));
    };
    return (
        <div className="branding-range-field">
            <div className="flex-between">
                <span className="form-label">{label}</span>
                <output className="branding-range-output">{Math.round(value * 100)}{suffix} <small>({value.toFixed(2)})</small></output>
            </div>
            <small className="branding-range-helper">{helper} Domain: {min.toFixed(2)}–{max.toFixed(2)}.</small>
            <div className="branding-range-inputs">
                <input type="range" min={min} max={max} step={step} value={value} onChange={(event) => update(event.target.value)} aria-label={`${label} slider`} />
                <input type="number" min={min} max={max} step={step} value={value} onChange={(event) => update(event.target.value)} aria-label={`${label} numeric value`} />
            </div>
            {error && <span className="branding-field-error">{error}</span>}
        </div>
    );
}

export function LogoPlacementCard({ policy, hasLogo, onChange, errors }: { policy: ChannelBugPolicy; hasLogo: boolean; onChange: (next: ChannelBugPolicy) => void; errors: Record<string, string> }) {
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
                <ToggleControl label="Channel Bug Placement Enablement" description="Activate this configured overlay placement. Runtime permission above remains an independent gate." checked={policy.enabled} onChange={(value) => update("enabled", value)} disabled={!hasLogo} disabledReason="Upload or restore a logo asset before activating overlay placement." />
                <div className="branding-control-grid">
                    <div className="form-group">
                        <label className="form-label" htmlFor="bug-position">Overlay position</label>
                        <select id="bug-position" value={policy.position} onChange={(event) => update("position", event.target.value as ChannelBugPolicy["position"])}>
                            <option value="TOP_LEFT">Top left</option>
                            <option value="TOP_RIGHT">Top right</option>
                            <option value="BOTTOM_LEFT">Bottom left</option>
                            <option value="BOTTOM_RIGHT">Bottom right</option>
                        </select>
                        <span className="form-helper">Anchor the logo within the video frame.</span>
                    </div>
                    <div className="form-group">
                        <label className="form-label" htmlFor="bug-timing">Display timing policy</label>
                        <select id="bug-timing" value={policy.timing_policy} onChange={(event) => update("timing_policy", event.target.value as ChannelBugPolicy["timing_policy"])}>
                            <option value="ALWAYS">Always</option>
                            <option value="AFTER_INTRO">After intro</option>
                            <option value="MAIN_CONTENT_ONLY">Main content only</option>
                        </select>
                        <span className="form-helper">Always, after the intro, or only during main content.</span>
                    </div>
                </div>
                <RangeField label="Logo scale" helper="Fraction of the output frame used by the overlay." value={policy.scale} min={0.01} max={1} step={0.01} suffix="%" error={errors.scale} onChange={(value) => update("scale", value)} />
                <RangeField label="Logo opacity" helper="Overlay visibility from transparent to fully opaque." value={policy.opacity} min={0} max={1} step={0.01} suffix="%" error={errors.opacity} onChange={(value) => update("opacity", value)} />
                <RangeField label="Horizontal safe margin" helper="Inset from the selected left or right edge." value={policy.safe_margin_x} min={0} max={0.25} step={0.01} suffix="%" error={errors.safe_margin_x} onChange={(value) => update("safe_margin_x", value)} />
                <RangeField label="Vertical safe margin" helper="Inset from the selected top or bottom edge." value={policy.safe_margin_y} min={0} max={0.25} step={0.01} suffix="%" error={errors.safe_margin_y} onChange={(value) => update("safe_margin_y", value)} />
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
