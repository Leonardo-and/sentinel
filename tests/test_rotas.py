"""Testes da integração: a CLI e as três rotas rodam sem robô.

Estes testes não olham para a serial nem abrem porta — eles só querem
provar que o que a usuária digita na linha de comando chega ao fim.
"""

from __future__ import annotations

import sys

import main
import pytest
from rotas.desvio_sonar import DesvioSonar
from rotas.pallet import RotaPallet
from rotas.percurso_fixo import PercursoFixo

from sentinel import SoBot, load_config
from sentinel.simulacao import PlacaVirtual, RelogioFake


def bot_em(placa: PlacaVirtual) -> SoBot:
    """Um ``SoBot`` pronto: rodas calibradas, ``MT0`` ligado, motores ligados."""
    bot = SoBot(serial_obj=placa, verbose=False)
    bot.configure_wheels(*load_config().wheel_params())
    bot.command_return(True, "MT0")
    bot.wait_for("CR OK MT0")
    bot.wheels_enable(True)
    placa._esperar_muita(0.05)
    return bot


# ----------------------------------------------------------------------------
# a CLI
# ----------------------------------------------------------------------------
def test_simular_roda_a_rotina_sem_serial(capsys):
    main.cli(["--simular", "--velocidade", "50"])
    saida = capsys.readouterr().out
    assert "[sim]" in saida
    assert "MT0 D700" in saida, "a rotina não chegou a mandar o primeiro movimento"


def test_simular_aceita_uma_pista(capsys):
    main.cli(["--simular", "--pista", "pista.toml", "--velocidade", "50"])
    assert "3000x2000" in capsys.readouterr().out


def test_simular_com_painel_serve_a_pagina(capsys):
    main.cli(["--simular", "--painel", "--velocidade", "50", "--esperar", "0",
              "--porta", "0"])
    assert "[painel] http://127.0.0.1:" in capsys.readouterr().out


def test_uma_pista_inexistente_cai_para_o_exemplo(capsys):
    """``carregar`` é gentil: arquivo que não existe vira a arena de exemplo."""
    main.cli(["--simular", "--velocidade", "50", "--pista", "nao-existe-pista.toml"])
    assert "[sim]" in capsys.readouterr().out


# ----------------------------------------------------------------------------
# percurso fixo: o quadrado fecha
# ----------------------------------------------------------------------------
def test_o_quadrado_fecha_no_ponto_de_partida():
    percurso = PercursoFixo()
    placa = PlacaVirtual(percurso.arena(), relogio=RelogioFake())
    inicio = (placa.pose.x, placa.pose.y)
    percurso.rodar(bot_em(placa))
    assert abs(placa.pose.x - inicio[0]) < 1.0
    assert abs(placa.pose.y - inicio[1]) < 1.0
    placa.close()


def test_o_quadrado_desenha_um_trilho():
    percurso = PercursoFixo()
    placa = PlacaVirtual(percurso.arena(), relogio=RelogioFake())
    percurso.rodar(bot_em(placa))
    # 4 retas em zigue-zague: o canto vira um vértice do rastro
    assert len(placa.estado().trilha) >= 4
    placa.close()


# ----------------------------------------------------------------------------
# desvio de sonar: reage a um pilar na frente
# ----------------------------------------------------------------------------
def test_o_desvio_encontra_o_pilar_e_vira():
    rota = DesvioSonar()
    placa = PlacaVirtual(rota.arena(), relogio=RelogioFake())
    bot = bot_em(placa)
    acoes = [rota.passo(bot) for _ in range(8)]
    assert any(acao.startswith("desviou") for acao in acoes), acoes
    placa.close()


def test_o_lado_livre_mira_para_o_lado_mais_despejado():
    """Compara o **mais perto** de cada lado: o lado com folga mínima maior.

    O pior caso de cada lado é o que manda, porque é ele que morde primeiro.
    """
    rota = DesvioSonar()
    rota._ultimo = {4: 100.0, 5: 900.0, 7: 9999.0, 8: 9999.0}
    assert rota.lado_livre() == "left"
    rota._ultimo = {4: 9999.0, 5: 9999.0, 7: 900.0, 8: 100.0}
    assert rota.lado_livre() == "right"
    rota._ultimo = {4: 100.0, 5: 100.0, 7: 100.0, 8: 100.0}
    assert rota.lado_livre() in ("left", "right"), "empate é escolha da casa"


def test_sem_infinito_e_o_pior_caso_do_desvio():
    """``INF`` do driver é "não viu nada", não "muito perto"."""
    rota = DesvioSonar()
    rota._ultimo = {1: None, 2: None, 3: None}
    assert rota.precisa_desviar() is False


# ----------------------------------------------------------------------------
# pallet: pega, carrega e entrega
# ----------------------------------------------------------------------------
def test_o_pallet_chega_na_prateleira():
    rota = RotaPallet()
    placa = PlacaVirtual(rota.arena(), relogio=RelogioFake())
    bot = bot_em(placa)
    resumo = rota.rodar(bot, placa)
    placa._esperar_muita(0.5)
    estado = placa.estado()
    assert estado.segure is None, "o pallet continua na mão"
    assert estado.imanes is False, "o ímã ficou ligado"
    assert "entregue" in resumo
    placa.close()


def test_o_pallet_e_uma_etapa_do_mtodo_que_nao_existe():
    """``rodar`` sem a placa não pode quebrar: só não confere a entrega."""
    rota = RotaPallet()
    placa = PlacaVirtual(rota.arena(), relogio=RelogioFake())
    assert "sem conferir" in rota.rodar(bot_em(placa))
    placa.close()


@pytest.mark.parametrize("modulo", ["percurso_fixo", "desvio_sonar", "pallet"])
def test_toda_rota_tem_cli(modulo):
    """Toda rota roda com ``python -m rotas.<nome> --help``."""
    import importlib

    rota = importlib.import_module(f"rotas.{modulo}")
    with pytest.raises(SystemExit) as erro:
        rota.cli(["--help"])
    assert erro.value.code == 0


@pytest.mark.skipif(sys.platform == "win32", reason="rota usa path do repositório")
def test_a_pista_versionada_carrega_e_sobrevive_ao_round_trip():
    """O ``pista.toml`` do repo abre e volta idêntico ao salvar de novo.

    Não compara com ``exemplo()`` de propósito: a pista versionada é para ser
    editada, e um teste que exige ela igual à de demonstração quebra no dia
    que alguém mover uma parede de propósito. O que tem de valer é que
    carregar-e-salvar não perde nada.
    """
    from sentinel.simulacao import carregar, salvar

    arena = carregar("pista.toml")
    assert arena.objetos, "a pista versionada não tem objeto nenhum"
    assert arena.paredes, "a pista versionada não tem parede nenhuma"
    assert arena.alvos, "a pista versionada não tem alvo nenhum"

    uma = salvar(arena, "/tmp/sentinel-ida.toml").read_text()
    outra = salvar(carregar("/tmp/sentinel-ida.toml"), "/tmp/sentinel-volta.toml").read_text()
    assert uma == outra, "salvar e recarregar mudou a pista"
