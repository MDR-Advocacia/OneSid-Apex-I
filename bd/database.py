import os
import logging
import psycopg2
from dotenv import load_dotenv

# Carrega variáveis de ambiente
load_dotenv()

# Configurações (padrão ou do .env)
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "onesid_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")

def get_connection():
    """Cria uma conexão com o PostgreSQL"""
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            port=DB_PORT
        )
        return conn
    except Exception as e:
        logging.error(f"❌ Erro ao conectar no PostgreSQL: {e}")
        return None

def inicializar_banco():
    """Cria as tabelas necessárias se não existirem"""
    conn = get_connection()
    if not conn:
        return

    try:
        cur = conn.cursor()
        
        # 1. Tabela de Processos (Pai)
        # Guarda o CNJ (ex: 7005938...) e o NPJ vinculado
        cur.execute("""
            CREATE TABLE IF NOT EXISTS processos (
                id SERIAL PRIMARY KEY,
                cnj VARCHAR(50) UNIQUE NOT NULL,
                npj VARCHAR(50),
                data_atualizacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 2. Tabela de Subsídios (Filho)
        # Guarda as linhas extraídas, vinculadas ao ID do processo
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

        conn.commit()
        logging.info("✅ Banco de dados PostgreSQL (Tabelas) verificado com sucesso.")
    except Exception as e:
        logging.error(f"❌ Erro ao inicializar tabelas: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

def salvar_processo(cnj, npj):
    """
    Insere o processo ou atualiza o NPJ se já existir.
    Retorna o ID do processo no banco (necessário para salvar os subsídios).
    """
    conn = get_connection()
    if not conn:
        return None

    processo_id = None
    try:
        cur = conn.cursor()
        
        # Upsert: Tenta inserir. Se o CNJ já existir (ON CONFLICT), atualiza o NPJ e a data.
        cur.execute("""
            INSERT INTO processos (cnj, npj, data_atualizacao)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (cnj) DO UPDATE 
            SET npj = EXCLUDED.npj, data_atualizacao = CURRENT_TIMESTAMP
            RETURNING id;
        """, (cnj, npj))
        
        processo_id = cur.fetchone()[0]
        conn.commit()
    except Exception as e:
        logging.error(f"❌ Erro ao salvar processo {cnj}: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()
    
    return processo_id

def salvar_lista_subsidios(processo_id, lista_dados):
    """
    Recebe o ID do processo e a lista de dicionários coletados.
    Limpa os subsídios antigos desse processo e insere os novos (Snapshot atual).
    """
    conn = get_connection()
    if not conn:
        return

    try:
        cur = conn.cursor()
        
        # Estratégia: Limpar anteriores para manter o retrato fiel da última extração
        cur.execute("DELETE FROM subsidios WHERE processo_id = %s", (processo_id,))
        
        query_insert = """
            INSERT INTO subsidios (processo_id, tipo, item, estado)
            VALUES (%s, %s, %s, %s)
        """
        
        count = 0
        for dado in lista_dados:
            cur.execute(query_insert, (processo_id, dado['tipo'], dado['item'], dado['estado']))
            count += 1
            
        conn.commit()
        logging.info(f"💾 Salvos {count} subsídios no PostgreSQL para o processo ID {processo_id}.")
    except Exception as e:
        logging.error(f"❌ Erro ao salvar subsídios no banco: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()