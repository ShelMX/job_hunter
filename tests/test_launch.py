from unittest.mock import MagicMock

import httpx

from job_hunter.presentation import cli


def test_fr_010_launch_idempotent_when_server_running(monkeypatch) -> None:
    """Если порт уже отвечает — launch открывает браузер и не стартует web."""
    opened: list[str] = []
    web_started: list[dict] = []

    def fake_get(url: str, *, timeout: float = 1.0) -> object:
        class Resp:
            status_code = 200

        return Resp()

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **kw: opened.append(url))
    monkeypatch.setattr(cli, "_start_web", lambda *a, **kw: web_started.append({"args": a, "kwargs": kw}))

    cli.launch(port=8777)

    assert opened == ["http://127.0.0.1:8777/"]
    assert web_started == []


def test_fr_010_launch_starts_web_when_server_missing(monkeypatch) -> None:
    """Если порт молчит — launch поднимает web-путь."""
    opened: list[str] = []
    web_started: list[dict] = []

    monkeypatch.setattr(httpx, "get", lambda *a, **kw: (_ for _ in ()).throw(Exception("connection refused")))
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **kw: opened.append(url))

    fake_ctx = MagicMock()
    fake_ctx.settings = MagicMock()
    monkeypatch.setattr(cli, "_prepare", lambda verbose: fake_ctx)
    monkeypatch.setattr(
        cli,
        "_start_web",
        lambda ctx, host, port, *, no_scheduler, verbose: web_started.append(
            {"ctx": ctx, "host": host, "port": port, "no_scheduler": no_scheduler, "verbose": verbose}
        ),
    )

    cli.launch(port=8777, verbose=True)

    assert opened == []
    assert len(web_started) == 1
    assert web_started[0]["ctx"] is fake_ctx
    assert web_started[0]["host"] == "127.0.0.1"
    assert web_started[0]["port"] == 8777
    assert web_started[0]["no_scheduler"] is False
    assert web_started[0]["verbose"] is True


def test_server_alive_true_on_200(monkeypatch) -> None:
    class Resp:
        status_code = 200

    monkeypatch.setattr(httpx, "get", lambda *a, **kw: Resp())
    assert cli._server_alive(8777) is True


def test_server_alive_false_on_error(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: (_ for _ in ()).throw(httpx.ConnectError("nope")))
    assert cli._server_alive(8777) is False
