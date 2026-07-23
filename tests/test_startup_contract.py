from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_application_defaults_match_local_startup_ports():
    config = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert 'alias="APP_PORT")' in config
    assert 'default=18000, alias="APP_PORT"' in config
    assert "APP_PORT=18000" in env_example
    assert "POSTGRES_PORT=15432" in env_example


def test_start_script_derives_urls_from_the_application_port():
    script = (ROOT / "start.bat").read_text(encoding="utf-8")
    powershell_script = (ROOT / "start.ps1").read_text(encoding="utf-8")

    assert "start.ps1" in script
    assert "$appPort" in powershell_script
    assert "Get-NetTCPConnection" in powershell_script
    assert "1_zhixing.html" in powershell_script
    assert "POSTGRES_PORT" in powershell_script


def test_readme_documents_the_actual_local_ports():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "-p 15432:5432" in readme
    assert "http://localhost:18000/docs" in readme
