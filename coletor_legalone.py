import time
import logging
import sys
import os
from dotenv import load_dotenv

from app_logging import build_logging_handlers

# Garante que a pasta de logs existe
os.makedirs("logs", exist_ok=True)
loki_handlers, loki_enabled = build_logging_handlers(
    "logs/coletor.log",
    service="coletor",
)

# Configura logs (Tela + Arquivo + Loki)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [LEGAL ONE] %(message)s',
    handlers=loki_handlers
)

load_dotenv()

if not loki_enabled:
    logging.warning("logging_loki não instalado. Logs seguirão apenas para stdout/arquivo.")

# Ajuste de Path para garantir importações
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

try:
    import bd.database as database
    import apexFluxoLegalOne
except ImportError as e:
    logging.error(f"Erro de importação: {e}")
    sys.exit(1)

def job_coleta():
    logging.info("🚀 Iniciando ciclo de coleta no Legal One...")
    try:
        # Inicializa banco se precisar
        database.inicializar_banco()
        
        # Chama a rotina de busca (Produtor)
        apexFluxoLegalOne.buscar_e_abastecer_fila()
        
    except Exception as e:
        logging.error(f"❌ Erro durante a coleta: {e}")
    
    logging.info("💤 Coleta finalizada. Aguardando próximo ciclo.")

if __name__ == "__main__":
    import schedule

    print("\n--- 📡 ROBÔ COLETOR LEGAL ONE (20 em 20 min) ---")
    
    # Executa imediatamente na partida
    job_coleta()
    
    # Agenda para rodar a cada 20 minutos
    schedule.every(20).minutes.do(job_coleta)

    # Loop infinito
    while True:
        schedule.run_pending()
        time.sleep(1)
