# Implementation decisions

These six rulings were made while executing the approved design. They are recorded in decision order with their tradeoffs.

1. Local public-package import is retained as the explicit adoption path in Task 8 — implements independently reusable templates — costs additional validation work if a document-only adoption path would suffice.

2. Derivatives may declare their own ownership/permission/license terms while preserving immutable source-version terms and inherited notices — standard MIT explicitly permits proprietary adaptations, so a forced same-license rule would contradict the user — cost if wrong: owners may need to correct incompatible third-party adaptation terms; the tool preserves source terms but does not adjudicate license compatibility.

3. Public-package import uses the existing authenticated create-program authority and makes the importer sole owner of a new pending draft — ownership is program-scoped, and a blank instance has no existing owner membership — cost if wrong: installations wanting restricted program creation will need a separate explicit creation-permission policy; imported authority is never trusted.

4. Controlled redaction records a standardized nonpersonal reason code with readable CLI choices rather than arbitrary free text — prevents a removal explanation from reintroducing personal data into the retained audit record — cost if wrong: an agency needing richer case explanations must keep them in its separately governed records.

5. A copied historical decision may be omitted from new candidate evidence only after explicit clean supersession; current document links must be replaced or removed, while clean history and old snapshots remain intact — gives owners a supported correction path without rewriting past approval — cost if wrong: owners must update evidence links before republishing, and richer retirement rules would need a later design change.

6. Defer expired-session/throttle cleanup and moving password verification outside the SQLite writer lock — final review grades these as maintenance/optimization for the approved small local installation, with no sustained-load claim — cost if wrong: long-lived data growth and login-related write delays need earlier hardening before wider use.
