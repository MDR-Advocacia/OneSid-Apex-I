import time
import logging
import sys
import os
from dotenv import load_dotenv

# Configura logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [MONITOR] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Carrega variáveis
load_dotenv("RPA/.env")

# Importa módulos do projeto
try:
    import bd.database as database
    # Importamos as funções do RPA para reutilizar (navegação, login, extração)
    # Precisamos adicionar o caminho do RPA ao path
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'RPA')))
    import main as rpa_core # Importa o main.py como módulo
except ImportError as e:
    logging.error(f"Erro de importação: {e}")
    sys.exit(1)

def verificar_processos_em_monitoramento():
    logging.info("🔍 Buscando processos marcados para monitoramento no banco...")
    
    conn = database.get_connection()
    if not conn: return
    
    processos_monitorados = []
    try:
        cur = conn.cursor()
        # Busca processos onde a flag 'em_monitoramento' é TRUE
        cur.execute("SELECT id, cnj, npj FROM processos WHERE em_monitoramento = TRUE")
        processos_monitorados = cur.fetchall()
    except Exception as e:
        logging.error(f"Erro ao buscar processos: {e}")
    finally:
        cur.close()
        conn.close()

    if not processos_monitorados:
        logging.info("✅ Nenhum processo em monitoramento no momento.")
        return

    logging.info(f"📋 Encontrados {len(processos_monitorados)} processos para verificar.")

    # Inicializa o driver UMA VEZ para processar a lista
    driver = rpa_core.uc.Chrome(options=rpa_core.uc.ChromeOptions(), use_subprocess=True, version_main=142)
    
    try:
        # Faz login
        if not rpa_core.fazer_login(driver):
            logging.error("❌ Falha no login do Monitor. Abortando.")
            return

        for proc in processos_monitorados:
            pid, cnj, npj = proc
            logging.info(f"⚙️ Verificando Processo: {cnj} (NPJ: {npj})")
            
            try:
                # 1. Acessa o processo (usando função do main.py)
                if rpa_core.acessar_processo_consulta_rapida(driver, cnj):
                    
                    # 2. Garante que estamos na edição (às vezes o link direto via NPJ é mais seguro se já temos ele)
                    # Como já temos o NPJ do banco, podemos ir direto para a URL de edição!
                    # Isso economiza o passo de "extrair_e_acessar_npj"
                    url_edicao = f"https://juridico.bb.com.br/paj/app/paj-cadastro/spas/processo/consulta/processo-consulta.app.html#/editar/{npj}/0/18"
                    driver.get(url_edicao)
                    time.sleep(10)

                    # 3. Coleta os dados atuais
                    dados_novos = rpa_core.coletar_lista_subsidios(driver)
                    
                    if dados_novos:
                        # 4. Atualiza o banco (Snapshot)
                        database.salvar_lista_subsidios(pid, dados_novos)
                        logging.info(f"✅ Dados atualizados.")

                        # 5. Verifica se ainda precisa monitorar
                        # Se NÃO tiver mais nenhum 'SOLICITADO', desliga o monitoramento
                        tem_pendencia = any(d['estado'].upper() == 'SOLICITADO' for d in dados_novos)
                        
                        if not tem_pendencia:
                            logging.info(f"🎉 Processo {cnj} não tem mais itens 'Solicitado'. Desligando monitoramento.")
                            database.atualizar_status_monitoramento(pid, False)
                        else:
                            logging.info(f"👀 Processo ainda tem itens 'Solicitado'. Continua monitorado.")
                    
                    else:
                        logging.warning("⚠️ Tabela vazia ou erro de leitura.")

                else:
                    logging.error("❌ Falha ao acessar processo.")

            except Exception as e:
                logging.error(f"Erro ao processar {cnj}: {e}")
            
            time.sleep(2) # Respiro

    finally:
        driver.quit()
        logging.info("🏁 Ciclo de monitoramento finalizado.")

if __name__ == "__main__":
    print("\n--- 🕵️ INICIANDO ROBÔ DE MONITORAMENTO ---")
    # Aqui você pode colocar um loop infinito com schedule se quiser rodar a cada X horas
    # Por enquanto, roda uma vez e para.
    verificar_processos_em_monitoramento()