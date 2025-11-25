import time
import os
import re
import logging
import json
import sys

# Adiciona o diretório raiz ao path para importar 'bd' corretamente
# Estamos em RPA/main.py, queremos acessar ../bd
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv

# Importa o módulo de banco que acabamos de criar
from bd import database

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

load_dotenv()

def limpar_apenas_digitos(texto):
    if not texto: return ""
    return re.sub(r'\D', '', str(texto))

def esperar_carregamento_completo(driver, timeout=30):
    logging.info("⏳ Aguardando página estabilizar...")
    try:
        wait = WebDriverWait(driver, timeout)
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(3)
        logging.info("✅ Página carregada e estável.")
        return True
    except Exception as e:
        logging.warning(f"⚠️ Alerta de timeout: {e}")
        return False

def buscar_elemento_em_todos_contextos(driver, by, value):
    # (Mesma função robusta de antes)
    try:
        driver.switch_to.default_content()
        return driver.find_element(by, value)
    except:
        pass
    
    driver.switch_to.default_content()
    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    for iframe in iframes:
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(iframe)
            return driver.find_element(by, value)
        except:
            continue
    driver.switch_to.default_content()
    return None

def acessar_processo_consulta_rapida(driver, numero_processo):
    numero_limpo = limpar_apenas_digitos(numero_processo)
    if not numero_limpo: return False

    url = f"https://juridico.bb.com.br/paj/juridico/v2?app=processoConsultaRapidoTomboApp&numeroTombo={numero_limpo}"
    logging.info(f"🚀 [ETAPA 1] Navegando para consulta: {numero_limpo}")
    driver.get(url)
    
    logging.info("⏳ Aguardando 10s fixos...")
    time.sleep(10)
    return True

def extrair_e_acessar_npj(driver):
    """
    Localiza o NPJ, valida o padrão e navega para edição.
    Retorna o (npj_limpo) string se sucesso, ou None se falha/inválido.
    """
    logging.info("🔍 Procurando NPJ...")
    elemento_npj = None
    
    seletores = [
        "//tr[contains(@ng-repeat, 'resultadoProcessos.processos')]//td[1]//span[contains(@class, 'ng-binding')]",
        "//td[contains(@style, 'width: 15%')]//span[contains(@class, 'ng-binding')]",
        "//*[text()[contains(.,'/') and contains(.,'-')]]"
    ]

    for tentativa in range(1, 4):
        for xpath in seletores:
            elemento_npj = buscar_elemento_em_todos_contextos(driver, By.XPATH, xpath)
            if elemento_npj: break
        if elemento_npj: break
        time.sleep(5)

    if not elemento_npj:
        logging.error("❌ ERRO CRÍTICO: Elemento do NPJ não encontrado na tela. Parando.")
        return None

    try:
        texto_npj = elemento_npj.text.strip()
        logging.info(f"✅ Texto Bruto Encontrado: {texto_npj}")
        
        driver.switch_to.default_content()
        
        # --- VALIDAÇÃO DE PADRÃO (REGEX) ---
        # Padrão esperado: 4 dígitos (ano) / N dígitos - N dígitos
        # Ex: 2022/0204732-000
        padrao_npj = r"\d{4}/\d+-\d+"
        if not re.search(padrao_npj, texto_npj):
            logging.error(f"❌ ERRO CRÍTICO: O texto encontrado '{texto_npj}' NÃO parece um NPJ válido. Abortando para evitar erros.")
            return None

        numeros = limpar_apenas_digitos(texto_npj)
        npj_final = numeros[:-3] if numeros.endswith('000') else numeros
        
        if not npj_final or len(npj_final) < 5: # Validação extra de tamanho mínimo
             logging.error(f"❌ ERRO: NPJ limpo '{npj_final}' parece inválido ou muito curto.")
             return None

        logging.info(f"🧹 NPJ Validado e Limpo: {npj_final}")

        nova_url = f"https://juridico.bb.com.br/paj/app/paj-cadastro/spas/processo/consulta/processo-consulta.app.html#/editar/{npj_final}/0/18"
        
        logging.info(f"🚀 [ETAPA 2] Indo para edição...")
        driver.get(nova_url)
        time.sleep(10)
        
        return npj_final

    except Exception as e:
        logging.error(f"❌ Erro ao processar NPJ: {e}")
        return None

def coletar_lista_subsidios(driver):
    """
    Navega pelas páginas e retorna uma LISTA de dicionários.
    Não salva JSON nem BD aqui, apenas coleta.
    """
    logging.info("📊 Iniciando coleta de subsídios...")
    lista_final = []
    pagina = 1
    
    xpath_linha = "//tr[contains(@ng-repeat, 'subsidio in vm.resultado.lista')]"
    
    # Foca no iframe certo antes de começar
    if not buscar_elemento_em_todos_contextos(driver, By.XPATH, xpath_linha):
        logging.warning("⚠️ Tabela não encontrada.")
        return []

    while True:
        logging.info(f"📄 Lendo Página {pagina}...")
        try:
            linhas = driver.find_elements(By.XPATH, xpath_linha)
            for linha in linhas:
                try:
                    tipo = linha.find_element(By.XPATH, "./td[4]").text.strip()
                    item = linha.find_element(By.XPATH, "./td[5]").text.strip()
                    estado = linha.find_element(By.XPATH, "./td[6]").text.strip()
                    lista_final.append({"tipo": tipo, "item": item, "estado": estado})
                except: pass
            
            # Tenta ir para próxima página
            xpath_prox = "//a[contains(@ng-click, 'vm.rechamarPesquisaProximo()')]"
            try:
                btn = driver.find_element(By.XPATH, xpath_prox)
                if "disabled" in btn.get_attribute("class") or btn.get_attribute("disabled"):
                    logging.info("⏹️ Fim da paginação.")
                    break
                
                driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                time.sleep(1)
                btn.click()
                time.sleep(5)
                pagina += 1
            except:
                break
        except Exception as e:
            logging.error(f"Erro paginação: {e}")
            break
            
    driver.switch_to.default_content()
    return lista_final

def executar_rpa():
    # --- INTERAÇÃO COM O USUÁRIO ---
    print("\n" + "="*50)
    processo_input = input("👉 Digite o número do processo (CNJ) ou pressione ENTER para usar o padrão: ").strip()
    print("="*50 + "\n")

    processo_cnj = processo_input if processo_input else "7005938-33.2022.8.22.0021"

    logging.info(f">>> INICIANDO ROBÔ ONE-SID PARA O PROCESSO: {processo_cnj} <<<")
    
    # 1. Inicializa Banco
    database.inicializar_banco()
    
    usuario_env = os.getenv("BB_USUARIO")
    senha_env = os.getenv("BB_SENHA")
    if not usuario_env or not senha_env: 
        logging.error("Credenciais não configuradas no .env")
        return

    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    driver = uc.Chrome(options=options, use_subprocess=True, version_main=142)

    try:
        # --- LOGIN ---
        logging.info("🔐 Logando...")
        driver.get('https://loginweb.bb.com.br/sso/XUI/?realm=/paj&goto=https://juridico.bb.com.br/wfj#login')
        wait = WebDriverWait(driver, 60)
        
        wait.until(EC.visibility_of_element_located((By.ID, "idToken1"))).send_keys(usuario_env)
        time.sleep(0.5)
        driver.find_element(By.ID, "loginButton_0").click()
        
        wait.until(EC.visibility_of_element_located((By.ID, "idToken3"))).send_keys(senha_env)
        time.sleep(0.5)
        
        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input#loginButton_0[name='callback_4']"))).click()
        logging.info("Login OK.")
        
        # --- FLUXO ---
        if acessar_processo_consulta_rapida(driver, processo_cnj):
            
            # Pega o NPJ da tela e vai para edição
            npj_encontrado = extrair_e_acessar_npj(driver)
            
            if npj_encontrado:
                # 1. Salva/Atualiza o PROCESSO no Banco
                logging.info(f"💾 Salvando processo no BD: CNJ={processo_cnj}, NPJ={npj_encontrado}")
                id_processo_bd = database.salvar_processo(processo_cnj, npj_encontrado)
                
                if id_processo_bd:
                    # 2. Coleta os Subsídios
                    lista_dados = coletar_lista_subsidios(driver)
                    
                    # 3. Salva os Subsídios no Banco
                    if lista_dados:
                        logging.info("🔄 Atualizando subsídios no banco (substituindo antigos pelos atuais)...")
                        database.salvar_lista_subsidios(id_processo_bd, lista_dados)
                    else:
                        logging.warning("Nenhum subsídio coletado para salvar.")
                else:
                    logging.error("Falha ao criar processo no banco. Subsídios não serão salvos.")
            else:
                 logging.error("🚫 FLUXO INTERROMPIDO: Não foi possível obter um NPJ válido.")

        logging.info("🏁 Fim. Mantendo 60s.")
        time.sleep(60)

    finally:
        driver.quit()

if __name__ == "__main__":
    executar_rpa()