import { EmptyState, PageSection, StatusBadge } from "@/components/ui";
import type { NarrativePlan, ScriptVersion } from "@/lib/api";
import { TechnicalDetails } from "./WorkflowPrimitives";

export function NarrativePlanPanel({
  plan,
  script,
}: {
  plan: NarrativePlan | null;
  script: ScriptVersion | null;
}) {
  if (!plan)
    return (
      <EmptyState
        title="No narrative plan yet"
        description="Research Brief → Narrative Plan → Script. Generate content to create and validate the plan before writing the script."
      />
    );
  const qa = plan.metadata.narrative_qa;
  return (
    <PageSection
      title={`Narrative Plan v${plan.version}`}
      description="Research Brief → Narrative Plan → Script"
    >
      <p>
        {plan.status} · {plan.format_profile} · Target{" "}
        {plan.target_duration_seconds}s · Estimated{" "}
        {plan.estimated_duration_seconds}s
      </p>
      <p>Narrative QA: {qa?.status || "Not evaluated"}</p>
      {script ? (
        <p>
          {script.narrative_plan_id
            ? `Script v${script.version} pins Narrative Plan v${script.narrative_plan_version} (${script.narrative_plan_id}).`
            : "Historical script without a narrative plan."}
        </p>
      ) : null}
      <TechnicalDetails
        label="Plan lineage"
        data={{
          plan_id: plan.id,
          research_brief_id: plan.research_brief_id,
          channel_dna_revision_id: plan.channel_dna_revision_id,
          supersedes_plan_id: plan.supersedes_plan_id,
        }}
      />
      {qa?.findings.map((finding, index) => (
        <p key={index}>
          {finding.severity} · {finding.code}: {finding.explanation}
        </p>
      ))}
      {[...plan.sections]
        .sort((a, b) => a.section_order - b.section_order)
        .map((section) => (
          <section className="workflow-card" key={section.id}>
            <div className="workflow-card-header">
              <h3>
                {section.section_order}. {section.role}
              </h3>
              <StatusBadge>{section.target_duration_seconds}s</StatusBadge>
            </div>
            <p className="workflow-copy">{section.objective}</p>
            <ul>
              {section.key_information.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
            <p>{section.grounding_references.length} grounding citations</p>
            {section.grounding_references.map((ref, index) => (
              <p key={index}>{ref.description}</p>
            ))}
            <TechnicalDetails
              label="Grounding provenance"
              data={section.grounding_references}
            />
          </section>
        ))}
    </PageSection>
  );
}
