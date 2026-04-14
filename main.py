import atexit
import logging
import os
import sys
import time

import undetected_chromedriver as uc
from dotenv import load_dotenv
from selenium.common.exceptions import WebDriverException

from app_logging import build_logging_handlers
from rpa import AuthService, BrowserFactory, PortalClient, PortalRPARunner, ProcessoService
from rpa.exceptions import RPAError


os.makedirs("logs", exist_ok=True)
loki_handlers, loki_enabled = build_logging_handlers(
    "logs/processador.log",
    service="processador",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [PORTAL RPA] %(message)s",
    handlers=loki_handlers,
)

load_dotenv()

if not loki_enabled:
    logging.warning("logging_loki não instalado. Logs seguirão apenas para stdout/arquivo.")

BROWSER_FACTORY = BrowserFactory()
DEFAULT_RUNNER = PortalRPARunner(browser_factory=BROWSER_FACTORY)
atexit.register(DEFAULT_RUNNER.close)


def criar_browser():
    return BROWSER_FACTORY.create_browser()


def _build_services(driver):
    auth_service = AuthService(driver)
    portal_client = PortalClient(driver, auth_service)
    processo_service = ProcessoService(driver, portal_client)
    return auth_service, portal_client, processo_service


def limpar_apenas_digitos(texto):
    return ProcessoService.limpar_apenas_digitos(texto)


def buscar_elemento_em_todos_contextos(driver, by, value):
    _, portal_client, _ = _build_services(driver)
    try:
        return portal_client.find_element_across_frames(by, value)
    except WebDriverException:
        return None


def fazer_login(driver):
    auth_service, _, _ = _build_services(driver)
    try:
        return auth_service.ensure_authenticated()
    except RPAError as exc:
        logging.error("❌ Falha no login: %s", exc)
        return False


def acessar_processo_consulta_rapida(driver, numero_processo):
    _, _, processo_service = _build_services(driver)
    try:
        return processo_service.acessar_processo_consulta_rapida(numero_processo)
    except RPAError as exc:
        logging.error("❌ Falha ao abrir consulta rápida %s: %s", numero_processo, exc)
        return False


def extrair_e_acessar_npj(driver):
    _, _, processo_service = _build_services(driver)
    try:
        return processo_service.extrair_e_acessar_npj()
    except RPAError as exc:
        logging.error("❌ Falha ao extrair/acessar NPJ: %s", exc)
        return None


def coletar_lista_subsidios(driver):
    _, _, processo_service = _build_services(driver)
    try:
        return processo_service.coletar_lista_subsidios()
    except RPAError as exc:
        logging.error("❌ Falha ao coletar subsídios: %s", exc)
        return None


def abrir_url_autenticada(driver, url, descricao="Página autenticada"):
    _, portal_client, _ = _build_services(driver)
    try:
        return portal_client.open_authenticated_url(
            url,
            description=descricao,
            timeout=30,
        )
    except RPAError as exc:
        logging.error("❌ Falha ao abrir URL autenticada: %s", exc)
        return False


def job_processar_portal():
    DEFAULT_RUNNER.run_cycle()


if __name__ == "__main__":
    import schedule

    print("\n--- 🤖 ROBÔ PROCESSADOR PORTAL (5 em 5 min) ---")

    job_processar_portal()
    schedule.every(5).minutes.do(job_processar_portal)

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    finally:
        DEFAULT_RUNNER.close()
