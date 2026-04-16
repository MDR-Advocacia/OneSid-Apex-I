import os
import logging
import hashlib
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "onesid_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
SCHEMA_INIT_LOCK_ID = 6012026041501

def get_connection():
    try:
        return psycopg2.connect(
            host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS, port=DB_PORT
        )
    except Exception as e:
        logging.error(f"❌ Erro conexão BD: {e}")
        return None


def _adquirir_lock_inicializacao(cur):
    """
    Serializa o bootstrap do schema para evitar corrida entre containers
    subindo ao mesmo tempo.
    """
    cur.execute("SELECT pg_advisory_xact_lock(%s);", (SCHEMA_INIT_LOCK_ID,))

def inicializar_banco():
    conn = get_connection()
    if not conn: return
    cur = None
    try:
        cur = conn.cursor()
        _adquirir_lock_inicializacao(cur)
        
        # Tabela Processos
        cur.execute("""
            CREATE TABLE IF NOT EXISTS processos (
                id SERIAL PRIMARY KEY,
                cnj VARCHAR(50) UNIQUE NOT NULL,
                npj VARCHAR(50),
                data_atualizacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # --- ATUALIZAÇÃO DE SCHEMA (MONITORAMENTO) ---
        # Adiciona a coluna em_monitoramento se ela não existir
        cur.execute("""
            ALTER TABLE processos 
            ADD COLUMN IF NOT EXISTS em_monitoramento BOOLEAN DEFAULT FALSE;
        """)

        # Tabela Subsídios
        cur.execute("""
            CREATE TABLE IF NOT EXISTS subsidios (
                id SERIAL PRIMARY KEY,
                processo_id INTEGER REFERENCES processos(id) ON DELETE CASCADE,
                tipo VARCHAR(255),
                item TEXT,
                estado VARCHAR(100),
                data_limite VARCHAR(20),
                data_extracao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # --- MIGRACAO: Adiciona data_limite em bancos ja existentes ---
        try:
            cur.execute("ALTER TABLE subsidios ADD COLUMN IF NOT EXISTS data_limite VARCHAR(20);")
        except Exception as e:
            logging.warning(f"⚠️ Não foi possível ajustar data_limite em subsidios: {e}")

        # Tabela Tarefas (Com checagem de migração antiga mantida)
        cur.execute("SELECT to_regclass('public.tarefas_legal_one')")
        if cur.fetchone()[0]:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='tarefas_legal_one' AND column_name='solicitante_id';")
            if not cur.fetchone():
                cur.execute("DROP TABLE tarefas_legal_one;")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS tarefas_legal_one (
                id SERIAL PRIMARY KEY,
                tarefa_id BIGINT UNIQUE NOT NULL,
                processo_cnj VARCHAR(50),
                solicitante_id VARCHAR(50),
                status VARCHAR(20) DEFAULT 'PENDENTE', 
                data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                data_conclusao TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS coleta_legalone_cursor (
                type_id BIGINT NOT NULL,
                sub_type_id BIGINT NOT NULL,
                ultimo_task_id BIGINT,
                atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (type_id, sub_type_id)
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS twotask_notificacoes (
                id SERIAL PRIMARY KEY,
                dedupe_key VARCHAR(64) UNIQUE NOT NULL,
                numero_processo VARCHAR(50) NOT NULL,
                id_responsavel BIGINT,
                observacao TEXT NOT NULL,
                status VARCHAR(20) DEFAULT 'ENVIANDO',
                tentativas INTEGER DEFAULT 0,
                ultimo_erro TEXT,
                data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                data_envio TIMESTAMP,
                data_atualizacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_twotask_notificacoes_status
            ON twotask_notificacoes (status, data_atualizacao);
        """)
        
        conn.commit()
        logging.info("✅ Banco verificado (Schema Monitoramento + Data Limite OK).")
    except Exception as e:
        logging.error(f"❌ Erro init banco: {e}")
        conn.rollback()
    finally:
        if cur:
            cur.close()
        conn.close()

# --- FUNÇÕES DE FILA ---

def inserir_tarefa_na_fila(tarefa_id, cnj, solicitante_id):
    conn = get_connection()
    if not conn: return False
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tarefas_legal_one (tarefa_id, processo_cnj, solicitante_id, status)
            VALUES (%s, %s, %s, 'PENDENTE')
            ON CONFLICT (tarefa_id) DO NOTHING;
        """, (tarefa_id, cnj, solicitante_id))
        rows = cur.rowcount
        conn.commit()
        return True if rows > 0 else False
    except Exception as e:
        logging.error(f"Erro ao inserir tarefa {tarefa_id} na fila: {e}")
        return None
    finally:
        if cur:
            cur.close()
        conn.close()


def tarefa_ja_na_fila(tarefa_id):
    conn = get_connection()
    if not conn:
        return False
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM tarefas_legal_one WHERE tarefa_id = %s LIMIT 1",
            (tarefa_id,),
        )
        return cur.fetchone() is not None
    except Exception as e:
        logging.error(f"Erro ao verificar tarefa {tarefa_id} na fila: {e}")
        return False
    finally:
        if cur:
            cur.close()
        conn.close()

def buscar_tarefas_pendentes():
    conn = get_connection()
    if not conn: return []
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT tarefa_id, processo_cnj, solicitante_id 
            FROM tarefas_legal_one 
            WHERE status IN ('PENDENTE', 'ERRO')
            ORDER BY data_criacao ASC
        """)
        return [{"tarefa_id": r[0], "processo_cnj": r[1], "solicitante_id": r[2]} for r in cur.fetchall()]
    except Exception as e:
        logging.error(f"Erro ao buscar tarefas pendentes: {e}")
        return []
    finally:
        if cur:
            cur.close()
        conn.close()


def obter_cursor_coleta(type_id, sub_type_id):
    conn = get_connection()
    if not conn:
        return None

    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ultimo_task_id
            FROM coleta_legalone_cursor
            WHERE type_id = %s AND sub_type_id = %s
            """,
            (type_id, sub_type_id),
        )
        row = cur.fetchone()
        return row[0] if row else None
    except Exception as e:
        logging.error(
            "Erro ao obter cursor da coleta para type_id=%s sub_type_id=%s: %s",
            type_id,
            sub_type_id,
            e,
        )
        return None
    finally:
        if cur:
            cur.close()
        conn.close()


def atualizar_cursor_coleta(type_id, sub_type_id, ultimo_task_id):
    conn = get_connection()
    if not conn:
        return False

    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO coleta_legalone_cursor (type_id, sub_type_id, ultimo_task_id, atualizado_em)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (type_id, sub_type_id) DO UPDATE
            SET ultimo_task_id = EXCLUDED.ultimo_task_id,
                atualizado_em = CURRENT_TIMESTAMP
            """,
            (type_id, sub_type_id, ultimo_task_id),
        )
        conn.commit()
        return True
    except Exception as e:
        logging.error(
            "Erro ao atualizar cursor da coleta para type_id=%s sub_type_id=%s: %s",
            type_id,
            sub_type_id,
            e,
        )
        return False
    finally:
        if cur:
            cur.close()
        conn.close()

def marcar_tarefa_concluida(tarefa_id, status_final='CONCLUIDO'):
    conn = get_connection()
    if not conn: return
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE tarefas_legal_one 
            SET status = %s, data_conclusao = CURRENT_TIMESTAMP
            WHERE tarefa_id = %s
        """, (status_final, tarefa_id))
        conn.commit()
    except Exception as e:
        logging.error(f"Erro ao atualizar status da tarefa {tarefa_id}: {e}")
    finally:
        if cur:
            cur.close()
        conn.close()

# --- FUNÇÕES DE DADOS E MONITORAMENTO ---

def salvar_processo(cnj, npj):
    conn = get_connection()
    if not conn: return None
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO processos (cnj, npj, data_atualizacao)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (cnj) DO UPDATE 
            SET npj = EXCLUDED.npj, data_atualizacao = CURRENT_TIMESTAMP
            RETURNING id;
        """, (cnj, npj))
        pid = cur.fetchone()[0]
        conn.commit()
        return pid
    except Exception as e:
        logging.error(f"Erro ao salvar processo {cnj}/{npj}: {e}")
        return None
    finally:
        if cur:
            cur.close()
        conn.close()

def atualizar_status_monitoramento(processo_id, ativar=True):
    """
    Ativa ou desativa a flag de monitoramento do processo.
    """
    conn = get_connection()
    if not conn: return
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("UPDATE processos SET em_monitoramento = %s WHERE id = %s", (ativar, processo_id))
        conn.commit()
        status_str = "ATIVADO" if ativar else "DESATIVADO"
        logging.info(f"👀 Monitoramento {status_str} para processo ID {processo_id}.")
    except Exception as e:
        logging.error(f"❌ Erro atualizar monitoramento: {e}")
    finally:
        if cur:
            cur.close()
        conn.close()

def salvar_lista_subsidios(processo_id, lista_dados):
    conn = get_connection()
    if not conn: return
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, tipo, item, estado, COALESCE(data_limite, '')
            FROM subsidios
            WHERE processo_id = %s
            ORDER BY id ASC
            """,
            (processo_id,),
        )
        existentes = [
            {
                "id": row[0],
                "tipo": row[1] or "",
                "item": row[2] or "",
                "estado": row[3] or "",
                "data_limite": row[4] or "",
            }
            for row in cur.fetchall()
        ]

        usados = set()
        preservados = set()

        for dado in lista_dados:
            normalizado = _normalizar_subsidio(dado)
            existente = _buscar_subsidio_existente(existentes, normalizado, usados)

            if existente:
                cur.execute(
                    """
                    UPDATE subsidios
                    SET tipo = %s,
                        item = %s,
                        estado = %s,
                        data_limite = %s,
                        data_extracao = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (
                        normalizado["tipo"],
                        normalizado["item"],
                        normalizado["estado"],
                        normalizado["data_limite"],
                        existente["id"],
                    ),
                )
                usados.add(existente["id"])
                preservados.add(existente["id"])
                continue

            cur.execute(
                """
                INSERT INTO subsidios (processo_id, tipo, item, estado, data_limite)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    processo_id,
                    normalizado["tipo"],
                    normalizado["item"],
                    normalizado["estado"],
                    normalizado["data_limite"],
                ),
            )

        for existente in existentes:
            if existente["id"] in preservados:
                continue
            cur.execute("DELETE FROM subsidios WHERE id = %s", (existente["id"],))

        conn.commit()
    except Exception as e:
        logging.error(f"Erro salvar subsidios: {e}")
    finally:
        if cur:
            cur.close()
        conn.close()


def _normalizar_subsidio(dado):
    return {
        "tipo": (dado.get("tipo") or "").strip(),
        "item": (dado.get("item") or "").strip(),
        "estado": (dado.get("estado") or "").strip(),
        "data_limite": (dado.get("data_limite") or "").strip(),
    }


def _buscar_subsidio_existente(existentes, subsidio, usados):
    for existente in existentes:
        if existente["id"] in usados:
            continue
        if (
            existente["tipo"] == subsidio["tipo"]
            and existente["item"] == subsidio["item"]
            and existente["data_limite"] == subsidio["data_limite"]
        ):
            return existente

    for existente in existentes:
        if existente["id"] in usados:
            continue
        if (
            existente["tipo"] == subsidio["tipo"]
            and existente["item"] == subsidio["item"]
        ):
            return existente

    return None

def recuperar_subsidios_anteriores(processo_id):
    """Retorna lista de dicionários com os subsídios atuais do banco para comparação."""
    conn = get_connection()
    if not conn: return []
    lista = []
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT tipo, item, estado, data_limite FROM subsidios WHERE processo_id = %s", (processo_id,))
        rows = cur.fetchall()
        for r in rows:
            lista.append({
                "tipo": r[0], 
                "item": r[1], 
                "estado": r[2], 
                "data_limite": r[3]
            })
    except Exception as e:
        logging.error(f"Erro ao recuperar subsídios do processo {processo_id}: {e}")
    finally:
        if cur:
            cur.close()
        conn.close()
    return lista


def buscar_processos_em_monitoramento():
    conn = get_connection()
    if not conn:
        return []

    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, cnj, npj
            FROM processos
            WHERE em_monitoramento = TRUE
            ORDER BY data_atualizacao ASC, id ASC
            """
        )
        return [
            {"processo_id": row[0], "cnj": row[1], "npj": row[2]}
            for row in cur.fetchall()
        ]
    except Exception as e:
        logging.error(f"Erro ao buscar processos em monitoramento: {e}")
        return []
    finally:
        if cur:
            cur.close()
        conn.close()


def buscar_processos_para_reconciliacao(*, limit=10, lookback_hours=168):
    conn = get_connection()
    if not conn:
        return []

    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, cnj, npj
            FROM processos
            WHERE COALESCE(em_monitoramento, FALSE) = FALSE
              AND npj IS NOT NULL
              AND data_atualizacao >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 hour')
            ORDER BY data_atualizacao DESC, id DESC
            LIMIT %s
            """,
            (lookback_hours, limit),
        )
        return [
            {"processo_id": row[0], "cnj": row[1], "npj": row[2]}
            for row in cur.fetchall()
        ]
    except Exception as e:
        logging.error(f"Erro ao buscar processos para reconciliação: {e}")
        return []
    finally:
        if cur:
            cur.close()
        conn.close()


def buscar_todos_solicitantes_por_cnj(cnj):
    """
    Retorna uma LISTA com os IDs de todos os solicitantes distintos 
    que possuem tarefas registradas para este CNJ.
    """
    conn = get_connection()
    if not conn: return []
    lista_ids = []
    cur = None
    try:
        cur = conn.cursor()
        # Seleciona IDs distintos para não notificar a mesma pessoa 2x se ela tiver 2 tarefas
        cur.execute("""
            SELECT DISTINCT solicitante_id 
            FROM tarefas_legal_one 
            WHERE processo_cnj = %s AND solicitante_id IS NOT NULL
        """, (cnj,))
        rows = cur.fetchall()
        for r in rows:
            if r[0]: # Garante que não é None/Vazio
                lista_ids.append(r[0])
    except Exception as e:
        logging.error(f"Erro ao buscar solicitantes: {e}")
    finally: 
        if cur:
            cur.close()
        conn.close()
    
    return lista_ids


def registrar_notificacoes_twotask(lista_notificacoes):
    """
    Registra notificações antes do POST para impedir envio duplicado em ciclos
    paralelos ou reexecuções próximas do monitor.
    """
    if not lista_notificacoes:
        return []

    conn = get_connection()
    if not conn:
        logging.error("❌ Não foi possível registrar notificações TwoTask para deduplicação.")
        return []

    notificacoes_para_envio = []
    cur = None

    try:
        cur = conn.cursor()
        for notificacao in lista_notificacoes:
            normalizada = _normalizar_notificacao_twotask(notificacao)
            if not normalizada:
                continue

            dedupe_key = _gerar_dedupe_key_notificacao(normalizada)
            cur.execute(
                """
                INSERT INTO twotask_notificacoes (
                    dedupe_key,
                    numero_processo,
                    id_responsavel,
                    observacao,
                    status,
                    data_atualizacao
                )
                VALUES (%s, %s, %s, %s, 'ENVIANDO', CURRENT_TIMESTAMP)
                ON CONFLICT (dedupe_key) DO NOTHING
                RETURNING id
                """,
                (
                    dedupe_key,
                    normalizada["numero_processo"],
                    normalizada["id_responsavel"],
                    normalizada["observacao"],
                ),
            )
            row = cur.fetchone()
            if not row:
                logging.info(
                    "🧯 Notificação TwoTask duplicada bloqueada localmente: processo=%s responsável=%s",
                    normalizada["numero_processo"],
                    normalizada["id_responsavel"],
                )
                continue

            normalizada["_dedupe_key"] = dedupe_key
            normalizada["_notificacao_id"] = row[0]
            notificacoes_para_envio.append(normalizada)

        conn.commit()
        return notificacoes_para_envio
    except Exception as e:
        conn.rollback()
        logging.error("❌ Erro ao registrar notificações TwoTask: %s", e)
        return []
    finally:
        if cur:
            cur.close()
        conn.close()


def marcar_notificacoes_twotask_enviadas(dedupe_keys):
    _atualizar_status_notificacoes_twotask(
        dedupe_keys,
        status="ENVIADO",
        ultimo_erro=None,
        marcar_envio=True,
    )


def marcar_notificacoes_twotask_erro(dedupe_keys, erro):
    _atualizar_status_notificacoes_twotask(
        dedupe_keys,
        status="ERRO",
        ultimo_erro=(erro or "Falha desconhecida")[:1000],
        marcar_envio=False,
    )


def _atualizar_status_notificacoes_twotask(
    dedupe_keys,
    *,
    status,
    ultimo_erro,
    marcar_envio,
):
    if not dedupe_keys:
        return

    conn = get_connection()
    if not conn:
        logging.error("❌ Não foi possível atualizar status das notificações TwoTask.")
        return

    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE twotask_notificacoes
            SET status = %s,
                tentativas = COALESCE(tentativas, 0) + 1,
                ultimo_erro = %s,
                data_envio = CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE data_envio END,
                data_atualizacao = CURRENT_TIMESTAMP
            WHERE dedupe_key = ANY(%s)
            """,
            (status, ultimo_erro, marcar_envio, list(dedupe_keys)),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("❌ Erro ao atualizar notificações TwoTask: %s", e)
    finally:
        if cur:
            cur.close()
        conn.close()


def _normalizar_notificacao_twotask(notificacao):
    numero_processo = (notificacao.get("numero_processo") or "").strip()
    observacao = " ".join((notificacao.get("observacao") or "").split())

    if not numero_processo or not observacao:
        logging.warning("⚠️ Notificação TwoTask incompleta ignorada: %s", notificacao)
        return None

    try:
        id_responsavel = int(notificacao.get("id_responsavel") or 0)
    except (TypeError, ValueError):
        id_responsavel = 0

    return {
        "numero_processo": numero_processo,
        "id_responsavel": id_responsavel,
        "observacao": observacao,
    }


def _gerar_dedupe_key_notificacao(notificacao):
    base = "|".join(
        [
            notificacao["numero_processo"],
            str(notificacao["id_responsavel"]),
            notificacao["observacao"],
        ]
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()
