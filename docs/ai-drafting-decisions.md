# AI drafting decisions and potential costs

Date: 2026-10-03. This durable record carries the decisions from the development
ledger, including rulings written without a `Ruling:` prefix. Costs below describe
the consequence of an incorrect decision, not an observed incident or a monetary
estimate. [Acceptance evidence](public-source-release.md) distinguishes tested
behavior from historical proof limits and operator acceptance.

## Execution and verification

| Decision | Reason and potential cost | Evidence or boundary |
| --- | --- | --- |
| Use PowerShell/Python equivalents of skill scratch scripts. | Git Bash was unavailable; Windows bash.exe was a WSL launcher. Incorrect equivalents could lose review artifacts or alter diff content. | Equivalent reports/diffs retained; no WSL success claimed. |
| Run one full suite per implementer deliverable; use focused gates for corrections and one final exact-tree full suite. | Duplicate review reruns add time without new coverage. Skipping the final gate could miss later integration changes. | Task 9 final 793 passed/9 skipped, no warnings; reviewers inspect supplied evidence. |
| Root dispatches independent whole-branch review after the verification commit; build final packages only after a clean review. | Premature packaging could deliver obsolete or unreviewed source. | Task 9 source/browser/test preparation complete; final reviewed-source package remains a subsequent gate. |
| Use supported CUA IAB after the preferred browser-client bridge was unavailable/untrusted. | Claiming browser acceptance without a working connection would fabricate evidence. | Actual native forms, screenshots and keyboard actions observed; failed bootstrap not repeated. |
| Use fixture CSP `script-src 'none'` when no browser engine JavaScript toggle exists. | A concealed script dependency could invalidate native-path acceptance. | Complete native Generate/Edit/Apply/Save observed; app scripts blocked, engine-disable and enhanced-script behavior unverified. |
| Save original screenshot Uint8Array bytes with the browser skill's documented Node fs/promises writeFile operation into QA output only. | Unsupported APIs or mislabeled viewport captures could leave incomplete visual proof. | Six originals inspected; observed viewport and JPEG dimensions separately recorded; initial desktop capture mislabeled mobile was renamed. |
| Fix confirmed live_server close-before-join race in the test harness; preserve genuine serving error propagation. | Filtering a warning could hide a serving failure. Production does not use this teardown. | Event-controlled RED 2 failures; loop stop/join then worker shutdown/socket close; caller receives sentinel error. Focused warning-as-error gate and final unfiltered full suite clean. |
| Address Task 1 incomplete chunk framing and Task 3 document assertion minors with meaningful regressions. | Premature EOF might leak/retry errors; a document assertion bypass might hide lost text. | Actual truncated chunked HTTP body produces sanitized no-retry error. Document sections require exact populated text and empty omitted fields. |
| Refresh stale editable 0.1 metadata to 0.2 with no dependency changes. | Installed version could disagree with source/health/artifacts. | No-deps/no-build-isolation editable install; importlib.metadata 0.2.0; pip check clean. Installed reviewed bundle still requires its own receipt. |

## Evidence, receipts and ordinary Save

| Decision | Reason and potential cost | Evidence or boundary |
| --- | --- | --- |
| Advisory review returns findings rather than mutable document content. | Findings must not silently change candidates/releases; users must navigate to an editor to act. | Review has no Apply/Save and zero browser DB changes. |
| Label historical response dispositions with their bound revision/status and whether they apply to the current revision. | An old reason next to corrected text could falsely present a settled current fact. | Task 2 correction and evidence tests; preview observed revision association. |
| Quarantine unsaved current fields/instructions and returned output; revalidate target after transport even when not selected. | Copies could send removed content, or a removed target could receive usable output. | Task 3 bounded validation and concurrent-change tests; no domain writes added. |
| Every generated question includes reason and source_handles; manual current fields may omit context. | Missing explanation/support loses CG008 traceability. | Output contract tests; context and core fields saved atomically by Tasks 5/6. |
| Validate receipt/source fingerprints inside the Save transaction before mutation, then attach with verified internal context without a second self-mutated revision check. | A post-save check would reject legitimate edits or bind different evidence; a non-atomic check could admit concurrent changes. | Rollback/source-writer/concurrent-save tests. |
| Separate expected own-Save revision bumps from later source corrections/manual edits for origin display. Preserve the original manifest. | Fresh accepted origins could immediately appear stale; changed support could falsely remain fresh. | Reviewed saved hash/target association plus source content/revision status; strict pre-save freshness remains. |
| Correct ownership: Task 4 service.py issues actual signed receipts; Task 5 evidence.py includes context/deferral fingerprints with quarantine and finite traversal. | Missing integration paths could lose provenance or leak reason text; recursive catalog/round traversal could fail. | Narrow integration and focused provenance/evidence tests. |
| Contributions/archives changes are conditional; avoid no-op edits when existing dynamic correction/removal fingerprints suffice. | Unnecessary edits add risk; ignoring real same-text response status/revision changes would lose staleness. | Provenance regressions prove real changes stale; aggregate-only display bumps tolerated. |
| Blank ai_receipt means an ordinary manual Save; nonquestion follow-ups use explicit question drafting rather than a mismatched receipt. | Manual saves could break, or unprovenanced questions could inherit the wrong authority. | Task 4/6 interfaces and native manual/follow-up tests. |

## Clarification context and immutable rounds

| Decision | Reason and potential cost | Evidence or boundary |
| --- | --- | --- |
| Hash reviewed question reason/source context alongside question fields. | Edited context could be untraced or fresh Saves falsely stale. | Task 5 provenance integration; receipt-free context-optional manual saves preserved. |
| Supply authorized workspace rounds summary via programs/routes.py. | Missing template data loses required navigation; incorrect scoping exposes private metadata. | Narrow read-only context integration and workspace/authority tests. |
| Count every valid nonremoved response, including abstain/not-applicable, as answered; show substantive content/conflict/review separately. | Answered-text-only interpretation contradicts the design and miscounts completion. | Task 5 spec correction; all response-kind tests. |
| Persist round-level kind, reason and sources, alongside frozen per-question context. Default sources are the exact union of selected question contexts; supplied additions require current authorization. | Per-question context alone loses round traceability; unrelated expansion sends unexpected evidence. | Historical ordinary batches remain metadata-free; frozen/scoped/union tests and browser third round. |
| Tolerate content-identical aggregate revision changes only for ordinary saved context normalization/issuance. | Sequential manual question saves otherwise invalidate unchanged peers. Overbroad tolerance could bypass real changes/removal or receipts. | Task 5 correction; strict signed receipt checks and actual source changes remain exact. |
| Preserve posted native question row positions/IDs/dependencies through validation failures, including blank rows. | Compacting a blank row could attach another row's dependencies or lose identity. | Task 6 focused fix and scoped re-review. |
| Add monotonic context_revision and finite intrinsic question/direct support descriptors. Preserve original signed/issued manifests, including self/cyclic support. | Same-handle support refresh/correction/status/removal could remain falsely fresh; recursive expansion could self-stale or fail. | Task 7 context/support/finite-cycle tests; strict receipt checks unchanged. |

## Upgrade, archives and public boundaries

| Decision | Reason and potential cost | Evidence or boundary |
| --- | --- | --- |
| Validate old backups against the exact historical migration schema before migration; use an unpublished temporary DB, then current validation before publication. | Current-schema validation cannot authenticate old schema; accepting invalid archives risks corrupt recovery. | Exact prefix includes 007a_exports.sql and ends 009_contact_log.sql; hash/semantic/tamper/interruption tests. |
| Enforce surviving scope/history witnesses and local same-program references; preserve historical material manifests when immutable version/lineage validates but former adoption scope has no surviving witness. | False claims of historical adoption remain possible where no witness survives. This is historical restoration only, never current/fresh generation or Save authority. | Missing mutable state/adoption witnesses explicitly documented; no fabricated proof. Archive hashes require externally trusted identity, not sender authenticity. |
| Use finite chronological removal-event prefixes to validate legitimate historical issued sources between removals. | Naive all/current quarantine reconstruction could reject valid private history or accept inconsistent sources. | Task 7 historical-source removal regression. Authored private history and frozen releases remain preserved. |
| Include reason-only authored copies in removal registry with correct program resolution. | Standalone context/round/deferral copies could evade quarantine and later be reused. | Four new descriptors, independent no-proposal copy regressions, interrupted restore/public boundary tests. |
| Update current health-version literals in both launchers and README to 0.2.0; preserve dated historical version rows and public format 1. | Mismatched launcher health prevents startup; misleading history loses evidence. | Version/launcher/archive/public exclusion tests. |
| Share one trusted ordered connection-level migration runner, preserving caller transactions and explicit old allowlist. | Duplicated execution drifts schema/transaction behavior. | Task 7 review correction, 90 focused archive/upgrade/DB checks. |
| No genuine production 0.1 archive was supplied; use a populated exact-packaged-schema fixture and disclose its scope. | Treating synthetic recovery as production recovery would overstate acceptance. | Frozen response correction, decisions, batches and nonempty approved release verified locally; operator backup/restore acceptance remains separate. |

## Gateway and deployment

| Decision | Reason and potential cost | Evidence or boundary |
| --- | --- | --- |
| Preserve optional /etc/onpf/ai-drafting.env after regenerated production.env, separate from provider/runtime settings. | App release updates could erase AI configuration. | Task 8 service integration; disabled installs/hardening preserved. |
| Add narrow sanitized gateway launcher copied into persistent independent runtime. | A launcher tied to app releases could disappear; inherited environment could leak secrets or enable unsafe callbacks. | Fixed Uvicorn launch/generic diagnostics, persistent copy/bundle tests. |
| Set direct WORKER_CONFIG request_timeout to 30 s. | Pinned initialize otherwise injected 600 s despite deployment 30 s; widening ONPF limits would hide the root cause. | Actual fake upstream prior 200 at 32.02 s; corrected 408 at 30.03 s; ONPF 45 s unchanged. |
| Add persistent ASGI facade because full LiteLLM proxy mounted admin/UI/assets and no config-only disable was verified. | Residual admin surface violates approved architecture; a bad shim could block startup/completions. | Facade copied/included independently; real pinned smoke and focused tests. |
| Forward only POST /v1/chat/completions and lifespan; deny every other HTTP route 404 and WebSocket 1008. | Mount-list filtering could miss a future surface; old health guidance would fail. | Actual 14 paths with/without key denied; readiness uses service/fixed socket plus separate fictional completion. |
| Audit complete Ubuntu 24.04 x86_64 CPython 3.12 target lock separately from Windows runtime smoke. | Incomplete/wrong target hashes could fail installation or accept wrong artifacts. | 115 target wheels, official versioned PyPI hashes and 204 active edges checked; Windows smoke runtime has 116 packages. |
| Keep native Linux/systemd/isolation and paid provider acceptance operator-only where unavailable locally. | WSL REGDB_E_CLASSNOTREG and absent container engines prevent a genuine local Linux claim. API billing/provider retention are separate; wrong disclosure risks unsafe deployment expectations and spend. | Actual Windows fake-upstream gateway behavior verified; Ubuntu service/hash install, protected identity/files, intended instance/HTTPS/backups, and paid OpenAI call remain pending. |

## Final review corrections — 2026-10-03

| Decision | Reason and potential cost | Evidence or boundary |
| --- | --- | --- |
| Explicitly enable the standalone fictional acceptance settings. | Direct gateway calls do not load the application environment; the old snippet stopped before HTTP. | The actual Python snippet extracted from setup documentation executes against an authenticated loopback fake gateway, covering success, malformed completion and provider failure with fixed sanitized output only. Paid acceptance remains separate. |
| Offer one selectable, previewed program-wide lifecycle source only in advisory review. | Approval, release and withdrawal can change without a program revision. Current draft wording alone cannot establish design approval. | Current draft and owner-scope digests; candidate/release exact hashes; rule, required/matching approval counts, status, quarantine availability, staleness and draft/scope comparisons. No owner identities/names, capability values, copied frozen text, change notes or response snapshots are sent. |
| Keep lifecycle status ephemeral; ordinary targets, saved contexts, origins and archive manifests reject its source kind. | Historical mutable lifecycle witnesses cannot be reconstructed reliably from a current status projection. Persisting them would introduce an unverifiable archive contract. | No migration or archive-schema change. Existing archive whitelist rejects even rehashed injected lifecycle manifests; current default source revalidation rejects them before context/round/question writes. Review receipts remain transient and cannot authorize an ordinary Save. |
| Clearly label the candidate link as program-wide review and reset follow-up support/selection with a visible notice. | A candidate-specific entry must not imply that only that candidate was analyzed; review-only support must not silently become saved question evidence. | All candidate hashes are visible in the selected source preview. Retained follow-up wording requires explicit selection of ordinary current sources and a new question generation/receipt. |
| Hash the complete current draft projection even when a candidate already differs or a release is historical. | A boolean mismatch alone would miss further draft changes; an aggregate revision alone misses lifecycle transitions. | Real HTTP changes for preparation, partial approval, release, withdrawal, draft changes and demotion reject results; review Edit/follow-up/Apply actions recheck exact source freshness. Frozen records remain unchanged. |

This record makes no deployment, operating permission, organizational approval,
merge or publication claim. The reviewed-source delivery receipt must preserve
these decisions and the exact test/review/package evidence before scratch cleanup.
