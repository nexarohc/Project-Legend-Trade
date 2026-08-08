"""app.mailer: best-effort SMTP sending with a logging fallback.

No real SMTP server is contacted here — `smtplib.SMTP` is monkeypatched with a
fake that records what it was asked to do, the same way the trading tests stub
a market data provider rather than hitting the network.
"""
from app.config import settings
from app.mailer import send_mail


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.started_tls = False
        self.login_args = None
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message):
        self.sent.append(message)


def test_send_mail_logs_instead_of_sending_when_smtp_is_not_configured(monkeypatch, caplog):
    monkeypatch.setattr(settings, "smtp_host", "")
    with caplog.at_level("WARNING", logger="dex.mailer"):
        result = send_mail("someone@example.com", "Subject", "Body text")
    assert result is False
    assert "someone@example.com" in caplog.text


def test_send_mail_sends_over_smtp_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_username", "user@example.com")
    monkeypatch.setattr(settings, "smtp_password", "app-password")
    monkeypatch.setattr(settings, "smtp_use_tls", True)
    monkeypatch.setattr(settings, "smtp_from", "dex@example.com")

    FakeSMTP.instances.clear()
    import smtplib

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    result = send_mail("someone@example.com", "Reset your password", "Link here")

    assert result is True
    assert len(FakeSMTP.instances) == 1
    server = FakeSMTP.instances[0]
    assert server.host == "smtp.example.com"
    assert server.started_tls is True
    assert server.login_args == ("user@example.com", "app-password")
    assert len(server.sent) == 1
    assert server.sent[0]["To"] == "someone@example.com"
    assert server.sent[0]["Subject"] == "Reset your password"


def test_send_mail_skips_login_when_no_username_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_username", "")
    monkeypatch.setattr(settings, "smtp_use_tls", False)

    FakeSMTP.instances.clear()
    import smtplib

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    result = send_mail("someone@example.com", "Subject", "Body")

    assert result is True
    server = FakeSMTP.instances[0]
    assert server.started_tls is False
    assert server.login_args is None


def test_send_mail_returns_false_on_smtp_failure(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")

    import smtplib

    class ExplodingSMTP(FakeSMTP):
        def send_message(self, message):
            raise smtplib.SMTPException("mailbox unavailable")

    monkeypatch.setattr(smtplib, "SMTP", ExplodingSMTP)

    result = send_mail("someone@example.com", "Subject", "Body")
    assert result is False
