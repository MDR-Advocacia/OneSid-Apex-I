import logging
import os

from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from .exceptions import (
    PortalElementNotFoundError,
    PortalNavigationError,
    PortalTimeoutError,
    SessionExpiredError,
)


class PortalClient:
    def __init__(self, driver, auth_service, *, timeout=None):
        self.driver = driver
        self.auth_service = auth_service
        self.timeout = timeout or int(os.getenv("RPA_DEFAULT_TIMEOUT", "30"))

    def open_authenticated_url(self, url, *, description, expected_url_fragment=None, timeout=None):
        timeout = timeout or self.timeout
        self.auth_service.ensure_authenticated()

        logging.info("🚀 [NAVEGAÇÃO] %s", description)
        logging.info("   -> URL: %s", url)

        try:
            self.driver.get(url)
        except TimeoutException as exc:
            raise PortalTimeoutError(
                f"Tempo excedido ao abrir {description}",
                current_url=self.safe_current_url(),
                expected=url,
            ) from exc
        except WebDriverException as exc:
            raise PortalNavigationError(
                f"Falha ao abrir {description}",
                current_url=self.safe_current_url(),
                expected=url,
            ) from exc

        self.wait_for_document_ready(timeout=timeout)
        self.raise_if_login_redirect(expected=f"manter sessão ativa em {description}")

        if expected_url_fragment:
            try:
                WebDriverWait(self.driver, timeout, poll_frequency=0.5).until(
                    lambda _driver: expected_url_fragment in self.safe_current_url()
                )
            except TimeoutException as exc:
                raise PortalNavigationError(
                    f"URL final divergente após abrir {description}",
                    current_url=self.safe_current_url(),
                    expected=expected_url_fragment,
                ) from exc

        return True

    def wait_for_document_ready(self, *, timeout=None):
        self.auth_service.wait_for_document_ready(timeout=timeout or self.timeout)

    def refresh(self):
        logging.info("🔄 Refresh da página atual: %s", self.safe_current_url())
        try:
            self.driver.refresh()
        except TimeoutException as exc:
            raise PortalTimeoutError(
                "Tempo excedido durante refresh da página",
                current_url=self.safe_current_url(),
                expected="refresh concluído",
            ) from exc
        self.wait_for_document_ready(timeout=min(self.timeout, 20))
        self.raise_if_login_redirect(expected="sessão válida após refresh")

    def raise_if_login_redirect(self, *, expected=None):
        if self.auth_service.is_login_page():
            raise SessionExpiredError(
                "Portal redirecionou para login",
                current_url=self.safe_current_url(),
                expected=expected,
            )

    def wait_for_element_across_frames(
        self,
        locators,
        *,
        timeout=None,
        description,
        ignore_confidential=False,
    ):
        timeout = timeout or self.timeout

        def locate(_driver):
            for by, value in locators:
                element = self.find_element_across_frames(by, value)
                if not element:
                    continue
                text = (element.text or "").strip()
                if ignore_confidential and "#Confidencial" in text:
                    continue
                return element
            return False

        try:
            return WebDriverWait(
                self.driver,
                timeout,
                poll_frequency=0.5,
                ignored_exceptions=(
                    NoSuchElementException,
                    StaleElementReferenceException,
                    WebDriverException,
                ),
            ).until(locate)
        except TimeoutException as exc:
            self.raise_if_login_redirect(expected=description)
            raise PortalElementNotFoundError(
                f"Elemento não localizado: {description}",
                current_url=self.safe_current_url(),
                expected=description,
            ) from exc

    def find_element_across_frames(self, by, value):
        matches = self.find_elements_across_frames(by, value)
        return matches[0] if matches else None

    def find_elements_across_frames(self, by, value):
        self.driver.switch_to.default_content()
        matches = self.driver.find_elements(by, value)
        if matches:
            return matches

        for frame_path in self._iter_frame_paths(max_depth=3):
            try:
                if not self._switch_to_frame_path(frame_path):
                    continue
                matches = self.driver.find_elements(by, value)
                if matches:
                    return matches
            except (StaleElementReferenceException, WebDriverException):
                continue

        self.driver.switch_to.default_content()
        return []

    def safe_current_url(self):
        return self.auth_service.safe_current_url()

    def _iter_frame_paths(self, max_depth=3):
        queue = [[]]
        while queue:
            path = queue.pop(0)
            if len(path) >= max_depth:
                continue

            if not self._switch_to_frame_path(path):
                continue

            frames = self.driver.find_elements(By.TAG_NAME, "iframe")
            for index in range(len(frames)):
                next_path = path + [index]
                yield next_path
                queue.append(next_path)

    def _switch_to_frame_path(self, frame_path):
        self.driver.switch_to.default_content()
        for index in frame_path:
            frames = self.driver.find_elements(By.TAG_NAME, "iframe")
            if index >= len(frames):
                return False
            self.driver.switch_to.frame(frames[index])
        return True
