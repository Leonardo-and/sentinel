"""Cinemática: perfil trapezoidal, reta/pivô/curva, sensores e colisão.

Tudo aqui é determinístico e sem ``sleep``: o tempo sai de :func:`duracao_ms`.
"""

from __future__ import annotations

import math

import pytest

from sentinel.simulacao import cinematica as cin
from sentinel.simulacao.cinematica import Ganhos, Pose, normalizar_graus
from sentinel.simulacao.pista import Alvo, Arena, Faixa, Montagem, Objeto, Parede, exemplo

RAD = cin.RAD_POR_GRAU
RAIO = Arena().robo.raio_corpo  # quanto o corpo precisa das bordas


def sonar(id_: int):
    return [s for s in exemplo().robo.sonares if s.id == id_][0]


def sala_com_parede_em(x: float, largura: float = 3000.0) -> Arena:
    """Sala com uma parede vertical em ``x`` e o robô bem longe das bordas."""
    return Arena(
        largura=largura,
        altura=2000.0,
        paredes=(Parede(x, 0.0, x, 2000.0),),
        montagem=Montagem(500.0, 300.0, 0.0),
    )


# --- convenção de eixos -----------------------------------------------------
def test_pose_na_tela_usa_angulo_horario():
    """Ângulo positivo gira para a direita (baixo da tela), como na câmera."""
    destino = cin.ir_para_frente(Pose.de_graus(0.0, 0.0, 90.0), 100.0)
    assert destino.y == pytest.approx(100.0), "90° leva para baixo (+y)"
    assert destino.x == pytest.approx(0.0, abs=1e-9)


def test_angulo_negativo_leva_para_cima():
    destino = cin.ir_para_frente(Pose.de_graus(0.0, 0.0, -90.0), 100.0)
    assert destino.y == pytest.approx(-100.0)


def test_de_graus_e_o_inverso_de_girada():
    pose = Pose.de_graus(10.0, 20.0, 45.0)
    assert pose.angulo == pytest.approx(math.radians(45.0))
    assert pose.girada(-math.radians(45.0)).angulo == pytest.approx(0.0)


# --- perfil trapezoidal ------------------------------------------------------
def test_reta_longa_tem_cruzeiro():
    """1000 mm a 100 mm/s com rampas de 1 s: 1 s + 9 s + 1 s = 11 s."""
    assert cin.duracao_ms(1000.0, 10, 1000, 1000) == pytest.approx(11_000.0, rel=1e-6)


def test_perfil_triangular_quando_a_rampa_sobrescreve():
    """50 mm não chegam a 100 mm/s: as rampas se cruzam (1·41·2 ms)."""
    assert cin.duracao_ms(50.0, 10, 1000, 1000) == pytest.approx(1414.2, rel=1e-3)


def test_rampa_nula_e_um_tempo_so():
    assert cin.duracao_ms(500.0, 10, 0, 0) == pytest.approx(5000.0)


def test_velocidade_zero_so_gasta_as_rampas():
    """``V0`` não anda: o robô paga as rampas ligando e desligando."""
    assert cin.duracao_ms(500.0, 0, 100, 100) == pytest.approx(200.0)


def test_distancia_zero_gasta_somente_as_rampas():
    assert cin.duracao_ms(0.0, 10, 100, 100) == pytest.approx(200.0)


def test_distancia_negativa_anda_o_mesmo_tempo():
    assert cin.duracao_ms(-1000.0, 10, 1000, 1000) == pytest.approx(11_000.0)


def test_tempo_e_crescente_na_distancia():
    assert cin.duracao_ms(200.0, 10, 500, 500) < cin.duracao_ms(800.0, 10, 500, 500)


def test_caminho_no_tempo_comeca_em_zero_e_termina_no_total():
    d, v, at, dt = 1200.0, 10, 400, 400
    assert cin.caminho_no_tempo(d, v, at, dt, 0.0) == pytest.approx(0.0)
    assert cin.caminho_no_tempo(d, v, at, dt, 1.0) == pytest.approx(d)


def test_caminho_no_tempo_e_crescente():
    d, v, at, dt = 1200.0, 10, 400, 400
    pontos = [cin.caminho_no_tempo(d, v, at, dt, f / 10) for f in range(11)]
    assert pontos == sorted(pontos)


def test_caminho_no_tempo_no_meio_da_rampa_acelera_mais_que_no_cruzeiro():
    """Nos primeiros 10% o ganho de posição é menor que nos 10% do cruzeiro."""
    d, v, at, dt = 5000.0, 10, 1000, 1000
    com_rampa = cin.caminho_no_tempo(d, v, at, dt, 0.1)
    no_cruzeiro = cin.caminho_no_tempo(d, v, at, dt, 0.5) - cin.caminho_no_tempo(d, v, at, dt, 0.4)
    assert com_rampa < no_cruzeiro


# --- reta, pivô e curva ------------------------------------------------------
def test_ir_para_frente_anda_em_mm():
    destino = cin.ir_para_frente(Pose(0.0, 0.0, 0.0), 500.0)
    assert (destino.x, destino.y) == (500.0, 0.0)


def test_ir_para_frente_aceita_re():
    assert cin.ir_para_frente(Pose(0.0, 0.0, 0.0), -500.0).x == pytest.approx(-500.0)


def test_girar_no_eixo_mantem_a_posicao():
    destino = cin.girar_no_eixo(Pose(300.0, 700.0, 0.0), 45.0)
    assert (destino.x, destino.y) == (300.0, 700.0)
    assert destino.angulo == pytest.approx(math.radians(45.0))


def test_curva_para_a_direita_desloca_para_a_direita():
    destino = cin.girar_diferencial(Pose(0.0, 0.0, 0.0), 90.0, 100.0, 262.0)
    assert destino.angulo == pytest.approx(math.radians(90.0))
    assert destino.y > 0, "o centro do arco fica à direita"


def test_curva_para_a_esquerda_desloca_para_a_esquerda():
    destino = cin.girar_diferencial(Pose(0.0, 0.0, 0.0), -90.0, 100.0, 262.0)
    assert destino.y < 0


def test_quatro_curvas_de_90_voltam_ao_ponto():
    pose = Pose(0.0, 0.0, 0.0)
    for _ in range(4):
        pose = cin.girar_diferencial(pose, 90.0, 50.0, 262.0)
    assert pose.distancia_ate((0.0, 0.0)) < 1e-6
    assert normalizar_graus(math.degrees(pose.angulo)) == pytest.approx(0.0, abs=1e-6)


def test_curva_com_raio_zero_vira_pivo():
    destino = cin.girar_diferencial(Pose(5.0, 5.0, 0.0), 90.0, -131.0, 262.0)
    assert (destino.x, destino.y) == (5.0, 5.0)


# --- ganhos PG ---------------------------------------------------------------
def test_ganhos_padrao_sao_neutros():
    g = Ganhos()
    assert g.reta(1000.0) == pytest.approx(1000.0)
    assert g.curva(90.0, 0.0) == pytest.approx(90.0)
    assert g.raio_interno(50.0) == pytest.approx(50.0)


def test_so_estica_a_reta():
    g = Ganhos(so=10.0)
    assert g.reta(1000.0) == pytest.approx(1100.0)


def test_ca_corrige_o_pivo():
    assert Ganhos(ca=-10.0).curva(90.0, Ganhos(ca=-10.0).ca) == pytest.approx(81.0)


def test_df_corrige_a_curva_diferencial():
    g = Ganhos(df=5.0)
    assert g.curva(90.0, g.df) == pytest.approx(94.5)


def test_ri_desconta_raio_interno():
    assert Ganhos(ri=-5.0).raio_interno(50.0) == pytest.approx(45.0)


def test_aplicar_ganhos_devolve_os_quatro_corrigidos():
    g = Ganhos(so=10.0, ca=-10.0, df=2.0, ri=-5.0)
    reta, pivo, dif, ri = cin.aplicar_ganhos(1000.0, 90.0, 90.0, 50.0, g)
    assert (reta, pivo, dif, ri) == pytest.approx((1100.0, 81.0, 91.8, 45.0))


def test_aplicar_ganhos_sem_ganhos_nao_altera_nada():
    assert cin.aplicar_ganhos(500.0, 90.0, 90.0, 50.0) == pytest.approx((500.0, 90.0, 90.0, 50.0))


def test_ganhos_de_dict_aceita_o_dicionario_do_config():
    g = Ganhos.de_dict({"so": 1.35, "ca": 2.87})
    assert g.so == pytest.approx(1.35)
    assert g.ca == pytest.approx(2.87)
    assert g.df == 0.0 and g.ri == 0.0


def test_ganhos_de_dict_com_none():
    assert Ganhos.de_dict(None) == Ganhos()


def test_normalizar_graus_une_os_dois_lados():
    assert normalizar_graus(270.0) == pytest.approx(-90.0)
    assert normalizar_graus(-90.0) == pytest.approx(-90.0)
    assert normalizar_graus(180.0) == pytest.approx(-180.0), "faixa é [-180, 180)"
    assert normalizar_graus(0.0) == pytest.approx(0.0)


# --- geometria auxiliar ------------------------------------------------------
def test_distancia_ate_aceita_pose_ou_tupla():
    pose = Pose(0.0, 0.0, 0.0)
    assert pose.distancia_ate((3.0, 4.0)) == pytest.approx(5.0)
    assert pose.distancia_ate(Pose(3.0, 4.0, 1.0)) == pytest.approx(5.0)


def test_para_mundo_e_para_local_sao_inversos():
    pose = Pose(300.0, 700.0, 0.7)
    volta = pose.para_local(pose.para_mundo((100.0, -50.0)))
    assert volta[0] == pytest.approx(100.0)
    assert volta[1] == pytest.approx(-50.0)


def test_y_local_positivo_e_a_direita():
    """Convenção que o ``pista.Sonar`` e o ``LINHA_PADRAO`` seguem."""
    assert Pose(0.0, 0.0, 0.0).para_mundo((0.0, 10.0))[1] > 0


# --- sensores: sonar ---------------------------------------------------------
def test_sonar_enxerga_a_parede_a_frente():
    """O sonar 2 fica 210 mm à frente do centro, então vê a parede mais perto."""
    arena = sala_com_parede_em(1000.0)
    pose = Pose(500.0, 300.0, 0.0)
    assert cin.alcance_sonar(pose, sonar(2), arena) == pytest.approx(1000.0 - 710.0)


def test_sonar_devolve_none_quando_passou_do_alcance():
    arena = sala_com_parede_em(2900.0)
    pose = Pose(500.0, 300.0, 0.0)
    # A parede está a 2190 mm, dentro do alcance padrão de 4000 mm.
    assert cin.alcance_sonar(pose, sonar(2), arena) == pytest.approx(2190.0)
    # Cortando o alcance, não acha nada e devolve ``None`` (o ``FFFF`` da placa).
    assert cin.alcance_sonar(pose, sonar(2), arena, alcance_mm=1000.0) is None


def test_sonar_lateral_direito_enxerga_e_o_esquerdo_nao():
    """Parede em ``y=1000``: o 4 (direita) vê, o 8 (esquerda) não."""
    arena = Arena(
        largura=3000.0,
        altura=2000.0,
        paredes=(Parede(0.0, 1000.0, 3000.0, 1000.0),),
        montagem=Montagem(1500.0, 500.0, 0.0),
    )
    pose = Pose(1500.0, 500.0, 0.0)
    assert cin.alcance_sonar(pose, sonar(4), arena) == pytest.approx(335.0)
    assert cin.alcance_sonar(pose, sonar(8), arena) is None


def test_sonar_desligado_nao_enxerga():
    s2 = sonar(2)
    desligado = type(s2)(s2.id, s2.x, s2.y, s2.angulo, desligado=True)
    assert cin.alcance_sonar(Pose(500.0, 300.0, 0.0), desligado, sala_com_parede_em(1000.0)) is None


def test_sonar_ve_o_objeto_no_chao():
    arena = Arena(
        largura=3000.0,
        altura=2000.0,
        objetos=(Objeto(900.0, 300.0, 200.0, 200.0, "vermelho", "p1"),),
        montagem=Montagem(500.0, 300.0, 0.0),
    )
    # O sonar 2 está 210 mm à frente do centro e o pallet começa em 800:
    # 800 − (500 + 210) = 90 mm.
    alcance = cin.alcance_sonar(Pose(500.0, 300.0, 0.0), sonar(2), arena)
    assert alcance == pytest.approx(90.0, abs=2.0)


def test_sonar_enxerga_mais_perto_o_que_esta_no_chao():
    """O pallet some da frente depois de pego — sem obstlo again."""
    arena = Arena(
        largura=3000.0,
        altura=2000.0,
        paredes=(Parede(1200.0, 0.0, 1200.0, 2000.0),),
        objetos=(Objeto(900.0, 300.0, 200.0, 200.0, "vermelho", "p1"),),
        montagem=Montagem(500.0, 300.0, 0.0),
    )
    pose = Pose(500.0, 300.0, 0.0)
    com = cin.alcance_sonar(pose, sonar(2), arena, objetos=list(arena.objetos))
    sem = cin.alcance_sonar(pose, sonar(2), arena, objetos=[])
    assert com < sem


# --- sensores: linha ---------------------------------------------------------
def _sala_com_faixa(y: float, largura_faixa: float = 200.0) -> Arena:
    return Arena(
        largura=3000.0,
        altura=2000.0,
        faixas=(Faixa(0.0, y, 3000.0, y, largura_faixa),),
        montagem=Montagem(1500.0, y - 300.0, 0.0),
    )


def test_sensores_de_linha_veem_a_faixa():
    arena = _sala_com_faixa(1000.0)
    assert cin.sensores_de_linha(Pose(1500.0, 700.0, 0.0), arena) == {1: 0, 2: 0, 3: 0}
    assert cin.sensores_de_linha(Pose(1500.0, 1000.0, 0.0), arena) == {1: 1, 2: 1, 3: 1}


def test_sensores_de_linha_distinguem_lado():
    """Varrido lateral: desloca o corpo até o sensor 3 (direita) pisar na faixa."""
    arena = Arena(
        largura=3000.0,
        altura=2000.0,
        faixas=(Faixa(0.0, 300.0, 3000.0, 300.0, 60.0),),
        montagem=Montagem(1500.0, 155.0, 0.0),
    )
    # Varrendo o corpo para baixo (+y = direita), a faixa passa pelos sensores
    # um a um: 3 → (1 e 2) → 1 → nada. A faixa cobre y de 270 a 330.
    esperado = {
        155.0: {1: 0, 2: 0, 3: 0},  # s1=100 s2=155 s3=210 — todos fora
        245.0: {1: 0, 2: 0, 3: 1},  # s3=300 entra
        335.0: {1: 1, 2: 0, 3: 0},  # s3=390 saiu, s1=280 entrou
        465.0: {1: 0, 2: 0, 3: 0},  # s1=410 saiu
    }
    for y, leitura in esperado.items():
        assert cin.sensores_de_linha(Pose(1500.0, y, 0.0), arena) == leitura, f"y={y}"


def test_ponto_sobre_faixa_respeita_a_espessura():
    faixa = Faixa(0.0, 300.0, 1000.0, 300.0, 40.0)
    assert cin.ponto_sobre_faixa(500.0, 300.0, faixa)
    assert cin.ponto_sobre_faixa(500.0, 315.0, faixa)
    assert not cin.ponto_sobre_faixa(500.0, 400.0, faixa)


def test_faixa_de_pontos_iguais_cai_no_circulo():
    faixa = Faixa(500.0, 300.0, 500.0, 300.0, 40.0)
    assert cin.ponto_sobre_faixa(500.0, 300.0, faixa)
    assert not cin.ponto_sobre_faixa(600.0, 300.0, faixa)


# --- sensores: infravermelho e digital ---------------------------------------
def test_infravermelho_devolve_o_maximo_quando_nao_ha_nada():
    arena = Arena(largura=3000.0, altura=2000.0, montagem=Montagem(1500.0, 1000.0, 0.0))
    assert cin.leitura_infravermelho(Pose(1500.0, 1000.0, 0.0), arena) == pytest.approx(150.0)


def test_infravermelho_enxerga_a_parede():
    arena = sala_com_parede_em(1400.0)
    # O sensor fica 200 mm à frente do centro: 1400 − 500 − 200 = 700 mm = 70 cm.
    assert cin.leitura_infravermelho(Pose(500.0, 300.0, 0.0), arena) == pytest.approx(70.0)


def test_digital_mapeia_os_dois_canais_extras():
    arena = _sala_com_faixa(1000.0)
    em_cima = cin.digital(Pose(1500.0, 1000.0, 0.0), arena)
    assert em_cima[1] == 1 and em_cima[2] == 1
    assert set(em_cima) == set(range(1, 9))


def test_digital_com_linha_perdida_fica_tudo_zero():
    arena = _sala_com_faixa(1000.0)
    longe = cin.digital(Pose(1500.0, 700.0, 0.0), arena)
    assert all(v == 0 for v in longe.values())


# --- colisão -----------------------------------------------------------------
def test_colide_perto_da_borda_da_sala():
    arena = Arena(largura=3000.0, altura=2000.0, montagem=Montagem(1500.0, 1000.0, 0.0))
    assert not cin.colide(Pose(1500.0, 1000.0, 0.0), arena, objetos=[])
    assert cin.colide(Pose(1500.0, RAIO - 10.0, 0.0), arena, objetos=[])
    assert cin.colide(Pose(1500.0, 2000.0 - RAIO + 10.0, 0.0), arena, objetos=[])
    assert not cin.colide(Pose(1500.0, RAIO + 50.0, 0.0), arena, objetos=[])


def test_primeiro_travamento_devolve_a_ultima_pose_livre():
    arena = sala_com_parede_em(1200.0)
    partida = Pose(500.0, 300.0, 0.0)
    travado = cin.primeiro_travamento(partida, cin.ir_para_frente(partida, 1000.0), arena, [])
    assert travado is not None
    assert travado.x < 1200.0
    assert cin.colide(travado, arena, objetos=[]) is False


def test_caminho_livre_devolve_none():
    arena = sala_com_parede_em(2000.0)
    partida = Pose(500.0, 300.0, 0.0)
    assert cin.primeiro_travamento(partida, cin.ir_para_frente(partida, 1000.0), arena, []) is None


def test_destino_dentro_da_parede_tambem_trava():
    arena = sala_com_parede_em(700.0)
    partida = Pose(500.0, 300.0, 0.0)
    travado = cin.primeiro_travamento(partida, cin.ir_para_frente(partida, 500.0), arena, [])
    assert travado is not None and travado.x < 700.0


def test_objeto_no_chao_bloqueia():
    arena = Arena(
        largura=3000.0,
        altura=2000.0,
        objetos=(Objeto(1000.0, 300.0, 100.0, 100.0, "vermelho", "p1"),),
        montagem=Montagem(500.0, 300.0, 0.0),
    )
    partida = Pose(500.0, 300.0, 0.0)
    travado = cin.primeiro_travamento(
        partida, cin.ir_para_frente(partida, 1000.0), arena, list(arena.objetos)
    )
    assert travado is not None and travado.x < 1000.0


def test_intersecao_raio_segmento_acerta():
    t = cin.intersecao_raio_segmento(0.0, 0.0, 1.0, 0.0, (100.0, -50.0, 100.0, 50.0))
    assert t == pytest.approx(100.0)


def test_intersecao_ignora_raio_que_nao_encosta():
    assert cin.intersecao_raio_segmento(0.0, 0.0, 1.0, 0.0, (-100.0, -50.0, -100.0, 50.0)) is None


def test_intersecao_ignora_raio_paralelo_ao_segmento():
    """Raio em +x e segmento também em +x na mesma linha: nunca cruza."""
    assert cin.intersecao_raio_segmento(0.0, 0.0, 1.0, 0.0, (10.0, 0.0, 100.0, 0.0)) is None


def test_intersecao_cruza_segmento_vertical():
    assert cin.intersecao_raio_segmento(
        0.0, 0.0, 1.0, 0.0, (50.0, -50.0, 50.0, 50.0)
    ) == pytest.approx(50.0)


# --- a arena como obstáculos -------------------------------------------------
def test_obstaculos_de_inclui_parede_e_objeto():
    arena = Arena(
        largura=3000.0,
        altura=2000.0,
        paredes=(Parede(0.0, 0.0, 3000.0, 0.0),),
        objetos=(Objeto(100.0, 100.0, 50.0, 50.0, "azul", "p1"),),
    )
    assert len(arena.obstaculos_de(list(arena.objetos))) == 5


def test_dentro_respeita_o_raio_do_corpo():
    arena = Arena(largura=3000.0, altura=2000.0)
    assert arena.dentro(1500.0, 1000.0, RAIO)
    assert not arena.dentro(1500.0, 10.0, RAIO)


def test_alvo_tem_cor_valida():
    with pytest.raises(ValueError, match="não é uma cor"):
        Alvo(0.0, 0.0, "lima")
