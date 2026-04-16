import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from requests import RequestException


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import bd.database as database


load_dotenv()

CLIENT_ID = os.environ.get("LEGAL_ONE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("LEGAL_ONE_CLIENT_SECRET")
BASE_URL = os.environ.get(
    "LEGAL_ONE_BASE_URL",
    "https://api.thomsonreuters.com/legalone/v1/api/rest",
)
REQUEST_TIMEOUT = int(os.getenv("LEGAL_ONE_REQUEST_TIMEOUT", "30"))
API_TOP_LIMIT = 30
PAGE_SIZE = min(int(os.getenv("LEGAL_ONE_PAGE_SIZE", "30")), API_TOP_LIMIT)
MAX_PAGES_PER_TYPE = int(os.getenv("LEGAL_ONE_MAX_PAGES_PER_TYPE", "100"))
REQUESTS_PER_MINUTE = max(1, int(os.getenv("LEGAL_ONE_REQUESTS_PER_MINUTE", "45")))
REQUEST_RETRIES = max(1, int(os.getenv("LEGAL_ONE_REQUEST_RETRIES", "3")))
MIN_REQUEST_INTERVAL_SECONDS = float(
    os.getenv(
        "LEGAL_ONE_MIN_REQUEST_INTERVAL_SECONDS",
        str(60 / REQUESTS_PER_MINUTE),
    )
)
RATE_LIMIT_BACKOFF_SECONDS = max(
    1.0,
    float(os.getenv("LEGAL_ONE_RATE_LIMIT_BACKOFF_SECONDS", "65")),
)
RETRY_BACKOFF_SECONDS = max(
    0.0,
    float(os.getenv("LEGAL_ONE_RETRY_BACKOFF_SECONDS", "5")),
)

TIPOS_TAREFA = [
    {"typeId": 30, "subTypeId": 1195},
    {"typeId": 28, "subTypeId": 961},
    {"typeId": 28, "subTypeId": 936}, # Apresentar  Planilha - BB Autor
    {"typeId": 26, "subTypeId": 1131}, # Solicitar  Monitoramento - ONESID
    {"typeId": 15, "subTypeId": 856}, # Solicitar Subsídio / Tarefa - Luna 12/01
    {"typeId": 13, "subTypeId": 843}, # Solicitar Subsídio / Tarefa - Luna 12/01
    {"typeId": 28, "subTypeId": 984}, # Solicitar Preposto
    {"typeId": 20, "subTypeId": 975}, # Obrigação de Fazer Complexa - Jonilson 28/01
    {"typeId": 20, "subTypeId": 1139} # Obrigação de Fazer Simples  - Jonilson 28/01
]

auth_token_cache = {"token": None, "expires_at": datetime.now(timezone.utc)}
last_request_at = 0.0

if PAGE_SIZE < 1:
    PAGE_SIZE = API_TOP_LIMIT
elif PAGE_SIZE > API_TOP_LIMIT:
    logging.warning(
        "⚠️ LEGAL_ONE_PAGE_SIZE acima do limite da API. Usando %s.",
        API_TOP_LIMIT,
    )
    PAGE_SIZE = API_TOP_LIMIT


def get_access_token():
    if auth_token_cache["token"] and datetime.now(timezone.utc) < (
        auth_token_cache["expires_at"] - timedelta(seconds=60)
    ):
        return auth_token_cache["token"]

    auth_url = "https://api.thomsonreuters.com/legalone/oauth?grant_type=client_credentials"
    response = requests.post(
        auth_url,
        auth=(CLIENT_ID, CLIENT_SECRET),
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    data = response.json()
    auth_token_cache["token"] = data["access_token"]
    auth_token_cache["expires_at"] = datetime.now(timezone.utc) + timedelta(
        seconds=int(data.get("expires_in", 1800))
    )
    return auth_token_cache["token"]


def make_api_request(url, params=None):
    last_error = None

    for tentativa in range(1, REQUEST_RETRIES + 1):
        token = get_access_token()
        _aguardar_cadencia_legal_one()

        try:
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params=params or {},
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 429:
                espera = _calcular_espera_rate_limit(response)
                last_error = f"429 Too Many Requests: {response.text[:300]}"
                logging.warning(
                    "⏳ [APEX] Legal One limitou requisições (429). Aguardando %.1fs antes de tentar novamente (%s/%s).",
                    espera,
                    tentativa,
                    REQUEST_RETRIES,
                )
                if tentativa < REQUEST_RETRIES:
                    time.sleep(espera)
                    continue

            if response.status_code >= 500 and tentativa < REQUEST_RETRIES:
                espera = RETRY_BACKOFF_SECONDS * tentativa
                last_error = f"status={response.status_code} body={response.text[:300]}"
                logging.warning(
                    "⏳ [APEX] Erro temporário Legal One (%s). Nova tentativa em %.1fs (%s/%s).",
                    response.status_code,
                    espera,
                    tentativa,
                    REQUEST_RETRIES,
                )
                if espera > 0:
                    time.sleep(espera)
                continue

            response.raise_for_status()
            return response.json()
        except RequestException as exc:
            last_error = exc
            if tentativa >= REQUEST_RETRIES:
                raise

            espera = RETRY_BACKOFF_SECONDS * tentativa
            logging.warning(
                "⏳ [APEX] Falha temporária na API Legal One. Nova tentativa em %.1fs (%s/%s): %s",
                espera,
                tentativa,
                REQUEST_RETRIES,
                exc,
            )
            if espera > 0:
                time.sleep(espera)

    raise RequestException(last_error or "Falha ao consultar API Legal One")


def _aguardar_cadencia_legal_one():
    global last_request_at

    if MIN_REQUEST_INTERVAL_SECONDS <= 0:
        return

    agora = time.monotonic()
    espera = MIN_REQUEST_INTERVAL_SECONDS - (agora - last_request_at)
    if espera > 0:
        time.sleep(espera)

    last_request_at = time.monotonic()


def _calcular_espera_rate_limit(response):
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(1.0, float(retry_after))
        except ValueError:
            pass

    return RATE_LIMIT_BACKOFF_SECONDS


def buscar_e_abastecer_fila():
    if not CLIENT_ID or not CLIENT_SECRET:
        logging.warning("⚠️ Credenciais Legal One ausentes.")
        return

    logging.info("📡 [APEX] Iniciando ciclo de busca incremental por tipo de tarefa.")

    total_novas = 0

    for config in TIPOS_TAREFA:
        total_novas += _processar_tipo_tarefa(config)

    logging.info("✅ [APEX] Ciclo concluído. Total de tarefas novas inseridas: %s", total_novas)


def _processar_tipo_tarefa(config):
    type_id = config["typeId"]
    sub_type_id = config["subTypeId"]
    checkpoint = database.obter_cursor_coleta(type_id, sub_type_id)

    logging.info(
        "🔎 [APEX] Buscando tarefas para typeId=%s / subTypeId=%s com checkpoint=%s.",
        type_id,
        sub_type_id,
        checkpoint if checkpoint is not None else "N/D",
    )

    total_novas = 0
    pagina = 1
    before_id = None
    maior_task_id_novo = None
    ciclo_completo = True
    parou_no_checkpoint = False
    litigation_cache = {}

    while pagina <= MAX_PAGES_PER_TYPE:
        try:
            tarefas = _buscar_pagina_tarefas(type_id, sub_type_id, before_id=before_id)
        except Exception as exc:
            logging.error(
                "❌ [APEX] Falha ao buscar página %s de typeId=%s / subTypeId=%s: %s",
                pagina,
                type_id,
                sub_type_id,
                exc,
            )
            ciclo_completo = False
            break

        if not tarefas:
            logging.info(
                "📭 [APEX] Nenhuma tarefa adicional para typeId=%s / subTypeId=%s.",
                type_id,
                sub_type_id,
            )
            break

        logging.info(
            "📋 [APEX] Página %s de typeId=%s / subTypeId=%s retornou %s tarefas.",
            pagina,
            type_id,
            sub_type_id,
            len(tarefas),
        )

        menor_task_id_da_pagina = None

        for task in tarefas:
            task_id = task.get("id")
            if not task_id:
                logging.warning(
                    "⚠️ [APEX] Tarefa sem id em typeId=%s / subTypeId=%s: %s",
                    type_id,
                    sub_type_id,
                    task,
                )
                ciclo_completo = False
                continue

            if menor_task_id_da_pagina is None or task_id < menor_task_id_da_pagina:
                menor_task_id_da_pagina = task_id

            if checkpoint is not None and task_id <= checkpoint:
                parou_no_checkpoint = True
                logging.info(
                    "🧭 [APEX] Checkpoint %s alcançado em typeId=%s / subTypeId=%s.",
                    checkpoint,
                    type_id,
                    sub_type_id,
                )
                break

            if maior_task_id_novo is None or task_id > maior_task_id_novo:
                maior_task_id_novo = task_id

            task_result = _processar_tarefa(task, litigation_cache)
            if task_result == "error":
                ciclo_completo = False
                continue

            if task_result == "inserted":
                total_novas += 1

        if parou_no_checkpoint:
            break

        if menor_task_id_da_pagina is None:
            ciclo_completo = False
            break

        if len(tarefas) < PAGE_SIZE:
            break

        before_id = menor_task_id_da_pagina
        pagina += 1

    if pagina > MAX_PAGES_PER_TYPE and not parou_no_checkpoint:
        logging.warning(
            "⚠️ [APEX] Limite de %s páginas atingido para typeId=%s / subTypeId=%s. Cursor não será avançado nesta rodada.",
            MAX_PAGES_PER_TYPE,
            type_id,
            sub_type_id,
        )
        ciclo_completo = False

    if ciclo_completo and maior_task_id_novo is not None:
        if database.atualizar_cursor_coleta(type_id, sub_type_id, maior_task_id_novo):
            logging.info(
                "💾 [APEX] Cursor atualizado para typeId=%s / subTypeId=%s: ultimo_task_id=%s.",
                type_id,
                sub_type_id,
                maior_task_id_novo,
            )
    elif not ciclo_completo:
        logging.warning(
            "⚠️ [APEX] Coleta incompleta para typeId=%s / subTypeId=%s. Cursor preservado para evitar perda de tarefas.",
            type_id,
            sub_type_id,
        )
    else:
        logging.info(
            "✅ [APEX] Nenhuma tarefa nova acima do checkpoint para typeId=%s / subTypeId=%s.",
            type_id,
            sub_type_id,
        )

    logging.info(
        "✅ [APEX] Finalizado typeId=%s / subTypeId=%s. Novas tarefas inseridas: %s.",
        type_id,
        sub_type_id,
        total_novas,
    )
    return total_novas


def _buscar_pagina_tarefas(type_id, sub_type_id, *, before_id=None):
    filtro = (
        f"(typeId eq {type_id} and subTypeId eq {sub_type_id}) "
        "and statusId eq 1 "
        "and relationships/any(r: r/linkType eq 'Litigation')"
    )
    if before_id is not None:
        filtro += f" and id lt {before_id}"

    params = {
        "$filter": filtro,
        "$expand": "relationships($select=id,linkId,linkType)",
        "$select": "id,finishedBy,relationships",
        "$top": PAGE_SIZE,
        "$orderby": "id desc",
    }

    url = f"{BASE_URL}/tasks"
    data = make_api_request(url, params)
    return data.get("value", [])


def _processar_tarefa(task, litigation_cache):
    task_id = task.get("id")
    solicitante_id = task.get("finishedBy")

    if task_id and database.tarefa_ja_na_fila(task_id):
        logging.info("↺ [APEX] Tarefa %s já existia na fila. Seguindo coleta.", task_id)
        return "duplicate"

    litigation_id = _extrair_litigation_id(task.get("relationships", []))

    if not litigation_id:
        logging.warning(
            "⚠️ [APEX] Tarefa %s sem relacionamento Litigation legível. Cursor será preservado.",
            task_id,
        )
        return "error"

    try:
        cnj = _buscar_cnj_por_litigation(litigation_id, litigation_cache)
    except Exception as exc:
        logging.error(
            "❌ [APEX] Falha ao resolver CNJ da tarefa %s via litigation %s: %s",
            task_id,
            litigation_id,
            exc,
        )
        return "error"

    if not cnj:
        logging.warning(
            "⚠️ [APEX] Tarefa %s sem CNJ retornado pela litigation %s. Cursor será preservado.",
            task_id,
            litigation_id,
        )
        return "error"

    insert_result = database.inserir_tarefa_na_fila(task_id, cnj, solicitante_id)
    if insert_result is None:
        logging.error(
            "❌ [APEX] Erro ao persistir tarefa %s na fila. Cursor será preservado.",
            task_id,
        )
        return "error"

    if insert_result:
        logging.info("➕ [APEX] Nova tarefa na fila: %s (CNJ: %s)", task_id, cnj)
        return "inserted"

    logging.info("↺ [APEX] Tarefa %s já existia na fila. Seguindo coleta.", task_id)
    return "duplicate"


def _extrair_litigation_id(relationships):
    for relationship in relationships or []:
        if relationship.get("linkType") == "Litigation":
            return relationship.get("linkId")
    return None


def _buscar_cnj_por_litigation(litigation_id, litigation_cache):
    if litigation_id in litigation_cache:
        return litigation_cache[litigation_id]

    lit_url = f"{BASE_URL}/litigations/{litigation_id}"
    lit_data = make_api_request(lit_url, {"$select": "identifierNumber"})
    cnj = lit_data.get("identifierNumber")
    litigation_cache[litigation_id] = cnj
    return cnj


if __name__ == "__main__":
    try:
        buscar_e_abastecer_fila()
    except RequestException as exc:
        logging.error("❌ [APEX] Erro HTTP durante coleta do Legal One: %s", exc)
