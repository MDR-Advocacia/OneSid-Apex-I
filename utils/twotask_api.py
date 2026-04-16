import requests
import logging
import os
import hashlib
from dotenv import load_dotenv
from requests import RequestException

load_dotenv()

# URL da API interna (TwoTask)
API_URL = os.getenv("API_NOTIFICACAO_URL", "http://twotask.mdr.local:8000/api/v1/tasks/batch-create")
API_TIMEOUT = int(os.getenv("API_NOTIFICACAO_TIMEOUT", "30"))
CONFIGURED_API_RETRIES = max(1, int(os.getenv("API_NOTIFICACAO_RETRIES", "1")))
ALLOW_POST_RETRY = os.getenv("API_NOTIFICACAO_PERMITIR_RETRY_POST", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def post_to_api(lista_processos, *, idempotency_key=None):
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

    batch_key = idempotency_key or _gerar_batch_key(lista_processos)
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": batch_key,
        "X-Idempotency-Key": batch_key,
    }
    last_error = None
    tentativas = CONFIGURED_API_RETRIES if ALLOW_POST_RETRY else 1

    if CONFIGURED_API_RETRIES > 1 and not ALLOW_POST_RETRY:
        logging.warning(
            "⚠️ API_NOTIFICACAO_RETRIES=%s ignorado para evitar duplicidade em POST não idempotente. "
            "Use API_NOTIFICACAO_PERMITIR_RETRY_POST=true apenas se o TwoTask respeitar Idempotency-Key.",
            CONFIGURED_API_RETRIES,
        )

    for tentativa in range(1, tentativas + 1):
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
                tentativas,
                last_error,
            )
        except RequestException as e:
            last_error = str(e)
            logging.error(
                "❌ Falha de conexão ao postar na API na tentativa %s/%s: %s",
                tentativa,
                tentativas,
                e,
            )

    logging.error("❌ Envio para API TwoTask falhou em definitivo: %s", last_error or "erro desconhecido")
    return False


def _gerar_batch_key(lista_processos):
    material = "|".join(
        sorted(
            f"{item.get('numero_processo', '')}|{item.get('id_responsavel', '')}|{item.get('observacao', '')}"
            for item in lista_processos
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
