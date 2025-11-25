import time
import os
import re
import logging
import json # Novo import
# Adiciona o diretório atual ao path para garantir que importe o módulo local
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Carrega as variáveis do arquivo .env
load_dotenv()

def limpar_apenas_digitos(texto):
    """
    Remove tudo que não for dígito.
    Ex: 2022/0204732-000 -> 20220204732000
    """
    if not texto:
        return ""
    return re.sub(r'\D', '', str(texto))

def acessar_processo_consulta_rapida(driver, numero_processo):
    """
    Navega para a URL inicial de consulta rápida usando o número do processo (CNJ).
    """
    numero_limpo = limpar_apenas_digitos(numero_processo)
    if not numero_limpo:
        logging.warning("Número de processo vazio ou inválido.")
        return False

    url_base = "https://juridico.bb.com.br/paj/juridico/v2?app=processoConsultaRapidoTomboApp&numeroTombo="
    url_final = f"{url_base}{numero_limpo}"
    
    logging.info(f"🚀 [ETAPA 1] Navegando para consulta rápida: {numero_limpo}")
    driver.get(url_final)
    
    # ESTRATÉGIA MUDADA: Espera fixa de 10 segundos para garantir carregamento total
    logging.info("⏳ Aguardando 10 segundos fixos para carregamento da página...")
    time.sleep(10)
    return True

def buscar_elemento_em_todos_contextos(driver, by, value):
    """
    Função Poderosa: Procura um elemento no contexto principal E dentro de qualquer iframe.
    IMPORTANTE: Se encontrar dentro de um iframe, MANTÉM o driver dentro desse iframe
    para permitir interação.
    """
    # 1. Tenta no contexto principal
    try:
        driver.switch_to.default_content()
        elemento = driver.find_element(by, value)
        logging.info("✅ Elemento encontrado no contexto principal!")
        return elemento
    except:
        pass

    # 2. Lista e varre iframes
    # Precisamos estar no default_content para listar os iframes
    driver.switch_to.default_content()
    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    
    if not iframes:
        return None
    
    for i, iframe in enumerate(iframes):
        try:
            # Garante que volta para a raiz antes de entrar no próximo iframe da lista
            driver.switch_to.default_content()
            driver.switch_to.frame(iframe)
            
            elemento = driver.find_element(by, value)
            logging.info(f"✅ Elemento encontrado dentro do iframe índice {i}!")
            # CORREÇÃO: Não voltamos para default_content aqui.
            # Retornamos com o foco DENTRO do iframe para podermos ler o texto.
            return elemento
        except:
            continue 

    # Se varreu tudo e não achou, volta para segurança
    driver.switch_to.default_content()
    return None

def extrair_e_acessar_npj(driver):
    """
    Localiza o NPJ usando a estrutura exata do Angular (ng-repeat) fornecida.
    """
    logging.info("🔍 Procurando NPJ na tabela (Scan Angular)...")
    
    elemento_npj = None
    max_tentativas = 5 
    
    # Estratégia de Seletores (do mais específico para o mais genérico)
    seletores_xpath = [
        # 1. Cirúrgico: Pega a primeira coluna da linha gerada pelo ng-repeat
        "//tr[contains(@ng-repeat, 'resultadoProcessos.processos')]//td[1]//span[contains(@class, 'ng-binding')]",
        
        # 2. Baseado no estilo da coluna (width: 15%)
        "//td[contains(@style, 'width: 15%')]//span[contains(@class, 'ng-binding')]",
        
        # 3. Genérico (Padrão de texto com / e -)
        "//*[text()[contains(.,'/') and contains(.,'-')]]"
    ]

    for tentativa in range(1, max_tentativas + 1):
        try:
            # Tenta cada seletor da lista
            for xpath in seletores_xpath:
                elemento_npj = buscar_elemento_em_todos_contextos(driver, By.XPATH, xpath)
                if elemento_npj:
                    logging.info(f"🎯 Alvo localizado usando XPath: {xpath}")
                    break
            
            if elemento_npj:
                break
            
            logging.warning(f"⚠️ Tentativa {tentativa}/{max_tentativas}: Tabela Angular ainda vazia ou carregando...")
            time.sleep(5) # Espera fixa entre tentativas
            
        except Exception as e:
            logging.error(f"Erro na tentativa {tentativa}: {e}")
            time.sleep(5)

    if not elemento_npj:
        logging.error("❌ Esgotado! O Angular não renderizou a tabela a tempo.")
        driver.save_screenshot("erro_tabela_vazia.png")
        return False

    try:
        # AQUI OCORRIA O ERRO: Agora vai funcionar porque o driver está focado no iframe correto
        texto_npj = elemento_npj.text.strip()
        logging.info(f"✅ Texto Bruto Extraído: {texto_npj}")
        
        # Importante: Voltar para o contexto principal para a próxima navegação
        driver.switch_to.default_content()
        
        # Limpeza e Validação
        # MUDANÇA: O NPJ precisa ter os 3 últimos '000' removidos
        numeros_brutos = limpar_apenas_digitos(texto_npj)
        
        if numeros_brutos.endswith('000'):
            npj_limpo = numeros_brutos[:-3]
        else:
            npj_limpo = numeros_brutos

        logging.info(f"🧹 NPJ Limpo (sem 000 final): {npj_limpo}")
        
        if not npj_limpo:
             logging.error("❌ Falha: O texto extraído não contém números válidos.")
             return False

        # Monta a nova URL conforme solicitado
        # Link: .../processo-consulta.app.html#/editar/NPJ/0/18
        nova_url = f"https://juridico.bb.com.br/paj/app/paj-cadastro/spas/processo/consulta/processo-consulta.app.html#/editar/{npj_limpo}/0/18"
        
        logging.info(f"🚀 [ETAPA 2] Acessando link de edição do NPJ...")
        logging.info(f"--> URL: {nova_url}")
        
        driver.get(nova_url)
        
        logging.info("⏳ Aguardando 10 segundos fixos para carregamento da edição...")
        time.sleep(10)
        
        return True

    except Exception as e:
        logging.error(f"❌ Erro ao processar NPJ: {e}")
        return False

def extrair_dados_subsidios_paginado(driver):
    """
    Coleta Tipo, Item e Estado da tabela de subsídios, clicando em 'Próximo'
    se houver paginação. Gera um JSON no final.
    """
    logging.info("📊 Iniciando extração de subsídios (com paginação)...")
    
    dados_coletados = []
    pagina_atual = 1
    
    # 1. Tenta localizar a tabela para garantir o foco no iframe/contexto correto
    # XPath de uma linha da tabela
    xpath_linha = "//tr[contains(@ng-repeat, 'subsidio in vm.resultado.lista')]"
    
    # Usa nossa função auxiliar para focar no iframe se necessário
    elemento_teste = buscar_elemento_em_todos_contextos(driver, By.XPATH, xpath_linha)
    
    if not elemento_teste:
        logging.warning("⚠️ Nenhuma tabela de subsídios encontrada.")
        return False
        
    # Loop de Paginação
    while True:
        logging.info(f"📄 Processando Página {pagina_atual}...")
        
        try:
            # Pega todas as linhas visíveis na página atual
            # IMPORTANTE: O driver já está no contexto correto (iframe) graças ao 'buscar_elemento_em_todos_contextos' chamado acima
            linhas = driver.find_elements(By.XPATH, xpath_linha)
            logging.info(f"   -> Encontradas {len(linhas)} linhas nesta página.")
            
            for linha in linhas:
                try:
                    # Extração baseada nos índices das colunas (1-based no XPath)
                    # Coluna 4: Tipo
                    # Coluna 5: Item
                    # Coluna 6: Estado
                    tipo = linha.find_element(By.XPATH, "./td[4]").text.strip()
                    item = linha.find_element(By.XPATH, "./td[5]").text.strip()
                    estado = linha.find_element(By.XPATH, "./td[6]").text.strip()
                    
                    dados_coletados.append({
                        "tipo": tipo,
                        "item": item,
                        "estado": estado
                    })
                except Exception as e:
                    logging.warning(f"   ⚠️ Erro ao ler linha individual: {e}")

            # Lógica do Botão Próximo
            # Procura o botão pelo ng-click
            xpath_proximo = "//a[contains(@ng-click, 'vm.rechamarPesquisaProximo()')]"
            
            # Tenta encontrar o botão
            try:
                btn_proximo = driver.find_element(By.XPATH, xpath_proximo)
                
                # Verifica se está desabilitado (atributo 'disabled' ou classe 'disabled')
                is_disabled = btn_proximo.get_attribute("disabled")
                classes = btn_proximo.get_attribute("class")
                
                if is_disabled or (classes and "disabled" in classes) or not btn_proximo.is_enabled():
                    logging.info("⏹️ Botão 'Próximo' desabilitado. Fim da extração.")
                    break
                
                # Se não está desabilitado, clica
                logging.info("➡️ Clicando em 'Próximo'...")
                # Scroll para garantir visibilidade
                driver.execute_script("arguments[0].scrollIntoView(true);", btn_proximo)
                time.sleep(1)
                btn_proximo.click()
                
                logging.info("⏳ Aguardando 5s para carregar próxima página...")
                time.sleep(5)
                pagina_atual += 1
                
            except Exception:
                logging.info("⏹️ Botão 'Próximo' não encontrado. Assumindo fim da lista.")
                break
                
        except Exception as e:
            logging.error(f"❌ Erro crítico na paginação: {e}")
            break

    # Salvar JSON
    arquivo_json = "subsidios.json"
    try:
        with open(arquivo_json, "w", encoding="utf-8") as f:
            json.dump(dados_coletados, f, indent=4, ensure_ascii=False)
        logging.info(f"✅ SUCESSO! {len(dados_coletados)} registros salvos em '{arquivo_json}'.")
        # Print do JSON no log para conferência
        print(json.dumps(dados_coletados, indent=4, ensure_ascii=False))
    except Exception as e:
        logging.error(f"❌ Erro ao salvar JSON: {e}")

    # Volta para o contexto padrão por segurança
    driver.switch_to.default_content()
    return True

def executar_rpa():
    logging.info(">>> INICIANDO ROBÔ ONE-SID (FORCE WAIT MODE) <<<")
    
    usuario_env = os.getenv("BB_USUARIO")
    senha_env = os.getenv("BB_SENHA")

    if not usuario_env or not senha_env:
        logging.critical("Credenciais não encontradas no .env")
        return

    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    
    try:
        logging.info("Iniciando driver...")
        driver = uc.Chrome(options=options, use_subprocess=True, version_main=142)
    except Exception as e:
        logging.critical(f"Erro ao abrir Chrome: {e}")
        return

    try:
        # --- 1. LOGIN ---
        logging.info("🔐 Acessando login...")
        driver.get('https://loginweb.bb.com.br/sso/XUI/?realm=/paj&goto=https://juridico.bb.com.br/wfj#login')
        
        wait = WebDriverWait(driver, 20)
        wait_long = WebDriverWait(driver, 60)

        logging.info("Inserindo usuário...")
        wait.until(EC.visibility_of_element_located((By.ID, "idToken1"))).send_keys(usuario_env)
        time.sleep(0.5)
        wait.until(EC.element_to_be_clickable((By.ID, "loginButton_0"))).click()
        
        logging.info("Inserindo senha...")
        wait_long.until(EC.visibility_of_element_located((By.ID, "idToken3"))).send_keys(senha_env)
        time.sleep(0.5)

        logging.info("Confirmando login...")
        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input#loginButton_0[name='callback_4']"))).click()
        
        # MUDANÇA PEDIDA: Removida a espera de 15s aqui
        logging.info("Login enviado.")
        
        # --- 2. FLUXO ---
        processo_alvo = "70018740220258220012"
        
        # Passo 1: Acessa Consulta Rápida
        if acessar_processo_consulta_rapida(driver, processo_alvo):
            # Passo 2: Acha o NPJ e vai para a edição
            if extrair_e_acessar_npj(driver):
                # Passo 3: Coleta os dados paginados e salva JSON
                extrair_dados_subsidios_paginado(driver)

        logging.info("🏁 Fluxo finalizado. Manterei aberto por 1 minuto.")
        time.sleep(60)

    except Exception as e:
        logging.error(f"Falha geral: {e}")
        driver.save_screenshot("erro_geral.png")
    
    finally:
        driver.quit()

if __name__ == "__main__":
    executar_rpa()