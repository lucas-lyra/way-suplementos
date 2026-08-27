-- ============================================================================
-- Way Suplementos (Django) — coluna "endereco" em "lojas"
-- Necessária para as Paradas de Rota mostrarem o endereço de entrega.
-- Idempotente: seguro rodar mais de uma vez.
-- ============================================================================

ALTER TABLE lojas ADD COLUMN IF NOT EXISTS endereco TEXT;
