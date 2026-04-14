import hashlib
import logging
import os
import re

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from .exceptions import PortalElementNotFoundError, PortalTimeoutError


class ProcessoService:
    NPJ_REGEX = re.compile(r"\d{4}/\d+-\d+")
    CONSULTA_URL_TEMPLATE = (
        "https://juridico.bb.com.br/paj/juridico/v2"
        "?app=processoConsultaRapidoTomboApp&numeroTombo={numero}"
    )
    PROCESSO_URL_TEMPLATE = (
        "https://juridico.bb.com.br/paj/app/paj-cadastro/spas/processo/consulta/"
        "processo-consulta.app.html#/editar/{npj}/0/18"
    )
    NPJ_SELECTORS = [
        (
            By.XPATH,
            "//tr[contains(@ng-repeat, 'resultadoProcessos.processos') "
            "and not(contains(., '#Confidencial'))]//td[1]//span[contains(@class, 'ng-binding')]",
        ),
        (
            By.XPATH,
            "//tr[contains(@ng-repeat, 'resultadoProcessos.processos')]//td[1]"
            "//span[contains(@class, 'ng-binding')]",
        ),
        (
            By.XPATH,
            "//td[contains(@style, 'width: 15%')]//span[contains(@class, 'ng-binding')]",
        ),
        (By.XPATH, "//*[text()[contains(.,'/') and contains(.,'-')]]"),
    ]
    SUBSIDIO_ROW_XPATH = "//tr[contains(@ng-repeat, 'subsidio in vm.resultado.lista')]"
    NEXT_PAGE_XPATH = "//a[contains(@ng-click, 'vm.rechamarPesquisaProximo()')]"

    def __init__(self, driver, portal_client, *, timeout=None):
        self.driver = driver
        self.portal_client = portal_client
        self.timeout = timeout or int(os.getenv("RPA_DEFAULT_TIMEOUT", "30"))
        self.search_timeout = int(os.getenv("RPA_SEARCH_TIMEOUT", "20"))
        self.pagination_timeout = int(os.getenv("RPA_PAGINATION_TIMEOUT", "15"))
        self.table_settle_timeout = int(os.getenv("RPA_TABLE_SETTLE_TIMEOUT", "8"))
        self.max_paginas = int(os.getenv("RPA_MAX_PAGINAS", "50"))
        self.page_read_attempts = int(os.getenv("RPA_PAGE_READ_ATTEMPTS", "3"))

    def acessar_processo_consulta_rapida(self, numero_processo):
        numero_limpo = self.limpar_apenas_digitos(numero_processo)
        if not numero_limpo:
            raise PortalElementNotFoundError(
                "Número do processo inválido para consulta rápida",
                expected="CNJ com dígitos",
            )

        url = self.CONSULTA_URL_TEMPLATE.format(numero=numero_limpo)
        self.portal_client.open_authenticated_url(
            url,
            description=f"Consulta rápida do processo {numero_limpo}",
            expected_url_fragment="processoConsultaRapidoTomboApp",
            timeout=self.timeout,
        )
        return True

    def extrair_e_acessar_npj(self):
        logging.info("🔍 Buscando NPJ da consulta rápida.")
        elemento_npj, texto = self._wait_for_npj_renderizado()

        numeros = self.limpar_apenas_digitos(texto)
        npj = numeros[:-3] if numeros.endswith("000") else numeros
        if len(npj) < 5:
            raise PortalElementNotFoundError(
                "NPJ extraído é inválido",
                current_url=self.portal_client.safe_current_url(),
                expected="NPJ com ao menos 5 dígitos",
            )

        logging.info("✅ NPJ encontrado: %s", npj)
        return self.abrir_processo_por_npj(npj)

    def abrir_processo_por_npj(self, npj):
        npj_limpo = self.limpar_apenas_digitos(npj)
        if not npj_limpo:
            raise PortalElementNotFoundError(
                "NPJ inválido para abrir página detalhada",
                current_url=self.portal_client.safe_current_url(),
                expected="NPJ com dígitos",
            )

        url = self.PROCESSO_URL_TEMPLATE.format(npj=npj_limpo)
        self.portal_client.open_authenticated_url(
            url,
            description=f"Página detalhada do NPJ {npj_limpo}",
            expected_url_fragment=f"/editar/{npj_limpo}",
            timeout=self.timeout,
        )
        self._wait_for_processo_header(npj_limpo)
        self.driver.switch_to.default_content()
        return npj_limpo

    def _wait_for_npj_renderizado(self):
        textos_vistos = []

        def locate(_driver):
            textos_vistos.clear()
            for by, value in self.NPJ_SELECTORS:
                elementos = self.portal_client.find_elements_across_frames(by, value)
                for elemento in elementos:
                    try:
                        texto = (elemento.text or "").strip()
                    except (StaleElementReferenceException, NoSuchElementException):
                        continue

                    if not texto or "#Confidencial" in texto:
                        continue

                    if texto not in textos_vistos:
                        textos_vistos.append(texto)

                    if self.NPJ_REGEX.search(texto):
                        return elemento, texto
            return False

        try:
            return WebDriverWait(self.driver, self.search_timeout, poll_frequency=0.5).until(
                locate
            )
        except TimeoutException as exc:
            exemplos = ", ".join(textos_vistos[:5]) if textos_vistos else "nenhum texto útil"
            raise PortalElementNotFoundError(
                f"NPJ não renderizou no tempo esperado. Exemplos vistos: {exemplos}",
                current_url=self.portal_client.safe_current_url(),
                expected=self.NPJ_REGEX.pattern,
            ) from exc

    def coletar_lista_subsidios(self):
        logging.info("📊 Coletando lista de subsídios.")
        self._wait_for_subsidios_renderizados()

        lista = []
        chaves_vistas = set()
        ultimo_hash = ""
        pagina = 1

        while pagina <= self.max_paginas:
            dados_pagina = self._coletar_subsidios_da_pagina(pagina)
            if not dados_pagina and not lista:
                raise PortalElementNotFoundError(
                    "Nenhuma linha de subsídio pôde ser lida",
                    current_url=self.portal_client.safe_current_url(),
                    expected="colunas tipo, item, estado e data",
                )

            hash_atual = self._hash_subsidios(dados_pagina)
            if hash_atual == ultimo_hash:
                logging.info("⏹️ Página repetida detectada. Encerrando paginação.")
                break

            ultimo_hash = hash_atual
            adicionados = self._adicionar_subsidios_unicos(
                lista,
                dados_pagina,
                chaves_vistas,
            )
            logging.info(
                "   -> Pág %s OK. Itens novos: %s. Total acumulado: %s",
                pagina,
                adicionados,
                len(lista),
            )

            if not self._ir_para_proxima_pagina(hash_atual):
                break

            pagina += 1

        self.driver.switch_to.default_content()
        return lista

    def _extrair_dados_subsidio(self, linha):
        try:
            raw_data = linha.find_element(By.XPATH, "./td[3]").text.strip()
            match_data = re.search(r"(\d{2}/\d{2}/\d{4})", raw_data)
            data_limite = match_data.group(1) if match_data else ""
            tipo = linha.find_element(By.XPATH, "./td[4]").text.strip()
            item = linha.find_element(By.XPATH, "./td[5]").text.strip()
            estado = linha.find_element(By.XPATH, "./td[6]").text.strip()

            if not tipo or not item or not estado:
                return None

            return {
                "tipo": tipo,
                "item": item,
                "estado": estado,
                "data_limite": data_limite,
            }
        except (NoSuchElementException, StaleElementReferenceException):
            return None

    def _ir_para_proxima_pagina(self, hash_atual):
        botao = self.portal_client.find_element_across_frames(By.XPATH, self.NEXT_PAGE_XPATH)
        if not botao:
            return False

        classes = (botao.get_attribute("class") or "").lower()
        style = (botao.get_attribute("style") or "").lower()
        if (
            "disabled" in classes
            or botao.get_attribute("disabled")
            or "ng-hide" in classes
            or "none" in style
        ):
            return False

        self.driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});",
            botao,
        )

        try:
            botao.click()
        except ElementClickInterceptedException:
            self.driver.execute_script("arguments[0].click();", botao)

        self._aguardar_mudanca_de_pagina(hash_atual)
        self._wait_for_subsidios_renderizados()
        return True

    def _aguardar_mudanca_de_pagina(self, hash_anterior):
        def table_changed(_driver):
            if self._loading_indicator_visible():
                return False
            elementos = self.portal_client.find_elements_across_frames(
                By.XPATH,
                self.SUBSIDIO_ROW_XPATH,
            )
            if not elementos:
                return False
            return self._hash_elementos(elementos) != hash_anterior

        try:
            WebDriverWait(self.driver, self.pagination_timeout, poll_frequency=0.5).until(
                table_changed
            )
        except TimeoutException as exc:
            raise PortalTimeoutError(
                "Paginação não alterou o conteúdo da tabela",
                current_url=self.portal_client.safe_current_url(),
                expected="mudança do conteúdo após clicar em próxima página",
            ) from exc

    def _wait_for_processo_header(self, npj):
        npj_exibicao = self.formatar_npj_exibicao(npj)
        def header_ready(_driver):
            self.driver.switch_to.default_content()
            try:
                body = self.driver.find_element(By.TAG_NAME, "body")
                texto = (body.text or "").strip()
            except (NoSuchElementException, StaleElementReferenceException):
                return False
            return npj_exibicao in texto

        try:
            WebDriverWait(self.driver, self.search_timeout, poll_frequency=0.5).until(
                header_ready
            )
        except TimeoutException as exc:
            raise PortalElementNotFoundError(
                f"Cabeçalho do processo não exibiu o NPJ {npj_exibicao}",
                current_url=self.portal_client.safe_current_url(),
                expected=f"NPJ {npj_exibicao} visível no corpo da página",
            ) from exc

    def _wait_for_subsidios_renderizados(self):
        state = {"last_hash": None, "stable_hits": 0}

        def grid_ready(_driver):
            if self._loading_indicator_visible():
                state["last_hash"] = None
                state["stable_hits"] = 0
                return False

            snapshot = self._snapshot_subsidio_rows()
            if not snapshot:
                state["last_hash"] = None
                state["stable_hits"] = 0
                return False

            current_hash = hashlib.md5("||".join(snapshot).encode("utf-8")).hexdigest()
            if current_hash == state["last_hash"]:
                state["stable_hits"] += 1
            else:
                state["last_hash"] = current_hash
                state["stable_hits"] = 0

            return state["stable_hits"] >= 2

        try:
            WebDriverWait(
                self.driver,
                self.search_timeout + self.table_settle_timeout,
                poll_frequency=0.5,
            ).until(grid_ready)
        except TimeoutException as exc:
            raise PortalTimeoutError(
                "Tabela de subsídios não estabilizou no tempo esperado",
                current_url=self.portal_client.safe_current_url(),
                expected="linhas de subsídio renderizadas e estáveis",
            ) from exc

    def _coletar_subsidios_da_pagina(self, pagina):
        ultimo_tamanho = 0

        for tentativa in range(1, self.page_read_attempts + 1):
            elementos = self.portal_client.find_elements_across_frames(
                By.XPATH,
                self.SUBSIDIO_ROW_XPATH,
            )
            if not elementos:
                if tentativa < self.page_read_attempts:
                    self._wait_for_subsidios_renderizados()
                    continue
                return []

            dados_pagina = []
            for linha in elementos:
                dado = self._extrair_dados_subsidio(linha)
                if dado:
                    dados_pagina.append(dado)

            if dados_pagina:
                return dados_pagina

            ultimo_tamanho = len(elementos)
            if tentativa < self.page_read_attempts:
                logging.info(
                    "   -> Pág %s com linhas ainda parciais na tentativa %s/%s. Revalidando renderização.",
                    pagina,
                    tentativa,
                    self.page_read_attempts,
                )
                self._wait_for_subsidios_renderizados()

        raise PortalElementNotFoundError(
            f"Linhas de subsídio não puderam ser lidas na página {pagina}",
            current_url=self.portal_client.safe_current_url(),
            expected=f"linhas com colunas preenchidas após {self.page_read_attempts} tentativas",
        )

    @staticmethod
    def _adicionar_subsidios_unicos(lista_destino, dados_pagina, chaves_vistas):
        adicionados = 0

        for dado in dados_pagina:
            chave = (
                dado.get("tipo", ""),
                dado.get("item", ""),
                dado.get("estado", ""),
                dado.get("data_limite", ""),
            )
            if chave in chaves_vistas:
                continue

            chaves_vistas.add(chave)
            lista_destino.append(dado)
            adicionados += 1

        return adicionados

    def _snapshot_subsidio_rows(self):
        elementos = self.portal_client.find_elements_across_frames(
            By.XPATH,
            self.SUBSIDIO_ROW_XPATH,
        )
        snapshot = []
        for linha in elementos:
            try:
                cols = [
                    linha.find_element(By.XPATH, "./td[3]").text.strip(),
                    linha.find_element(By.XPATH, "./td[4]").text.strip(),
                    linha.find_element(By.XPATH, "./td[5]").text.strip(),
                    linha.find_element(By.XPATH, "./td[6]").text.strip(),
                ]
            except (NoSuchElementException, StaleElementReferenceException):
                continue

            normalized = [col for col in cols if col]
            if normalized:
                snapshot.append("|".join(cols))
        return snapshot

    def _loading_indicator_visible(self):
        indicators = self.portal_client.find_elements_across_frames(
            By.XPATH,
            (
                "//*[contains(concat(' ', normalize-space(@class), ' '), ' loader__text ') "
                "and normalize-space(.)='Carregando...']"
            ),
        )
        for indicator in indicators:
            try:
                text = (indicator.text or "").strip()
                if text and indicator.is_displayed():
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def limpar_apenas_digitos(texto):
        if not texto:
            return ""
        return re.sub(r"\D", "", str(texto))

    @staticmethod
    def formatar_npj_exibicao(npj):
        npj_limpo = ProcessoService.limpar_apenas_digitos(npj)
        if len(npj_limpo) <= 4:
            return npj_limpo
        return f"{npj_limpo[:4]}/{npj_limpo[4:]}-000"

    @staticmethod
    def _hash_subsidios(dados_pagina):
        chunks = [
            f"{item['tipo']}|{item['item']}|{item['estado']}|{item['data_limite']}"
            for item in dados_pagina
        ]
        return hashlib.md5("||".join(chunks).encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_elementos(elementos):
        chunks = [elemento.text.strip() for elemento in elementos]
        return hashlib.md5("||".join(chunks).encode("utf-8")).hexdigest()
