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
    """
    Inicializa as tabelas.
    Inclui migração automática: Se a tabela de tarefas estiver no formato antigo,
    ela é recriada para incluir o campo 'solicitante_id'.
    """
    conn = get_connection()
    if not conn: return
    try:
        cur = conn.cursor()
        
        # 1. Tabelas Core (Processos e Subsídios)
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

        # --- CHECAGEM DE MIGRAÇÃO DA TABELA DE TAREFAS ---
        # Verifica se a tabela existe
        cur.execute("SELECT to_regclass('public.tarefas_legal_one')")
        tabela_existe = cur.fetchone()[0]
        
        if tabela_existe:
            # Verifica se a coluna NOVA 'solicitante_id' existe
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='tarefas_legal_one' AND column_name='solicitante_id';
            """)
            # Se a coluna nova NÃO existe, derruba a tabela antiga para recriar
            if not cur.fetchone():
                logging.warning("⚠️ Detectado formato antigo da tabela 'tarefas_legal_one'. Recriando para atualização...")
                cur.execute("DROP TABLE tarefas_legal_one;")

        # 2. Criação da Tabela de Tarefas (Com o schema NOVO)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tarefas_legal_one (
                id SERIAL PRIMARY KEY,
                tarefa_id BIGINT UNIQUE NOT NULL,
                processo_cnj VARCHAR(50),
                solicitante_id VARCHAR(50),  -- Campo NOVO (antigo finalizado_por_id)
                status VARCHAR(20) DEFAULT 'PENDENTE', 
                data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                data_conclusao TIMESTAMP
            );
        """)
        
        conn.commit()
        logging.info("✅ Banco de dados verificado e atualizado.")
    except Exception as e:
        logging.error(f"❌ Erro init banco: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

# --- FUNÇÕES DE FILA (Produtor/Consumidor) ---

def inserir_tarefa_na_fila(tarefa_id, cnj, solicitante_id):
    """
    Produtor: Insere tarefa na fila.
    """
    conn = get_connection()
    if not conn: return False
    try:
        cur = conn.cursor()
        # Usa a coluna solicitante_id
        cur.execute("""
            INSERT INTO tarefas_legal_one (tarefa_id, processo_cnj, solicitante_id, status)
            VALUES (%s, %s, %s, 'PENDENTE')
            ON CONFLICT (tarefa_id) DO NOTHING;
        """, (tarefa_id, cnj, solicitante_id))
        rows = cur.rowcount
        conn.commit()
        return rows > 0 
    except Exception as e:
        logging.error(f"❌ Erro inserir fila: {e}")
        return False
    finally:
        cur.close()
        conn.close()

def buscar_tarefas_pendentes():
    """
    Consumidor: Busca tarefas pendentes.
    Retorna o dicionário com a chave 'solicitante_id' que o main.py espera.
    """
    conn = get_connection()
    if not conn: return []
    try:
        cur = conn.cursor()
        # Seleciona a coluna solicitante_id
        cur.execute("""
            SELECT tarefa_id, processo_cnj, solicitante_id 
            FROM tarefas_legal_one 
            WHERE status IN ('PENDENTE', 'ERRO')
            ORDER BY data_criacao ASC
        """)
        # Monta o dicionário com a chave correta
        tarefas = [
            {"tarefa_id": row[0], "processo_cnj": row[1], "solicitante_id": row[2]} 
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
        logging.error(f"❌ Erro atualizar status: {e}")
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