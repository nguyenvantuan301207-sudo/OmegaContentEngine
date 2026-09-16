# ADR-002: Visual Attribution Delivery Semantics

**Date**: 2026-09-16
**Status**: Proposed

## Context

OMEGA currently records the rights status and attribution text of each rendered
visual in the immutable, artifact-scoped `ProductionRuntimeTruthSnapshot`.
Production QA blocks an `ATTRIBUTION_REQUIRED` visual when that text is absent,
and Guardian maps the finding to a blocking copyright/license risk.

That establishes that attribution metadata exists. It does not establish that a
viewer or recipient receives the attribution.

The current architecture has three materially different delivery boundaries:

- the rendered media artifact and its V2 render manifest;
- local or downloaded exports, which may exist without publishing;
- a target-specific `PublishIntent`, whose description is checksummed and then
  forwarded by the YouTube adapter.

`ProductionRuntimeTruth` is created atomically with one immutable media artifact.
A publish intent is created later, can be revised per target, and is not derived
from runtime attribution today. A successful publish attempt records the remote
video identity, but not attribution-specific delivery evidence. Existing intro
and outro support composes pre-existing brand videos; it is not an attribution
credit renderer.

The decision must preserve the established authority rule:

> Runtime or physical evidence is authoritative over planning intent.

## Decision

Adopt **MODEL_D: FLEXIBLE_DELIVERY**.

External attribution delivery is required whenever a rendered visual has
`ATTRIBUTION_REQUIRED`. Internal RuntimeTruth metadata is necessary source
evidence, but is not a delivery channel.

An attribution obligation may be satisfied through one or more approved channels:

1. **PHYSICAL_RENDER**: the exact credit is visibly embedded in the media. An
   end card is one physical-render presentation, not a separate channel.
2. **PUBLISH_METADATA**: the exact credit is included in metadata delivered to a
   specific publishing target.
3. **EXPORT_SIDECAR**: the exact credit is included in a sidecar that is delivered
   as part of one indivisible export package. A sidecar left only in internal
   storage does not satisfy delivery, and a bare MP4 is not such a package.

The allowed channel set and any placement constraint are properties of each
asset's rights obligation, not a global preference. Consequently, one asset may
permit publish metadata while another requires physical rendering. The artifact
or delivery target is compliant only when every obligation is covered by a channel
allowed for that obligation.

When a provider or legacy record says only `ATTRIBUTION_REQUIRED` but does not
state which delivery channels are permitted, the allowed-channel state is
`UNKNOWN`. OMEGA must not infer that every channel is legally sufficient. A new
external delivery is blocked until the provider contract is resolved or the
artifact is rerendered from resolved rights data.

## Canonical evidence model

Implementation should define two related, immutable concepts.

### Attribution obligation

An artifact-bound obligation is derived from rendered runtime truth and contains
at least:

- stable obligation ID;
- artifact ID and artifact content SHA-256;
- source visual identity (scene index plus provider asset ID or content hash);
- normalized delivery text and its SHA-256;
- allowed delivery channels;
- placement constraint, if any (`ARTIFACT_WIDE`, `SCENE_ADJACENT`, or another
  versioned provider-defined rule);
- source rights reference and policy/schema version.

The obligation belongs in RuntimeTruth because it describes the rights of the
visual actually used in the immutable artifact. Rendered physical-credit evidence
also belongs there because it is artifact-bound and known at render finalization.
RuntimeTruth must not be mutated later with publish results.

### Attribution delivery evidence

A delivery record proves that one or more obligations were delivered and contains
at least:

- evidence ID, schema version, and creation timestamp;
- artifact ID and artifact content SHA-256;
- covered obligation IDs;
- selected channel;
- canonical delivered text and text SHA-256;
- delivery target identity;
- channel-specific proof and verification state.

Channel-specific proof is:

- **PHYSICAL_RENDER**: RuntimeTruth physical-credit structure, presentation
  coordinates/timing or end-card identity, renderer semantics version, and final
  media hash.
- **EXPORT_SIDECAR**: sidecar hash, package manifest hash, and export receipt
  proving that media and sidecar were emitted as one package.
- **PUBLISH_METADATA**: publish-intent ID/checksum, exact metadata payload digest,
  publish-attempt ID, platform/account target, and successful provider receipt.

An intent or selected channel is not proof by itself. `verified` must therefore be
a result state backed by channel-specific evidence, not an unchecked caller-supplied
boolean.

The authoritative delivery evidence is the evidence produced at the actual
delivery boundary and bound to the immutable artifact hash:

- RuntimeTruth is authoritative for physical rendering;
- an export receipt is authoritative for a sidecar package;
- a successful publish attempt plus its immutable metadata digest and provider
  receipt is authoritative for publish metadata.

## Enforcement boundaries

Use multiple gates because no single checkpoint observes every valid channel.

1. **Before render**: resolve every attribution obligation, including allowed
   channels and placement constraints. Block unresolved rights. This is a
   feasibility gate, not delivery proof.
2. **After render / artifact QA**: continue validating runtime attribution text.
   If physical delivery was selected or required, validate physical evidence from
   RuntimeTruth and the final artifact. Do not accept planning intent.
3. **Before export**: allow a bare media export only when physical delivery covers
   every obligation. Otherwise require a sidecar package and validate its exact
   manifest before release.
4. **Before PublishIntent approval and again before external publish**: derive the
   target's required attribution deterministically from RuntimeTruth; validate that
   the immutable intent payload covers all obligations not already satisfied
   physically. Re-check the intent checksum and artifact hash immediately before
   the external side effect.
5. **At publish finalization**: persist the provider-targeted delivery evidence.
   A missing or ambiguous receipt leaves the attempt failed or unknown; it must not
   be reported as verified delivery.

Production QA owns artifact and export-package checks. Publisher preflight owns
target-specific metadata checks. Guardian maps failures at both protected
boundaries to blocking copyright/license risk.

## Deterministic multi-asset policy

Canonical credit construction uses the following rules:

1. Normalize text to Unicode NFC, trim leading/trailing whitespace, and collapse
   internal whitespace runs to one ASCII space. Preserve case and punctuation.
2. Sort obligations by scene sequence index, then provider identity, then provider
   asset ID or visual content hash, then normalized text. Missing optional values
   sort as empty strings. All comparisons use explicit Unicode code-point order.
3. Deduplicate only exact normalized credit text. Do not use case folding or fuzzy
   matching. A deduplicated line retains the complete, stably ordered list of
   covered obligation IDs.
4. Credits are artifact-wide by default only when the rights obligation permits
   it. A scene-adjacent requirement remains scene-specific and cannot be collapsed
   into a final artifact-wide line.
5. Repeated provider credits are deduplicated only when their normalized required
   text is identical. Similar provider names do not imply equivalent obligations.
6. Never truncate required attribution. For physical credits, use a versioned,
   deterministic pagination/layout policy. If the renderer cannot fit all required
   text within its declared limits, fail feasibility or choose another permitted
   channel. If physical rendering is mandatory, block the render.

The normalization, ordering, layout-policy version, selected physical obligations,
and rendered credit text participate in the canonical render fingerprint. Thus a
physical-credit change invalidates the media cache. Publish-only metadata does not
change the media cache; it changes the PublishIntent checksum. Sidecar content has
its own deterministic package checksum.

## Non-publish and reuse policy

- **Local render only**: may remain an internal artifact after artifact QA, but it
  is not considered externally delivered. Any later release must pass an export
  or publish gate.
- **Downloaded MP4**: requires complete physical delivery. Otherwise the system
  must offer an indivisible media-plus-sidecar export package rather than a bare
  MP4.
- **Mission render without publication**: follows the same artifact and export
  rules as interactive rendering; mission mode does not weaken rights policy.
- **Interactive render**: follows the same rules as mission rendering.
- **Cached artifact reuse**: is valid only when the cached RuntimeTruth schema,
  obligations, physical evidence, artifact hash, and relevant semantics versions
  match. Target-specific metadata evidence is never inferred from a cache hit.
- **Republishing an existing artifact**: creates target-specific evidence for the
  new publish attempt. Existing physical evidence may be reused after hash and
  schema validation; prior publish-metadata evidence cannot satisfy a different
  target.
- **Publishing to different platforms**: each platform/account target gets a
  separately checksummed intent and delivery record. Platform length or formatting
  constraints may select different allowed channels, but may not alter or truncate
  the required text silently.

## Legacy policy

Artifacts created before delivery evidence exists have delivery state `UNKNOWN`,
not `VERIFIED`, and are not grandfathered for a new export or publish operation.
They remain readable and playable internally; existing files are not mutated or
deleted.

Before a new external delivery:

- if immutable runtime rights data fully identifies the obligations, OMEGA may
  create a new sidecar package or target-specific publish intent and evidence;
- physical delivery may be claimed only when legacy physical evidence can be
  deterministically validated against the artifact, otherwise rerendering is
  required;
- if RuntimeTruth or allowed-channel information is absent or ambiguous, delivery
  is blocked pending rights resolution and re-QA/rerender.

No migration may rewrite old RuntimeTruth payloads to manufacture evidence.

## Repository impact

Implementation requires:

- a versioned obligation/delivery contract and a RuntimeTruth schema revision;
- render-contract support for deterministic physical credits and cache validation;
- an append-only persistence model for target/package delivery evidence;
- PublishIntent derivation from RuntimeTruth, attribution coverage in its checksum,
  and publish-result evidence from adapters;
- artifact, export, publish-preflight, and publish-finalization gates;
- new QA rule codes and Guardian mappings for unresolved or undelivered
  attribution.

A database migration is expected for durable append-only delivery evidence and
publisher receipt fields. It must be source-only until separately approved and
must not rewrite immutable legacy RuntimeTruth.

## Phased implementation

Do not name this work P18-E3 without an explicit roadmap decision.

1. **Canonical contract and evidence foundation**: add versioned obligation,
   channel, coverage, and delivery-evidence domain models; define legacy `UNKNOWN`;
   persist append-only evidence; expose read APIs. No physical rendering or real
   publishing side effects.
2. **Export delivery**: implement deterministic sidecar/package generation and the
   export gate. This provides a publisher-independent compliant path.
3. **Physical delivery**: add deterministic credit composition (including end-card
   presentation), RuntimeTruth evidence, render fingerprints, cache validation, and
   post-render QA.
4. **Publisher metadata delivery**: derive metadata from RuntimeTruth, extend intent
   and adapter/result contracts, add preflight/finalization evidence, and cover each
   target independently.
5. **Guardian and legacy rollout**: enable blocking mappings at export/publish
   checkpoints, legacy re-QA behavior, and monitored enforcement after all required
   channels are available.

The first implementation phase is the canonical contract and evidence foundation.
It prevents later renderer, exporter, and publisher changes from inventing
incompatible proof formats.

## Alternatives considered

### MODEL_A: INTERNAL_METADATA_ONLY

Rejected because metadata presence does not prove recipient-visible delivery. It
would label the current gap as compliance.

### MODEL_B: PHYSICAL_RENDER_ONLY

Rejected as unnecessarily restrictive. It changes every media artifact and cache
key, increases layout/overflow risk, and cannot exploit target metadata even when
the rights obligation permits it. Physical rendering remains mandatory when an
individual obligation requires it.

### MODEL_C: PUBLISH_METADATA_ONLY

Rejected because local downloads and non-publishing exports are first-class flows,
and an artifact may be publisher-independent. It also makes compliance depend on
one downstream adapter.

### MODEL_E: DUAL_REQUIREMENT

Rejected because requiring both physical and publish metadata for every asset is
not supported by current rights evidence and would duplicate credits, reduce
backward compatibility, and impose target-specific behavior on non-publish flows.
Both channels may still be selected when an individual obligation requires both.

## Consequences

- External attribution is proven instead of inferred from internal metadata.
- RuntimeTruth remains the authority for what was rendered; publish evidence is
  appended rather than written back into immutable artifact truth.
- Interactive, mission, local export, cached reuse, and publishing share one
  obligation model while retaining channel-specific proof.
- The model is more complex than a single global channel and requires schema,
  persistence, QA, Guardian, renderer/exporter, and publisher work.
- No production behavior changes merely by accepting this ADR.
