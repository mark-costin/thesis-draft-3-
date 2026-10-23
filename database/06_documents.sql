-- 06_documents.sql
-- Table: documents
-- Purpose: Uploaded lab documents and their Chandra OCR job status.
-- The file itself is Fernet-encrypted on the Docker volume; only its storage key is stored here.

CREATE TABLE IF NOT EXISTS public.documents (
    document_id     integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    patient_id      integer NOT NULL
                    REFERENCES public.patients(patient_id) ON DELETE CASCADE,
    storage_key     uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),  -- server-generated file name
                                                                     -- (never the uploaded name: blocks path traversal)
    mime_type       character varying(30) NOT NULL,
    file_size_bytes integer NOT NULL,
    file_sha256     character(64) NOT NULL,                          -- integrity check of the original file
    ocr_engine      character varying(50) NOT NULL DEFAULT 'Chandra OCR',
    ocr_text        text,                                            -- cleared after verification (data minimization)
    ocr_confidence  numeric(5,2),
    status          character varying(20) NOT NULL DEFAULT 'pending',
    error_message   character varying(255),                          -- OCR failure reason, no PHI
    uploaded_at     timestamptz NOT NULL DEFAULT now(),
    processed_at    timestamptz,
    verified_at     timestamptz,
    CONSTRAINT documents_mime_type_check
        CHECK (mime_type IN ('image/jpeg', 'image/png', 'application/pdf')),
    CONSTRAINT documents_file_size_check
        CHECK (file_size_bytes > 0 AND file_size_bytes <= 10485760),     -- 10 MB
    CONSTRAINT documents_sha256_format_check CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT documents_ocr_confidence_check
        CHECK (ocr_confidence IS NULL OR ocr_confidence BETWEEN 0 AND 100),
    CONSTRAINT documents_status_check
        CHECK (status IN ('pending', 'processing', 'ready_for_review', 'verified', 'rejected', 'failed')),
    CONSTRAINT documents_verified_check
        CHECK (status <> 'verified' OR verified_at IS NOT NULL),
    -- The same file uploaded twice by the same patient is rejected
    CONSTRAINT documents_patient_file_unique UNIQUE (patient_id, file_sha256),
    CONSTRAINT documents_id_patient_key UNIQUE (document_id, patient_id)  -- target of 08_lab_results FK
);

CREATE INDEX IF NOT EXISTS ix_documents_patient_uploaded ON public.documents (patient_id, uploaded_at DESC);
CREATE INDEX IF NOT EXISTS ix_documents_status ON public.documents (status)
    WHERE status IN ('pending', 'processing');                       -- the OCR worker's queue view
