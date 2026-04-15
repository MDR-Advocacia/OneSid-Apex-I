import logging

from selenium.common.exceptions import WebDriverException

from bd import database

from .auth_service import AuthService
from .browser_factory import BrowserFactory
from .exceptions import (
    LoginError,
    PortalElementNotFoundError,
    PortalNavigationError,
    PortalTimeoutError,
    SessionExpiredError,
    TemporaryPortalError,
)
from .portal_client import PortalClient
from .processo_service import ProcessoService


class PortalRPARunner:
    def __init__(self, browser_factory=None, *, max_task_attempts=3):
        self.browser_factory = browser_factory or BrowserFactory()
        self.max_task_attempts = max_task_attempts
        self.driver = None
        self.auth_service = None
        self.portal_client = None
        self.processo_service = None

    def run_cycle(self):
        logging.info("🏁 Iniciando ciclo de processamento no Portal.")
        database.inicializar_banco()

        fila_pendente = database.buscar_tarefas_pendentes()
        if not fila_pendente:
            logging.info("✅ Nenhuma tarefa pendente no banco.")
            return

        logging.info("📋 Processando %s tarefas da fila.", len(fila_pendente))

        try:
            self.ensure_browser()
        except Exception as exc:
            logging.error("❌ Não foi possível preparar o browser: %s", exc)
            return

        for tarefa in fila_pendente:
            self._processar_tarefa(tarefa)

        logging.info("💤 Ciclo de processamento finalizado.")

    def ensure_browser(self):
        if self.driver is not None:
            try:
                _ = self.driver.current_url
                return self.driver
            except WebDriverException:
                logging.warning("⚠️ Browser anterior ficou inválido. Será recriado.")
                self._reset_state()

        logging.info("🌐 Inicializando browser do portal.")
        self.driver = self.browser_factory.create_browser()
        self.auth_service = AuthService(self.driver)
        self.portal_client = PortalClient(self.driver, self.auth_service)
        self.processo_service = ProcessoService(self.driver, self.portal_client)
        return self.driver

    def restart_browser(self, reason):
        logging.warning("🔁 Reiniciando browser. Motivo: %s", reason)
        self.close()
        return self.ensure_browser()

    def close(self):
        if self.driver is not None:
            try:
                self.browser_factory.close_browser(self.driver)
            except Exception:
                logging.exception("Erro ao encerrar browser.")
        self._reset_state()

    def _processar_tarefa(self, tarefa):
        cnj = tarefa["processo_cnj"]
        tarefa_id = tarefa["tarefa_id"]

        logging.info("⚙️ Processando CNJ: %s", cnj)

        try:
            self._processar_tarefa_com_retry(tarefa)
            database.marcar_tarefa_concluida(tarefa_id, "CONCLUIDO")
        except Exception as exc:
            logging.error("❌ Falha definitiva no CNJ %s: %s", cnj, exc)
            database.marcar_tarefa_concluida(tarefa_id, "ERRO")

    def _processar_tarefa_com_retry(self, tarefa):
        cnj = tarefa["processo_cnj"]
        last_error = None

        for tentativa in range(1, self.max_task_attempts + 1):
            try:
                self.ensure_browser()
                self.auth_service.ensure_authenticated()
                self._processar_tarefa_uma_vez(tarefa)
                return
            except SessionExpiredError as exc:
                last_error = exc
                logging.warning(
                    "⚠️ Sessão expirada para o CNJ %s na tentativa %s/%s: %s",
                    cnj,
                    tentativa,
                    self.max_task_attempts,
                    exc,
                )
                if tentativa < self.max_task_attempts:
                    self.auth_service.ensure_authenticated(force_login=True)
                    continue
            except LoginError as exc:
                last_error = exc
                logging.error(
                    "❌ Falha de login para o CNJ %s na tentativa %s/%s: %s",
                    cnj,
                    tentativa,
                    self.max_task_attempts,
                    exc,
                )
                if tentativa < self.max_task_attempts:
                    self.restart_browser("falha de login")
                    continue
            except (PortalTimeoutError, TemporaryPortalError, PortalNavigationError) as exc:
                last_error = exc
                logging.warning(
                    "⏳ Erro temporário/timeout para o CNJ %s na tentativa %s/%s: %s",
                    cnj,
                    tentativa,
                    self.max_task_attempts,
                    exc,
                )
                if tentativa < self.max_task_attempts:
                    self._recuperar_navegacao(cnj)
                    continue
            except PortalElementNotFoundError as exc:
                last_error = exc
                logging.warning(
                    "🔎 Elemento não encontrado para o CNJ %s na tentativa %s/%s: %s",
                    cnj,
                    tentativa,
                    self.max_task_attempts,
                    exc,
                )
                if tentativa < self.max_task_attempts:
                    self._reabrir_processo(cnj)
                    continue
            except WebDriverException as exc:
                last_error = exc
                logging.error(
                    "🧨 Erro de WebDriver para o CNJ %s na tentativa %s/%s: %s",
                    cnj,
                    tentativa,
                    self.max_task_attempts,
                    exc,
                )
                if tentativa < self.max_task_attempts:
                    self.restart_browser("erro de webdriver")
                    continue
            except Exception as exc:
                last_error = exc
                logging.exception(
                    "❌ Erro inesperado para o CNJ %s na tentativa %s/%s.",
                    cnj,
                    tentativa,
                    self.max_task_attempts,
                )
                if tentativa < self.max_task_attempts:
                    self.restart_browser("erro inesperado")
                    continue
            break

        raise last_error or RuntimeError("Falha ao processar tarefa sem erro identificado")

    def _processar_tarefa_uma_vez(self, tarefa):
        cnj = tarefa["processo_cnj"]

        self.processo_service.acessar_processo_consulta_rapida(cnj)
        npj = self.processo_service.extrair_e_acessar_npj()

        processo_id = database.salvar_processo(cnj, npj)
        if not processo_id:
            raise RuntimeError(f"Não foi possível salvar o processo {cnj} / NPJ {npj}")

        dados = self.processo_service.coletar_lista_subsidios()
        if dados:
            database.salvar_lista_subsidios(processo_id, dados)
            logging.info("✅ %s subsídios salvos para %s.", len(dados), cnj)

            tem_solicitado = any(
                item["estado"].upper() == "SOLICITADO"
                for item in dados
                if item.get("estado")
            )
            if tem_solicitado:
                logging.info(
                    "🚨 Processo %s possui itens 'SOLICITADO'. Ativando monitoramento.",
                    cnj,
                )
                database.atualizar_status_monitoramento(processo_id, True)

    def _recuperar_navegacao(self, cnj):
        try:
            self.portal_client.refresh()
            self.auth_service.ensure_authenticated()
        except Exception as exc:
            logging.warning("⚠️ Refresh não recuperou a navegação: %s", exc)

        try:
            self._reabrir_processo(cnj)
        except Exception as exc:
            logging.warning("⚠️ Reabertura da URL também falhou: %s", exc)
            self.restart_browser("falha na recuperação de navegação")

    def _reabrir_processo(self, cnj):
        logging.info("↩️ Reabrindo consulta rápida do CNJ %s.", cnj)
        self.processo_service.acessar_processo_consulta_rapida(cnj)

    def _reset_state(self):
        self.driver = None
        self.auth_service = None
        self.portal_client = None
        self.processo_service = None
