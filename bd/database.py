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
        
        # Tabelas Core
        cur.execute("""
            CREATE TABLE IF NOT EXISTS processos (
                id SERIAL PRIMARY KEY,
                cnj VARCHAR(50) UNIQUE NOT NULL,
                npj VARCHAR(50),
                data_atualizacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
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

        # Tabela de FILA (Tarefas Legal One)
        # Adicionamos a coluna STATUS para controlar o fluxo
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tarefas_legal_one (
                id SERIAL PRIMARY KEY,
                tarefa_id BIGINT UNIQUE NOT NULL,
                processo_cnj VARCHAR(50),
                finalizado_por_id VARCHAR(50),
                status VARCHAR(20) DEFAULT 'PENDENTE', 
                data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                data_conclusao TIMESTAMP
            );
        """)
        
        conn.commit()
        logging.info("✅ Banco verificado. Tabela de fila pronta.")
    except Exception as e:
        logging.error(f"❌ Erro init banco: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

# --- FUNÇÕES DE FILA (Produtor/Consumidor) ---

def inserir_tarefa_na_fila(tarefa_id, cnj, user_id):
    """
    Produtor: Insere uma nova tarefa na fila com status PENDENTE.
    Se já existir (mesmo ID), não faz nada.
    """
    conn = get_connection()
    if not conn: return False
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tarefas_legal_one (tarefa_id, processo_cnj, finalizado_por_id, status)
            VALUES (%s, %s, %s, 'PENDENTE')
            ON CONFLICT (tarefa_id) DO NOTHING;
        """, (tarefa_id, cnj, user_id))
        rows = cur.rowcount
        conn.commit()
        return rows > 0 # Retorna True se inseriu uma nova
    except Exception as e:
        logging.error(f"❌ Erro inserir fila: {e}")
        return False
    finally:
        cur.close()
        conn.close()

def buscar_tarefas_pendentes():
    """
    Consumidor: Busca todas as tarefas que ainda não foram processadas.
    """
    conn = get_connection()
    if not conn: return []
    try:
        cur = conn.cursor()
        # Pega PENDENTE ou ERRO (para tentar de novo)
        cur.execute("""
            SELECT tarefa_id, processo_cnj, finalizado_por_id 
            FROM tarefas_legal_one 
            WHERE status IN ('PENDENTE', 'ERRO')
            ORDER BY data_criacao ASC
        """)
        # Converte para lista de dicts para facilitar
        tarefas = [
            {"tarefa_id": row[0], "processo_cnj": row[1], "finalizado_por_id": row[2]} 
            for row in cur.fetchall()
        ]
        return tarefas
    except Exception as e:
        logging.error(f"❌ Erro buscar pendentes: {e}")
        return []
    finally:
        cur.close()
        conn.close()

def marcar_tarefa_concluida(tarefa_id, status_final='CONCLUIDO'):
    """
    Atualiza o status da tarefa após o robô terminar.
    Status pode ser: CONCLUIDO ou ERRO
    """
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
    except Exception as e:
        logging.error(f"❌ Erro atualizar status tarefa: {e}")
    finally:
        cur.close()
        conn.close()

# --- FUNÇÕES DE DADOS (Processos/Subsídios) ---

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