import time
import os
import re
import logging
import sys
import hashlib  # Import para gerar hash dos dados

# Adiciona o diretório raiz ao path para importar 'bd' corretamente
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv

# Módulos do Projeto
from bd import database
import apexFluxoLegalOne

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

load_dotenv()

# --- FUNÇÃO DE LOGIN REUTILIZÁVEL (BLINDAGEM) ---

def fazer_login(driver):
    """
    Realiza o login no portal. Retorna True se sucesso.
    Verifica se já está logado ou se precisa preencher credenciais.
    """
    usuario = os.getenv("BB_USUARIO")
    senha = os.getenv("BB_SENHA")
    
    if not usuario or not senha:
        logging.error("❌ Credenciais ausentes no .env")
        return False

    logging.info("🔐 Iniciando procedimento de Login...")
    try:
        if "sso/XUI" not in driver.current_url and "login" not in driver.current_url:
            driver.get('https://loginweb.bb.com.br/sso/XUI/?realm=/paj&goto=https://juridico.bb.com.br/wfj#login')
        
        wait = WebDriverWait(driver, 60)
        
        wait.until(EC.visibility_of_element_located((By.ID, "idToken1"))).clear()
        driver.find_element(By.ID, "idToken1").send_keys(usuario)
        time.sleep(0.5)
        driver.find_element(By.ID, "loginButton_0").click()
        
        wait.until(EC.visibility_of_element_located((By.ID, "idToken3"))).send_keys(senha)
        time.sleep(0.5)
        
        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input#loginButton_0[name='callback_4']"))).click()
        
        logging.info("✅ Login enviado. Aguardando 15s para estabilização...")
        time.sleep(15)
        return True
    except Exception as e:
        logging.error(f"❌ Falha ao realizar login: {e}")
        try:
            driver.save_screenshot("erro_login_recuperacao.png")
        except:
            pass
        return False

# --- FUNÇÕES DE NAVEGAÇÃO E EXTRAÇÃO ---

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
    
    try:
        driver.get(url)
        time.sleep(5) 
        
        if "sso/XUI" in driver.current_url or len(driver.find_elements(By.ID, "idToken1")) > 0:
            logging.warning("⚠️ SESSÃO EXPIRADA DETECTADA! Iniciando re-login automático...")
            if fazer_login(driver):
                logging.info("🔄 Sessão recuperada. Retentando acesso...")
                driver.get(url) 
                time.sleep(5)
            else:
                logging.error("❌ Não foi possível recuperar a sessão.")
                return False
        
        logging.info("⏳ Aguardando carregamento (10s)...")
        time.sleep(10)
        return True
    except Exception as e:
        logging.error(f"Erro na navegação: {e}")
        return False

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

    if not elemento_npj:
        logging.warning("⚠️ NPJ não encontrado (ou lista vazia/confidencial).")
        return None

    try:
        texto = elemento_npj.text.strip()
        driver.switch_to.default_content()
        if not re.search(r"\d{4}/\d+-\d+", texto): 
            logging.warning(f"⚠️ Texto inválido para NPJ: {texto}")
            return None
            
        numeros = limpar_apenas_digitos(texto)
        npj = numeros[:-3] if numeros.endswith('000') else numeros
        if len(npj) < 5: return None
        
        logging.info(f"✅ NPJ Localizado: {npj}")
        driver.get(f"https://juridico.bb.com.br/paj/app/paj-cadastro/spas/processo/consulta/processo-consulta.app.html#/editar/{npj}/0/18")
        time.sleep(10)
        return npj
    except: return None

def coletar_lista_subsidios(driver):
    """
    Coleta subsídios com PROTEÇÃO CONTRA LOOP INFINITO.
    """
    logging.info("📊 Coletando subsídios...")
    lista = []
    xpath = "//tr[contains(@ng-repeat, 'subsidio in vm.resultado.lista')]"
    
    if not buscar_elemento_em_todos_contextos(driver, By.XPATH, xpath):
        logging.warning("⚠️ Tabela não encontrada/vazia. Verificando erro...")
        return None 

    # Controle de Loop
    ultimo_hash_pagina = ""
    pagina_atual = 1
    MAX_PAGINAS = 50 # Trava de segurança para não rodar eternamente

    while pagina_atual <= MAX_PAGINAS:
        try:
            # 1. Coleta dados da página atual
            elementos = driver.find_elements(By.XPATH, xpath)
            if not elementos and not lista: return None 

            dados_pagina_atual = []
            texto_bruto_para_hash = ""

            for linha in elementos:
                try:
                    tipo = linha.find_element(By.XPATH, "./td[4]").text.strip()
                    item = linha.find_element(By.XPATH, "./td[5]").text.strip()
                    estado = linha.find_element(By.XPATH, "./td[6]").text.strip()
                    
                    registro = {"tipo": tipo, "item": item, "estado": estado}
                    dados_pagina_atual.append(registro)
                    
                    # Cria string única para identificar se a página mudou
                    texto_bruto_para_hash += f"{tipo}{item}{estado}"
                except: pass
            
            # 2. Verifica se estamos lendo a MESMA página de novo (Loop Infinito)
            hash_atual = hashlib.md5(texto_bruto_para_hash.encode('utf-8')).hexdigest()
            if hash_atual == ultimo_hash_pagina:
                logging.info(f"⏹️ Detectada página repetida (Hash idêntico). Encerrando paginação na pág {pagina_atual}.")
                break
            
            ultimo_hash_pagina = hash_atual
            
            # Adiciona os novos dados à lista principal
            lista.extend(dados_pagina_atual)
            logging.info(f"   -> Página {pagina_atual} processada. Total coletado: {len(lista)}")

            # 3. Tenta ir para a próxima
            xpath_prox = "//a[contains(@ng-click, 'vm.rechamarPesquisaProximo()')]"
            try:
                btn = driver.find_element(By.XPATH, xpath_prox)
                
                # Verifica classes e atributos de desabilitado
                classes_btn = btn.get_attribute("class") or ""
                is_disabled = btn.get_attribute("disabled")
                style_btn = btn.get_attribute("style") or "" # Às vezes escondem com display: none

                if "disabled" in classes_btn or is_disabled or "ng-hide" in classes_btn or "display: none" in style_btn:
                    logging.info("⏹️ Botão 'Próximo' desabilitado/invisível. Fim.")
                    break
                
                driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                time.sleep(1)
                btn.click()
                
                logging.info("⏳ Carregando próxima página...")
                time.sleep(5) # Tempo para a tabela atualizar
                pagina_atual += 1
                
            except Exception:
                logging.info("⏹️ Botão 'Próximo' não encontrado.")
                break
                
        except Exception as e:
            logging.error(f"Erro na extração: {e}")
            break
    
    if pagina_atual > MAX_PAGINAS:
        logging.warning("⚠️ Limite de segurança de páginas atingido! Loop interrompido forçadamente.")

    driver.switch_to.default_content()
    return lista

# --- ORQUESTRADOR ---

def executar_rpa():
    print("🤖 INICIANDO ORQUESTRADOR (SOLICITAÇÕES DE MONITORAMENTO)")
    database.inicializar_banco()

    apexFluxoLegalOne.buscar_e_abastecer_fila()
    fila_pendente = database.buscar_tarefas_pendentes()
    
    if not fila_pendente:
        logging.info("✅ Nenhuma solicitação pendente.")
        return

    logging.info(f"📋 Processando {len(fila_pendente)} solicitações...")

    usuario_env = os.getenv("BB_USUARIO")
    senha_env = os.getenv("BB_SENHA")
    if not usuario_env or not senha_env: return

    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    driver = uc.Chrome(options=options, use_subprocess=True, version_main=142)

    try:
        if not fazer_login(driver):
            logging.error("❌ Falha no login inicial. Abortando.")
            return

        for tarefa in fila_pendente:
            cnj = tarefa['processo_cnj']
            t_id = tarefa['tarefa_id']
            solicitante = tarefa['solicitante_id']
            
            logging.info(f"\n⚙️ Atendendo {solicitante} | CNJ: {cnj}")
            
            try:
                if acessar_processo_consulta_rapida(driver, cnj):
                    npj = extrair_e_acessar_npj(driver)
                    if npj:
                        pid = database.salvar_processo(cnj, npj)
                        if pid:
                            dados = coletar_lista_subsidios(driver)
                            if dados is None:
                                logging.error("⚠️ Erro tabela. Marcando para retentativa.")
                                database.marcar_tarefa_concluida(t_id, 'ERRO')
                            else:
                                if dados: 
                                    database.salvar_lista_subsidios(pid, dados)
                                    logging.info(f"✅ {len(dados)} subsídios salvos.")
                                else:
                                    logging.info("✅ Processo sem subsídios.")
                                database.marcar_tarefa_concluida(t_id, 'CONCLUIDO')
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