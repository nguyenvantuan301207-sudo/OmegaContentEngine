import type {
  ProductionRequest,
  ScriptQAStatus,
  ScriptVersionSummary,
} from "./api.ts";

export function resolveCurrentScript(
  scripts: ScriptVersionSummary[],
): ScriptVersionSummary | null {
  if (!scripts.length) return null;
  const current = scripts.find((s) => s.is_current);
  if (current) return current;
  return [...scripts].sort((a, b) => b.version - a.version)[0] ?? null;
}

export function isScriptEligibleForProduction(
  qaStatus?: ScriptQAStatus | string | null,
): boolean {
  return qaStatus === "PASSED" || qaStatus === "PASSED_WITH_WARNINGS";
}

export interface ProductionHandoffResult {
  requestId: string;
  reused: boolean;
  scriptVersionId: string;
}

export async function proceedToProductionHandoff({
  channelId,
  scripts,
  currentQaStatus,
  listRequests,
  createRequest,
}: {
  channelId: string;
  scripts: ScriptVersionSummary[];
  currentQaStatus?: ScriptQAStatus | string | null;
  listRequests: (channelId: string) => Promise<ProductionRequest[]>;
  createRequest: (
    channelId: string,
    payload: { script_version_id: string },
  ) => Promise<ProductionRequest>;
}): Promise<ProductionHandoffResult> {
  const currentScript = resolveCurrentScript(scripts);
  if (!currentScript) {
    throw new Error(
      "A current script version is required to proceed to production.",
    );
  }

  const effectiveQaStatus = currentQaStatus ?? currentScript.qa_status;
  if (!isScriptEligibleForProduction(effectiveQaStatus)) {
    throw new Error(
      `Script QA status is ${effectiveQaStatus}. Only scripts with PASSED or PASSED_WITH_WARNINGS status can proceed to production.`,
    );
  }

  const existingRequests = await listRequests(channelId);
  const reusable = existingRequests.find(
    (req) =>
      req.script_version_id === currentScript.id &&
      req.status !== "FAILED" &&
      req.status !== "CANCELLED",
  );

  if (reusable) {
    return {
      requestId: reusable.id,
      reused: true,
      scriptVersionId: currentScript.id,
    };
  }

  const created = await createRequest(channelId, {
    script_version_id: currentScript.id,
  });

  return {
    requestId: created.id,
    reused: false,
    scriptVersionId: currentScript.id,
  };
}
