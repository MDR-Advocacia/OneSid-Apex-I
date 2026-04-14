import atexit
import logging
import os
import time

from dotenv import load_dotenv

from app_logging import build_logging_handlers
from rpa import BrowserFactory, MonitorRPARunner
from utils import twotask_api as twotask


os.makedirs("logs", exist_ok=True)
loki_handlers, loki_enabled = build_logging_handlers(
    "logs/monitor.log",
    service="monitor",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [MONITOR] %(message)s",
    handlers=loki_handlers,
)

load_dotenv()

if not loki_enabled:
    logging.warning("logging_loki não instalado. Logs seguirão apenas para stdout/arquivo.")


MONITOR_RUNNER = MonitorRPARunner(
    browser_factory=BrowserFactory(),
    notifier=twotask.post_to_api,
)
atexit.register(MONITOR_RUNNER.close)


def verificar_processos_em_monitoramento():
    MONITOR_RUNNER.run_cycle()


def job():
    logging.info("⏰ Iniciando ciclo agendado de monitoramento.")
    verificar_processos_em_monitoramento()
    logging.info("💤 Ciclo finalizado. Aguardando próximo agendamento.")


if __name__ == "__main__":
    import schedule

    print("\n--- 🕵️ ROBÔ DE MONITORAMENTO EM EXECUÇÃO (LOOP) ---")

    schedule.every(5).minutes.do(job)
    job()

    while True:
        schedule.run_pending()
        time.sleep(1)
