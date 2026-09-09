import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "app" / "boneco_windows_daemon.py"
LOCAL_API = ROOT / "core" / "boneco-local-api.py"
BROWSER_MANAGER = ROOT / "platform" / "windows" / "chatgpt_browser.py"
OVERLAY_BRIDGE = ROOT / "core" / "chatgpt-browser-overlay-bridge.py"
WINDOWS_RUNNER = ROOT / "core" / "boneco-clipboard-runner.ps1"
SPEC = importlib.util.spec_from_file_location("boneco_windows_daemon", MODULE)
daemon = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(daemon)
LOCAL_API_SPEC = importlib.util.spec_from_file_location("boneco_local_api", LOCAL_API)
local_api = importlib.util.module_from_spec(LOCAL_API_SPEC)
assert LOCAL_API_SPEC.loader is not None
LOCAL_API_SPEC.loader.exec_module(local_api)


class WindowsWebViewDaemonTests(unittest.TestCase):
    def test_prompt_is_windows_powershell_first(self):
        prompt = daemon.build_pilot_prompt("crie um arquivo html", "job-123")

        self.assertIn("BONECO RUN Windows", prompt)
        self.assertIn("bloco PowerShell", prompt)
        self.assertIn("```powershell", prompt)
        self.assertIn("Nao use Bash", prompt)
        self.assertIn("Nao altere Linux", prompt)

    def test_prompt_uses_computer_access_scope(self):
        prompt = daemon.build_pilot_prompt(
            "crie um arquivo em outra pasta",
            "job-123",
            {
                "project_dir": r"C:\Projeto",
                "access_scope": "computer",
            },
        )

        self.assertIn("Escopo de acesso configurado: este_computador", prompt)
        self.assertIn("Unidades detectadas neste computador", prompt)
        self.assertIn("Pode acessar discos locais, removiveis e unidades mapeadas", prompt)
        self.assertIn("Nao peca autorizacao para criar", prompt)
        self.assertIn("Peca confirmacao antes de apagar", prompt)
        self.assertIn("preserve acentos e c cedilha", prompt)
        self.assertIn("meta charset", prompt)
        self.assertIn("UTF-8", prompt)

    def test_prompt_uses_full_access_scope(self):
        prompt = daemon.build_pilot_prompt(
            "configure o projeto inteiro",
            "job-123",
            {
                "project_dir": r"C:\Projeto",
                "access_scope": "full",
            },
        )

        self.assertIn("Escopo de acesso configurado: full_acesso", prompt)
        self.assertIn("O usuario ativou Full acesso", prompt)
        self.assertIn("Nao peca confirmacao para criar, editar, instalar", prompt)

    def test_windows_folder_picker_does_not_depend_on_tkinter(self):
        source = MODULE.read_text(encoding="utf-8")

        self.assertIn("def select_folder_windows", source)
        self.assertIn("FolderBrowserDialog", source)
        self.assertIn("powershell_executable()", source)

        select_folder_windows = source[
            source.index("def select_folder_windows"):
            source.index("def select_folder()")
        ]
        self.assertNotIn("tkinter", select_folder_windows)

    def test_status_payload_has_main_sections(self):
        payload = daemon.system_status()

        self.assertTrue(payload["ok"])
        self.assertIn("version", payload)
        self.assertIn("config", payload)
        self.assertIn("machine", payload)
        self.assertIn("mobile", payload)
        self.assertIn("browser", payload)
        self.assertIn("local_api", payload)
        self.assertIn("job", payload)
        self.assertIn("last_job", payload)

    def test_null_characters_are_sanitized_before_paths_and_processes(self):
        self.assertEqual(daemon.clean_text("abc\x00def"), "abcdef")
        self.assertEqual(local_api.clean_text("api\x00local"), "apilocal")

        prompt = daemon.build_pilot_prompt(
            "crie\x00 arquivo",
            "job-123",
            {
                "project_dir": str(ROOT) + "\x00",
                "access_scope": "computer",
                "project_target_url": "http://127.0.0.1:3000\x00",
            },
        )

        self.assertNotIn("\x00", prompt)
        self.assertIn("crie arquivo", prompt)

    def test_local_api_uses_windows_webview_config(self):
        old_windows_config = local_api.WINDOWS_CONFIG_FILE
        old_project_dir_file = local_api.PROJECT_DIR_FILE
        old_project_config_file = local_api.PROJECT_CONFIG_FILE
        old_default_project_dir = local_api.DEFAULT_PROJECT_DIR

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "Projeto"
            project_dir.mkdir()
            config_file = root / "windows-webview-config.json"
            config_file.write_text(
                json.dumps(
                    {
                        "project_dir": str(project_dir),
                        "project_target_url": "http://127.0.0.1:3000",
                        "chatgpt_project_url": "https://chatgpt.com/g/project",
                        "chatgpt_api_url": "https://chatgpt.com/g/api",
                    }
                ),
                encoding="utf-8",
            )

            try:
                local_api.WINDOWS_CONFIG_FILE = config_file
                local_api.PROJECT_DIR_FILE = root / "project_dir"
                local_api.PROJECT_CONFIG_FILE = root / "project-config.json"
                local_api.DEFAULT_PROJECT_DIR = project_dir

                self.assertEqual(local_api.configured_project_dir(), str(project_dir.resolve()))
                self.assertEqual(local_api.configured_project_url(str(project_dir)), "http://127.0.0.1:3000")
                self.assertEqual(local_api.configured_chatgpt_url("project"), "https://chatgpt.com/g/project")
                self.assertEqual(local_api.configured_chatgpt_url("api"), "https://chatgpt.com/g/api")
            finally:
                local_api.WINDOWS_CONFIG_FILE = old_windows_config
                local_api.PROJECT_DIR_FILE = old_project_dir_file
                local_api.PROJECT_CONFIG_FILE = old_project_config_file
                local_api.DEFAULT_PROJECT_DIR = old_default_project_dir

    def test_mobile_agent_uses_configured_gateway(self):
        old_token_value = daemon.token_value
        old_config = daemon.config
        old_start_background = daemon.start_background
        calls = {}

        def fake_start_background(*args, **kwargs):
            calls["args"] = args
            calls["kwargs"] = kwargs
            return 12345

        try:
            daemon.token_value = lambda: "token"
            daemon.config = lambda: {"gateway_url": "https://run.example.com"}
            daemon.start_background = fake_start_background

            result = daemon.start_mobile_agent()

            self.assertTrue(result["ok"])
            self.assertEqual(result["gateway_url"], "https://run.example.com")
            self.assertEqual(calls["kwargs"]["env"]["BONECO_AGENT_SERVER_URL"], "https://run.example.com")
        finally:
            daemon.token_value = old_token_value
            daemon.config = old_config
            daemon.start_background = old_start_background

    def test_update_manifest_reports_available_version(self):
        result = daemon.update_payload_from_manifest(
            {
                "version": "0.2.1",
                "download_url": "https://github.com/golgol2/boneco-run-windows/releases/download/v0.2.1/BONECO_RUN_WINDOWS_INSTALLER.exe",
                "notes": ["correcao da api", "correcao do mobile"],
            },
            "https://raw.githubusercontent.com/golgol2/boneco-run-windows/main/update/latest.json",
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["current_version"], daemon.APP_VERSION)
        self.assertEqual(result["latest_version"], "0.2.1")
        self.assertIn("correcao da api", result["notes"])

    def test_final_job_result_is_extracted_for_overlay_feedback(self):
        log_text = """
job_status=done
----- FINAL RESPONSE -----
# BONECO_JOB_DONE
# BONECO_JOB_ID: job-123
# BONECO_SUMMARY
Foram encontradas 7 pastas na Area de Trabalho.
# BONECO_VALIDATION
Contagem feita com Get-ChildItem e exit_code 0.
----- END FINAL RESPONSE -----
"""

        result = daemon.parse_final_job_result(log_text)

        self.assertTrue(result["available"])
        self.assertEqual(result["job_id"], "job-123")
        self.assertEqual(result["state"], "completed")
        self.assertIn("7 pastas", result["summary"])
        self.assertIn("exit_code 0", result["validation"])

    def test_required_files_exist(self):
        required = [
            "app/web/index.html",
            "app/web/style.css",
            "app/web/app.js",
            "app/web/assets/boneco-run.ico",
            "app/web/assets/boneco-run.png",
            "assets/boneco-run.ico",
            "tray/boneco_tray.ps1",
            "Start-BonecoRunWindows.cmd",
            "Start-BonecoRunWindows.vbs",
            "Start-BonecoRunTray.vbs",
            "setup.cmd",
            "setup-preinstall.ps1",
            "install.ps1",
            "TERMOS-PT-BR.txt",
            "core/boneco-job-controller.py",
            "core/boneco-device-agent.py",
            "core/boneco-local-api.py",
            "core/boneco-clipboard-runner.ps1",
            "core/chatgpt-browser-adapter.py",
            "core/chatgpt-browser-overlay.py",
            "core/chatgpt-browser-overlay-bridge.py",
            "core/chatgpt-browser-read.py",
            "core/chatgpt-browser-send.py",
            "platform/windows/chatgpt_browser.py",
        ]

        missing = [name for name in required if not (ROOT / name).is_file()]
        self.assertEqual(missing, [])

    def test_installer_uses_single_file_bootstrap_contract_and_icon(self):
        setup = (ROOT / "setup.cmd").read_text(encoding="utf-8")
        preinstall = (ROOT / "setup-preinstall.ps1").read_text(encoding="utf-8")
        install = (ROOT / "install.ps1").read_text(encoding="utf-8")
        tray = (ROOT / "tray" / "boneco_tray.ps1").read_text(encoding="utf-8")
        terms = (ROOT / "TERMOS-PT-BR.txt").read_text(encoding="utf-8")

        self.assertIn("%LOCALAPPDATA%\\BonecoRunWindows", setup)
        self.assertIn('"%~dp0."', setup)
        self.assertIn("setup-preinstall.ps1", setup)
        self.assertIn("robocopy", setup)
        self.assertIn(":RUN_INSTALL", setup)
        self.assertIn("[switch]$AcceptTerms", install)
        self.assertIn("Validacao do daemon falhou", install)
        self.assertIn("Digite ACEITO", install)
        self.assertIn("assets\\boneco-run.ico", install)
        self.assertIn("assets\\boneco-run.ico", tray)
        self.assertIn("BONECO_RUN_WINDOWS_DAEMON", preinstall)
        self.assertIn("boneco_windows_daemon.py", preinstall)
        self.assertIn("I Data Tecnologie", terms)
        self.assertIn("22.045.469/0001-80", terms)

    def test_project_browser_defaults_to_wide_window(self):
        source = BROWSER_MANAGER.read_text(encoding="utf-8")

        self.assertIn('parser.add_argument("--width", type=int, default=1280)', source)
        self.assertIn('parser.add_argument("--height", type=int, default=720)', source)
        self.assertIn('--window-position=-32000,-32000', source)
        self.assertNotIn('default=430', source)
        self.assertNotIn('default=820', source)

    def test_overlay_left_side_is_clean_chat(self):
        source = OVERLAY_BRIDGE.read_text(encoding="utf-8")

        self.assertIn("chat-composer-left-20260907", source)
        self.assertIn('grid-template-rows: minmax(0, 1fr) auto;', source)
        self.assertIn('grid-template-columns: minmax(380px, 1fr) minmax(420px, 1fr);', source)
        self.assertIn("class=\"composer\"", source)
        self.assertIn("width: 100%;", source[source.index(".composer {"):source.index(".composer:focus-within")])
        self.assertIn("margin: 0;", source[source.index(".composer {"):source.index(".composer:focus-within")])
        self.assertIn("padding: 10px 16px 16px;", source)
        self.assertIn("padding: 18px 16px 24px;", source[source.index(".chat-feed"):source.index(".chat-feed::-webkit-scrollbar")])
        self.assertIn("margin-left: auto;", source[source.index(".message.user"):source.index(".message.system")])
        self.assertIn("margin-right: auto;", source[source.index(".message.system"):source.index(".message.warn")])
        self.assertIn("window.__bonecoRunReloadGuardInstalled", source)
        self.assertIn("key === 'f5'", source)
        self.assertIn("Page.addScriptToEvaluateOnNewDocument", source)
        self.assertIn(">Enviar</button>", source)
        self.assertNotIn("data-refresh", source)
        self.assertNotIn(">Atualizar</button>", source)
        self.assertIn("border: 0;", source[source.index(".chat-feed"):source.index(".chat-feed::-webkit-scrollbar")])

    def test_windows_runner_sets_utf8_defaults(self):
        source = WINDOWS_RUNNER.read_text(encoding="utf-8")

        self.assertIn("[Console]::OutputEncoding = $Utf8NoBom", source)
        self.assertIn('$PSDefaultParameterValues["Set-Content:Encoding"] = "UTF8"', source)
        self.assertIn('$PSDefaultParameterValues["Out-File:Encoding"] = "UTF8"', source)


if __name__ == "__main__":
    unittest.main()
