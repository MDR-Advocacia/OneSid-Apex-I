import time
import os
import re
import logging
import sys
import hashlib
import schedule 

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv

from bd import database
# import apexFluxoLegalOne  <-- REMOVIDO DO FLUXO, agora roda separado

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [PORTAL RPA] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

load_dotenv()

# --- FUNÇÃO DE LOGIN REUTILIZÁVEL ---
def fazer_login(driver):
    usuario = os.getenv("BB_USUARIO")
    senha = os.getenv("BB_SENHA")
    if not usuario or not senha: return False

    logging.info("🔐 Iniciando Login...")
    try:
        if "sso/XUI" not in driver.current_url and "login" not in driver.current_url:
            driver.get('https://loginweb.bb.com.br/sso/XUI/?realm=/paj&goto=https://juridico.bb.com.br/wfj#login')
        
        wait = WebDriverWait(driver, 60)
        wait.until(EC.visibility_of_element_located((By.ID, "idToken1"))).clear()
        driver.find_element(By.ID, "idToken1").send_keys(usuario)
        time.sleep(0.5); driver.find_element(By.ID, "loginButton_0").click()
        wait.until(EC.visibility_of_element_located((By.ID, "idToken3"))).send_keys(senha)
        time.sleep(0.5); wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input#loginButton_0[name='callback_4']"))).click()
        
        logging.info("✅ Login enviado. Aguardando 15s...")
        time.sleep(15)
        return True
    except: return False

# --- FUNÇÕES AUXILIARES ---
def limpar_apenas_digitos(texto):
    if not texto: return ""
    return re.sub(r'\D', '', str(texto))

def buscar_elemento_em_todos_contextos(driver, by, value):
    try:
        driver.switch_to.default_content()
        return driver.find_element(by, value)
    except: pass
    driver.switch_to.default_content()
    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    for iframe in iframes:
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(iframe)
            return driver.find_element(by, value)
        except: continue
    driver.switch_to.default_content()
    return None

def acessar_processo_consulta_rapida(driver, numero_processo):
    numero_limpo = limpar_apenas_digitos(numero_processo)
    if not numero_limpo: return False
    
    url = f"https://juridico.bb.com.br/paj/juridico/v2?app=processoConsultaRapidoTomboApp&numeroTombo={numero_limpo}"
    logging.info(f"🚀 [NAVEGAÇÃO] Indo para: {numero_limpo}")
    
    try:
        driver.get(url)
        time.sleep(5) 
        if "sso/XUI" in driver.current_url or len(driver.find_elements(By.ID, "idToken1")) > 0:
            logging.warning("⚠️ SESSÃO EXPIRADA! Re-logando...")
            if fazer_login(driver):
                driver.get(url); time.sleep(5)
            else: return False
        logging.info("⏳ Aguardando 10s...")
        time.sleep(10)
        return True
    except: return False

def extrair_e_acessar_npj(driver):
    logging.info("🔍 Buscando NPJ...")
    elemento_npj = None
    
    seletores = [
        "//tr[contains(@ng-repeat, 'resultadoProcessos.processos') and not(contains(., '#Confidencial'))]//td[1]//span[contains(@class, 'ng-binding')]",
        "//tr[contains(@ng-repeat, 'resultadoProcessos.processos')]//td[1]//span[contains(@class, 'ng-binding')]",
        "//td[contains(@style, 'width: 15%')]//span[contains(@class, 'ng-binding')]",
        "//*[text()[contains(.,'/') and contains(.,'-')]]"
    ]

    for _ in range(3):
        for xpath in seletores:
            elemento_npj = buscar_elemento_em_todos_contextos(driver, By.XPATH, xpath)
            if elemento_npj:
                if "#Confidencial" not in elemento_npj.text: break
                else: elemento_npj = None
        if elemento_npj: break
        time.sleep(3)

    if not elemento_npj: return None

    try:
        texto = elemento_npj.text.strip()
        driver.switch_to.default_content()
        if not re.search(r"\d{4}/\d+-\d+", texto): return None
        numeros = limpar_apenas_digitos(texto)
        npj = numeros[:-3] if numeros.endswith('000') else numeros
        if len(npj) < 5: return None
        
        logging.info(f"✅ NPJ: {npj}")
        driver.get(f"https://juridico.bb.com.br/paj/app/paj-cadastro/spas/processo/consulta/processo-consulta.app.html#/editar/{npj}/0/18")
        time.sleep(10)
        return npj
    except: return None

def coletar_lista_subsidios(driver):
    logging.info("📊 Coletando subsídios...")
    lista = []
    xpath = "//tr[contains(@ng-repeat, 'subsidio in vm.resultado.lista')]"
    
    if not buscar_elemento_em_todos_contextos(driver, By.XPATH, xpath):
        logging.warning("⚠️ Tabela vazia ou não encontrada.")
        return None 

    ultimo_hash = ""
    pag = 1
    MAX_PAG = 50

    while pag <= MAX_PAG:
        try:
            elementos = driver.find_elements(By.XPATH, xpath)
            if not elementos and not lista: return None 

            dados_pag = []
            str_hash = ""

            for linha in elementos:
                try:
                    # Tenta capturar data da coluna 3 usando Regex
                    raw_data = linha.find_element(By.XPATH, "./td[3]").text.strip()
                    match_data = re.search(r'(\d{2}/\d{2}/\d{4})', raw_data)
                    data_limite = match_data.group(1) if match_data else ""

                    t = linha.find_element(By.XPATH, "./td[4]").text.strip()
                    i = linha.find_element(By.XPATH, "./td[5]").text.strip()
                    e = linha.find_element(By.XPATH, "./td[6]").text.strip()
                    
                    dados_pag.append({
                        "tipo": t, 
                        "item": i, 
                        "estado": e, 
                        "data_limite": data_limite
                    })
                    str_hash += f"{t}{i}{e}{data_limite}"
                except: pass
            
            h_atual = hashlib.md5(str_hash.encode()).hexdigest()
            if h_atual == ultimo_hash:
                logging.info("⏹️ Página repetida. Fim.")
                break
            ultimo_hash = h_atual
            
            lista.extend(dados_pag)
            logging.info(f"   -> Pág {pag} OK. Total: {len(lista)}")

            xpath_prox = "//a[contains(@ng-click, 'vm.rechamarPesquisaProximo()')]"
            try:
                btn = driver.find_element(By.XPATH, xpath_prox)
                classes = btn.get_attribute("class") or ""
                style = btn.get_attribute("style") or ""
                if "disabled" in classes or btn.get_attribute("disabled") or "ng-hide" in classes or "none" in style:
                    break
                
                driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                time.sleep(1)
                btn.click()
                logging.info("⏳ Próxima página...")
                time.sleep(5)
                pag += 1
            except: break
        except: break
    
    driver.switch_to.default_content()
    return lista

# --- ORQUESTRADOR DE PROCESSAMENTO ---

def job_processar_portal():
    logging.info("🏁 Iniciando ciclo de processamento no Portal...")
    database.inicializar_banco()

    # REMOVIDO: apexFluxoLegalOne.buscar_e_abastecer_fila()
    # Agora só olha o que já está no banco:
    fila_pendente = database.buscar_tarefas_pendentes()
    
    if not fila_pendente:
        logging.info("✅ Nenhuma tarefa pendente no banco.")
        return

    logging.info(f"📋 Processando {len(fila_pendente)} tarefas da fila...")

    usuario_env = os.getenv("BB_USUARIO")
    senha_env = os.getenv("BB_SENHA")
    if not usuario_env or not senha_env: return

    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    driver = uc.Chrome(options=options, use_subprocess=True, version_main=142)

    try:
        if not fazer_login(driver):
            logging.error("❌ Falha login.")
            return

        for tarefa in fila_pendente:
            cnj = tarefa['processo_cnj']
            t_id = tarefa['tarefa_id']
            
            logging.info(f"\n⚙️ Processando CNJ: {cnj}")
            
            try:
                if acessar_processo_consulta_rapida(driver, cnj):
                    npj = extrair_e_acessar_npj(driver)
                    if npj:
                        pid = database.salvar_processo(cnj, npj)
                        if pid:
                            dados = coletar_lista_subsidios(driver)
                            if dados is None:
                                database.marcar_tarefa_concluida(t_id, 'ERRO')
                            else:
                                if dados: 
                                    database.salvar_lista_subsidios(pid, dados)
                                    logging.info(f"✅ {len(dados)} subsídios salvos.")
                                    
                                    # Verifica se precisa ativar monitoramento
                                    tem_solicitado = any(d['estado'].upper() == 'SOLICITADO' for d in dados)
                                    
                                    if tem_solicitado:
                                        logging.info(f"🚨 ATENÇÃO: Processo {cnj} tem itens 'Solicitado'. Ativando monitoramento!")
                                        database.atualizar_status_monitoramento(pid, True)

                                database.marcar_tarefa_concluida(t_id, 'CONCLUIDO')
                        else:
                             database.marcar_tarefa_concluida(t_id, 'ERRO')
                    else:
                        database.marcar_tarefa_concluida(t_id, 'ERRO')
                else:
                    database.marcar_tarefa_concluida(t_id, 'ERRO')

            except Exception as e:
                logging.error(f"Erro loop: {e}")
                database.marcar_tarefa_concluida(t_id, 'ERRO')
            
            time.sleep(3)

    finally:
        driver.quit()
        logging.info("💤 Ciclo de processamento finalizado.")

if __name__ == "__main__":
    
    print("\n--- 🤖 ROBÔ PROCESSADOR PORTAL (5 em 5 min) ---")
    
    # Executa a primeira vez
    job_processar_portal()
    
    # Agenda para rodar a cada 5 minutos
    schedule.every(5).minutes.do(job_processar_portal)
    
    while True:
        schedule.run_pending()
        time.sleep(1)