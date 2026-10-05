"""O painel é testado pela HTTP, sem navegador: é a mesma coisa que o JS faz.

Cada teste sobe um servidor em porta 0 (o sistema escolhe uma livre), fala com
ele por ``urllib`` e desliga. Se a página carregar e o estado chegar, o painel
funciona — o resto é canvas.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

from sentinel import SoBot, load_config
from sentinel.simulacao import Alvo, Arena, Montagem, Objeto, Parede, PlacaVirtual, exemplo
from sentinel.simulacao.painel import PALETA, Painel
from sentinel.simulacao.placa import RelogioReal


# --- helpers ----------------------------------------------------------------
@pytest.fixture
def arena() -> Arena:
    return Arena(
        largura=3000.0,
        altura=2000.0,
        objetos=(Objeto(2000.0, 1000.0, 300.0, 220.0, "vermelho", "pallet1"),),
        montagem=Montagem(500.0, 1000.0, 0.0),
    )


@pytest.fixture
def placa(arena: Arena) -> PlacaVirtual:
    return PlacaVirtual(arena)


@pytest.fixture
def painel(placa: PlacaVirtual, tmp_path) -> Painel:
    # ``arquivo`` explícito: sem ele o Painel acha o pista.toml do repo e um
    # POST /pista sem caminho salvaria em cima do arquivo versionado.
    with Painel(placa, porta=0, arquivo=tmp_path / "pista.toml") as p:
        yield p


def pegar(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read()


def pedir(painel: Painel, rota: str, corpo: dict | None = None) -> dict:
    req = urllib.request.Request(
        painel.url + rota,
        method="POST",
        data=json.dumps(corpo).encode() if corpo is not None else b"",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def esperar(p: PlacaVirtual, condicao, timeout: float = 5.0):
    """Espera a placa chegar num estado, em vez de dormir um tempo chutado."""
    fim = time.monotonic() + timeout
    while time.monotonic() < fim:
        estado = p.estado()
        if condicao(estado):
            return estado
        time.sleep(0.01)
    raise AssertionError(f"a condição não veio em {timeout}s (estado: {p.estado()})")


def bot_pronto(p: PlacaVirtual) -> SoBot:
    b = SoBot(serial_obj=p, verbose=False)
    b.configure_wheels(*load_config().wheel_params())
    b.command_return(True, "MT0")
    b.wait_for("CR OK MT0")
    b.wheels_enable(True)
    # ``MT0 E1`` é de fila e não tem ack: dá um instante para a thread da
    # placa pegá-lo, senão o painel lê o estado de antes do comando.
    p._esperar_muita(0.05)
    return b


# --- a página ---------------------------------------------------------------
def test_a_raiz_serve_a_pagina_com_o_canvas(painel: Painel):
    status, corpo = pegar(painel.url)
    assert status == 200
    assert b"<canvas" in corpo
    assert b"EventSource" in corpo, "o JS tem de assinar o fluxo de estado"


def test_rota_desconhecida_da_404(painel: Painel):
    with pytest.raises(urllib.error.HTTPError) as erro:
        pegar(painel.url + "nada")
    assert erro.value.code == 404


# --- o estado ---------------------------------------------------------------
def test_o_estado_tem_a_pista_o_robo_e_os_sensores(painel: Painel, placa: PlacaVirtual):
    bot_pronto(placa)
    with urllib.request.urlopen(painel.url + "estado", timeout=5) as r:
        estado = json.load(r)
    assert set(estado) == {
        "arquivo",
        "paleta",
        "pista",
        "robo",
        "sensores",
        "log",
        "tempo",
        "velocidade",
    }
    assert estado["pista"]["largura"] == 3000.0
    assert [o["id"] for o in estado["pista"]["objetos"]] == ["pallet1"]
    assert estado["robo"]["x"] == 500.0
    assert estado["robo"]["motores"] is True
    assert len(estado["pista"]["sonares"]) == 8
    assert estado["paleta"] == list(PALETA)


def test_o_estado_mostra_o_robo_indo_e_o_rastro_acumulando(painel: Painel, placa: PlacaVirtual):
    b = bot_pronto(placa)
    antes = painel.retrato()
    b.move(400, accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    depois = painel.retrato()
    assert depois["robo"]["x"] == pytest.approx(900.0, abs=1)
    assert len(depois["robo"]["trilha"]) > len(antes["robo"]["trilha"])


def test_o_estado_mostra_o_que_o_robo_esta_fazendo(painel: Painel, placa: PlacaVirtual):
    bot_pronto(placa)
    placa.write(b"LT RD255 GR0 BL0")
    placa.write(b"BZ E1")
    placa.write(b"DO5 E1")
    placa._esperar_muita(0.05)
    estado = painel.retrato()
    assert estado["robo"]["led"] == [255, 0, 0]
    assert estado["robo"]["buzzer"] is True
    assert estado["robo"]["reles"]["5"] is True
    assert any("LT" in linha for linha in estado["log"]), estado["log"]


def test_o_estado_mostra_o_pallet_segurado():
    """A garra pega, e o painel mostra o pallet carregado e os ímãs ligados.

    O elevador leva 7 s por curso (guia §3.22), então o relógio vai 25x para o
    teste não transformar 14 s de elevador em 14 s de espera.
    """
    perto = Arena(
        largura=3000.0,
        altura=2000.0,
        objetos=(Objeto(950.0, 1000.0, 300.0, 220.0, "vermelho", "pallet1"),),
        montagem=Montagem(500.0, 1000.0, 0.0),
    )
    placa = PlacaVirtual(perto, relogio=RelogioReal(25.0))
    with Painel(placa, porta=0) as painel:
        b = bot_pronto(placa)
        b.move(400, accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
        placa.write(b"EL UP")
        placa.write(b"DO5 E0")
        placa.write(b"EL DN")
        placa.write(b"DO5 E1")
        # A placa é assíncrona: espera a condição, não um tempo no relógio.
        esperar(placa, lambda e: e.segure is not None and e.imanes)
        estado = painel.retrato()
    assert estado["robo"]["segure"] == "pallet1", "a garra não pegou o pallet"
    assert estado["robo"]["imenes"] is True
    placa.close()


# --- o fluxo SSE ------------------------------------------------------------
def test_o_fluxo_manda_o_estado_repetidamente(painel: Painel):
    with urllib.request.urlopen(painel.url + "eventos", timeout=5) as fluxo:
        eventos = 0
        while eventos < 3:
            linha = fluxo.readline()
            if linha.startswith(b"data: "):
                json.loads(linha[6:])
                eventos += 1
    assert eventos == 3, "o fluxo parou de mandar estado"


# --- o editor ---------------------------------------------------------------
ARENA_EDITADA = {
    "largura": 4000.0,
    "altura": 3000.0,
    "cor_piso": "branco",
    "paredes": [{"x1": 0, "y1": 0, "x2": 4000, "y2": 0}],
    "objetos": [
        {
            "x": 1000,
            "y": 1000,
            "largura": 300,
            "altura": 220,
            "cor": "azul",
            "id": "caixa1",
            "pegavel": True,
        }
    ],
    "faixas": [{"x1": 500, "y1": 2500, "x2": 3500, "y2": 2500, "largura": 40}],
    "montagem": {"x": 600, "y": 600, "angulo_graus": 90},
}


def test_salvar_grava_o_toml_e_manda_a_simulacao_usar_a_pista_nova(
    painel: Painel, placa: PlacaVirtual, tmp_path
):
    destino = tmp_path / "pista.toml"
    resposta = pedir(painel, "pista", {"arena": ARENA_EDITADA, "caminho": str(destino)})
    assert resposta["ok"] is True
    assert resposta["caminho"] == str(destino)
    assert destino.exists()

    # A simulação já está usando a arena nova — é o que o editor promete.
    assert placa.arena.largura == 4000.0
    assert placa.arena.montagem == Montagem(600.0, 600.0, 90.0)
    assert [o.id for o in placa.arena.objetos] == ["caixa1"]
    assert placa._objetos.keys() == {"caixa1"}, "os objetos da placa têm que ser recarregados"


def test_o_toml_salvo_aguentava_um_round_trip(painel: Painel, tmp_path):
    from sentinel.simulacao import carregar, salvar

    destino = tmp_path / "pista.toml"
    pedir(painel, "pista", {"arena": ARENA_EDITADA, "caminho": str(destino)})
    de_bola = carregar(destino)
    assert de_bola.largura == 4000.0
    assert de_bola.montagem.angulo == 90.0
    assert de_bola.objetos[0].cor == "azul"
    assert de_bola.faixas[0].largura == 40.0
    assert len(de_bola.paredes) == 1
    salvar(de_bola, destino)  # não pode explodir


def test_recarregar_le_o_arquivo_de_novo(painel: Painel, tmp_path):
    destino = tmp_path / "pista.toml"
    pedir(painel, "pista", {"arena": ARENA_EDITADA, "caminho": str(destino)})
    resposta = pedir(painel, "recarregar")
    assert resposta["ok"] is True
    assert resposta["pista"]["largura"] == 4000.0
    assert [o["id"] for o in resposta["pista"]["objetos"]] == ["caixa1"]


def test_exemplo_troca_a_pista_no_vo(painel: Painel, placa: PlacaVirtual):
    pedir(painel, "pista", {"arena": ARENA_EDITADA, "caminho": "/tmp/nao-existe.toml"})
    resposta = pedir(painel, "exemplo")
    assert resposta["ok"] is True
    assert placa.arena.largura == exemplo().largura


@pytest.fixture
def painel_cheio(tmp_path) -> Painel:
    """Um painel de pista completa: parede, alvo, objeto e prateleira."""
    completa = Arena(
        largura=3000.0,
        altura=2000.0,
        paredes=(Parede(0.0, 0.0, 3000.0, 0.0), Parede(3000.0, 2000.0, 0.0, 2000.0)),
        objetos=(Objeto(2000.0, 1000.0, 300.0, 220.0, "vermelho", "pallet1"),),
        alvos=(Alvo(2600.0, 600.0, "vermelho", "prateleira_azul"),),
        montagem=Montagem(500.0, 1000.0, 0.0),
    )
    with Painel(PlacaVirtual(completa), porta=0, arquivo=tmp_path / "cheia.toml") as p:
        yield p


def test_o_editor_manda_de_volta_a_pista_inteira(painel_cheio: Painel):
    """O que o navegador manda no POST volta inteiro no GET /estado.

    Este é o round-trip do editor: parede encurtada, nome de alvo e objeto
    movido têm de sobreviver à ida e à volta. Sem ele, salvar a pista perderia
    as paredes em silêncio e ninguém perceberia até o robô atravessar a parede.
    """
    painel = painel_cheio
    estado = painel.retrato()
    pista = json.loads(json.dumps(estado["pista"]))
    pista["paredes"][1]["x2"] = 1200.0  # encurta a parede de baixo
    pista["alvos"][0]["nome"] = "prateleira_azul"
    pista["objetos"][0]["x"] += 120.0
    pedir(painel, "pista", {"arena": pista})
    volta = painel.retrato()["pista"]
    assert volta["paredes"][1]["x2"] == 1200.0
    assert [a["nome"] for a in volta["alvos"]] == ["prateleira_azul"]
    assert volta["objetos"][0]["x"] == pytest.approx(pista["objetos"][0]["x"])
    assert painel.arena.alvos[0].nome == "prateleira_azul"


def test_o_aceita_o_apelido_pista_no_post(painel: Painel):
    """O GET chama de ``pista`` e o POST de ``arena``; os dois têm de valer."""
    estado = painel.retrato()
    pedir(painel, "pista", {"pista": estado["pista"]})
    assert painel.retrato()["pista"]["objetos"]


def test_uma_chave_errada_nao_apaga_a_pista(painel: Painel, placa: PlacaVirtual):
    """Sem a chave, o painel responde 400 — nunca uma pista sem parede."""
    antes = len(placa.arena.paredes)
    with pytest.raises(urllib.error.HTTPError) as erro:
        pedir(painel, "pista", {"planeta": painel.retrato()["pista"]})
    assert erro.value.code == 400
    assert "arena" in json.load(erro.value)["erro"]
    assert len(placa.arena.paredes) == antes, "a pista foi apagada por um erro de digitação"


def test_pista_invalida_responde_400_com_o_motivo(painel: Painel):
    with pytest.raises(urllib.error.HTTPError) as erro:
        pedir(painel, "pista", {"arena": {"largura": -10}})
    assert erro.value.code == 400
    assert "inválida" in json.load(erro.value)["erro"]


def test_recarregar_sem_arquivo_da_erro_e_nao_derruba_o_painel(painel: Painel, tmp_path):
    painel.arquivo = tmp_path / "nao-existe.toml"
    with pytest.raises(urllib.error.HTTPError) as erro:
        pedir(painel, "recarregar")
    assert erro.value.code == 400
    # o servidor continua de pé
    assert pegar(painel.url)[0] == 200
