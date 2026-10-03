import pytest

from tools.detect_secrets_plugins.project_tokens import ProjectTokenAssignmentDetector


def detects(line: str) -> bool:
    detector = ProjectTokenAssignmentDetector()
    return bool(detector.analyze_line(filename="probe", line=line, line_number=1))


@pytest.mark.parametrize(
    "line",
    [
        'DERIV_API_TOKEN = "a1B2c3D4e5F6g7H"',
        "DERIV_API_TOKEN=a1B2c3D4e5F6g7H",
        "TELEGRAM_BOT_TOKEN=1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawE",
        'ANTHROPIC_API_KEY = "sk-ant-api03-Xk9pQ2rT7vLm4nB8wZ1cY6hJ3dF5gS0aE"',
        "client_secret: 'abcdefgh12'",
        'token = "a1B2c3D4e5F6g7H"',
        '    "db_password": "hunter2hunter2",',
    ],
)
def test_literal_secret_assignment_is_detected(line: str) -> None:
    assert detects(line)


@pytest.mark.parametrize(
    "line",
    [
        "DERIV_API_TOKEN=",
        "DERIV_API_TOKEN=   # one per environment",
        "token = settings.deriv_api_token",
        'deriv_api_token: SecretStr = Field(alias="DERIV_API_TOKEN")',
        'os.environ["DERIV_API_TOKEN"]',
        "TRADING_MODE=SIGNAL",
        "DATABASE_URL=sqlite:///./data/tradingagent.db",
        'token = "short"',
    ],
)
def test_non_secret_is_ignored(line: str) -> None:
    assert not detects(line)
