"""Formatação, validação e tratamento de erros do driver."""
from __future__ import annotations

import pytest

from conftest import FakeSerial
from sentinel.sobot import SoBot, SoBotError, SoBotTimeout, _num


class TestFormatacao:
    @pytest.mark.parametrize(
        ("valor", "esperado"),
        [
            (100, "100"),
            (100.0, "100"),
            (99.6, "99,6"),
            (262.35, "262,35"),
            (-0.5, "-0,5"),
            (-0.0, "0"),
            (5, "5"),
        ],
    )
    def test_num_usa_virgula_decimal(self, valor, esperado):
        assert _num(valor) == esperado

    def test_send_recusa_minusculas(self, serial):
        bot = SoBot(serial_obj=serial)
        with pytest.raises(ValueError, match="MAIÚSCULAS"):
            bot.send("mt0 e1")

    def test_move_monta_comando(self, serial):
        SoBot(serial_obj=serial).move(500, accel_ms=1000, decel_ms=1000, speed_cm_s=10)
        assert serial.comandos == ["MT0 D500 AT1000 DT1000 V10"]

    def test_move_negativo_e_re(self, serial):
        SoBot(serial_obj=serial).move(-1000, accel_ms=5000, decel_ms=5000, speed_cm_s=10)
        assert serial.comandos == ["MT0 D-1000 AT5000 DT5000 V10"]

    def test_turn_monta_comando(self, serial):
        SoBot(serial_obj=serial).turn(90, "direita", accel_ms=500, decel_ms=500)
        assert serial.comandos == ["MT0 D90 R AT500 DT500 V10"]

    def test_turn_diferencial(self, serial):
        SoBot(serial_obj=serial).turn_differential(
            90, "esquerda", inner_radius_mm=100, speed_cm_s=5,
        )
        assert serial.comandos == ["MT0 D90 DF L RI100 V5"]

    def test_configure_wheels(self, serial):
        SoBot(serial_obj=serial).configure_wheels(99.6, 100.32, 260.35)
        assert serial.comandos == [
            "WP MT1 WD99,6",
            "WP MT2 WD100,32",
            "WP DW260,35",
        ]

    def test_command_return_geral_e_por_id(self, serial):
        bot = SoBot(serial_obj=serial)
        bot.command_return(True)
        bot.command_return(False, "MT0")
        assert serial.comandos == ["CR1", "MT0 CR0"]

    def test_command_return_recusa_id_desconhecido(self, serial):
        with pytest.raises(ValueError, match="não documentado"):
            SoBot(serial_obj=serial).command_return(True, "XX")

    def test_contem_varios_blocos_na_mesma_linha(self, serial):
        SoBot(serial_obj=serial).pulse_speed(left_pps=500, right_pps=-500)
        assert serial.comandos == ["MT1 E1 PS500 MT2 E1 PS-500"]


class TestValidacao:
    @pytest.mark.parametrize(
        ("chamada"),
        [
            lambda b: b.move(100, speed_cm_s=26),
            lambda b: b.move(100, accel_ms=60001),
            lambda b: b.move(70000),
            lambda b: b.turn(401, "R"),
            lambda b: b.turn_differential(90, "L", inner_radius_mm=9),
            lambda b: b.configure_wheels(89, 100, 262),
            lambda b: b.set_wheel_distance(300),
            lambda b: b.delay(10),
            lambda b: b.led(256, 0, 0),
            lambda b: b.uart_send("0123456789ABCDEF"),
            lambda b: b.motor3_move(100, 3000),
        ],
    )
    def test_rejeita_fora_da_faixa(self, serial, chamada):
        with pytest.raises(ValueError, match="fora da faixa"):
            chamada(SoBot(serial_obj=serial))

    def test_rejeita_direcao_invalida(self, serial):
        with pytest.raises(ValueError, match="direction"):
            SoBot(serial_obj=serial).turn(90, "frente")

    def test_rejeita_acao_invalida(self, serial):
        with pytest.raises(ValueError, match="action"):
            SoBot(serial_obj=serial).continuous_move("dancar")

    def test_brake_recusa_motor_invalido(self, serial):
        with pytest.raises(ValueError, match="MT0 e MT3"):
            SoBot(serial_obj=serial).brake(1)


class TestRespostas:
    def test_leituras_de_sensor(self):
        serial = FakeSerial({
            "SS0": "SS1 0150 SS2 FFFF SS3 NULL SS4 0210 SS5 0300 SS6 0400 SS7 0500 SS8 0600",
            "SI": "SI 050",
            "SL": "SL1 0 SL2 1 SL3 0",
            "MT0 MS": "MT0 MS D+00500,00",
        })
        bot = SoBot(serial_obj=serial)

        sonar = bot.read_sonar()
        assert sonar[1] == 150
        assert sonar[2] == float("inf")
        assert sonar[3] is None

        assert bot.read_infrared() == 50
        assert bot.read_line_sensors() == {1: 0, 2: 1, 3: 0}
        assert bot.wheel_status() == {
            "kind": "straight", "value": 500.0,
            "raw": "MT0 MS D+00500,00",
        }

    def test_error_vira_excecao(self):
        serial = FakeSerial({"SS0": "ERROR=01 SS"})
        with pytest.raises(SoBotError) as exc:
            SoBot(serial_obj=serial).read_sonar()
        assert exc.value.code == 1
        assert exc.value.detail == "SS"

    def test_timeout_sem_resposta(self, serial):
        bot = SoBot(serial_obj=serial, default_timeout=0.05)
        with pytest.raises(SoBotTimeout):
            bot.wait_for("CR OK MT0", timeout=0.05)


class TestCicloDeVida:
    def test_context_manager_fecha_a_serial(self, serial):
        with SoBot(serial_obj=serial) as bot:
            assert bot.ser is serial
        assert serial.fechado

    def test_wait_true_consome_o_ack(self, serial):
        SoBot(serial_obj=serial).move(500, wait=True)
        assert serial.comandos == ["MT0 D500 AT1000 DT1000 V10"]
