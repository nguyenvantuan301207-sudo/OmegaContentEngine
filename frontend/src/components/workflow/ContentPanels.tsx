import { Alert, EmptyState, PageSection, StatusBadge } from "@/components/ui";
import type {
  ContentHook,
  ContentIntent,
  ContentOutline,
  ContentQAResult,
  ScriptVersion,
  ScriptVersionSummary,
} from "@/lib/api";
import {
  TechnicalDetails,
  WorkflowStatusSummary,
  statusTone,
} from "./WorkflowPrimitives";

export function ScriptPanel({
  script,
  revisions,
  busy,
  onRevision,
}: {
  script: ScriptVersion | null;
  revisions: ScriptVersionSummary[];
  busy: boolean;
  onRevision: (version: number) => void;
}) {
  if (!script)
    return (
      <EmptyState
        title="No script generated"
        description="Generate content to create the first script version."
      />
    );
  return (
    <PageSection
      title={script.title}
      description={`${script.estimated_word_count} words · ${script.estimated_duration_seconds} seconds · QA ${script.qa_status}`}
      actions={
        revisions.length > 1 ? (
          <div className="ui-inline-actions" aria-label="Script revisions">
            {revisions.map((revision) => (
              <button
                type="button"
                className={`btn btn-sm ${revision.version === script.version ? "btn-primary" : "btn-secondary"}`}
                disabled={busy}
                onClick={() => onRevision(revision.version)}
                key={revision.id}
              >
                v{revision.version}
                {revision.is_current ? " · current" : ""}
              </button>
            ))}
          </div>
        ) : undefined
      }
    >
      <div className="workflow-script">
        <article className="workflow-card">
          <span className="ui-eyebrow">Opening hook</span>
          <p className="workflow-copy">{script.hook_text}</p>
        </article>
        {script.sections.map((section) => (
          <section className="workflow-script-section" key={section.id}>
            <div className="workflow-card-header">
              <div>
                <span className="ui-eyebrow">
                  Section {section.section_order}
                </span>
                <h3>{section.heading}</h3>
              </div>
              <StatusBadge>{section.estimated_duration_seconds}s</StatusBadge>
            </div>
            {section.retention_beat ? (
              <Alert
                title={`Retention beat · ${section.retention_beat.beat_type}`}
              >
                {section.retention_beat.purpose}
              </Alert>
            ) : null}
            <p className="workflow-copy">{section.narration_text}</p>
            {section.statements.map((statement) => (
              <article className="workflow-script-statement" key={statement.id}>
                <div className="workflow-card-header">
                  <StatusBadge>{statement.statement_type}</StatusBadge>
                  <span className="workflow-entity-meta">
                    {statement.citations.length} citation
                    {statement.citations.length === 1 ? "" : "s"}
                  </span>
                </div>
                <p className="workflow-copy">{statement.statement_text}</p>
              </article>
            ))}
          </section>
        ))}
        <div className="workflow-card-grid">
          <article className="workflow-card">
            <span className="ui-eyebrow">Closing</span>
            <p className="workflow-copy">{script.closing_text}</p>
          </article>
          <article className="workflow-card">
            <span className="ui-eyebrow">Call to action</span>
            <p className="workflow-copy">{script.cta_text}</p>
          </article>
        </div>
        <TechnicalDetails
          label="Script metadata"
          data={{
            id: script.id,
            version: script.version,
            style_snapshot: script.style_snapshot,
          }}
        />
      </div>
    </PageSection>
  );
}

export function IntentHooksPanel({
  intent,
  hooks,
  busy,
  onSelect,
}: {
  intent: ContentIntent | null;
  hooks: ContentHook[];
  busy: boolean;
  onSelect: (id: string) => void;
}) {
  if (!intent && hooks.length === 0)
    return (
      <EmptyState
        title="No intent or hooks"
        description="These artifacts appear after content generation."
      />
    );
  return (
    <div className="workflow-detail">
      {intent ? (
        <PageSection title="Content intent" description={intent.viewer_promise}>
          <div className="workflow-card-grid">
            <article className="workflow-card">
              <h3>Audience direction</h3>
              <div className="workflow-data-list">
                <div className="workflow-data-row">
                  <span>Primary goal</span>
                  <strong>{intent.primary_goal}</strong>
                </div>
                <div className="workflow-data-row">
                  <span>Audience intent</span>
                  <strong>{intent.audience_intent}</strong>
                </div>
                <div className="workflow-data-row">
                  <span>Tone</span>
                  <strong>{intent.tone}</strong>
                </div>
                <div className="workflow-data-row">
                  <span>Pace</span>
                  <strong>{intent.pace}</strong>
                </div>
              </div>
            </article>
            <article className="workflow-card">
              <h3>Editorial promise</h3>
              <p className="workflow-copy">{intent.central_question}</p>
              <p className="workflow-copy">
                <strong>Takeaway:</strong> {intent.core_takeaway}
              </p>
            </article>
          </div>
          <TechnicalDetails data={intent} />
        </PageSection>
      ) : null}
      <PageSection
        title="Hook variants"
        description="Select the opening that best fulfills the viewer promise."
      >
        {hooks.length === 0 ? (
          <EmptyState title="No hook variants" />
        ) : (
          <div className="workflow-card-grid">
            {hooks.map((hook) => (
              <article className="workflow-card" key={hook.id}>
                <div className="workflow-card-header">
                  <StatusBadge tone={hook.selected ? "success" : "neutral"}>
                    {hook.selected ? "SELECTED" : hook.hook_type}
                  </StatusBadge>
                  <strong>{hook.score.toFixed(1)}</strong>
                </div>
                <p className="workflow-copy">{hook.text}</p>
                <div className="workflow-chip-list">
                  {hook.reason_codes.map((reason) => (
                    <span className="workflow-chip" key={reason}>
                      {reason}
                    </span>
                  ))}
                </div>
                {!hook.selected ? (
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    disabled={busy}
                    onClick={() => onSelect(hook.id)}
                  >
                    Select hook
                  </button>
                ) : null}
                <TechnicalDetails data={hook} />
              </article>
            ))}
          </div>
        )}
      </PageSection>
    </div>
  );
}

export function OutlinePanel({ outline }: { outline: ContentOutline | null }) {
  if (!outline)
    return (
      <EmptyState
        title="No outline available"
        description="Generate content to create a structured outline."
      />
    );
  return (
    <PageSection
      title="Narrative outline"
      description={outline.opening_description}
    >
      <div className="workflow-detail">
        {outline.sections.map((section, index) => (
          <article className="workflow-card" key={section.section_id}>
            <div className="workflow-card-header">
              <div>
                <span className="ui-eyebrow">Section {index + 1}</span>
                <h3>{section.title}</h3>
              </div>
              <StatusBadge>{section.estimated_duration_seconds}s</StatusBadge>
            </div>
            <p className="workflow-copy">{section.objective}</p>
            <ul className="ui-check-list">
              {section.key_points.map((point) => (
                <li key={point}>{point}</li>
              ))}
            </ul>
            <p className="workflow-copy">
              <strong>Retention goal:</strong> {section.retention_goal}
            </p>
            <TechnicalDetails data={section} />
          </article>
        ))}
        <Alert title="Closing direction">{outline.closing_description}</Alert>
      </div>
    </PageSection>
  );
}

export function CitationsPanel({ script }: { script: ScriptVersion | null }) {
  const statements =
    script?.sections.flatMap((section) => section.statements) || [];
  const cited = statements.filter(
    (statement) => statement.citations.length > 0,
  );
  return (
    <PageSection
      title="Citations and provenance"
      description="Attribution is grouped by script statement rather than presented as raw JSON."
    >
      {cited.length === 0 ? (
        <EmptyState
          title="No citations attached"
          description="No script statements currently carry research citations."
        />
      ) : (
        <div className="workflow-card-grid">
          {cited.map((statement) => (
            <article className="workflow-card" key={statement.id}>
              <p className="workflow-copy">{statement.statement_text}</p>
              <StatusBadge tone="success">
                {statement.citations.length} linked citation
                {statement.citations.length === 1 ? "" : "s"}
              </StatusBadge>
              <TechnicalDetails data={statement.citations} />
            </article>
          ))}
        </div>
      )}
    </PageSection>
  );
}

export function ContentQAPanel({
  qa,
  onRerun,
  busy,
}: {
  qa: ContentQAResult | null;
  onRerun: () => void;
  busy: boolean;
}) {
  if (!qa)
    return (
      <EmptyState
        title="No QA result"
        description="QA is available after a script version has been generated."
      />
    );
  return (
    <PageSection
      title="Script quality assurance"
      description="Blocking findings are shown before informational warnings."
      actions={
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={onRerun}
          disabled={busy}
        >
          Run QA again
        </button>
      }
    >
      <WorkflowStatusSummary
        metrics={[
          { label: "QA status", value: qa.status, status: qa.status },
          { label: "Findings", value: qa.findings.length },
        ]}
      />
      {qa.findings.length === 0 ? (
        <Alert tone="success" title="No findings">
          The selected script version passed all content QA checks.
        </Alert>
      ) : (
        <div className="workflow-card-grid">
          {qa.findings.map((finding, index) => (
            <article
              className={`workflow-card ${finding.severity === "BLOCKING" || finding.severity === "ERROR" ? "workflow-error-card" : ""}`}
              key={`${finding.rule_code}-${index}`}
            >
              <div className="workflow-card-header">
                <h3>{finding.rule_code}</h3>
                <StatusBadge tone={statusTone(finding.severity)}>
                  {finding.severity}
                </StatusBadge>
              </div>
              <p className="workflow-copy">{finding.message}</p>
              <TechnicalDetails data={finding.details} />
            </article>
          ))}
        </div>
      )}
      <TechnicalDetails
        label="QA execution details"
        data={{ id: qa.id, executed_at: qa.executed_at }}
      />
    </PageSection>
  );
}
