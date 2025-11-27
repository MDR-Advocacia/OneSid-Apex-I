import os
import logging
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "onesid_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")

def get_connection():
    try:
        return psycopg2.connect(
            host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS, port=DB_PORT
        )
    except Exception as e:
        logging.error(f"❌ Erro conexão BD: {e}")
        return None

def inicializar_banco():
    conn = get_connection()
    if not conn: return
    try:
        cur = conn.cursor()
        
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
        # Adiciona a coluna se ela não existir
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
                data_extracao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

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
        
        conn.commit()
        logging.info("✅ Banco verificado (Schema Monitoramento OK).")
    except Exception as e:
        logging.error(f"❌ Erro init banco: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

# --- FUNÇÕES DE FILA ---

def inserir_tarefa_na_fila(tarefa_id, cnj, solicitante_id):
    conn = get_connection()
    if not conn: return False
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tarefas_legal_one (tarefa_id, processo_cnj, solicitante_id, status)
            VALUES (%s, %s, %s, 'PENDENTE')
            ON CONFLICT (tarefa_id) DO NOTHING;
        """, (tarefa_id, cnj, solicitante_id))
        rows = cur.rowcount
        conn.commit()
        return rows > 0 
    except: return False
    finally: cur.close(); conn.close()

def buscar_tarefas_pendentes():
    conn = get_connection()
    if not conn: return []
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT tarefa_id, processo_cnj, solicitante_id 
            FROM tarefas_legal_one 
            WHERE status IN ('PENDENTE', 'ERRO')
            ORDER BY data_criacao ASC
        """)
        return [{"tarefa_id": r[0], "processo_cnj": r[1], "solicitante_id": r[2]} for r in cur.fetchall()]
    except: return []
    finally: cur.close(); conn.close()

def marcar_tarefa_concluida(tarefa_id, status_final='CONCLUIDO'):
    conn = get_connection()
    if not conn: return
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE tarefas_legal_one 
            SET status = %s, data_conclusao = CURRENT_TIMESTAMP
            WHERE tarefa_id = %s
        """, (status_final, tarefa_id))
        conn.commit()
    except: pass
    finally: cur.close(); conn.close()

# --- FUNÇÕES DE DADOS E MONITORAMENTO ---

def salvar_processo(cnj, npj):
    conn = get_connection()
    if not conn: return None
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
    except: return None
    finally: cur.close(); conn.close()

def atualizar_status_monitoramento(processo_id, ativar=True):
    """
    Ativa ou desativa a flag de monitoramento do processo.
    """
    conn = get_connection()
    if not conn: return
    try:
        cur = conn.cursor()
        cur.execute("UPDATE processos SET em_monitoramento = %s WHERE id = %s", (ativar, processo_id))
        conn.commit()
        status_str = "ATIVADO" if ativar else "DESATIVADO"
        logging.info(f"👀 Monitoramento {status_str} para processo ID {processo_id}.")
    except Exception as e:
        logging.error(f"❌ Erro atualizar monitoramento: {e}")
    finally:
        cur.close()
        conn.close()

def salvar_lista_subsidios(processo_id, lista_dados):
    conn = get_connection()
    if not conn: return
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM subsidios WHERE processo_id = %s", (processo_id,))
        for d in lista_dados:
            cur.execute(
                "INSERT INTO subsidios (processo_id, tipo, item, estado) VALUES (%s, %s, %s, %s)",
                (processo_id, d['tipo'], d['item'], d['estado'])
            )
        conn.commit()
    except: pass
    finally: cur.close(); conn.close()

def recuperar_subsidios_anteriores(processo_id):
    """Retorna lista de dicionários com os subsídios atuais do banco para comparação."""
    conn = get_connection()
    if not conn: return []
    lista = []
    try:
        cur = conn.cursor()
        cur.execute("SELECT tipo, item, estado FROM subsidios WHERE processo_id = %s", (processo_id,))
        rows = cur.fetchall()
        for r in rows:
            lista.append({"tipo": r[0], "item": r[1], "estado": r[2]})
    except: pass
    finally: cur.close(); conn.close()
    return lista

def buscar_solicitante_por_cnj(cnj):
    """Busca o ID do solicitante na tabela de tarefas original."""
    conn = get_connection()
    if not conn: return None
    solicitante = None
    try:
        cur = conn.cursor()
        # Pega o solicitante da tarefa mais recente para este CNJ
        cur.execute("""
            SELECT solicitante_id FROM tarefas_legal_one 
            WHERE processo_cnj = %s 
            ORDER BY data_criacao DESC LIMIT 1
        """, (cnj,))
        res = cur.fetchone()
        if res: solicitante = res[0]
    except: pass
    finally: cur.close(); conn.close()
    return solicitante