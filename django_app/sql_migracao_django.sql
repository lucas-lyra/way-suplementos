-- ============================================================================
-- Way Suplementos (Django) — colunas novas na tabela "movimentacoes"
--
-- IMPORTANTE: rode este script DEPOIS de "python manage.py migrate" (que cria
-- a tabela auth_user do Django) — a FK abaixo depende dela já existir.
--
-- Idempotente: seguro rodar mais de uma vez.
-- ============================================================================

ALTER TABLE movimentacoes ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'aprovado'
    CHECK (status IN ('aguardando_aprovacao', 'aprovado', 'rejeitado'));

ALTER TABLE movimentacoes ADD COLUMN IF NOT EXISTS motivo_rejeicao TEXT;

-- FK "responsável" de verdade, pedida no briefing (o campo "responsavel" em
-- texto já existente é mantido para compatibilidade com registros antigos,
-- que têm nomes de usuários do Supabase Auth em vez de IDs do Django).
ALTER TABLE movimentacoes ADD COLUMN IF NOT EXISTS responsavel_user_id INTEGER REFERENCES auth_user(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_movimentacoes_status ON movimentacoes (status);
CREATE INDEX IF NOT EXISTS idx_movimentacoes_responsavel_user ON movimentacoes (responsavel_user_id);
