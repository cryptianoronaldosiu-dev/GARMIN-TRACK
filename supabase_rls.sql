-- ============================================================
-- POLÍTICAS RLS PARA EL DASHBOARD DE ENTRENAMIENTO
-- Pegar en: Supabase → SQL Editor → Run
--
-- Hay DOS opciones. Corre la base común + UNA de las dos.
-- ============================================================

-- ──────────────────────────────────────────────
-- BASE COMÚN (correr siempre)
-- ──────────────────────────────────────────────
ALTER TABLE actividades             ENABLE ROW LEVEL SECURITY;
ALTER TABLE analisis                ENABLE ROW LEVEL SECURITY;
ALTER TABLE plan_entrenos           ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_status_history ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_read_actividades" ON actividades;
DROP POLICY IF EXISTS "anon_read_analisis"    ON analisis;
DROP POLICY IF EXISTS "anon_read_plan"        ON plan_entrenos;
DROP POLICY IF EXISTS "anon_read_ts"          ON training_status_history;
DROP POLICY IF EXISTS "anon_insert_actividades" ON actividades;
DROP POLICY IF EXISTS "anon_insert_analisis"    ON analisis;
DROP POLICY IF EXISTS "anon_insert_ts"          ON training_status_history;
DROP POLICY IF EXISTS "anon_update_plan"        ON plan_entrenos;

-- Lectura pública (la necesita el dashboard en ambas opciones)
CREATE POLICY "anon_read_actividades" ON actividades
  FOR SELECT TO anon USING (true);
CREATE POLICY "anon_read_analisis" ON analisis
  FOR SELECT TO anon USING (true);
CREATE POLICY "anon_read_plan" ON plan_entrenos
  FOR SELECT TO anon USING (true);
CREATE POLICY "anon_read_ts" ON training_status_history
  FOR SELECT TO anon USING (true);

-- ──────────────────────────────────────────────
-- OPCIÓN A — MÁXIMA SEGURIDAD (recomendada)
-- No corras nada más. Anon = solo lectura.
-- Requiere: cambiar el secret SUPABASE_KEY en GitHub
-- (repo → Settings → Secrets → Actions) por la service_role
-- key (Supabase → Settings → API). Es 1 minuto y NO requiere
-- tocar código: garmin_sync.py usa la key que le llegue.
-- Si Make.com escribe en "analisis", usar service_role ahí también.
-- Nota: el chat Coach IA no podrá editar el plan (ver MEMORIA).
-- ──────────────────────────────────────────────

-- ──────────────────────────────────────────────
-- OPCIÓN B — TODO SIGUE FUNCIONANDO CON LA ANON KEY
-- Corre este bloque además de la base común.
--
-- Permite exactamente lo que la app necesita y nada más:
--   · INSERT en actividades + training_status_history (garmin_sync.py)
--   · INSERT en analisis (Make.com / Claude)
--   · UPDATE en plan_entrenos (chat Coach IA)
--   · NADA de DELETE, ni UPDATE sobre actividades/historial
--
-- Riesgo asumido: cualquiera con la URL del dashboard podría
-- insertar filas basura o editar el plan (no borrar nada).
-- ──────────────────────────────────────────────
CREATE POLICY "anon_insert_actividades" ON actividades
  FOR INSERT TO anon WITH CHECK (true);

CREATE POLICY "anon_insert_ts" ON training_status_history
  FOR INSERT TO anon WITH CHECK (true);

CREATE POLICY "anon_insert_analisis" ON analisis
  FOR INSERT TO anon WITH CHECK (true);

CREATE POLICY "anon_update_plan" ON plan_entrenos
  FOR UPDATE TO anon USING (true) WITH CHECK (true);
