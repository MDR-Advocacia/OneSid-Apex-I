import logging
import os
import time

import requests

from .exceptions import OneLogUnavailableError


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_API_URL = "http://api-onelog.mdradvocacia.com"
HEARTBEAT_INTERVAL_SECONDS = 15 * 60

_last_heartbeat = 0
_current_sector = None

# Circuit breaker: após falhas consecutivas do OneLog, bloqueia novas
# tentativas por uma janela exponencial em vez de martelar a API (e de
# provocar restarts de browser em cascata nos runners).
_consecutive_failures = 0
_blocked_until = 0.0


def _backoff_base_seconds():
    return int(os.getenv("ONELOG_BACKOFF_BASE_SECONDS", "30"))


def _backoff_max_seconds():
    return int(os.getenv("ONELOG_BACKOFF_MAX_SECONDS", "900"))


def _register_failure():
    global _consecutive_failures, _blocked_until
    _consecutive_failures += 1
    delay = min(
        _backoff_max_seconds(),
        _backoff_base_seconds() * (2 ** (_consecutive_failures - 1)),
    )
    _blocked_until = time.time() + delay
    logging.warning(
        "🚧 OneLog em backoff por %ss após %s falha(s) consecutiva(s).",
        delay,
        _consecutive_failures,
    )


def _register_success():
    global _consecutive_failures, _blocked_until
    _consecutive_failures = 0
    _blocked_until = 0.0


def _ensure_not_blocked():
    remaining = _blocked_until - time.time()
    if remaining > 0:
        raise OneLogUnavailableError(
            f"OneLog em backoff por falhas consecutivas; "
            f"nova tentativa liberada em {int(remaining)}s"
        )


def is_configured():
    return bool(os.getenv("ONELOG_USERNAME")) and bool(os.getenv("ONELOG_PASSWORD"))


def get_session():
    _ensure_not_blocked()
    try:
        session_data = _get_session_inner()
    except PermissionError:
        # Credencial recusada: repetir não resolve, mas também não é
        # indisponibilidade — deixa o erro claro para o operador.
        raise
    except OneLogUnavailableError:
        raise
    except (requests.RequestException, TimeoutError, RuntimeError) as exc:
        _register_failure()
        raise OneLogUnavailableError(f"OneLog indisponível: {exc}") from exc

    _register_success()
    return session_data


def _get_session_inner():
    global _current_sector

    username = os.getenv("ONELOG_USERNAME")
    password = os.getenv("ONELOG_PASSWORD")
    api_url = os.getenv("ONELOG_API_URL", DEFAULT_API_URL).rstrip("/")
    user_agent = os.getenv("ONELOG_USER_AGENT", DEFAULT_USER_AGENT)

    if not username or not password:
        raise ValueError("Credenciais ONELOG_USERNAME/ONELOG_PASSWORD não configuradas")

    payload = {
        "username": username,
        "password": password,
        "user_agent": user_agent,
    }

    logging.info("🔑 Solicitando sessão autenticada ao OneLog.")
    login_response = requests.post(
        f"{api_url}/api/zerocore/login",
        json=payload,
        timeout=int(os.getenv("ONELOG_REQUEST_TIMEOUT", "15")),
    )
    if login_response.status_code in {401, 403}:
        message = _response_message(login_response)
        raise PermissionError(f"Acesso negado no OneLog: {message}")
    login_response.raise_for_status()

    login_data = login_response.json()
    _current_sector = login_data.get("setor")

    if login_data.get("status") == "sucesso":
        logging.info("✅ OneLog retornou sessão pronta.")
        return {
            "cookies": login_data.get("cookies", []),
            "user_agent": login_data.get("user_agent") or user_agent,
        }

    logging.info("⏳ Login enfileirado no OneLog. Aguardando conclusão.")
    max_attempts = int(os.getenv("ONELOG_STATUS_ATTEMPTS", "150"))
    poll_seconds = float(os.getenv("ONELOG_STATUS_INTERVAL_SECONDS", "2"))

    for _ in range(max_attempts):
        time.sleep(poll_seconds)
        status_response = requests.get(
            f"{api_url}/api/zerocore/status",
            params={"setor": _current_sector},
            timeout=int(os.getenv("ONELOG_REQUEST_TIMEOUT", "15")),
        )
        status_response.raise_for_status()
        status_data = status_response.json()

        if status_data.get("mensagem"):
            logging.info("[OneLog] %s", status_data["mensagem"])
        if status_data.get("erro"):
            raise RuntimeError("OneLog informou falha ao autenticar no portal")
        if status_data.get("concluido"):
            return _fetch_final_session(api_url, username, password, _current_sector, user_agent)

    raise TimeoutError("Tempo limite esgotado aguardando sessão do OneLog")


def renew_session():
    global _last_heartbeat

    if not is_configured():
        return False

    now = time.time()
    if now - _last_heartbeat < HEARTBEAT_INTERVAL_SECONDS:
        return True
    if not _current_sector:
        return False

    api_url = os.getenv("ONELOG_API_URL", DEFAULT_API_URL).rstrip("/")
    payload = {
        "username": os.getenv("ONELOG_USERNAME"),
        "password": os.getenv("ONELOG_PASSWORD"),
        "setor": _current_sector,
        "user_agent": os.getenv("ONELOG_USER_AGENT", DEFAULT_USER_AGENT),
    }

    try:
        response = requests.post(
            f"{api_url}/api/zerocore/renew",
            json=payload,
            timeout=int(os.getenv("ONELOG_REQUEST_TIMEOUT", "15")),
        )
        response.raise_for_status()
        _last_heartbeat = now
        logging.info("💓 Marcapasso OneLog enviado.")
        return True
    except Exception as exc:
        logging.warning("⚠️ Falha ao renovar sessão no OneLog: %s", exc)
        return False


def _fetch_final_session(api_url, username, password, sector, default_user_agent):
    payload = {
        "username": username,
        "password": password,
        "setor": sector,
    }
    response = requests.post(
        f"{api_url}/api/zerocore/session",
        json=payload,
        timeout=int(os.getenv("ONELOG_REQUEST_TIMEOUT", "15")),
    )
    response.raise_for_status()
    session_data = response.json()

    if session_data.get("status") != "sucesso":
        raise RuntimeError("OneLog não retornou uma sessão final válida")

    logging.info("✅ Cookies resgatados do OneLog.")
    return {
        "cookies": session_data.get("cookies", []),
        "user_agent": session_data.get("user_agent") or default_user_agent,
    }


def _response_message(response):
    try:
        data = response.json()
        return data.get("mensagem") or data.get("message") or response.text
    except Exception:
        return response.text
