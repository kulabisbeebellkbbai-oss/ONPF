CREATE TRIGGER freeze_supporting_revision BEFORE UPDATE ON additional_document_revisions
BEGIN SELECT RAISE(ABORT,'Supporting history is immutable'); END;
CREATE TRIGGER freeze_organizer_revision BEFORE UPDATE ON organizer_clarifications
BEGIN SELECT RAISE(ABORT,'Organizer clarification history is immutable'); END;
