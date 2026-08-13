import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import undetected_chromedriver as uc

from .exceptions import BrowserInitializationError


def _env_flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class BrowserFactory:
    def __init__(self):
        self.version_main = self._resolve_version_main()
        self.profile_dir = os.getenv("RPA_CHROME_PROFILE_DIR")
        self.headless = _env_flag("RPA_HEADLESS", False)
        self.no_sandbox = _env_flag("RPA_CHROME_NO_SANDBOX", False)
        self.disable_gpu = _env_flag("RPA_CHROME_DISABLE_GPU", False)
        self.page_load_timeout = int(os.getenv("RPA_PAGE_LOAD_TIMEOUT", "60"))
        appdata = os.getenv("APPDATA")
        if appdata:
            self.cache_dir = Path(appdata) / "undetected_chromedriver"
        else:
            self.cache_dir = Path.home() / ".local" / "share" / "undetected_chromedriver"
        self.temp_profile_prefix = "onesid-rpa-chrome-"
        self._cleanup_stale_temp_profiles()

    def build_options(self):
        options = uc.ChromeOptions()
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")

        if self.headless:
            options.add_argument("--headless=new")
            options.add_argument("--window-size=1920,1080")
            # Porta de debug fica dinâmica (o undetected_chromedriver escolhe
            # uma porta livre). Porta fixa 9222 fazia um Chrome vazado impedir
            # toda inicialização seguinte, realimentando o ciclo de falhas.
        else:
            options.add_argument("--start-maximized")
        if self.no_sandbox:
            options.add_argument("--no-sandbox")
        if self.disable_gpu:
            options.add_argument("--disable-gpu")

        return options

    def create_browser(self):
        options = self.build_options()
        profile_path, persistent_profile = self._resolve_profile_path()
        try:
            return self._start_chrome(
                options,
                user_data_dir=profile_path,
                persistent_profile=persistent_profile,
            )
        except FileExistsError as exc:
            logging.warning(
                "⚠️ Cache do undetected_chromedriver ficou inconsistente. Limpando %s e tentando novamente.",
                self.cache_dir,
            )
            self._kill_chrome_tree(profile_path)
            self._clear_uc_cache()
            try:
                return self._start_chrome(
                    options,
                    user_data_dir=profile_path,
                    persistent_profile=persistent_profile,
                )
            except Exception as retry_exc:
                logging.exception("Falha ao inicializar o navegador após limpar o cache do driver.")
                self._kill_chrome_tree(profile_path)
                self._cleanup_profile_dir(profile_path, persistent_profile)
                raise BrowserInitializationError(
                    "Falha ao inicializar o navegador"
                ) from retry_exc
        except Exception as exc:
            # Se o uc.Chrome falhou DEPOIS de spawnar o processo (ex.: timeout
            # ao conectar na porta de debug), o Chrome fica órfão. Mata a
            # árvore associada ao perfil antes de qualquer nova tentativa.
            self._kill_chrome_tree(profile_path)
            if persistent_profile:
                logging.warning(
                    "⚠️ Falha ao abrir o Chrome com o perfil persistente em %s. Tentando perfil temporário.",
                    profile_path,
                )
                temp_profile_path = Path(tempfile.mkdtemp(prefix=self.temp_profile_prefix))
                temp_options = self.build_options()
                try:
                    return self._start_chrome(
                        temp_options,
                        user_data_dir=temp_profile_path,
                        persistent_profile=False,
                    )
                except Exception as retry_exc:
                    logging.exception(
                        "Falha ao inicializar o navegador após fallback para perfil temporário."
                    )
                    self._kill_chrome_tree(temp_profile_path)
                    self._cleanup_profile_dir(temp_profile_path, False)
                    raise BrowserInitializationError(
                        "Falha ao inicializar o navegador"
                    ) from retry_exc

            logging.exception("Falha ao inicializar o navegador com Chrome major %s.", self.version_main)
            self._cleanup_profile_dir(profile_path, persistent_profile)
            raise BrowserInitializationError("Falha ao inicializar o navegador") from exc

    def close_browser(self, driver):
        if driver is None:
            return

        profile_path = getattr(driver, "_rpa_user_data_dir", None)
        persistent_profile = getattr(driver, "_rpa_persistent_profile", True)

        try:
            driver.keep_user_data_dir = True
        except Exception:
            pass

        try:
            driver.quit()
        except Exception:
            logging.exception(
                "driver.quit() falhou. Forçando encerramento da árvore do Chrome."
            )
        finally:
            # Garantia dura: mesmo que o quit() tenha falhado ou deixado
            # processos para trás, a árvore do Chrome deste perfil morre aqui,
            # ANTES de neutralizarmos o finalizador do driver.
            self._kill_driver_service(driver)
            self._kill_chrome_tree(profile_path)
            self._neutralize_driver_finalizer(driver)
            if profile_path:
                self._cleanup_profile_dir(Path(profile_path), persistent_profile)

    def _start_chrome(self, options, *, user_data_dir, persistent_profile):
        logging.info("🌐 Inicializando Chrome com major version %s.", self.version_main)
        driver = uc.Chrome(
            options=options,
            use_subprocess=True,
            user_data_dir=str(user_data_dir),
            version_main=self.version_main,
        )
        driver.set_page_load_timeout(self.page_load_timeout)
        driver._rpa_user_data_dir = str(user_data_dir)
        driver._rpa_persistent_profile = persistent_profile
        return driver

    def _resolve_profile_path(self):
        if self.profile_dir:
            profile_path = Path(self.profile_dir).expanduser().resolve()
            profile_path.mkdir(parents=True, exist_ok=True)
            logging.info("🗂️ Perfil persistente do Chrome habilitado em %s", profile_path)
            return profile_path, True

        profile_path = Path(tempfile.mkdtemp(prefix=self.temp_profile_prefix))
        return profile_path, False

    def _resolve_version_main(self):
        env_version = os.getenv("RPA_CHROME_VERSION_MAIN", "").strip()
        if env_version:
            return int(env_version)

        detected_version = self._detect_installed_chrome_major()
        if detected_version:
            return detected_version

        logging.warning("⚠️ Não foi possível detectar a versão do Chrome. Usando fallback 144.")
        return 144

    @staticmethod
    def _detect_installed_chrome_major():
        if os.name != "nt":
            for chrome_command in (
                "google-chrome",
                "google-chrome-stable",
                "chromium",
                "chromium-browser",
            ):
                command_path = shutil.which(chrome_command)
                if not command_path:
                    continue

                try:
                    completed = subprocess.run(
                        [command_path, "--version"],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                except Exception:
                    continue

                version_tokens = completed.stdout.strip().split()
                if not version_tokens:
                    continue

                version = version_tokens[-1]
                if version and version[0].isdigit():
                    return int(version.split(".", 1)[0])

            return None

        possible_paths = [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        ]

        local_appdata = os.getenv("LOCALAPPDATA")
        if local_appdata:
            possible_paths.append(
                Path(local_appdata) / "Google" / "Chrome" / "Application" / "chrome.exe"
            )

        for chrome_path in possible_paths:
            if not chrome_path.exists():
                continue
            try:
                import win32api  # type: ignore

                info = win32api.GetFileVersionInfo(str(chrome_path), "\\")
                ms = info["FileVersionMS"]
                ls = info["FileVersionLS"]
                version = ".".join(
                    str(part)
                    for part in (
                        ms >> 16,
                        ms & 0xFFFF,
                        ls >> 16,
                        ls & 0xFFFF,
                    )
                )
                return int(version.split(".", 1)[0])
            except Exception:
                pass
            try:
                completed = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        f"(Get-Item '{chrome_path}').VersionInfo.ProductVersion",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                version = completed.stdout.strip()
                if version:
                    return int(version.split(".", 1)[0])
            except Exception:
                continue
        return None

    def _clear_uc_cache(self):
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir, ignore_errors=True)

    @staticmethod
    def _kill_chrome_tree(profile_path):
        """Mata qualquer processo Chrome associado ao perfil informado.

        Cada driver usa um --user-data-dir exclusivo, então o padrão só
        alcança a árvore do browser deste driver. SIGTERM primeiro; se algo
        sobreviver, SIGKILL. Sem isso, um quit() falho ou um uc.Chrome que
        morreu no meio da inicialização deixa Chromes órfãos acumulando até
        esgotar RAM/PIDs do container.
        """
        if not profile_path:
            return

        pattern = f"--user-data-dir={profile_path}"
        try:
            subprocess.run(
                ["pkill", "-TERM", "-f", pattern],
                check=False,
                capture_output=True,
            )
            time.sleep(2)
            survivors = subprocess.run(
                ["pgrep", "-f", pattern],
                check=False,
                capture_output=True,
            )
            if survivors.returncode == 0:
                logging.warning(
                    "🔪 Chrome do perfil %s sobreviveu ao SIGTERM. Aplicando SIGKILL.",
                    profile_path,
                )
                subprocess.run(
                    ["pkill", "-KILL", "-f", pattern],
                    check=False,
                    capture_output=True,
                )
        except Exception:
            logging.exception(
                "Falha ao encerrar árvore do Chrome do perfil %s.", profile_path
            )

    @staticmethod
    def _kill_driver_service(driver):
        """Encerra o processo do chromedriver deste driver, se ainda existir."""
        try:
            process = getattr(getattr(driver, "service", None), "process", None)
            if process is not None and process.poll() is None:
                process.kill()
        except Exception:
            pass

    @staticmethod
    def _neutralize_driver_finalizer(driver):
        try:
            driver.keep_user_data_dir = True
        except Exception:
            pass

        try:
            driver.quit = lambda *args, **kwargs: None
        except Exception:
            pass

        try:
            if hasattr(driver, "service"):
                driver.service.process = None
        except Exception:
            pass

        try:
            driver.browser_pid = None
        except Exception:
            pass

        try:
            driver.reactor = None
        except Exception:
            pass

        try:
            driver.patcher = None
        except Exception:
            pass

    @staticmethod
    def _cleanup_profile_dir(profile_path, persistent_profile):
        if persistent_profile or not profile_path:
            return

        path = Path(profile_path)
        if not path.exists():
            return

        time.sleep(0.5)
        for _ in range(10):
            try:
                shutil.rmtree(path, ignore_errors=False)
                return
            except FileNotFoundError:
                return
            except (OSError, PermissionError):
                time.sleep(0.5)

        logging.debug("Não foi possível remover o perfil temporário do Chrome agora: %s", path)

    def _cleanup_stale_temp_profiles(self):
        temp_root = Path(tempfile.gettempdir())
        if not temp_root.exists():
            return

        for candidate in temp_root.glob(f"{self.temp_profile_prefix}*"):
            if not candidate.is_dir():
                continue
            try:
                shutil.rmtree(candidate, ignore_errors=False)
            except (OSError, PermissionError):
                continue
