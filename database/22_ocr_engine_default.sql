-- 22_ocr_engine_default.sql
-- Purpose: documents.ocr_engine names the engine that actually read the document (set by the
-- OCR worker: 'PaddleOCR-VL', 'PDF text layer'). The old default 'Chandra OCR' named an engine
-- that never ran in this app, so a document no engine had read yet (pending, failed, or OCR
-- switched off) looked as if Chandra had read it. The default is now 'none'.
-- Run on an existing med_db as postgres. Safe to run more than once.

ALTER TABLE public.documents ALTER COLUMN ocr_engine SET DEFAULT 'none';

-- No Chandra engine was ever configured, so every 'Chandra OCR' value is the old default
UPDATE public.documents SET ocr_engine = 'none' WHERE ocr_engine = 'Chandra OCR';

COMMENT ON COLUMN public.documents.ocr_engine IS
    'Engine that read the document (set by the OCR worker); ''none'' until an engine has read it';
