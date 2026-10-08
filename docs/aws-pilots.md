# ONPF 0.1.0 AWS pilots

Status: pilot requirements recorded on 2026-09-28. AWS deployment and live pilot execution remain pending. This document does not record completed tests or approval to operate either community program.

## Two concurrent pilots

| Pilot | Program being designed | Design input and decisions |
| --- | --- | --- |
| Group pilot | Barrie Bridge art, hobby, maker and gardening program, with the volunteer guided-art program released separately | Multiple responses to batched inquiries; designated decision owners refine and approve the design. |
| Individual pilot | Barrie Bridge participant self-cooking program: helping participants get into the kitchen to cook for themselves | One person supplies design input and makes final design decisions. |

Working interpretation: “single user input” means one contributor working through successive batches, rather than one submission for the entire program. The number of people who eventually participate in cooking is a separate program-design question and remains undecided.

The individual pilot runs only on AWS. Do not create a local pilot database, seed a local kitchen program, or require a local application installation. A browser connects to the AWS-hosted application.

## Fresh start for the kitchen program

Create a new blank program with the working title **Barrie Bridge participant self-cooking program**. Use the purpose supplied above and Barrie, Ontario, Canada as its local context. Assign the individual designer as its sole decision owner; the existing “all owners” approval rule then requires that person's explicit approval.

Reuse the ONPF software and blank framework structures. Do not import, copy, adapt, or prefill previous kitchen-program outputs, recipes, policies, forms, assessments, proposals or decisions. Do not seed the program from the fictional demonstration or another program's public package or shared materials. Develop its content from fresh input in this pilot; leave unknown answers unresolved.

Use the core inquiry framework without the optional art, hobby, maker or gardening modules. Version 0.1.0 has no cooking-specific inquiry module. Record missing questions and awkward workflow steps as pilot findings; the general framework is not a complete kitchen operating standard.

## AWS setup

Follow the [AWS deployment runbook](aws-deployment.md) and complete its live acceptance checks first. The operator reports `onpf.example.org` registered through Route 53. The intended URL is `https://onpf.example.org`; deployment, DNS/HTTPS acceptance and live accounts remain pending.

Use separate program records in the same AWS installation and US East (Ohio), `us-east-2`, the operator-reported existing region, for the two pilots. Follow the deployment guide's account and region checks before creating resources; do not change regions or activate advanced features just for these pilots. Grant program membership deliberately; membership in one program does not automatically grant access to another. This is application-level separation: the server administrator and instance backups cover the shared installation.

Create the individual owner's account on AWS using the runbook's account procedure, then create the blank kitchen program through the hosted interface. Record its program reference and application release commit in the private pilot log. Keep account credentials, invitation links and participant input out of this repository.

## Pilot sequence and acceptance record

1. Confirm the kitchen program starts with blank documents and no imported responses, materials or approvals.
2. Issue available independent core questions together in batches. Have the same individual provide fresh responses, including unknown or not-applicable answers where appropriate. Use the direct questionnaire flow for browser input.
3. Preserve responses and corrections; refine them into proposals, explain each response's disposition and record owner decisions. Follow the [facilitator guide](facilitator-guide.md) in the AWS instance.
4. Draft the program documents from that fresh evidence. Log missing inquiry coverage separately from unanswered questions and software defects.
5. Prepare and inspect an exact release candidate, then explicitly approve it as the sole owner. Keep organizational permission to operate separate from document approval.
6. Export the approved reusable program under the MIT No Attribution (MIT-0) license. Check that public outputs exclude private responses and that authored text contains no unintended personal details.
7. During overlapping use of the group and individual pilots, verify that edits, responses and approvals remain attached to the intended program. With accounts assigned only to their own pilot, verify that private records of the other pilot are inaccessible.

For each step record date, AWS application version/commit, program reference, expected result, observed result and pass/fail/not-run status. The starting status for every live check is **not run**. Keep identifying evidence in the private pilot log; report software findings without copying program participants' personal information.

Both pilots evaluate the same first application version. Their program releases and approval schedules are independent. Any proposed software or question-catalog changes should be tracked separately so results remain attributable to the version actually tested.
