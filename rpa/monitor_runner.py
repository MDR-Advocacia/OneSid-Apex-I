import logging
import os

from selenium.common.exceptions import WebDriverException

from bd import database

from .exceptions import (
    LoginError,
    PortalElementNotFoundError,
    PortalNavigationError,
    PortalTimeoutError,
    SessionExpiredError,
    TemporaryPortalError,
)
from .rpa_runner import PortalRPARunner


class MonitorRPARunner(PortalRPARunner):
    def __init__(self, browser_factory=None, *, max_process_attempts=3, notifier=None):
        super().__init__(browser_factory=browser_factory, max_task_attempts=max_process_attempts)
        self.notifier = notifier
        self.reconcile_enabled = self._env_flag("RPA_MONITOR_RECONCILE_ENABLED", True)
        self.reconcile_limit = int(os.getenv("RPA_MONITOR_RECONCILE_LIMIT", "10"))
        self.reconcile_lookback_hours = int(
            os.getenv("RPA_MONITOR_RECONCILE_LOOKBACK_HOURS", "168")
        )
        self.notification_retry_batch_limit = int(
            os.getenv("API_NOTIFICACAO_RETRY_LIMIT", "10")
        )
        self.notification_max_attempts = int(
            os.getenv("API_NOTIFICACAO_MAX_TENTATIVAS", "5")
        )
        self.notification_retry_sending_after_minutes = int(
            os.getenv("API_NOTIFICACAO_RETRY_ENVIANDO_MINUTES", "10")
        )
        self.monitor_table_timeout = int(os.getenv("RPA_MONITOR_TABLE_TIMEOUT", "25"))
        self.monitor_batch_limit = int(os.getenv("RPA_MONITOR_BATCH_LIMIT", "50"))

    def run_cycle(self):
        logging.info("🔍 Buscando processos marcados para monitoramento.")
        database.inicializar_banco()

        processos_monitorados = database.buscar_processos_em_monitoramento(
            limit=self.monitor_batch_limit,
        )
        candidatos_reconciliacao = self._buscar_candidatos_reconciliacao(
            exclude_process_ids={processo["processo_id"] for processo in processos_monitorados}
        )

        if not processos_monitorados and not candidatos_reconciliacao:
            logging.info("✅ Nenhum processo em monitoramento no momento.")
            logging.info("🧩 Nenhum candidato recente para reconciliação de monitoramento.")
            if self.notifier:
                self._reenviar_notificacoes_pendentes()
            return

        try:
            self.ensure_browser()
        except Exception as exc:
            logging.error("❌ Não foi possível preparar o browser do monitor: %s", exc)
            return

        if not processos_monitorados:
            logging.info("✅ Nenhum processo em monitoramento no momento.")
        else:
            logging.info("📋 Encontrados %s processos para verificar.", len(processos_monitorados))

        for processo in processos_monitorados:
            notificacoes = self._processar_processo(processo)
            if notificacoes and self.notifier:
                self._enviar_notificacoes(notificacoes)

        self._reconciliar_falsos_negativos(candidatos_reconciliacao)

        if self.notifier:
            self._reenviar_notificacoes_pendentes()

        logging.info("🏁 Ciclo de monitoramento finalizado.")

    def _processar_processo(self, processo):
        cnj = processo["cnj"]
        npj = processo.get("npj")
        logging.info("⚙️ Verificando processo: %s (NPJ: %s)", cnj, npj or "N/D")

        try:
            return self._processar_processo_com_retry(processo)
        except Exception as exc:
            logging.error("❌ Falha definitiva ao monitorar %s: %s", cnj, exc)
            return []

    def _processar_processo_com_retry(self, processo):
        cnj = processo["cnj"]
        last_error = None

        for tentativa in range(1, self.max_task_attempts + 1):
            try:
                self.ensure_browser()
                self.auth_service.ensure_authenticated()
                return self._processar_processo_uma_vez(processo)
            except SessionExpiredError as exc:
                last_error = exc
                logging.warning(
                    "⚠️ Sessão expirada no monitor para %s na tentativa %s/%s: %s",
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
                    "❌ Falha de login no monitor para %s na tentativa %s/%s: %s",
                    cnj,
                    tentativa,
                    self.max_task_attempts,
                    exc,
                )
                if tentativa < self.max_task_attempts:
                    self.restart_browser("falha de login no monitor")
                    continue
            except (PortalTimeoutError, TemporaryPortalError, PortalNavigationError) as exc:
                last_error = exc
                logging.warning(
                    "⏳ Erro temporário/navegação no monitor para %s na tentativa %s/%s: %s",
                    cnj,
                    tentativa,
                    self.max_task_attempts,
                    exc,
                )
                if tentativa < self.max_task_attempts:
                    self._recuperar_navegacao_monitorada(processo)
                    continue
            except PortalElementNotFoundError as exc:
                last_error = exc
                logging.warning(
                    "🔎 Elemento não encontrado no monitor para %s na tentativa %s/%s: %s",
                    cnj,
                    tentativa,
                    self.max_task_attempts,
                    exc,
                )
                if tentativa < self.max_task_attempts:
                    self._reabrir_processo_monitorado(processo, preferir_fallback=True)
                    continue
            except WebDriverException as exc:
                last_error = exc
                logging.error(
                    "🧨 Erro de WebDriver no monitor para %s na tentativa %s/%s: %s",
                    cnj,
                    tentativa,
                    self.max_task_attempts,
                    exc,
                )
                if tentativa < self.max_task_attempts:
                    self.restart_browser("erro de webdriver no monitor")
                    continue
            except Exception as exc:
                last_error = exc
                logging.exception(
                    "❌ Erro inesperado no monitor para %s na tentativa %s/%s.",
                    cnj,
                    tentativa,
                    self.max_task_attempts,
                )
                if tentativa < self.max_task_attempts:
                    self.restart_browser("erro inesperado no monitor")
                    continue
            break

        raise last_error or RuntimeError("Falha ao monitorar processo sem erro identificado")

    def _processar_processo_uma_vez(self, processo):
        processo_id = processo["processo_id"]
        cnj = processo["cnj"]
        npj_atual = processo.get("npj")

        subsidios_antigos = database.recuperar_subsidios_anteriores(processo_id)
        npj_confirmado = self._reabrir_processo_monitorado(processo)

        if npj_confirmado and npj_confirmado != npj_atual:
            processo_id = database.salvar_processo(cnj, npj_confirmado) or processo_id
            processo["npj"] = npj_confirmado
            npj_atual = npj_confirmado
            logging.info("🧭 NPJ do monitor atualizado para %s no processo %s.", npj_confirmado, cnj)

        dados_novos, status_coleta = self._coletar_subsidios_monitorados()
        if status_coleta == "indisponivel":
            dados_novos, status_coleta, processo_id = self._tentar_coleta_por_npj_base(
                processo,
                processo_id,
                npj_atual,
                origem="abertura direta",
            )

        if status_coleta == "indisponivel" and npj_atual:
            logging.warning(
                "⚠️ Subsídios indisponíveis pela abertura direta do NPJ %s. Tentando consulta rápida pelo CNJ %s.",
                npj_atual,
                cnj,
            )
            npj_fallback = self._reabrir_processo_monitorado(
                processo,
                preferir_fallback=True,
            )
            if npj_fallback and npj_fallback != processo.get("npj"):
                processo_id = database.salvar_processo(cnj, npj_fallback) or processo_id
                processo["npj"] = npj_fallback
                logging.info(
                    "🧭 NPJ do monitor corrigido via consulta rápida para %s no processo %s.",
                    npj_fallback,
                    cnj,
                )

            dados_novos, status_coleta = self._coletar_subsidios_monitorados()
            if status_coleta == "indisponivel":
                dados_novos, status_coleta, processo_id = self._tentar_coleta_por_npj_base(
                    processo,
                    processo_id,
                    processo.get("npj"),
                    origem="consulta rápida",
                )

        if status_coleta == "indisponivel":
            erro = "Tabela de subsídios indisponível após abertura direta e consulta rápida"
            logging.warning("⚠️ %s para %s.", erro, cnj)
            database.registrar_monitoramento_falha(processo_id, erro)
            return []

        if status_coleta == "vazio":
            logging.info(
                "📭 Processo %s sem subsídios visíveis. Atualizando base e desligando monitoramento.",
                cnj,
            )
            database.salvar_lista_subsidios(processo_id, [])
            database.atualizar_status_monitoramento(processo_id, False)
            return []

        if not dados_novos:
            erro = "Tabela de subsídios sem linhas legíveis"
            logging.warning("⚠️ %s para %s.", erro, cnj)
            database.registrar_monitoramento_falha(processo_id, erro)
            return []

        notificacoes = self._montar_notificacoes(cnj, subsidios_antigos, dados_novos)
        database.salvar_lista_subsidios(processo_id, dados_novos)

        tem_pendencia = any(
            item["estado"].upper() == "SOLICITADO"
            for item in dados_novos
            if item.get("estado")
        )
        if not tem_pendencia:
            logging.info("🎉 Processo %s sem pendências. Desligando monitoramento.", cnj)
            database.atualizar_status_monitoramento(processo_id, False)

        return notificacoes

    def _coletar_subsidios_monitorados(self):
        return self.processo_service.coletar_lista_subsidios(
            tolerar_indisponivel=True,
            wait_timeout=self.monitor_table_timeout,
            incluir_status=True,
        )

    def _tentar_coleta_por_npj_base(self, processo, processo_id, npj_origem, *, origem):
        npj_base = self._extrair_npj_base(npj_origem)
        if not npj_base or npj_base == processo.get("npj"):
            return [], "indisponivel", processo_id

        cnj = processo["cnj"]
        logging.warning(
            "⚠️ Subsídios indisponíveis via %s para NPJ %s. Tentando NPJ base %s no processo %s.",
            origem,
            npj_origem,
            npj_base,
            cnj,
        )

        try:
            npj_confirmado = self.processo_service.abrir_processo_por_npj(npj_base)
        except (PortalElementNotFoundError, PortalNavigationError, PortalTimeoutError) as exc:
            logging.warning(
                "⚠️ Tentativa pelo NPJ base %s falhou para %s: %s",
                npj_base,
                cnj,
                exc,
            )
            return [], "indisponivel", processo_id

        dados_novos, status_coleta = self._coletar_subsidios_monitorados()
        if status_coleta != "indisponivel" and npj_confirmado != processo.get("npj"):
            processo_id = database.salvar_processo(cnj, npj_confirmado) or processo_id
            processo["npj"] = npj_confirmado
            logging.info(
                "🧭 NPJ do monitor corrigido para base %s no processo %s.",
                npj_confirmado,
                cnj,
            )

        return dados_novos, status_coleta, processo_id

    @staticmethod
    def _extrair_npj_base(npj):
        apenas_digitos = "".join(ch for ch in str(npj or "") if ch.isdigit())
        if len(apenas_digitos) <= 11:
            return ""
        return apenas_digitos[:-3]

    def _reabrir_processo_monitorado(self, processo, preferir_fallback=False):
        cnj = processo["cnj"]
        npj = processo.get("npj")

        if npj and not preferir_fallback:
            try:
                logging.info("↪️ Abrindo processo monitorado diretamente pelo NPJ %s.", npj)
                return self.processo_service.abrir_processo_por_npj(npj)
            except (PortalElementNotFoundError, PortalNavigationError, PortalTimeoutError) as exc:
                logging.warning(
                    "⚠️ Abertura direta por NPJ falhou para %s. Caindo para consulta rápida. Motivo: %s",
                    cnj,
                    exc,
                )

        logging.info("↩️ Reabrindo processo monitorado pela consulta rápida do CNJ %s.", cnj)
        self.processo_service.acessar_processo_consulta_rapida(cnj)
        return self.processo_service.extrair_e_acessar_npj()

    def _recuperar_navegacao_monitorada(self, processo):
        try:
            self.portal_client.refresh()
            self.auth_service.ensure_authenticated()
        except Exception as exc:
            logging.warning("⚠️ Refresh não recuperou a navegação do monitor: %s", exc)

        try:
            self._reabrir_processo_monitorado(processo)
        except Exception as exc:
            logging.warning("⚠️ Reabertura do processo monitorado também falhou: %s", exc)
            self.restart_browser("falha na recuperação de navegação do monitor")

    def _reconciliar_falsos_negativos(self, candidatos):
        if not candidatos:
            return

        logging.info(
            "🧩 Reconciliando %s processos recentes fora do monitoramento.",
            len(candidatos),
        )

        for processo in candidatos:
            self._reconciliar_processo(processo)

    def _buscar_candidatos_reconciliacao(self, *, exclude_process_ids):
        if not self.reconcile_enabled:
            return []

        return [
            processo
            for processo in database.buscar_processos_para_reconciliacao(
                limit=self.reconcile_limit,
                lookback_hours=self.reconcile_lookback_hours,
            )
            if processo["processo_id"] not in exclude_process_ids
        ]

    def _reconciliar_processo(self, processo):
        cnj = processo["cnj"]
        logging.info("🧭 Reconciliando processo fora do monitoramento: %s", cnj)

        try:
            self._reconciliar_processo_com_retry(processo)
        except Exception as exc:
            logging.warning("⚠️ Não foi possível reconciliar %s nesta rodada: %s", cnj, exc)

    def _reconciliar_processo_com_retry(self, processo):
        cnj = processo["cnj"]
        last_error = None

        for tentativa in range(1, self.max_task_attempts + 1):
            try:
                self.ensure_browser()
                self.auth_service.ensure_authenticated()
                self._reconciliar_processo_uma_vez(processo)
                return
            except SessionExpiredError as exc:
                last_error = exc
                logging.warning(
                    "⚠️ Sessão expirada na reconciliação de %s na tentativa %s/%s: %s",
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
                    "❌ Falha de login na reconciliação de %s na tentativa %s/%s: %s",
                    cnj,
                    tentativa,
                    self.max_task_attempts,
                    exc,
                )
                if tentativa < self.max_task_attempts:
                    self.restart_browser("falha de login na reconciliação")
                    continue
            except (PortalTimeoutError, TemporaryPortalError, PortalNavigationError) as exc:
                last_error = exc
                logging.warning(
                    "⏳ Erro temporário/navegação na reconciliação de %s na tentativa %s/%s: %s",
                    cnj,
                    tentativa,
                    self.max_task_attempts,
                    exc,
                )
                if tentativa < self.max_task_attempts:
                    self._recuperar_navegacao_monitorada(processo)
                    continue
            except PortalElementNotFoundError as exc:
                last_error = exc
                logging.warning(
                    "🔎 Elemento não encontrado na reconciliação de %s na tentativa %s/%s: %s",
                    cnj,
                    tentativa,
                    self.max_task_attempts,
                    exc,
                )
                if tentativa < self.max_task_attempts:
                    self._reabrir_processo_monitorado(processo, preferir_fallback=True)
                    continue
            except WebDriverException as exc:
                last_error = exc
                logging.error(
                    "🧨 Erro de WebDriver na reconciliação de %s na tentativa %s/%s: %s",
                    cnj,
                    tentativa,
                    self.max_task_attempts,
                    exc,
                )
                if tentativa < self.max_task_attempts:
                    self.restart_browser("erro de webdriver na reconciliação")
                    continue
            except Exception as exc:
                last_error = exc
                logging.exception(
                    "❌ Erro inesperado na reconciliação de %s na tentativa %s/%s.",
                    cnj,
                    tentativa,
                    self.max_task_attempts,
                )
                if tentativa < self.max_task_attempts:
                    self.restart_browser("erro inesperado na reconciliação")
                    continue
            break

        raise last_error or RuntimeError("Falha ao reconciliar processo sem erro identificado")

    def _reconciliar_processo_uma_vez(self, processo):
        processo_id = processo["processo_id"]
        cnj = processo["cnj"]
        npj_atual = processo.get("npj")

        npj_confirmado = self._reabrir_processo_monitorado(processo)
        if npj_confirmado and npj_confirmado != npj_atual:
            processo_id = database.salvar_processo(cnj, npj_confirmado) or processo_id
            processo["npj"] = npj_confirmado
            logging.info("🧭 NPJ reconciliado para %s no processo %s.", npj_confirmado, cnj)

        dados_novos, status_coleta = self._coletar_subsidios_monitorados()
        if status_coleta == "indisponivel":
            erro = "Reconciliação com tabela de subsídios indisponível"
            logging.warning("⚠️ %s para %s.", erro, cnj)
            database.registrar_monitoramento_falha(processo_id, erro)
            return

        if status_coleta == "vazio":
            logging.info(
                "📭 Reconciliação de %s sem subsídios visíveis. Mantendo fora do monitoramento.",
                cnj,
            )
            database.salvar_lista_subsidios(processo_id, [])
            database.atualizar_status_monitoramento(processo_id, False)
            return

        if not dados_novos:
            erro = "Reconciliação sem linhas de subsídio legíveis"
            logging.warning("⚠️ %s para %s.", erro, cnj)
            database.registrar_monitoramento_falha(processo_id, erro)
            return

        database.salvar_lista_subsidios(processo_id, dados_novos)

        tem_pendencia = any(
            item["estado"].upper() == "SOLICITADO"
            for item in dados_novos
            if item.get("estado")
        )

        if tem_pendencia:
            logging.info(
                "🚨 Reconciliação detectou itens 'SOLICITADO' no processo %s. Reativando monitoramento.",
                cnj,
            )
            database.atualizar_status_monitoramento(processo_id, True)

    def _enviar_notificacoes(self, notificacoes):
        notificacoes_registradas = database.registrar_notificacoes_twotask(notificacoes)
        if not notificacoes_registradas:
            logging.info("🧯 Nenhuma notificação nova para enviar ao TwoTask após deduplicação.")
            return

        self._postar_notificacoes_registradas(notificacoes_registradas)

    def _reenviar_notificacoes_pendentes(self):
        notificacoes = database.buscar_lotes_notificacoes_twotask_para_reenvio(
            limite_lotes=self.notification_retry_batch_limit,
            max_tentativas=self.notification_max_attempts,
            reenviar_enviando_apos_minutos=self.notification_retry_sending_after_minutes,
        )
        if not notificacoes:
            return

        lotes = {}
        for notificacao in notificacoes:
            lotes.setdefault(notificacao["_batch_ref"], []).append(notificacao)

        logging.info(
            "🔁 Reenviando %s lote(s) de aviso de retorno de subsídio pendentes.",
            len(lotes),
        )
        for lote in lotes.values():
            self._postar_notificacoes_registradas(lote)

    def _postar_notificacoes_registradas(self, notificacoes_registradas):
        dedupe_keys = [item["_dedupe_key"] for item in notificacoes_registradas]
        payload = [
            {
                "numero_processo": item["numero_processo"],
                "id_responsavel": item["id_responsavel"],
                "observacao": item["observacao"],
            }
            for item in notificacoes_registradas
        ]
        batch_key = self._batch_dedupe_key(dedupe_keys)

        try:
            try:
                enviado = self.notifier(payload, idempotency_key=batch_key)
            except TypeError:
                enviado = self.notifier(payload)
        except Exception as exc:
            logging.exception("❌ Erro inesperado ao enviar notificações para o TwoTask.")
            database.marcar_notificacoes_twotask_erro(dedupe_keys, str(exc))
            return

        if enviado:
            database.marcar_notificacoes_twotask_enviadas(dedupe_keys)
            return

        database.marcar_notificacoes_twotask_erro(
            dedupe_keys,
            "API TwoTask retornou falha no envio do lote",
        )

    @staticmethod
    def _montar_notificacoes(cnj, subsidios_antigos, dados_novos):
        itens_alterados = []
        for antigo in subsidios_antigos:
            estado_antigo = (antigo.get("estado") or "").upper()
            if estado_antigo != "SOLICITADO":
                continue

            correspondente_novo = next(
                (
                    item
                    for item in dados_novos
                    if item["item"] == antigo["item"]
                    and item["tipo"] == antigo["tipo"]
                    and item.get("data_limite") == antigo.get("data_limite")
                ),
                None,
            )

            if correspondente_novo and correspondente_novo["estado"].upper() != "SOLICITADO":
                dt_info = (
                    f" ({correspondente_novo.get('data_limite')})"
                    if correspondente_novo.get("data_limite")
                    else ""
                )
                itens_alterados.append(
                    f"{correspondente_novo['tipo']} "
                    f"{correspondente_novo['item']}{dt_info}: "
                    f"{correspondente_novo['estado']}"
                )

        if not itens_alterados:
            return []

        lista_interessados = database.buscar_todos_solicitantes_por_cnj(cnj)
        observacao_str = " | ".join(itens_alterados)

        if not lista_interessados:
            logging.warning(
                "⚠️ Alteração detectada no %s, mas nenhum solicitante foi encontrado.",
                cnj,
            )
            return []

        logging.info(
            "🔔 Notificando %s solicitantes sobre o processo %s.",
            len(lista_interessados),
            cnj,
        )

        notificacoes = []
        for solicitante_id in lista_interessados:
            try:
                id_final = int(solicitante_id)
            except (TypeError, ValueError):
                id_final = 0

            notificacoes.append(
                {
                    "numero_processo": cnj,
                    "id_responsavel": id_final,
                    "observacao": observacao_str,
                }
            )
        return notificacoes

    @staticmethod
    def _env_flag(name, default=False):
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _batch_dedupe_key(dedupe_keys):
        import hashlib

        material = "|".join(sorted(dedupe_keys))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()
