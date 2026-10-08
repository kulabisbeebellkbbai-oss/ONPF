# AI drafting integration with the deployed Community Garden update

The AI implementation and the deployed Community Garden update originally
diverged from the same 0.1.0 source. Deploying the standalone AI bundle would
remove existing features and collide with the deployed clarification schema.
The deployment release combines both histories before changing production.

## Preserved records and behavior

- Deployed migrations 010 through 017 remain unchanged. The additive AI migration
  is 018; native rounds use `drafting_clarification_rounds`. Existing clarification
  metadata remains in its original tables, and both round types affect readiness.
- Management, viewer restrictions, operating permissions, additional documents,
  immutable proposal snapshots and incorporations, publications, tours and
  recorded removal remain supported. Frozen release/publication bytes remain
  unchanged by migration.
- Private restore accepts the exact historical 0.1.0 prefix, deployed 0.2.0 schema
  through 017, and combined schema through 018. Original hashes and references
  are validated before migration into unpublished storage.

## Replacement drafting contract

The separate private gateway and explicit native Generate, Apply and Save flow
replace the earlier direct provider transport, consent/profile endpoints, macro
and JavaScript. Legacy active provider settings fail with a fixed migration
message. Provider credentials never enter application configuration. The former
drafting tests are superseded by the gateway, evidence, provenance, native route,
lifecycle and deployed integration suites; their old endpoints are not retained.

The new flow has a 45-second total deadline, bounded evidence/output and one
in-flight generation in the supported app process. Old application usage-counter
quotas are no longer enforced by drafting. The deployed `drafting_attempts` table
is retained as historical runtime state and omitted from logical backups.
Provider account spending controls remain an operator responsibility.

## Integration verification

Focused regressions cover both clarification gates, production proposal evidence
and immutable decision snapshots, viewer restrictions, populated schema upgrade,
historical/current private recovery and frozen hash preservation. The full
combined suite and independent integration review are deployment gates. Live
Linux/gateway/provider acceptance and independently verified pre/post backups
are recorded separately in the deployment receipt.
