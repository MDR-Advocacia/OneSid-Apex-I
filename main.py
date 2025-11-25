import time
import os
import re
import logging
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv

from bd import database
import apexFluxoLegalOne

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
load_dotenv()

# --- (Funções de Navegação e Extração MANTIDAS IGUAIS) ---
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
    logging.info(f"🚀 [NAVEGAÇÃO] Indo para processo: {numero_limpo}")
    driver.get(url)
    logging.info("⏳ Aguardando 10s fixos...")
    time.sleep(10)
    return True

def extrair_e_acessar_npj(driver):
    logging.info("🔍 Buscando NPJ...")
    elemento_npj = None
    seletores = ["//tr[contains(@ng-repeat, 'resultadoProcessos.processos')]//td[1]//span[contains(@class, 'ng-binding')]", "//td[contains(@style, 'width: 15%')]//span[contains(@class, 'ng-binding')]", "//*[text()[contains(.,'/') and contains(.,'-')]]"]
    for _ in range(3):
        for xpath in seletores:
            elemento_npj = buscar_elemento_em_todos_contextos(driver, By.XPATH, xpath)
            if elemento_npj: break
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
        driver.get(f"https://juridico.bb.com.br/paj/app/paj-cadastro/spas/processo/consulta/processo-consulta.app.html#/editar/{npj}/0/18")
        time.sleep(10)
        return npj
    except: return None

def coletar_lista_subsidios(driver):
    logging.info("📊 Coletando subsídios...")
    lista = []
    xpath = "//tr[contains(@ng-repeat, 'subsidio in vm.resultado.lista')]"
    if not buscar_elemento_em_todos_contextos(driver, By.XPATH, xpath): return []
    while True:
        try:
            for linha in driver.find_elements(By.XPATH, xpath):
                try:
                    lista.append({
                        "tipo": linha.find_element(By.XPATH, "./td[4]").text.strip(),
                        "item": linha.find_element(By.XPATH, "./td[5]").text.strip(),
                        "estado": linha.find_element(By.XPATH, "./td[6]").text.strip()
                    })
                except: pass
            try:
                btn = driver.find_element(By.XPATH, "//a[contains(@ng-click, 'vm.rechamarPesquisaProximo()')]")
                if "disabled" in btn.get_attribute("class") or btn.get_attribute("disabled"): break
                driver.execute_script("arguments[0].scrollIntoView(true);", btn); time.sleep(1); btn.click(); time.sleep(5)
            except: break
        except: break
    driver.switch_to.default_content()
    return lista

# --- ORQUESTRADOR ---

def executar_rpa():
    print("🤖 INICIANDO ORQUESTRADOR PRODUTOR-CONSUMIDOR")
    database.inicializar_banco()

    # 1. PRODUTOR: Abastece o banco
    apexFluxoLegalOne.buscar_e_abastecer_fila()

    # 2. CONSUMIDOR: Busca o que tem para fazer
    fila_pendente = database.buscar_tarefas_pendentes()
    
    if not fila_pendente:
        logging.info("✅ Tudo limpo! Nenhuma tarefa pendente no banco.")
        return

    logging.info(f"📋 Encontradas {len(fila_pendente)} tarefas pendentes no banco. Iniciando RPA...")

    usuario_env = os.getenv("BB_USUARIO")
    senha_env = os.getenv("BB_SENHA")
    if not usuario_env or not senha_env: return

    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    driver = uc.Chrome(options=options, use_subprocess=True, version_main=142)

    try:
        # LOGIN ÚNICO
        driver.get('https://loginweb.bb.com.br/sso/XUI/?realm=/paj&goto=https://juridico.bb.com.br/wfj#login')
        wait = WebDriverWait(driver, 60)
        wait.until(EC.visibility_of_element_located((By.ID, "idToken1"))).send_keys(usuario_env)
        time.sleep(0.5); driver.find_element(By.ID, "loginButton_0").click()
        wait.until(EC.visibility_of_element_located((By.ID, "idToken3"))).send_keys(senha_env)
        time.sleep(0.5); wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input#loginButton_0[name='callback_4']"))).click()
        logging.info("Login OK. Aguardando 15s...")
        time.sleep(15)

        for tarefa in fila_pendente:
            cnj = tarefa['processo_cnj']
            t_id = tarefa['tarefa_id']
            
            logging.info(f"⚙️ Processando CNJ: {cnj} (ID Tarefa: {t_id})")
            
            try:
                if acessar_processo_consulta_rapida(driver, cnj):
                    npj = extrair_e_acessar_npj(driver)
                    if npj:
                        pid = database.salvar_processo(cnj, npj)
                        if pid:
                            dados = coletar_lista_subsidios(driver)
                            if dados: database.salvar_lista_subsidios(pid, dados)
                            
                            # SUCESSO: Marca no banco como CONCLUIDO
                            database.marcar_tarefa_concluida(t_id, 'CONCLUIDO')
                            logging.info(f"✅ Tarefa {t_id} concluída e salva.")
                        else:
                             database.marcar_tarefa_concluida(t_id, 'ERRO')
                    else:
                        logging.error("NPJ não achado.")
                        database.marcar_tarefa_concluida(t_id, 'ERRO')
                else:
                    database.marcar_tarefa_concluida(t_id, 'ERRO')

            except Exception as e:
                logging.error(f"Erro no loop: {e}")
                database.marcar_tarefa_concluida(t_id, 'ERRO')
            
            time.sleep(3)

    finally:
        driver.quit()

if __name__ == "__main__":
    executar_rpa()