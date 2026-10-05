"""A placa virtual: o protocolo visto do lado do firmware.

O que importa aqui é que o driver real (``SoBot``) converse com a placa sem
saber que ela é simulada — por isso os testes usam o driver de verdade, não
``write()`` solto. O relógio é :class:`RelogioFake`, então tudo é instantâneo
e determinístico.
"""

from __future__ import annotations

import time

import pytest

from sentinel import SoBot, load_config
from sentinel.simulacao import cinematica as cin
from sentinel.simulacao.pista import Arena, Faixa, Montagem, Objeto, Parede, exemplo
from sentinel.simulacao.placa import PlacaVirtual, RelogioFake, RelogioReal, separar

CR_MT0 = "CR OK MT0"


# --- helpers ----------------------------------------------------------------
def placa(arena: Arena | None = None, **kwargs) -> PlacaVirtual:
    kwargs.setdefault("relogio", RelogioFake())
    return PlacaVirtual(arena if arena is not None else exemplo(), **kwargs)


def bot_pronto(p: PlacaVirtual) -> SoBot:
    """Driver com ``WP``, ``CR`` ligado e motores ligados — como a §0.5 manda."""
    b = SoBot(serial_obj=p, verbose=False)
    b.configure_wheels(*load_config().wheel_params())
    b.command_return(True, "MT0")
    b.wait_for(CR_MT0)  # drena o ack imediato do CR1 (gotcha do AGENTS.md)
    b.wheels_enable(True)
    return b


def sala_livre(largura: float = 6000.0, altura: float = 4000.0) -> Arena:
    """Sala grande, sem paredes: para testar movimento sem colisão no meio."""
    return Arena(largura=largura, altura=altura, montagem=Montagem(1000.0, 2000.0, 0.0))


def _deadline(segundos: float) -> float:
    """Absoluto: ``time.monotonic()`` sempre na hora real, nunca no relógio fake."""
    return time.monotonic() + segundos


# --- o parser ---------------------------------------------------------------
def test_separar_reta():
    (cmd,) = separar("MT0 D500 AT1000 DT1000 V10")
    assert cmd.cab == "MT0"
    assert cmd.params == ("D500", "AT1000", "DT1000", "V10")
    assert cmd.ident == "MT0"


def test_separar_numero_grudado_no_cabecalho():
    (cmd,) = separar("DO5 E1")
    assert cmd.cab == "DO"
    assert cmd.ident == "DO5", "o DO5 guarda qual saída é"


def test_separar_cr_vira_comando_proprio():
    comandos = separar("MT0 CR1")
    assert [c.cab for c in comandos] == ["MT0", "CR"]
    assert comandos[1].params == ("1",), "o '1' sobra como parâmetro do CR"
    assert comandos[1].ident == "MT0", "o CR carrega o comando a que se refere"


def test_separar_mistura_modo_fixo_e_quebra():
    """``MT0 BC`` é um comando de movimento que tambem limpa a fila."""
    comandos = separar("MT0 BC")
    assert [c.cab for c in comandos] == ["MT0", "BC"]


def test_separar_re_com_varios_parametros():
    (cmd,) = separar("PG SO1,35 CA2,87")
    assert cmd.params == ("SO1,35", "CA2,87")


def test_comando_param_devolve_o_valor_sem_o_nome():
    (cmd,) = separar("MT0 D500 AT1000")
    assert cmd.param("D") == "500"
    assert cmd.param("AT") == "1000"
    assert cmd.param("RI") is None
    assert cmd.param("RI", "50") == "50", "o padrão cobre o que faltar"


def test_comando_numero_troca_virgula_por_ponto():
    (cmd,) = separar("MT0 D500 AT1000 V10")
    assert cmd.numero("V") == pytest.approx(10.0)
    (cmd2,) = separar("PG SO1,35")
    assert cmd2.numero("SO") == pytest.approx(1.35)


def test_comando_flag_detecta_flag():
    (cmd,) = separar("MT0 D90 DF R RI100")
    assert cmd.flag("DF")
    assert not cmd.flag("MC")


def test_o_cr_vira_comando_proprio_com_o_ident_do_alvo():
    lt, cr = separar("LT E1 CR1")
    assert lt.cab == "LT" and lt.ident == "LT"
    assert cr.cab == "CR" and cr.ident == "LT", "o CR1 se refere ao LT"
    assert cr.tem_cr and cr.pede_ack


def test_cr0_nao_pede_ack():
    (_, cr) = separar("MT0 CR0")
    assert not cr.tem_cr


def test_separar_normaliza_espacos_extras():
    (cmd,) = separar("  MT0   D500   V10  ")
    assert cmd.cab == "MT0"
    assert cmd.params == ("D500", "V10")


# --- a superfície de serial -------------------------------------------------
def test_write_devolve_o_tamanho_do_comando():
    p = placa()
    assert p.write(b"MT0 E1") == len(b"MT0 E1")
    p.close()


def test_write_vazio_nao_faz_nada():
    p = placa()
    assert p.write(b"   ") == 3
    assert p.log == ()
    p.close()


def test_read_respeita_o_tamanho_pedido():
    p = placa()
    p.write(b"SS0")
    p._esperar("SS1")
    primeiro = p.read(2)
    assert len(primeiro) == 2
    p.close()


def test_in_waiting_conta_os_bytes():
    p = placa()
    assert p.in_waiting == 0
    p.write(b"SS0")
    p._esperar("SS1")
    assert p.in_waiting > 0
    p.close()


def test_flush_joga_fora_a_saida_pendente():
    p = placa()
    p.write(b"SS0")
    p._esperar("SS1")
    p.flush()
    assert p.in_waiting == 0
    p.close()


def test_close_para_o_robo():
    p = placa()
    bot_pronto(p)
    p.close()
    assert p.estado().vel_mm_s == 0


def test_placa_e_context_manager():
    with placa() as p:
        bot_pronto(p)
        p._esperar_muita(0.05)
        assert p.estado().motores_ligados


# --- o ack do CR (o gotcha do AGENTS.md) -------------------------------------
def test_cr_responde_imediatamente_e_o_ack_so_espera_o_fim():
    """``CR1`` responde na hora; o ``D500`` só confirma depois de andar.

    É o que faz ``wait_for`` devolver *antes* do movimento se o ack pendente
    não for drenado — o erro que o AGENTS.md descreve.
    """
    p = placa(sala_livre())
    b = SoBot(serial_obj=p, verbose=False)
    b.configure_wheels(*load_config().wheel_params())

    b.command_return(True, "MT0")
    assert b.wait_for(CR_MT0) == CR_MT0  # ack imediato, sem andar
    assert p.estado().vel_mm_s == 0
    assert p.relogio.tempo() == pytest.approx(0.0)

    b.wheels_enable(True)
    b.move(500, accel_ms=100, decel_ms=100, speed_cm_s=25, wait=True)
    assert p.relogio.tempo() > 0, "o movimento consumiu tempo simulado"
    p.close()


def test_sem_cr_nao_vem_ack():
    p = placa(sala_livre())
    b = SoBot(serial_obj=p, verbose=False)
    b.configure_wheels(*load_config().wheel_params())
    b.wheels_enable(True)
    b.move(500, accel_ms=100, decel_ms=100, speed_cm_s=25, wait=False)
    p._esperar_muita()
    assert not any("CR OK" in linha for linha in p.log)
    p.close()


def test_cr_geral_liga_o_ack_de_todos():
    p = placa(sala_livre())
    p.write(b"CR1")
    p._esperar("CR OK geral")
    b = bot_pronto(p)
    b.move(200, accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    p.close()


def test_cr_com_valor_ruim_da_erro():
    p = placa()
    p.write(b"CRX")
    p._esperar("ERROR=01")
    p.close()


# --- movimento em modo fixo --------------------------------------------------
def test_move_anda_a_distancia_pedida():
    p = placa(sala_livre())
    b = bot_pronto(p)
    b.move(500, accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    assert p.pose.x == pytest.approx(1500.0)
    assert p.pose.y == pytest.approx(2000.0)
    p.close()


def test_move_aceita_re():
    p = placa(sala_livre())
    b = bot_pronto(p)
    b.move(-300, accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    assert p.pose.x == pytest.approx(700.0)
    p.close()


def test_o_tempo_simulado_segue_o_perfil_trapezoidal():
    p = placa(sala_livre())
    b = bot_pronto(p)
    b.move(500, accel_ms=1000, decel_ms=1000, speed_cm_s=10, wait=True)
    esperado = cin.duracao_ms(500.0, 10, 1000, 1000) / 1000.0
    assert p.relogio.tempo() == pytest.approx(esperado, rel=1e-3)
    p.close()


def test_turn_direita_gira_para_o_lado_direito():
    """``R`` é +y na tela: o ângulo do robô cresce."""
    p = placa(sala_livre())
    b = bot_pronto(p)
    b.turn(90, "right", accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    assert p.pose.angulo == pytest.approx(cin.RAD_POR_GRAU * 90.0, rel=1e-3)
    p.close()


def test_turn_esquerda_gira_para_o_lado_esquerdo():
    p = placa(sala_livre())
    b = bot_pronto(p)
    b.turn(90, "left", accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    assert p.pose.angulo == pytest.approx(-cin.RAD_POR_GRAU * 90.0, rel=1e-3)
    p.close()


def test_curva_diferencial_desloca_o_corpo():
    p = placa(sala_livre())
    b = bot_pronto(p)
    antes = (p.pose.x, p.pose.y)
    b.send("MT0 D90 DF R RI100 AT0 DT0 V25")
    p._esperar(CR_MT0)
    assert p.pose.y > antes[1], "a curva para a direita joga o corpo para +y"
    p.close()


def test_movimento_desligado_e_ignorado():
    """Com ``E0`` o firmware ignora o movimento — o robô não anda."""
    p = placa(sala_livre())
    b = SoBot(serial_obj=p, verbose=False)
    b.configure_wheels(*load_config().wheel_params())
    b.command_return(True, "MT0")
    b.wait_for(CR_MT0)
    b.move(500, accel_ms=0, decel_ms=0, speed_cm_s=25, wait=False)
    p._esperar_muita()
    assert p.pose.x == pytest.approx(1000.0), "continua onde estava"
    p.close()


def test_mt0_ms_devolve_o_ultimo_movimento():
    p = placa(sala_livre())
    b = bot_pronto(p)
    b.move(500, accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    status = b.wheel_status()
    assert status["kind"] == "straight"
    assert status["value"] == pytest.approx(500.0)
    p.close()


def test_mt0_ms_de_uma_curva():
    p = placa(sala_livre())
    b = bot_pronto(p)
    b.turn(45, "right", accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    status = b.wheel_status()
    assert status["kind"] in ("turn_right", "turn_left")
    assert abs(status["value"]) == pytest.approx(45.0)
    p.close()


def test_ms_nao_da_ack_duplicado():
    """``MS`` já responde sozinho; não pode sair um ``CR OK MT0`` atrás."""
    p = placa(sala_livre())
    b = bot_pronto(p)
    b.move(200, accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    p._saida.clear()  # o log é histórico; o que interessa é a saída
    p.write(b"MT0 MS")
    p._esperar("MT0 MS")
    p._esperar_muita(0.05)
    assert not any(CR_MT0 in linha for linha in p._saida.decode().splitlines()), p._saida
    p.close()


# --- a fila ------------------------------------------------------------------
def test_fila_roda_em_sequencia():
    """Três comandos de uma vez rodam na ordem, e a pose soma as distâncias."""
    p = placa(sala_livre())
    b = bot_pronto(p)
    for _ in range(3):
        p.write(b"MT0 D200 AT0 DT0 V25")
    for _ in range(3):
        b.wait_for(CR_MT0)
    assert p.pose.x == pytest.approx(1000.0 + 600.0)
    assert p.estado().fila == 0
    p.close()


def test_comando_de_fila_antes_do_movimento_roda_antes():
    p = placa(sala_livre())
    b = bot_pronto(p)
    p.write(b"LT E1 RD0 GR255 BL0")
    p.write(b"MT0 D200 AT0 DT0 V25")
    b.wait_for(CR_MT0)
    assert p.estado().led == (0, 255, 0), "o LED ligou antes de o robô andar"
    p.close()


def test_bc_limpa_a_fila():
    p = placa(sala_livre())
    bot_pronto(p)
    for _ in range(4):
        p.write(b"MT0 D200 AT0 DT0 V25")
    p._esperar(CR_MT0)
    p.write(b"MT0 BC")
    p._esperar_muita()
    assert p.estado().fila == 0
    p.close()


def test_dl_atrasa_a_fila():
    """``DL`` é de fila: o movimento seguinte espera o atraso."""
    p = placa(sala_livre())
    b = bot_pronto(p)
    inicio = p.relogio.tempo()
    p.write(b"DL2000")
    p.write(b"MT0 D100 AT0 DT0 V25")
    b.wait_for(CR_MT0)
    # 2 s de DL + os 100 mm do movimento a 250 mm/s.
    assert p.relogio.tempo() - inicio == pytest.approx(2.0 + 0.4, rel=1e-3)
    p.close()


# --- o lado da curva, o tempo do arco e a parada de emergência ---------------
def test_df_respeita_o_lado_mandado():
    """``DF`` sem ``R``/``L`` não tem lado; com eles, manda o comando (guia §3.5)."""
    for comando, lado in [(b"MT0 D300 DF R RI100", 1.0), (b"MT0 D300 DF L RI100", -1.0),
                          (b"MT0 D300 DF RI100", 1.0)]:
        p = placa(sala_livre())
        bot_pronto(p)
        p.write(comando + b" AT0 DT0 V25")
        p._esperar_muita(0.05)
        direcao = 1.0 if p.pose.y > 2000.0 else -1.0
        assert direcao == lado, f"{comando!r} foi para o lado errado"
        assert p.pose.angulo * (1 if lado > 0 else -1) > 0
        p.close()


def test_df_usa_o_tempo_do_arco_e_nao_da_corda():
    """Com ``DF`` o ``D`` são graus: o tempo sai do arco, não da linha reta."""
    p = placa(sala_livre())
    bot_pronto(p)
    inicio = p.relogio.tempo()
    p.write(b"MT0 D300 DF R RI100 AT0 DT0 V25")
    p._esperar_muita(0.05)
    raio = 100.0 + p.dist_rodas_mm / 2.0
    arco = raio * 300.0 * cin.RAD_POR_GRAU
    assert p.relogio.tempo() - inicio == pytest.approx(arco / 250.0, rel=1e-3)
    corda = p.pose.distancia_ate((1000.0, 2000.0))
    assert arco > corda * 3, "o arco é bem mais longo que a corda"
    p.close()


def test_o_painel_enxerga_o_robo_andando():
    """O movimento fixo acontece em fatias: a pose avança enquanto ele corre."""
    p = placa(sala_livre(), relogio=RelogioReal(1.0))
    bot_pronto(p)
    p.write(b"MT0 D500 AT0 DT0 V25")  # 2 s de relógio real
    amostras = []
    while time.monotonic() < _deadline(4.0):
        amostras.append(round(p.estado().pose.x))
        if amostras[-1] >= 1500:
            break
        time.sleep(0.02)
    assert len(set(amostras)) > 10, f"a pose não avançou: {amostras}"
    assert amostras == sorted(amostras), "o robô andou para trás?"
    # A máquina é fraca e o teste roda sob carga: o essencial é ter chegado,
    # não bater no milímetro exato.
    assert amostras[-1] == pytest.approx(1500, abs=60)
    p.close()


def test_bc_corta_o_movimento_e_limpa_a_fila():
    """``BC`` durante o movimento para na hora — não espera a fila terminar."""
    p = placa(sala_livre(), relogio=RelogioReal(1.0))
    bot_pronto(p)
    p.write(b"MT0 D3000 AT0 DT0 V10")  # 30 s de relógio real
    p.write(b"MT0 D1000 AT0 DT0 V10")
    time.sleep(0.4)
    antes = p.pose.x
    p.write(b"MT0 BC")
    time.sleep(0.3)
    depois = p.pose.x
    assert depois - antes < 60, f"andou demais depois do BC: {antes} -> {depois}"
    assert depois < 1400, f"o BC não interrompeu o movimento: {depois}"
    assert p.estado().fila == 0, "o BC tem que limpar a fila"
    assert p.estado().vel_mm_s == 0.0
    time.sleep(0.2)
    assert p.pose.x == depois, "o robô continuou andando depois do BC"
    p.close()


def test_kc_corta_na_hora():
    p = placa(sala_livre(), relogio=RelogioReal(1.0))
    bot_pronto(p)
    p.write(b"MT0 D5000 AT0 DT0 V5")  # 100 s de relógio real
    time.sleep(0.3)
    p.write(b"KC")
    time.sleep(0.2)
    depois = p.pose.x
    assert depois < 1200, f"o KC não interrompeu: {depois}"
    assert p.estado().vel_mm_s == 0.0
    assert p.estado().fila == 0
    time.sleep(0.2)
    assert p.pose.x == depois, "o robô continuou andando depois do KC"
    p.close()


# --- colisões ---------------------------------------------------------------
def test_robo_bate_na_parede_e_para_antes():
    p = placa()
    b = bot_pronto(p)
    # A parede de baixo fica em y=2000; o robô nasce em (300,300) olhando +x.
    b.move(10000, accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    assert p.pose.x == pytest.approx(3000.0 - p.arena.robo.raio_corpo, abs=60.0)
    assert p.estado().colidiu
    p.close()


def test_colisao_para_no_ultimo_movimento_livre():
    p = placa(sala_livre(largura=2000.0))
    b = bot_pronto(p)
    b.move(10000, accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    assert p.pose.x < 2000.0
    assert not cin.colide(p.pose, p.arena, objetos=p.objetos_no_chao())
    p.close()


def test_desligar_colisao_deixa_atravessar():
    p = placa(sala_livre(largura=2000.0), colidir=False)
    b = bot_pronto(p)
    b.move(5000, accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    assert p.pose.x == pytest.approx(6000.0)
    p.close()


def test_o_tempo_conta_somente_o_que_andou():
    """Bater numa parede não gasta o tempo do caminho inteiro."""
    colidindo = placa(sala_livre(largura=2000.0))
    b1 = bot_pronto(colidindo)
    b1.move(10000, accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    colidindo.close()

    livre = placa(sala_livre(largura=20000.0))
    b2 = bot_pronto(livre)
    b2.move(10000, accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    livre.close()
    assert colidindo.relogio.tempo() < livre.relogio.tempo()


# --- sensores ----------------------------------------------------------------
def test_sonares_devolvem_oito_valores():
    p = placa()
    leitura = p.leitura_sonares()
    assert sorted(leitura) == list(range(1, 9))


def test_sonar_devolve_none_quando_nao_ha_nada():
    p = placa(sala_livre())
    assert all(v is None for v in p.leitura_sonares().values())


def test_ss_na_serial_tem_oito_campos():
    p = placa()
    b = SoBot(serial_obj=p, verbose=False)
    linha = b.query("SS0", "SS1")
    for indice in range(1, 9):
        assert f"SS{indice} " in linha
    p.close()


def test_sl_tem_tres_campos():
    p = placa()
    b = SoBot(serial_obj=p, verbose=False)
    linha = b.query("SL", "SL1")
    assert "SL1 " in linha and "SL2 " in linha and "SL3 " in linha
    assert "SL4" not in linha
    p.close()


def test_sl_marca_preto_sobre_a_faixa():
    arena = Arena(
        largura=3000.0,
        altura=2000.0,
        faixas=(Faixa(0.0, 1000.0, 3000.0, 1000.0, 400.0),),
        montagem=Montagem(1500.0, 1000.0, 0.0),
    )
    p = placa(arena)
    assert p.estado().pose.y == pytest.approx(1000.0)
    leitura = cin.sensores_de_linha(p.pose, arena)
    assert leitura == {1: 1, 2: 1, 3: 1}
    p.close()


def test_si_devolve_fff_no_longe():
    p = placa(sala_livre())
    b = SoBot(serial_obj=p, verbose=False)
    assert b.query("SI", "SI") == "SI FFF"
    p.close()


def test_si_devolve_distancia_em_cm():
    """A 2,7 m o infra dá ``FFF``: o alcance do ``SI`` é 150 cm (guia §3.12)."""
    p = placa()
    b = SoBot(serial_obj=p, verbose=False)
    assert b.query("SI", "SI") == "SI FFF"
    p.close()


def test_si_mede_a_parede_de_perto():
    arena = Arena(
        largura=3000.0,
        altura=2000.0,
        paredes=(Parede(1200.0, 0.0, 1200.0, 2000.0),),
        montagem=Montagem(500.0, 300.0, 0.0),
    )
    p = placa(arena)
    b = SoBot(serial_obj=p, verbose=False)
    # Sensor 200 mm à frente do centro, parede a 700: 500 mm = 50 cm.
    assert b.query("SI", "SI") == "SI 050"
    p.close()


def test_di_tem_oito_canais():
    p = placa()
    b = SoBot(serial_obj=p, verbose=False)
    linha = b.query("DI0", "DI1")
    for indice in range(1, 9):
        assert f"DI{indice} " in linha
    p.close()


def test_sa_tem_os_eixos():
    p = placa()
    b = SoBot(serial_obj=p, verbose=False)
    linha = b.query("SA", "AX")
    for eixo in ("AX", "AY", "AZ"):
        assert eixo in linha
    p.close()


# --- periféricos ------------------------------------------------------------
def test_led_liga_e_desliga():
    p = placa()
    b = bot_pronto(p)
    b.led(255, 0, 0)
    p._esperar_muita()
    assert p.estado().led == (255, 0, 0)
    b.led_off()
    p._esperar_muita()
    assert p.estado().led is None
    p.close()


def test_buzzer_liga_e_desliga():
    p = placa()
    b = bot_pronto(p)
    b.buzzer(True)
    p._esperar_muita()
    assert p.estado().buzzer
    b.buzzer(False)
    p._esperar_muita()
    assert not p.estado().buzzer
    p.close()


def test_rele_do_eletromano():
    p = placa()
    b = bot_pronto(p)
    b.digital_output(5, True)
    p._esperar_muita()
    estado = p.estado()
    assert estado.imanes
    assert estado.relés[5]
    p.close()


def test_saida_fora_de_faixa_da_erro():
    p = placa()
    p.write(b"DO9 E1")
    p._esperar("ERROR=01")
    p.close()


def test_elevador_sobe_e_desce():
    p = placa()
    b = bot_pronto(p)
    b.elevator("up")
    p._esperar_muita(0.05)
    assert p.estado().elevador == pytest.approx(1.0)
    b.elevator("down")
    p._esperar_muita(0.05)
    assert p.estado().elevador == pytest.approx(0.0)
    p.close()


# --- pallet: o guindaste do guia §6.5 ---------------------------------------
def arena_do_pallet() -> Arena:
    return Arena(
        largura=3000.0,
        altura=2000.0,
        objetos=(Objeto(1500.0, 700.0, 300.0, 220.0, "vermelho", "pallet1"),),
        montagem=Montagem(1500.0, 300.0, 0.0),
    )


def vira_e_avanca_ate_o_pallet(b: SoBot) -> None:
    """De (1500,300) olhando +x, encosta no pallet em (1500,700).

    Gira 90° e anda 400 mm: o corpo do robô (raio 200) encosta na quina do
    pallet e a garra alcança a borda. É a manobra do guia §6.5.
    """
    b.turn(90, "right", accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    b.move(400, accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)


def ergue_a_garra(b: SoBot) -> None:
    """Elevador em cima, ímãs desligados: a garra fica livre para pegar."""
    b.elevator("up")
    b.digital_output(5, False)


def test_pallet_e_pegado_so_com_elevador_baixo_e_imaes_ligados():
    p = placa(arena_do_pallet())
    b = bot_pronto(p)
    vira_e_avanca_ate_o_pallet(b)

    # Elevador em cima: os ímãs não pegam nada, mesmo encostado.
    ergue_a_garra(b)
    b.digital_output(5, True)
    p._esperar_muita(0.05)
    assert p.estado().segure is None

    # Desce a plataforma e religa os ímãs: agora pega.
    b.elevator("down")
    b.digital_output(5, False)
    b.digital_output(5, True)
    p._esperar_muita(0.05)
    assert p.estado().segure is not None
    assert p.estado().segure.id == "pallet1"
    p.close()


def test_pallet_pegado_some_do_chao_e_dos_sonares():
    p = placa(arena_do_pallet())
    b = bot_pronto(p)
    vira_e_avanca_ate_o_pallet(b)
    antes = [o.id for o in p.objetos_no_chao()]
    ergue_a_garra(b)
    b.elevator("down")
    b.digital_output(5, False)
    b.digital_output(5, True)
    p._esperar_muita(0.05)
    assert antes == ["pallet1"]
    assert p.objetos_no_chao() == []
    p.close()


def test_soltar_imanes_devolve_o_pallet_ao_chao():
    p = placa(arena_do_pallet())
    b = bot_pronto(p)
    vira_e_avanca_ate_o_pallet(b)
    ergue_a_garra(b)
    b.elevator("down")
    b.digital_output(5, True)
    p._esperar_muita(0.05)
    assert p.estado().segure is not None
    b.digital_output(5, False)
    p._esperar_muita(0.05)
    assert p.estado().segure is None
    assert [o.id for o in p.objetos_no_chao()] == ["pallet1"]
    p.close()


# --- erros ------------------------------------------------------------------
def test_comando_desconhecido_da_erro_00():
    p = placa()
    p.write(b"XX9 E1")
    p._esperar("ERROR=00")
    p.close()


def test_misturar_mt0_com_modo_pulso_da_erro_02():
    """Regra de segurança: nunca misturar os dois (guia §12)."""
    p = placa()
    p.write(b"MT1 E1")
    p._esperar_muita()
    p.write(b"MT0 D500")
    p._esperar("ERROR=02")
    p.close()


# --- o estado que o painel consome ------------------------------------------
def test_estado_tem_o_que_o_painel_precisa():
    p = placa()
    e = p.estado()
    assert e.pose.x == pytest.approx(300.0)
    assert isinstance(e.led, (tuple, type(None)))
    assert e.fila == 0
    assert len(e.trilha) >= 1
    assert e.tempo >= 0.0
    p.close()


def test_trilha_acumula_com_o_movimento():
    p = placa(sala_livre())
    b = bot_pronto(p)
    for _ in range(3):
        b.move(200, accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    assert len(p.estado().trilha) >= 4
    p.close()


def test_log_tem_o_que_entrou_e_saiu():
    p = placa()
    b = bot_pronto(p)
    b.move(200, accel_ms=0, decel_ms=0, speed_cm_s=25, wait=True)
    log = p.log
    assert any(linha.startswith(">>") for linha in log)
    assert any(linha.startswith("<<") for linha in log)
    p.close()


def test_log_nao_cresce_sem_limite():
    p = placa()
    for indice in range(600):
        p.write(f"MT0 D{indice % 900}".encode())
    assert len(p.log) <= 400
    p.close()


# --- controle externo de tempo ----------------------------------------------
def test_relogio_fake_avanca_so_quando_dorme():
    r = RelogioFake()
    assert r.tempo() == 0.0
    r.dormir(2.5)
    assert r.tempo() == pytest.approx(2.5)


def test_relogio_fake_ignora_dormir_negativo():
    r = RelogioFake()
    r.dormir(-3.0)
    assert r.tempo() == 0.0


def test_placa_rejeita_velocidade_nao_positiva():
    from sentinel.simulacao.placa import RelogioReal

    with pytest.raises(ValueError, match="velocidade"):
        RelogioReal(0.0)


def test_velocidade_maior_que_um_acelera_a_simulacao():
    """O relógio real divide a espera, mas o tempo simulado anda o mesmo."""
    from sentinel.simulacao.placa import RelogioReal

    assert RelogioReal(4.0).velocidade == 4.0
