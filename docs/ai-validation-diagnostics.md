# AI output validation diagnostics

Rejected suggestions retain the existing `invalid_ai_output` code and HTTP 502
status. The browser error additionally shows a fixed `Validation reason` code.
These codes are selected by application validators, never from provider text.
They do not identify participants, source records, unknown provider field names,
or rejected values. Generation still saves no program records. Rejected JSON
and prompts are not added to logs, receipts, database tables or backup content.

Missing-key failures also identify the missing schema path, for example
`fields.questions[].source_handles`. These names come exclusively from the
application's schema. Unknown upstream keys, array indices, source IDs and
provider values are not included.

Useful codes include:

| Code | Meaning |
| --- | --- |
| `question_missing_answer_type` | A draft question omitted the required text-answer type. |
| `followup_unexpected_answer_type` | A possible follow-up used the draft-question shape. |
| `unexpected_fields` / `missing_required_fields` | An object violated its permitted or required keys. |
| `invalid_question_stage` / `invalid_question_document` | A question used an unsupported stage or document. |
| `unselected_source` / `duplicate_source` / `wrong_source_kind` | An evidence reference violated the selected-source contract. |
| `undeclared_citation` / `missing_question_support` / `missing_sources` | The output did not establish required source support. |
| `question_limit_exceeded` / `uncertainty_limit_exceeded` | The output exceeded the respective 20-item limit. |
| `invalid_json` / `duplicate_json_key` / `nonfinite_json_value` | JSON decoding or strict JSON validation failed. |
| `output_byte_limit` / `invalid_unicode` | The response exceeded the byte limit or used invalid Unicode. |
| `invalid_document_fields` / `invalid_structure` | Document or other structural validation failed. |

Record the visible code when a generation fails. Do not enable raw payload
logging to investigate it. A code identifies the failed requirement; it does
not preserve the original rejected response or prove that another attempt will
fail the same requirement. There is no automatic provider retry.

Question generation now sends a strict provider JSON schema requiring the
complete output envelope and every draft-question key. Its prompt distinguishes
`fields.questions` (draft questions, with `answer_type`) from top-level
`questions` (additional follow-ups, without `answer_type`). Other target types
retain JSON-object mode. Local output validation remains authoritative and
unchanged in what it accepts. Models used for question drafting must support
strict JSON-schema output; unsupported providers fail safely without an
automatic fallback or retry.

JSON mode guarantees parseable JSON but does not guarantee specific keys;
see [OpenAI's structured-output guide](https://developers.openai.com/api/docs/guides/structured-outputs)
and [LiteLLM's schema proxy documentation](https://docs.litellm.ai/docs/completion/json_mode).

A proposal-generation success does not establish that
question, decision, document or advisory-review generation is reliable.
