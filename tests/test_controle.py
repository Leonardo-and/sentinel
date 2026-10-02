"""Controle F710: leitura não-bloqueante, borda de subida e parada de emergência."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from sentinel import ControleConfig, load_controle
from sentinel.controle import ControleF710, Evento


@dataclass
class Tecla:
    """O que a biblioteca ``inputs`` entrega: tipo, código e estado."""

    ev_type: str
    code: str
    state: int


class GamepadFalso:
    """Gamepad que entrega uma fila de eventos e depois fica vazio."""

    def __init__(self, eventos=()):
        self.eventos = list(eventos)

    def __iter__(self):
        return self

    def __next__(self):
        if not self.eventos:
            raise StopIteration
        return self.eventos.pop(0)


def _controle(eventos=(), **kwargs) -> ControleF710:
    return ControleF710(ControleConfig(**kwargs), gamepad=GamepadFalso(eventos))


def aperta(codigo="BTN_SOUTH"):
    return Tecla("KeyDown", codigo, 1)


def solta(codigo="BTN_SOUTH"):
    return Tecla("KeyUp", codigo, 0)


# --- mapeamento --------------------------------------------------------------
def test_botao_vira_cor():
    assert ControleConfig().acao("BTN_SOUTH") == "vermelho"


def test_botao_de_parada_tem_prioridade_sobre_o_mapa():
    cfg = ControleConfig(botoes={"BTN_START": "vermelho"}, parada=("BTN_START",))

    assert cfg.acao("BTN_START") == "parada"


def test_codigo_nao_mapeado_vem_vazio():
    assert ControleConfig().acao("BTN_THUMBR") is None


def test_acao_ignora_caixa():
    assert ControleConfig().acao("btn_south") == "vermelho"


def test_botao_sem_cor_e_erro_de_configuracao():
    with pytest.raises(ValueError, match="sem cor"):
        ControleConfig(botoes={"BTN_SOUTH": "  "})


def test_cor_parada_tem_que_ficar_em_parada():
    with pytest.raises(ValueError, match=r"\[controle.parada\]"):
        ControleConfig(botoes={"BTN_SOUTH": "parada"})


def test_parada_vazia_e_erro_de_configuracao():
    with pytest.raises(ValueError, match="parada vazio"):
        ControleConfig(parada=("",))


# --- leitura -----------------------------------------------------------------
def test_evento_de_subida_vira_cor():
    controle = _controle([aperta()])

    (evento,) = controle.eventos()

    assert evento == Evento(botao="BTN_SOUTH", acao="vermelho")
    assert not evento.parada


def test_soltar_botao_nao_gera_evento():
    controle = _controle([aperta(), solta()])

    assert len(controle.eventos()) == 1


def test_botao_que_ficou_preso_nao_repete_evento():
    controle = _controle([aperta(), aperta(), aperta()])

    assert len(controle.eventos()) == 1


def test_soltar_e_apertar_de_novo_volta_a_gerar_evento():
    controle = _controle([aperta(), solta(), aperta()])

    assert [e.acao for e in controle.eventos()] == ["vermelho", "vermelho"]


def test_parada_e_o_comeco_do_evento():
    controle = _controle([aperta("BTN_START")])

    (evento,) = controle.eventos()

    assert evento.acao == "parada" and evento.parada


def test_ignora_eventos_que_nao_sao_botao():
    controle = _controle([
        Tecla("Absolute", "ABS_X", 128),      # eixo analógico
        Tecla("SYN", "SYN_REPORT", 0),        # sincronismo
        aperta(),
    ])

    assert [e.botao for e in controle.eventos()] == ["BTN_SOUTH"]


def test_botao_desconhecido_avisa_com_acao_vazia():
    controle = _controle([aperta("BTN_THUMBR")])

    (evento,) = controle.eventos()

    assert evento.acao == ""
    assert not evento.parada


def test_sem_toque_devolve_vazio():
    assert _controle([]).eventos() == ()


def test_varios_botoes_no_mesmo_frame():
    controle = _controle([aperta("BTN_SOUTH"), aperta("BTN_NORTH")])

    assert [e.acao for e in controle.eventos()] == ["vermelho", "verde"]


# --- parada de emergência ----------------------------------------------------
def test_segurando_parada_ignora_botao_de_cor():
    controle = _controle([aperta("BTN_SOUTH")])
    controle.eventos()

    assert not controle.segurando_parada()


def test_segurando_parada_ativa_ate_soltar():
    controle = _controle([aperta("BTN_START")])
    controle.eventos()
    assert controle.segurando_parada()

    controle = ControleF710(ControleConfig(), gamepad=GamepadFalso([]))
    controle._presente["BTN_START"] = False     # o laço já consumiu o KeyUp
    assert not controle.segurando_parada()


def test_mapa_mostra_o_que_esta_configurado():
    mapa = _controle([]).mapa()

    assert mapa["BTN_SOUTH"] == "vermelho"
    assert mapa["BTN_START"] == "parada"
    assert mapa["BTN_SELECT"] == "parada"


# --- abertura ----------------------------------------------------------------
def test_abrir_falha_sem_controle_conectado(monkeypatch):
    class Dispositivos:
        gamepads = []

    class Modulo:
        devices = Dispositivos()

    monkeypatch.setattr("sentinel.controle._inputs", lambda: Modulo)

    with pytest.raises(RuntimeError, match="nenhum controle USB"):
        ControleF710(ControleConfig()).abrir()


def test_abrir_usa_o_primeiro_gamepad(monkeypatch):
    achado = GamepadFalso([aperta()])

    class Dispositivos:
        gamepads = [achado]

    class Modulo:
        devices = Dispositivos()

    monkeypatch.setattr("sentinel.controle._inputs", lambda: Modulo)
    controle = ControleF710(ControleConfig())

    assert controle.abrir() is controle
    assert controle.gamepad is achado
    assert [e.acao for e in controle.eventos()] == ["vermelho"]


def test_context_manager_nao_quebra_o_gamepad():
    gamepad = GamepadFalso([aperta()])

    with ControleF710(ControleConfig(), gamepad=gamepad) as controle:
        assert controle.gamepad is gamepad


# --- leitura do TOML ---------------------------------------------------------
def test_load_controle_sem_arquivo_usa_padrao(tmp_path, monkeypatch):
    monkeypatch.delenv("SENTINEL_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    cfg = load_controle()

    assert cfg.acao("BTN_SOUTH") == "vermelho"
    assert cfg.acao("BTN_START") == "parada"


def test_load_controle_le_o_toml(tmp_path, monkeypatch):
    (tmp_path / "sentinel.toml").write_text(
        '[controle]\nparada = ["BTN_MODE"]\n\n'
        '[controle.botoes]\n"BTN_TRIGGER" = "vermelho"\n'
    )
    monkeypatch.setenv("SENTINEL_CONFIG", str(tmp_path / "sentinel.toml"))

    cfg = load_controle()

    assert cfg.acao("BTN_TRIGGER") == "vermelho"
    assert cfg.acao("BTN_MODE") == "parada"
    assert cfg.acao("BTN_START") is None      # o padrão foi substituído


def test_load_controle_rejeita_parada_como_texto(tmp_path, monkeypatch):
    (tmp_path / "sentinel.toml").write_text('[controle]\nparada = "BTN_START"\n')
    monkeypatch.setenv("SENTINEL_CONFIG", str(tmp_path / "sentinel.toml"))

    with pytest.raises(ValueError, match="lista"):
        load_controle()


def test_load_controle_rejeita_chave_desconhecida(tmp_path, monkeypatch):
    (tmp_path / "sentinel.toml").write_text("[controle]\ndeadzone = 0.2\n")
    monkeypatch.setenv("SENTINEL_CONFIG", str(tmp_path / "sentinel.toml"))

    with pytest.raises(ValueError, match="deadzone"):
        load_controle()
