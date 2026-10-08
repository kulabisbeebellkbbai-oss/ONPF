CREATE TRIGGER retired_program_documents_insert BEFORE INSERT ON program_documents
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_program_documents_update BEFORE UPDATE ON program_documents
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_decision_fields_insert BEFORE INSERT ON decision_fields
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_decision_fields_update BEFORE UPDATE ON decision_fields
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_inquiry_questions_insert BEFORE INSERT ON inquiry_questions
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_inquiry_questions_update BEFORE UPDATE ON inquiry_questions
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_batches_insert BEFORE INSERT ON batches
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_batches_update BEFORE UPDATE ON batches
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_proposals_insert BEFORE INSERT ON proposals
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_proposals_update BEFORE UPDATE ON proposals
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_decisions_insert BEFORE INSERT ON decisions
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_decisions_update BEFORE UPDATE ON decisions
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_materials_insert BEFORE INSERT ON materials
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_materials_update BEFORE UPDATE ON materials
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_program_materials_insert BEFORE INSERT ON program_materials
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_program_materials_update BEFORE UPDATE ON program_materials
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_additional_documents_insert BEFORE INSERT ON additional_documents
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_additional_documents_update BEFORE UPDATE ON additional_documents
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_organizer_clarifications_insert BEFORE INSERT ON organizer_clarifications
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_organizer_clarifications_update BEFORE UPDATE ON organizer_clarifications
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_permission_records_insert BEFORE INSERT ON permission_records
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_permission_records_update BEFORE UPDATE ON permission_records
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_release_candidates_insert BEFORE INSERT ON release_candidates
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_release_candidates_update BEFORE UPDATE ON release_candidates
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_publications_insert BEFORE INSERT ON publications
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_publications_update BEFORE UPDATE ON publications
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_responses_insert BEFORE INSERT ON responses
WHEN EXISTS(SELECT 1 FROM programs WHERE id=(SELECT program_id FROM batches WHERE id=NEW.batch_id) AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_responses_update BEFORE UPDATE ON responses
WHEN EXISTS(SELECT 1 FROM programs WHERE id=(SELECT program_id FROM batches WHERE id=NEW.batch_id) AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_response_revisions_insert BEFORE INSERT ON response_revisions
WHEN EXISTS(SELECT 1 FROM programs WHERE id=(SELECT b.program_id FROM responses r JOIN batches b ON b.id=r.batch_id WHERE r.id=NEW.response_id) AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_response_revisions_update BEFORE UPDATE ON response_revisions
WHEN EXISTS(SELECT 1 FROM programs WHERE id=(SELECT b.program_id FROM responses r JOIN batches b ON b.id=r.batch_id WHERE r.id=NEW.response_id) AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_candidate_approvals_insert BEFORE INSERT ON candidate_approvals
WHEN EXISTS(SELECT 1 FROM programs WHERE id=(SELECT program_id FROM release_candidates WHERE id=NEW.candidate_id) AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_candidate_approvals_update BEFORE UPDATE ON candidate_approvals
WHEN EXISTS(SELECT 1 FROM programs WHERE id=(SELECT program_id FROM release_candidates WHERE id=NEW.candidate_id) AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_releases_insert BEFORE INSERT ON releases
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
CREATE TRIGGER retired_releases_update BEFORE UPDATE ON releases
WHEN EXISTS(SELECT 1 FROM programs WHERE id=NEW.program_id AND retired_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'project_retired'); END;
