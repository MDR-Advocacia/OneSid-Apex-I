import requests
import logging
import os
from dotenv import load_dotenv
from requests import RequestException

load_dotenv()

# URL da API interna (TwoTask)
API_URL = os.getenv("API_NOTIFICACAO_URL", "http://twotask.mdr.local:8000/api/v1/tasks/batch-create")
API_TIMEOUT = int(os.getenv("API_NOTIFICACAO_TIMEOUT", "30"))
API_RETRIES = int(os.getenv("API_NOTIFICACAO_RETRIES", "2"))

def post_to_api(lista_processos):
    """
    Recebe uma lista de dicionários com os dados dos processos atualizados
    e envia para a API externa no formato padrão 'Onesid'.
    """
    if not lista_processos:
        # logging.info("📭 Lista vazia. Nada a postar na API.")
        return False

    # Monta o payload final
    payload = {
        "fonte": "Onesid",
        "processos": lista_processos
    }

    logging.info("📤 Postando %s atualizações para API TwoTask em %s...", len(lista_processos), API_URL)

    headers = {'Content-Type': 'application/json'}
    last_error = None

    for tentativa in range(1, API_RETRIES + 1):
        try:
            response = requests.post(
                API_URL,
                json=payload,
                headers=headers,
                timeout=API_TIMEOUT,
            )

            if response.status_code in [200, 201]:
                logging.info("✅ POST enviado com sucesso na tentativa %s.", tentativa)
                return True

            last_error = (
                f"status={response.status_code} body={response.text[:500]}"
            )
            logging.error(
                "❌ Erro na API TwoTask na tentativa %s/%s: %s",
                tentativa,
                API_RETRIES,
                last_error,
            )
        except RequestException as e:
            last_error = str(e)
            logging.error(
                "❌ Falha de conexão ao postar na API na tentativa %s/%s: %s",
                tentativa,
                API_RETRIES,
                e,
            )

    logging.error("❌ Envio para API TwoTask falhou em definitivo: %s", last_error or "erro desconhecido")
    return False
