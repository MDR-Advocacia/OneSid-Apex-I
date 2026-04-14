import logging
import os
import time

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .exceptions import LoginError, PortalTimeoutError


class AuthService:
    LOGIN_URL = (
        "https://loginweb.bb.com.br/sso/XUI/?realm=/paj"
        "&goto=https://juridico.bb.com.br/wfj#login"
    )
    PORTAL_HOME_URL = "https://juridico.bb.com.br/paj/juridico/v2"
    LOGIN_URL_HINTS = ("loginweb.bb.com.br", "sso/xui")
    LOGIN_FIELD_IDS = ("idToken1", "idToken3")
    USERNAME_LOCATOR = (By.ID, "idToken1")
    PASSWORD_LOCATOR = (By.ID, "idToken3")
    USERNAME_SUBMIT_LOCATOR = (By.ID, "loginButton_0")
    PASSWORD_SUBMIT_LOCATOR = (
        By.CSS_SELECTOR,
        "input#loginButton_0[name='callback_4']",
    )

    def __init__(self, driver, *, timeout=None, login_timeout=None):
        self.driver = driver
        self.timeout = timeout or int(os.getenv("RPA_DEFAULT_TIMEOUT", "30"))
        self.login_timeout = login_timeout or int(os.getenv("RPA_LOGIN_TIMEOUT", "60"))
        self.login_stage_timeout = int(os.getenv("RPA_LOGIN_STAGE_TIMEOUT", "20"))
        self.login_stage_attempts = int(os.getenv("RPA_LOGIN_STAGE_ATTEMPTS", "3"))

    def ensure_authenticated(self, force_login=False):
        if not self._credentials_configured():
            raise LoginError("Credenciais BB_USUARIO/BB_SENHA não configuradas")

        if not force_login and self.is_session_active(probe=True):
            logging.info("✅ Sessão ativa reaproveitada.")
            return True

        logging.info("🔐 Autenticação necessária. Iniciando login no portal.")
        return self.login()

    def login(self):
        usuario = os.getenv("BB_USUARIO")
        senha = os.getenv("BB_SENHA")

        try:
            self.driver.get(self.LOGIN_URL)
            self.wait_for_document_ready(timeout=self.timeout)

            username_input = WebDriverWait(self.driver, self.login_timeout).until(
                EC.visibility_of_element_located(self.USERNAME_LOCATOR)
            )
            username_input.clear()
            username_input.send_keys(usuario)
            self._click_and_wait_password_stage()

            password_input = self._wait_for_password_input()
            password_input.clear()
            password_input.send_keys(senha)
            self._click_and_wait_authentication()

            self._wait_until_authenticated()
            logging.info("✅ Login confirmado com sucesso em %s", self.safe_current_url())
            return True
        except TimeoutException as exc:
            raise LoginError(
                self._extract_login_error() or "Tempo excedido no fluxo de autenticação",
                current_url=self.safe_current_url(),
                expected="URL autenticada e ausência dos campos de login",
            ) from exc

    def is_session_active(self, probe=False):
        if self._looks_authenticated():
            return True

        if not probe:
            return False

        logging.info("🔎 Validando sessão ativa no portal.")
        self.driver.get(self.PORTAL_HOME_URL)
        self.wait_for_document_ready(timeout=min(self.timeout, 20))
        return self._looks_authenticated()

    def is_login_page(self):
        current_url = self.safe_current_url().lower()
        if any(fragment in current_url for fragment in self.LOGIN_URL_HINTS):
            return True
        return self._login_fields_present()

    def wait_for_document_ready(self, *, timeout=None):
        timeout = timeout or self.timeout

        def document_ready(_driver):
            try:
                return _driver.execute_script("return document.readyState") == "complete"
            except WebDriverException:
                return False

        try:
            WebDriverWait(self.driver, timeout, poll_frequency=0.5).until(document_ready)
        except TimeoutException as exc:
            raise PortalTimeoutError(
                "Documento não atingiu estado 'complete'",
                current_url=self.safe_current_url(),
                expected="document.readyState == complete",
            ) from exc

    def safe_current_url(self):
        try:
            return self.driver.current_url or ""
        except WebDriverException:
            return ""

    def _click_when_clickable(self, locator, description):
        WebDriverWait(self.driver, self.login_timeout).until(
            EC.element_to_be_clickable(locator)
        ).click()
        logging.info("➡️ Clique realizado em %s", description)

    def _click_and_wait_password_stage(self):
        last_error = None

        for tentativa in range(1, self.login_stage_attempts + 1):
            self._click_when_clickable(
                self.USERNAME_SUBMIT_LOCATOR,
                "botão de avanço do usuário",
            )

            try:
                self._wait_for_password_input()
                return
            except TimeoutException as exc:
                last_error = exc
                login_error = self._extract_login_error()
                if login_error:
                    raise LoginError(
                        login_error,
                        current_url=self.safe_current_url(),
                        expected="campo de senha disponível após informar o usuário",
                    ) from exc

                if tentativa < self.login_stage_attempts and self._username_stage_active():
                    logging.warning(
                        "⚠️ Tela de senha não abriu após o usuário na tentativa %s/%s. Repetindo avanço.",
                        tentativa,
                        self.login_stage_attempts,
                    )
                    continue
                break

        raise last_error or TimeoutException()

    def _click_and_wait_authentication(self):
        last_error = None

        for tentativa in range(1, self.login_stage_attempts + 1):
            self._click_when_clickable(
                self.PASSWORD_SUBMIT_LOCATOR,
                "botão de envio da senha",
            )

            try:
                self._wait_until_authenticated(timeout=self.login_stage_timeout)
                return
            except TimeoutException as exc:
                last_error = exc
                login_error = self._extract_login_error()
                if login_error:
                    raise LoginError(
                        login_error,
                        current_url=self.safe_current_url(),
                        expected="autenticação concluída após envio da senha",
                    ) from exc

                if tentativa < self.login_stage_attempts and self._password_stage_active():
                    logging.warning(
                        "⚠️ Portal permaneceu na etapa de senha após o envio na tentativa %s/%s. Repetindo envio.",
                        tentativa,
                        self.login_stage_attempts,
                    )
                    continue
                break

        raise last_error or TimeoutException()

    def _wait_for_password_input(self):
        deadline = time.monotonic() + self.login_timeout

        while time.monotonic() < deadline:
            timeout = min(self.login_stage_timeout, max(1, int(deadline - time.monotonic())))
            try:
                return WebDriverWait(self.driver, timeout, poll_frequency=0.5).until(
                    EC.visibility_of_element_located(self.PASSWORD_LOCATOR)
                )
            except TimeoutException:
                if self._looks_authenticated():
                    raise LoginError(
                        "Fluxo autenticado sem apresentar a etapa de senha",
                        current_url=self.safe_current_url(),
                        expected="campo de senha visível",
                    )

                login_error = self._extract_login_error()
                if login_error:
                    raise LoginError(
                        login_error,
                        current_url=self.safe_current_url(),
                        expected="campo de senha visível",
                    )

                if not self._username_stage_active() and not self._password_stage_active():
                    self.wait_for_document_ready(timeout=min(self.timeout, 10))

        raise TimeoutException()

    def _wait_until_authenticated(self, *, timeout=None):
        timeout = timeout or self.login_timeout

        def authenticated(_driver):
            try:
                return self._looks_authenticated()
            except WebDriverException:
                return False

        WebDriverWait(self.driver, timeout, poll_frequency=0.5).until(authenticated)
        self.wait_for_document_ready(timeout=min(self.timeout, 20))

    def _looks_authenticated(self):
        current_url = self.safe_current_url().lower()
        if not current_url:
            return False
        if any(fragment in current_url for fragment in self.LOGIN_URL_HINTS):
            return False
        if "juridico.bb.com.br" not in current_url:
            return False
        return not self._login_fields_present()

    def _login_fields_present(self):
        for field_id in self.LOGIN_FIELD_IDS:
            try:
                if self.driver.find_elements(By.ID, field_id):
                    return True
            except WebDriverException:
                return False
        return False

    def _extract_login_error(self):
        selectors = [
            (By.CSS_SELECTOR, "[role='alert']"),
            (By.CSS_SELECTOR, ".alert"),
            (By.CSS_SELECTOR, ".error"),
            (By.CSS_SELECTOR, ".errors"),
        ]
        for by, selector in selectors:
            try:
                elements = self.driver.find_elements(by, selector)
            except WebDriverException:
                continue
            for element in elements:
                message = (element.text or "").strip()
                if message:
                    return message
        return None

    def _username_stage_active(self):
        try:
            elementos = self.driver.find_elements(*self.USERNAME_LOCATOR)
        except WebDriverException:
            return False
        return any(elemento.is_displayed() for elemento in elementos)

    def _password_stage_active(self):
        try:
            elementos = self.driver.find_elements(*self.PASSWORD_LOCATOR)
        except WebDriverException:
            return False
        return any(elemento.is_displayed() for elemento in elementos)

    @staticmethod
    def _credentials_configured():
        return bool(os.getenv("BB_USUARIO")) and bool(os.getenv("BB_SENHA"))
