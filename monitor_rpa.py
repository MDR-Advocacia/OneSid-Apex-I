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

load_dotenv("RPA/.env")

# --- IMPORTS ---
try:
    import bd.database as database
    
    # Adiciona utils ao path para importar a api
    sys.path.append(os.path.abspath(os.path.dirname(__file__)))
    import utils.twotask_api as twotask
    
    # Importa Core do RPA
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'RPA')))
    import main as rpa_core 
except ImportError as e:
    logging.error(f"Erro de importação: {e}")
    sys.exit(1)

def verificar_processos_em_monitoramento():
    logging.info("🔍 Buscando processos marcados para monitoramento...")
    
    conn = database.get_connection()
    if not conn: return
    
    processos_monitorados = []
    try:
        cur = conn.cursor()
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

    driver = rpa_core.uc.Chrome(options=rpa_core.uc.ChromeOptions(), use_subprocess=True, version_main=142)
    
    lista_para_notificar = []

    try:
        if not rpa_core.fazer_login(driver):
            logging.error("❌ Falha no login do Monitor. Abortando.")
            return

        for proc in processos_monitorados:
            pid, cnj, npj = proc
            logging.info(f"⚙️ Verificando Processo: {cnj} (NPJ: {npj})")
            
            try:
                # 1. Recupera o estado ANTERIOR do banco (Snapshot A)
                subsidios_antigos = database.recuperar_subsidios_anteriores(pid)

                # 2. Acessa o site
                if rpa_core.acessar_processo_consulta_rapida(driver, cnj):
                    url_edicao = f"https://juridico.bb.com.br/paj/app/paj-cadastro/spas/processo/consulta/processo-consulta.app.html#/editar/{npj}/0/18"
                    driver.get(url_edicao)
                    time.sleep(10)

                    # 3. Coleta o estado NOVO (Snapshot B)
                    dados_novos = rpa_core.coletar_lista_subsidios(driver)
                    
                    if dados_novos:
                        # --- LÓGICA DE COMPARAÇÃO E NOTIFICAÇÃO ---
                        itens_alterados = []
                        
                        # Vamos varrer o que existia antes
                        for antigo in subsidios_antigos:
                            # Só nos interessa o que estava "SOLICITADO" (case insensitive)
                            if antigo['estado'].upper() == 'SOLICITADO':
                                # Busca esse mesmo item na lista nova (pelo Tipo e Item)
                                correspondente_novo = next(
                                    (n for n in dados_novos if n['item'] == antigo['item'] and n['tipo'] == antigo['tipo']), 
                                    None
                                )
                                
                                # Se achou e o estado mudou (não é mais SOLICITADO), então houve andamento!
                                if correspondente_novo and correspondente_novo['estado'].upper() != 'SOLICITADO':
                                    msg = f"{correspondente_novo['tipo']} {correspondente_novo['item']} {correspondente_novo['estado']}"
                                    itens_alterados.append(msg)

                        # Se detectamos alterações relevantes, prepara para envio
                        if itens_alterados:
                            id_resp = database.buscar_solicitante_por_cnj(cnj)
                            # Se não achar o ID, usa um padrão ou loga aviso. O JSON pede int, cuidado se for None.
                            id_resp_final = int(id_resp) if id_resp and str(id_resp).isdigit() else 0
                            
                            observacao_str = " | ".join(itens_alterados) # Junta tudo numa string bonita
                            
                            logging.info(f"🔔 Detectada alteração em itens solicitados! Resp: {id_resp_final}")
                            
                            lista_para_notificar.append({
                                "numero_processo": cnj,
                                "id_responsavel": id_resp_final,
                                "observacao": observacao_str
                            })

                        # 4. Atualiza o banco com o snapshot novo
                        database.salvar_lista_subsidios(pid, dados_novos)
                        
                        # 5. Verifica se desliga o monitoramento
                        tem_pendencia = any(d['estado'].upper() == 'SOLICITADO' for d in dados_novos)
                        if not tem_pendencia:
                            logging.info(f"🎉 Processo limpo. Desligando monitoramento.")
                            database.atualizar_status_monitoramento(pid, False)
                    
                    else:
                        logging.warning("⚠️ Tabela vazia ou erro de leitura.")
                else:
                    logging.error("❌ Falha ao acessar processo.")

            except Exception as e:
                logging.error(f"Erro ao processar {cnj}: {e}")
            
            time.sleep(2)

        # --- ENVIO EM LOTE PARA API ---
        if lista_para_notificar:
            twotask.post_to_api(lista_para_notificar)

    finally:
        driver.quit()
        logging.info("🏁 Ciclo de monitoramento finalizado.")

if __name__ == "__main__":
    print("\n--- 🕵️ INICIANDO ROBÔ DE MONITORAMENTO COM NOTIFICAÇÃO ---")
    verificar_processos_em_monitoramento()