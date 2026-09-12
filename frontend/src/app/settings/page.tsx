"use client";

import { useEffect, useMemo, useState } from "react";
import { getSystemInfo, getSystemStatus, getTTSCapability, type SystemInfo, type SystemStatus, type TTSCapability } from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";
import { DEFAULT_PREFERENCES, persistPreferences, readPreferences, type LocalPreferences } from "@/lib/preferences";
import { Alert, StatusBadge } from "@/components/ui";

type Section = "general" | "appearance" | "workspace" | "providers" | "production" | "guardian" | "publishing" | "cost" | "privacy" | "developer" | "language" | "notifications";
type Classification = "REAL_WRITABLE" | "LOCAL_PREFERENCE" | "REAL_READ_ONLY" | "UNSUPPORTED";

const sections: Array<[Section, string, string]> = [
  ["general", "⌂", "General"], ["appearance", "✦", "Appearance"], ["workspace", "◇", "Workspace"],
  ["providers", "⚡", "AI & Providers"], ["production", "▶", "Production"], ["guardian", "◆", "Guardian"],
  ["publishing", "↑", "Publishing"], ["cost", "$", "Usage & Cost"], ["privacy", "▣", "Data & Privacy"],
  ["developer", "⚙", "Developer"], ["language", "◎", "Language & Region"], ["notifications", "◉", "Notifications"],
];

const matrix: Array<{ control: string; classification: Classification; source: string; persistence: string; effect: string; refresh: string }> = [
  { control: "Active Channel", classification: "REAL_WRITABLE", source: "OperatorContext + channel API", persistence: "localStorage", effect: "Topbar and channel-aware pages", refresh: "Yes" },
  { control: "Show internal/test channels", classification: "LOCAL_PREFERENCE", source: "Browser preference", persistence: "localStorage", effect: "Visibility of test, demo, and historical E2E channels across surfaces", refresh: "Yes" },
  { control: "Theme", classification: "LOCAL_PREFERENCE", source: "Browser preference", persistence: "localStorage", effect: "Application color theme", refresh: "Yes" },
  { control: "Density", classification: "LOCAL_PREFERENCE", source: "Browser preference", persistence: "localStorage", effect: "Workspace spacing", refresh: "Yes" },
  { control: "Reduce motion", classification: "LOCAL_PREFERENCE", source: "Browser preference", persistence: "localStorage", effect: "Animations and transitions", refresh: "Yes" },
  { control: "High contrast", classification: "LOCAL_PREFERENCE", source: "Browser preference", persistence: "localStorage", effect: "Borders and muted text", refresh: "Yes" },
  { control: "Large UI text", classification: "LOCAL_PREFERENCE", source: "Browser preference", persistence: "localStorage", effect: "Base interface scale", refresh: "Yes" },
  { control: "Show technical IDs", classification: "LOCAL_PREFERENCE", source: "Browser preference", persistence: "localStorage", effect: "Technical disclosures", refresh: "Yes" },
  { control: "Auto-save editor drafts", classification: "UNSUPPORTED", source: "No editor integration", persistence: "None", effect: "Disabled", refresh: "N/A" },
  { control: "Confirm destructive actions", classification: "UNSUPPORTED", source: "No global confirmation API", persistence: "None", effect: "Disabled", refresh: "N/A" },
  { control: "Local TTS Configuration", classification: "REAL_READ_ONLY", source: "GET /api/v1/system/tts", persistence: "READ_ONLY", effect: "Runtime capability & policy preview", refresh: "Server-owned" },
  { control: "Runtime and channel configuration", classification: "REAL_READ_ONLY", source: "System/channel APIs", persistence: "Backend", effect: "Informational", refresh: "Server-owned" },
];

function Capability({ kind }: { kind: Classification }) {
  const label = kind === "LOCAL_PREFERENCE" ? "Editable · local" : kind === "REAL_WRITABLE" ? "Editable · context" : kind === "REAL_READ_ONLY" ? "Read-only" : "Unsupported";
  return <StatusBadge tone={kind === "UNSUPPORTED" ? "neutral" : kind === "REAL_READ_ONLY" ? "info" : "success"}>{label}</StatusBadge>;
}

function Row({ title, description, value, classification = "REAL_READ_ONLY" }: { title: string; description: string; value: React.ReactNode; classification?: Classification }) {
  return <div className={`setting-row capability-${classification.toLowerCase()}`}><div><b>{title}</b><div className="small muted">{description}</div></div><div className="settings-row-value">{value}<Capability kind={classification} /></div></div>;
}

function SectionCard({ eyebrow, title, kind, children }: { eyebrow: string; title: string; kind: Classification; children: React.ReactNode }) {
  return <div className="card pad"><div className="settings-card-title"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div><Capability kind={kind} /></div>{children}</div>;
}

export default function SettingsPage() {
  const {
    channels,
    visibleChannels,
    selectedChannelId,
    setSelectedChannelId,
    channelsLoading,
    showInternalChannels,
    setShowInternalChannels,
    selectedChannelClassification,
    isSelectedChannelInternal,
  } = useOperatorContext();
  const [activeSection, setActiveSection] = useState<Section>("general");
  const [prefs, setPrefs] = useState<LocalPreferences>(DEFAULT_PREFERENCES);
  const [notice, setNotice] = useState<string | null>(null);
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [ttsCapability, setTtsCapability] = useState<TTSCapability | null>(null);

  useEffect(() => { setPrefs(readPreferences()); }, []);
  useEffect(() => {
    void Promise.allSettled([getSystemInfo(), getSystemStatus(), getTTSCapability()]).then(([info, status, tts]) => {
      if (info.status === "fulfilled") setSystemInfo(info.value);
      if (status.status === "fulfilled") setSystemStatus(status.value);
      if (tts.status === "fulfilled") setTtsCapability(tts.value);
    });
  }, []);

  const updatePref = <K extends keyof LocalPreferences>(key: K, value: LocalPreferences[K]) => {
    setPrefs((current) => { const updated = { ...current, [key]: value }; persistPreferences(updated); return updated; });
    if (key === "showInternalChannels") {
      setShowInternalChannels(Boolean(value));
    }
    setNotice("Preference applied and saved in this browser");
    window.setTimeout(() => setNotice(null), 2400);
  };

  const activeChannel = channels.find((channel) => channel.id === selectedChannelId) ?? channels[0] ?? null;
  const timezone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone, []);

  return <div className="ui-page-stack settings-page">
    <div className="page-head settings-head"><div><div className="eyebrow">OPERATOR CONTROL</div><h1 className="title">Settings</h1><p className="sub">Real preferences, runtime facts, and clearly labelled capability boundaries.</p></div>{notice && <span className="badge ok">✓ {notice}</span>}</div>
    <div className="settings-shell">
      <aside className="card settings-nav" aria-label="Settings sections">{sections.map(([id, icon, label]) => <button type="button" key={id} className={`snav ${activeSection === id ? "active" : ""}`} onClick={() => setActiveSection(id)}><span>{icon}</span>{label}</button>)}</aside>
      <section className="settings-content">
        {activeSection === "general" && <>
          <div className="settings-grid">
            <SectionCard eyebrow="CONTEXT" title="Workspace defaults" kind="REAL_WRITABLE">
              <Row
                title="Active Channel"
                description="Updates OperatorContext and every channel-aware surface"
                classification="REAL_WRITABLE"
                value={
                  <select
                    className="select"
                    value={selectedChannelId}
                    onChange={(event) => void setSelectedChannelId(event.target.value)}
                    disabled={channelsLoading || channels.length === 0}
                  >
                    {(showInternalChannels ? channels : visibleChannels).map((channel) => (
                      <option key={channel.id} value={channel.id}>
                        {channel.name} · {channel.platform}
                      </option>
                    ))}
                  </select>
                }
              />
              <Row
                title="Show internal/test channels"
                description="Include test fixtures, demo channels, and historical E2E fixtures across channels, production, and mission setup"
                classification="LOCAL_PREFERENCE"
                value={
                  <button
                    type="button"
                    className={`switch ${showInternalChannels ? "on" : ""}`}
                    aria-pressed={showInternalChannels}
                    onClick={() => {
                      const next = !showInternalChannels;
                      updatePref("showInternalChannels", next);
                    }}
                    aria-label="Toggle internal and test channel visibility"
                  />
                }
              />
              <Row title="Auto-save editor drafts" description="No shared editor draft integration is exposed" classification="UNSUPPORTED" value={<button className="switch" disabled aria-label="Auto-save unsupported" />} />
              <Row title="Confirm destructive actions" description="Confirmation remains owned by each implemented flow" classification="UNSUPPORTED" value={<button className="switch" disabled aria-label="Global destructive confirmation unsupported" />} />
            </SectionCard>
            <SectionCard eyebrow="ACCESSIBILITY" title="Display assistance" kind="LOCAL_PREFERENCE">
              {([ ["reduceMotion", "Reduce motion", "Disables non-essential animations and smooth scrolling"], ["highContrast", "High contrast", "Strengthens surface borders and muted text"], ["largeText", "Large UI text", "Increases the application base type scale"] ] as const).map(([key, title, description]) => <Row key={key} title={title} description={description} classification="LOCAL_PREFERENCE" value={<button type="button" className={`switch ${prefs[key] ? "on" : ""}`} aria-pressed={prefs[key]} onClick={() => updatePref(key, !prefs[key])} aria-label={`Toggle ${title.toLowerCase()}`} />} />)}
            </SectionCard>
          </div>
          <details className="card settings-matrix"><summary><span><span className="eyebrow">AUDIT</span><b>Settings interaction matrix</b></span><span className="muted small">{matrix.length} controls reviewed</span></summary><div className="settings-matrix-scroll"><table><thead><tr><th>Control</th><th>Classification</th><th>State source</th><th>Persistence</th><th>Visible effect</th><th>Survives refresh</th></tr></thead><tbody>{matrix.map((row) => <tr key={row.control}><td>{row.control}</td><td><code>{row.classification}</code></td><td>{row.source}</td><td>{row.persistence}</td><td>{row.effect}</td><td>{row.refresh}</td></tr>)}</tbody></table></div></details>
        </>}

        {activeSection === "appearance" && <div className="settings-grid">
          <SectionCard eyebrow="APPEARANCE" title="Theme and density" kind="LOCAL_PREFERENCE">
            <Row title="Theme" description="Dark, light, or operating-system preference" classification="LOCAL_PREFERENCE" value={<div className="segmented-control">{(["dark", "system", "light"] as const).map((theme) => <button type="button" key={theme} className={prefs.theme === theme ? "active" : ""} onClick={() => updatePref("theme", theme)}>{theme}</button>)}</div>} />
            <Row title="Density" description="Changes page, card, navigation, and timeline spacing" classification="LOCAL_PREFERENCE" value={<div className="segmented-control">{(["comfortable", "compact"] as const).map((density) => <button type="button" key={density} className={prefs.density === density ? "active" : ""} onClick={() => updatePref("density", density)}>{density}</button>)}</div>} />
          </SectionCard>
          <SectionCard eyebrow="DISCLOSURE" title="Technical information" kind="LOCAL_PREFERENCE">
            <Row title="Show technical IDs" description="Shows or hides ID and hash disclosures on integrated surfaces" classification="LOCAL_PREFERENCE" value={<button type="button" className={`switch ${prefs.showTechnicalIds ? "on" : ""}`} aria-pressed={prefs.showTechnicalIds} onClick={() => updatePref("showTechnicalIds", !prefs.showTechnicalIds)} aria-label="Toggle technical ID visibility" />} />
            <Row title="Color system" description="Theme-aware surface tokens" value={<span className="readout">OMEGA V2</span>} />
          </SectionCard>
        </div>}

        {activeSection === "workspace" && <SectionCard eyebrow="WORKSPACE" title="Authoritative channel context" kind="REAL_READ_ONLY">{activeChannel ? <><Row title="Channel" description="Current OperatorContext selection" value={<span className="readout">{activeChannel.name}</span>} /><Row title="Classification" description="Channel provenance classification" value={<span className="readout">{selectedChannelClassification}{isSelectedChannelInternal ? " (Internal fixture)" : " (Product visible)"}</span>} /><Row title="Platform" description="Persisted channel platform" value={<span className="readout">{activeChannel.platform}</span>} /><Row title="Primary language" description="Persisted channel language" value={<span className="readout">{activeChannel.primary_language}</span>} /><Row title="State" description="Persisted channel lifecycle" value={<StatusBadge tone={activeChannel.state === "ACTIVE" ? "success" : "neutral"}>{activeChannel.state}</StatusBadge>} /></> : <Alert tone="info" title="No channel available">No persisted channel context was returned.</Alert>}</SectionCard>}

        {activeSection === "providers" && <div className="settings-grid">
          <SectionCard eyebrow="AI & PROVIDERS" title="Local TTS Engine (Kokoro ONNX)" kind="REAL_READ_ONLY">
            {ttsCapability ? (
              <>
                <Row title="Engine & Model" description="Active on-premise local TTS runtime" value={<span className="readout">{ttsCapability.engine} ({ttsCapability.model})</span>} />
                <Row title="Model Readiness" description="Persistent weights integrity status" value={<StatusBadge tone={ttsCapability.readiness === "READY" ? "success" : ttsCapability.readiness === "CHECKSUM_MISMATCH" ? "danger" : "neutral"}>{ttsCapability.readiness}</StatusBadge>} />
                <Row title="Execution Device" description="Runtime inference acceleration" value={<span className="readout">{ttsCapability.device} (Available: {ttsCapability.available_devices.join(", ")})</span>} />
                <Row title="Server Defaults" description="Deployment baseline narration policy" value={<span className="readout">Voice: {ttsCapability.default_voice} · Lang: {ttsCapability.default_language} · Speed: 1.0x · Profile: {ttsCapability.default_profile}</span>} />
                <Row
                  title="Active Channel Preference"
                  description="Policy stored in active channel metadata"
                  value={
                    <span className="readout">
                      {(() => {
                        const nar = (activeChannel?.metadata as Record<string, unknown> | undefined)?.narration as Record<string, unknown> | undefined;
                        if (!nar) return "(None configured — falls through to server defaults)";
                        return `Voice: ${(nar.voice as string) || (nar.voice_ref as string) || "default"} · Lang: ${(nar.language as string) || "default"} · Speed: ${(nar.speed as string) || "default"} · Profile: ${(nar.profile as string) || "default"}`;
                      })()}
                    </span>
                  }
                />
                <Row
                  title="Resolved Policy Preview"
                  description="Field-wise resolution result for current active channel"
                  value={
                    <span className="readout">
                      {(() => {
                        const nar = (activeChannel?.metadata as Record<string, unknown> | undefined)?.narration as Record<string, unknown> | undefined;
                        const v = (nar?.voice as string) || (nar?.voice_ref as string) || ttsCapability.default_voice;
                        const l = (nar?.language as string) || ttsCapability.default_language;
                        const s = (nar?.speed as string) || "1.0";
                        const p = (nar?.profile as string) || ttsCapability.default_profile;
                        return `Voice: ${v} · Lang: ${l} · Speed: ${s}x · Profile: ${p} · Device: ${ttsCapability.device}`;
                      })()}
                    </span>
                  }
                />
                <div className="pad-sm muted small">SETTINGS_PERSISTENCE = READ_ONLY. No global write endpoint is exposed.</div>
              </>
            ) : (
              <Alert tone="info" title="Loading capability">Querying /api/v1/system/tts runtime capability...</Alert>
            )}
          </SectionCard>
          <Unsupported eyebrow="EXTERNAL PROVIDERS" title="Cloud & external AI providers" message="Provider API keys and dynamic routing remain deployment-owned. Per-request cloud switching is deferred to future phase." />
        </div>}
        {activeSection === "production" && <ReadOnly eyebrow="PRODUCTION" title="Request-owned output settings" message="Resolution, frame rate, codecs, and container are authoritative on each persisted production request. There is no global write API." />}
        {activeSection === "guardian" && <ReadOnly eyebrow="QA / GUARDIAN" title="Governance policy" message="Guardian results and publication gates are visible in mission and production workspaces. This frontend has no global policy write endpoint." />}
        {activeSection === "publishing" && <Unsupported eyebrow="PUBLISHING" title="External gateways" message="The frontend cannot authoritatively determine provider connectivity here. Publishing actions remain scoped to real eligible artifacts." />}
        {activeSection === "cost" && <Unsupported eyebrow="USAGE & COST" title="Account telemetry" message="No editable quotas or aggregate cost metrics are exposed on this surface. Mission-scoped cost evidence remains in Guardian views where available." />}
        {activeSection === "privacy" && <Unsupported eyebrow="DATA & PRIVACY" title="Storage controls" message="Retention, export, and deletion controls are not exposed by the current frontend API. No working-looking controls are rendered." />}
        {activeSection === "developer" && <SectionCard eyebrow="DEVELOPER" title="Runtime diagnostics" kind="REAL_READ_ONLY"><Row title="Application" description="Reported by the system info endpoint" value={<span className="readout">{systemInfo ? `${systemInfo.app} ${systemInfo.version}` : "Unavailable"}</span>} /><Row title="Environment" description="Reported by the system info endpoint" value={<span className="readout">{systemInfo?.environment ?? "Unavailable"}</span>} /><Row title="System status" description="Reported by the system status endpoint" value={<StatusBadge tone={systemStatus?.status === "ok" ? "success" : "neutral"}>{systemStatus?.status ?? "Unavailable"}</StatusBadge>} /><Row title="API base" description="Public target; no credentials displayed" value={<span className="readout text-mono">{process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"}</span>} /></SectionCard>}
        {activeSection === "language" && <SectionCard eyebrow="LANGUAGE & REGION" title="Resolved locale" kind="REAL_READ_ONLY"><Row title="Interface language" description="Current fixed console language" value={<span className="readout">English (US)</span>} /><Row title="Browser timezone" description="Resolved by the active browser" value={<span className="readout">{timezone}</span>} /><Row title="Content language" description="Owned by the selected channel" value={<span className="readout">{activeChannel?.primary_language ?? "No channel"}</span>} /></SectionCard>}
        {activeSection === "notifications" && <Unsupported eyebrow="NOTIFICATIONS" title="Notification delivery" message="Desktop notifications and external webhooks are not wired to product behavior. They remain intentionally disabled and unclaimed." />}
      </section>
    </div>
  </div>;
}

function Unsupported({ eyebrow, title, message }: { eyebrow: string; title: string; message: string }) { return <SectionCard eyebrow={eyebrow} title={title} kind="UNSUPPORTED"><Alert tone="info" title="Capability unavailable">{message}</Alert></SectionCard>; }
function ReadOnly({ eyebrow, title, message }: { eyebrow: string; title: string; message: string }) { return <SectionCard eyebrow={eyebrow} title={title} kind="REAL_READ_ONLY"><Alert tone="info" title="Runtime-owned configuration">{message}</Alert></SectionCard>; }
