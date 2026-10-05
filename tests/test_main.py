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


def test_anda_a_rotina_de_passos_e_para(bot_falso):
    main.main()
    assert bot_falso.comandos == [
        "WP MT1 WD100",
        "WP MT2 WD100",
        "WP DW262",
        "MT0 CR1",
        "MT0 E1",
        "MT0 D700 AT1000 DT1000 V10",
        "MT0 D90 R AT1000 DT1000 V10",
        "MT0 D500 AT1000 DT1000 V10",
        "MT0 D45 L AT1000 DT1000 V10",
        "MT0 D-400 AT1000 DT1000 V10",
        "MT0 D600 AT1000 DT1000 V10",
        "MT0 E0",
    ]


def test_passos_desconhecido_e_erro_de_programa(bot_falso, monkeypatch):
    """Uma tabela editada errado tem de reclamar, não mandar lixo ao robô."""
    monkeypatch.setattr(main, "PASSOS", (("pular", 3),))
    with pytest.raises(ValueError, match="passo desconhecido"):
        main.main()
    assert bot_falso.comandos[-1] == "MT0 E0", "as rodas ficaram ligadas"


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


# --- a rotina na simulação ---------------------------------------------------
@pytest.mark.parametrize("pista", ["pista.toml", None])
def test_a_rotina_completa_sem_bater(pista):
    """A sequência inteira tem de caber na arena — em ``pista.toml`` e no exemplo.

    Se alguém aumentar um número em ``PASSOS``, este teste avisa antes de a
    rotina sair batendo na parede no pátio.
    """
    from sentinel.simulacao import PlacaVirtual, RelogioFake, carregar, exemplo

    arena = carregar(pista) if pista else exemplo()
    with PlacaVirtual(arena, relogio=RelogioFake()) as placa:
        with SoBot(serial_obj=placa, verbose=False) as bot:
            main._movimentos(bot)
        estado = placa.estado()
        assert estado.colidiu is False, f"a rotina bateu em {pista}"
        assert len(estado.trilha) >= len(main.PASSOS), "o rastro sumiu"
        assert not estado.motores_ligados, "as rodas ficaram ligadas"


def test_a_rotina_mesma_no_robô_e_na_simulação(bot_falso):
    """O mesmo ``PASSOS`` gera a mesma sequência de comandos nos dois."""
    comandos_reais = list(bot_falso.comandos)
    bot_falso.comandos.clear()

    from sentinel.simulacao import PlacaVirtual, RelogioFake, exemplo

    with PlacaVirtual(exemplo(), relogio=RelogioFake()) as placa:
        with SoBot(serial_obj=placa, verbose=False) as bot:
            main._movimentos(bot)
    comandos_sim = [c for c in placa.log if c.startswith(">> ")]

    def movimentos(comandos):
        return [c[3:] for c in comandos if c.startswith("MT0 D")]

    assert movimentos(comandos_reais) == movimentos(comandos_sim)


# --- roteamento da CLI --------------------------------------------------------
@pytest.fixture
def sem_robô(monkeypatch):
    """Substitui as rotinas por dublês e registra o que foi chamado."""
    chamados = []

    monkeypatch.setattr(main, "main", lambda: chamados.append(("rotina", ())))
    monkeypatch.setattr(main, "depurar_visao", lambda cor: chamados.append(("visao", cor)))
    monkeypatch.setattr(main, "listar_botoes", lambda: chamados.append(("botoes", ())))
    return chamados


def test_cli_sem_argumentos_roda_a_rotina(sem_robô):
    main.cli([])

    assert sem_robô == [("rotina", ())]


def test_cli_debug_de_visao_manda_a_cor(sem_robô):
    main.cli(["--debug-visao", "--cor", "azul"])

    assert sem_robô == [("visao", "azul")]


def test_cli_debug_de_visao_corr_eh_vermelho(sem_robô):
    main.cli(["--debug-visao"])

    assert sem_robô == [("visao", "vermelho")]


def test_cli_listar_botoes_nao_toca_no_robô(sem_robô):
    main.cli(["--listar-botoes"])

    assert sem_robô == [("botoes", ())]


def test_cli_trata_ctrl_c_sem_traceback(sem_robô, monkeypatch, capsys):
    def interrompe():
        raise KeyboardInterrupt

    monkeypatch.setattr(main, "listar_botoes", interrompe)
    with pytest.raises(SystemExit) as erro:
        main.cli(["--listar-botoes"])

    assert erro.value.code == 130
    assert "interrompido" in capsys.readouterr().out
