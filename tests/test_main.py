"""A rotina de main.py envia a sequência correta e para ao final."""
from __future__ import annotations

import main
import pytest

from sentinel import Config
from sentinel.sobot import SoBot


@pytest.fixture
def bot_falso(monkeypatch, serial, tmp_path):
    """Faz ``main.SoBot`` abrir a FakeSerial em vez da porta real."""
    monkeypatch.setenv("SENTINEL_CONFIG", str(tmp_path / "sentinel.toml"))
    monkeypatch.setattr(
        main,
        "SoBot",
        lambda port, baud, verbose=False: SoBot(serial_obj=serial, verbose=verbose),
    )
    monkeypatch.setattr(
        main,
        "CFG",
        Config(port="/dev/fake", baud=57600),
    )
    return serial


def test_anda_para_frente_e_para(bot_falso):
    main.main()
    assert bot_falso.comandos == [
        "WP MT1 WD100",
        "WP MT2 WD100",
        "WP DW262",
        "MT0 CR1",
        "MT0 E1",
        "MT0 D500 AT1000 DT1000 V10",
        "MT0 E0",
    ]


def test_encerra_comando_mesmo_em_erro(bot_falso, monkeypatch):
    def move(*args, **kwargs):
        bot_falso.comandos.append("MT0 D500 AT1000 DT1000 V10")
        raise TimeoutError("robô não respondeu")

    monkeypatch.setattr(SoBot, "move", move)
    with pytest.raises(TimeoutError):
        main.main()
    assert bot_falso.comandos[-1] == "MT0 E0"


def test_ganhos_sao_enviados_quando_configurados(bot_falso, monkeypatch):
    monkeypatch.setattr(main, "CFG", Config(so=1.35, ca=2.87))
    main.main()
    assert bot_falso.comandos[3:4] == ["PG SO1,35 CA2,87"]
