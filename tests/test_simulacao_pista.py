"""A pista: modelo da arena, cores, geometria e o round-trip do TOML."""

from __future__ import annotations

import tomllib

import pytest

from sentinel.simulacao import pista
from sentinel.simulacao.pista import (
    Alvo,
    Arena,
    CameraArena,
    Faixa,
    Montagem,
    Objeto,
    Parede,
    RoboArena,
    carregar,
    exemplo,
    padrao_pista,
    salvar,
)


# --- cores e validação ------------------------------------------------------
def test_cor_desconhecida_e_rejeitada():
    with pytest.raises(ValueError, match="não é uma cor"):
        Objeto(0.0, 0.0, 100.0, 100.0, "lima", "p1")


def test_cor_e_normalizada_para_minuscula():
    assert Objeto(0.0, 0.0, 100.0, 100.0, "AZUL", "p1").cor == "azul"


def test_tamanho_zero_e_rejeitado():
    with pytest.raises(ValueError, match="positivo"):
        Objeto(0.0, 0.0, 0.0, 10.0, "branco", "p1")


def test_sala_sem_area_e_rejeitada():
    with pytest.raises(ValueError):
        Arena(largura=0.0, altura=1000.0)


def test_faixa_com_largura_zero_e_rejeitada():
    with pytest.raises(ValueError):
        Faixa(0.0, 100.0, 1000.0, 100.0, 0.0)


# --- geometria --------------------------------------------------------------
def test_objeto_tem_quatro_cantos_em_ordem():
    """Os lados fecham o polígono — a câmera e o sonar dependem disso."""
    lados = Objeto(100.0, 200.0, 40.0, 20.0, "branco", "p1").lados
    assert len(lados) == 4
    pontos = [p for lado in lados for p in lado]
    assert len(set(pontos)) == 4  # nenhum vértice repetido


def test_objeto_centro_e_extremos():
    o = Objeto(100.0, 200.0, 40.0, 20.0, "branco", "p1")
    assert (o.x, o.y) == (100.0, 200.0)
    assert (o.largura, o.altura) == (40.0, 20.0)


# --- robô e câmera -----------------------------------------------------------
def test_robo_tem_oito_sonares():
    assert [s.id for s in RoboArena().sonares] == list(range(1, 9))


def test_sonar_1_e_o_canto_esquerdo_da_frente():
    """No referencial do robô ``+y`` é a direita, então esquerda é ``y`` negativo."""
    s1 = exemplo().robo.sonares[0]
    assert s1.id == 1
    assert s1.x > 0, "o sonar 1 é frontal"
    assert s1.y < 0, "o sonar 1 é o da esquerda"
    assert s1.angulo < 0, "o feixe do sonar 1 aponta para a esquerda"


def test_sonar_4_aponta_para_a_direita():
    s4 = [s for s in exemplo().robo.sonares if s.id == 4][0]
    assert s4.y > 0
    assert s4.angulo == pytest.approx(90.0)


def test_sonares_2_e_6_ficam_no_eixo():
    por_id = {s.id: s for s in exemplo().robo.sonares}
    assert (por_id[2].x > 0, por_id[2].y, por_id[2].angulo) == (True, 0.0, 0.0)
    assert (por_id[6].x < 0, por_id[6].y, por_id[6].angulo) == (True, 0.0, 180.0)


def test_sensores_de_linha_1_esquerda_3_direita():
    linha = exemplo().robo.linha
    assert linha[1][1] < 0 < linha[3][1]
    assert linha[2][1] == 0


def test_camera_padrao_tem_fov_e_inclinacao():
    cam = CameraArena()
    assert 0 < cam.fov < 180
    assert cam.altura > 0


# --- a sala de exemplo -------------------------------------------------------
def test_exemplo_e_uma_sala_3x2():
    arena = exemplo()
    assert (arena.largura, arena.altura) == (3000.0, 2000.0)
    assert len(arena.paredes) == 4


def test_exemplo_tem_pallet_e_prateleira():
    arena = exemplo()
    assert [o.id for o in arena.objetos] == ["pallet1"]
    assert [a.cor for a in arena.alvos] == ["azul"]


def test_exemplo_comeca_dentro_da_sala():
    m = exemplo().montagem
    assert 0 < m.x < 3000.0
    assert 0 < m.y < 2000.0


# --- round-trip do TOML -----------------------------------------------------
def test_round_trip_preserva_a_arena(tmp_path):
    arena = exemplo()
    arquivo = tmp_path / "pista.toml"
    salvar(arena, arquivo)
    assert carregar(arquivo) == arena


def test_padrao_pista_e_o_texto_do_exemplo(tmp_path):
    arquivo = tmp_path / "padrao.toml"
    arquivo.write_text(padrao_pista(), encoding="utf-8")
    assert carregar(arquivo) == exemplo()


def test_salvar_gera_toml_legivel(tmp_path):
    arquivo = tmp_path / "pista.toml"
    salvar(exemplo(), arquivo)
    texto = arquivo.read_text(encoding="utf-8")
    assert "[pista]" in texto
    assert "pallet1" in texto
    assert "vermelho" in texto


def test_salvar_devolve_o_caminho_usado(tmp_path):
    destino = salvar(exemplo(), tmp_path / "sub" / "pista.toml")
    assert destino.is_file()


def test_carregar_usa_o_caminho_padrao_quando_dado_none(tmp_path, monkeypatch):
    monkeypatch.setenv(pista.ENV_PISTA, str(tmp_path / "minha.toml"))
    salvar(exemplo(), tmp_path / "minha.toml")
    assert carregar(None) == exemplo()


def test_env_para_arquivo_inexistente_e_erro(tmp_path, monkeypatch):
    monkeypatch.setenv(pista.ENV_PISTA, str(tmp_path / "sumiu.toml"))
    with pytest.raises(FileNotFoundError, match=pista.ENV_PISTA):
        carregar(None)


def test_carregar_sem_arquivo_devolve_a_pista_de_demonstracao(tmp_path):
    assert carregar(tmp_path / "nao-existe.toml") == exemplo()


def test_toml_incompleto_cai_no_padrao(tmp_path):
    """Uma pista incompleta ainda abre — com o que faltar no padrão."""
    arquivo = tmp_path / "p.toml"
    arquivo.write_text("[pista]\nlargura = 2000.0\n", encoding="utf-8")
    arena = carregar(arquivo)
    assert arena.largura == 2000.0
    assert arena.altura == exemplo().altura


def test_toml_invalido_levanta_erro(tmp_path):
    arquivo = tmp_path / "p.toml"
    arquivo.write_text("isto nao e toml = = =", encoding="utf-8")
    with pytest.raises(tomllib.TOMLDecodeError):
        carregar(arquivo)


def test_parede_tem_espessura():
    assert Parede(0.0, 0.0, 100.0, 0.0, espessura=25.0).espessura == 25.0


def test_arena_de_dict_completo():
    arena = Arena(
        largura=1000.0,
        altura=800.0,
        cor_piso="preto",
        paredes=[Parede(0.0, 0.0, 1000.0, 0.0)],
        objetos=[Objeto(500.0, 400.0, 100.0, 100.0, "verde", "g1")],
        alvos=[Alvo(100.0, 700.0, "vermelho", "a1")],
        faixas=[Faixa(0.0, 400.0, 1000.0, 400.0, 30.0)],
        montagem=Montagem(100.0, 100.0, 45.0),
    )
    assert arena.cor_piso == "preto"
    assert arena.montagem.angulo == 45.0
    assert arena.alvos[0].nome == "a1"
    assert arena.objetos[0].pegavel is True
