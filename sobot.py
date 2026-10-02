"""
sobot.py - Driver Python para o robô SoBot (SOLIS Tecnologia).

Baseado em: Guia de Referência dos Comandos v0.4.11 e Apostila SoBot v0.4.17.
Dependência: pyserial (pip install pyserial). Para testes sem robô, passe `serial_obj`.

Regras do protocolo que este driver aplica por você:
  * comandos em ASCII MAIÚSCULO, tokens separados por espaço, sem terminador;
  * decimais com VÍRGULA (WD99,6 / PG SO1,35);
  * validação das faixas documentadas (levanta ValueError em vez de deixar o robô
    "clampar" silenciosamente);
  * respostas ERROR=xx viram exceção SoBotError.

Uso rápido:
    from sobot import SoBot
    with SoBot() as bot:                       # /dev/ttyACM0, 57600
        bot.command_return(True, "MT0")        # pede "CR OK MT0" ao fim de cada movimento
        bot.wheels_enable(True)
        bot.move(1000, accel_ms=5000, decel_ms=5000, speed_cm_s=10, wait=True)
        bot.wheels_enable(False)
"""
from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Union

DEFAULT_PORT = "/dev/ttyACM0"
DEFAULT_BAUD = 57600
QUEUE_LIMIT = 100          # comandos de ação armazenáveis na fila do robô
INF = float("inf")         # leitura acima do alcance (FFFF / FFF)

Number = Union[int, float]


class SoBotError(Exception):
    """Robô respondeu ERROR=xx."""

    def __init__(self, line: str):
        super().__init__(line)
        m = re.match(r"ERROR=(\d+)\s*(.*)", line)
        self.code = int(m.group(1)) if m else None
        self.detail = m.group(2).strip() if m else ""
        self.line = line


class SoBotTimeout(Exception):
    """Nenhuma resposta esperada dentro do tempo limite."""


# ----------------------------------------------------------------------------
# helpers de formatação / validação
# ----------------------------------------------------------------------------
def _num(value: Number) -> str:
    """Formata número no padrão do robô: sem zeros à direita e vírgula decimal."""
    if isinstance(value, int):
        return str(value)
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    return text.replace(".", ",")


def _check(name: str, value: Number, lo: Number, hi: Number) -> Number:
    if not (lo <= value <= hi):
        raise ValueError(f"{name}={value} fora da faixa documentada [{lo}, {hi}]")
    return value


def _dir_flag(direction: str) -> str:
    d = direction.strip().upper()
    if d in ("R", "RIGHT", "DIREITA"):
        return "R"
    if d in ("L", "LEFT", "ESQUERDA"):
        return "L"
    raise ValueError("direction deve ser 'R' (direita) ou 'L' (esquerda)")


# ----------------------------------------------------------------------------
# driver
# ----------------------------------------------------------------------------
class SoBot:
    def __init__(self, port: str = DEFAULT_PORT, baudrate: int = DEFAULT_BAUD,
                 serial_obj=None, default_timeout: float = 1.0, verbose: bool = False):
        if serial_obj is None:
            import serial  # pyserial
            serial_obj = serial.Serial(port, baudrate, timeout=0, dsrdtr=False)
            try:
                serial_obj.flush()
            except Exception:
                pass
        self.ser = serial_obj
        self.default_timeout = default_timeout
        self.verbose = verbose
        self._buf = b""
        self._lines: List[str] = []

    # --- ciclo de vida -------------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    # --- I/O bruto -----------------------------------------------------------
    def send(self, command: str) -> None:
        """Envia um comando cru (já no formato do robô). Sem terminador."""
        command = command.strip()
        if command != command.upper():
            raise ValueError(f"Comandos devem estar em MAIÚSCULAS: {command!r}")
        if self.verbose:
            print(f">> {command}")
        self.ser.write(command.encode("ascii"))

    def _pump(self) -> None:
        n = getattr(self.ser, "in_waiting", 0)
        if n:
            self._buf += self.ser.read(n)
        while b"\n" in self._buf:
            raw, self._buf = self._buf.split(b"\n", 1)
            line = raw.decode("ascii", "replace").strip()
            if line:
                if self.verbose:
                    print(f"<< {line}")
                self._lines.append(line)

    def read_line(self, timeout: Optional[float] = None) -> Optional[str]:
        """Próxima linha recebida (ou None no timeout). Levanta SoBotError em ERROR=xx."""
        timeout = self.default_timeout if timeout is None else timeout
        end = time.monotonic() + timeout
        while True:
            self._pump()
            if self._lines:
                line = self._lines.pop(0)
                if line.startswith("ERROR="):
                    raise SoBotError(line)
                return line
            if time.monotonic() >= end:
                return None
            time.sleep(0.005)

    def wait_for(self, prefix: str, timeout: Optional[float] = None) -> str:
        """Espera uma linha que comece com `prefix`; descarta as demais."""
        timeout = self.default_timeout if timeout is None else timeout
        end = time.monotonic() + timeout
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise SoBotTimeout(f"sem resposta '{prefix}' em {timeout}s")
            line = self.read_line(min(remaining, 0.1))
            if line and line.startswith(prefix):
                return line

    def query(self, command: str, prefix: str, timeout: Optional[float] = None) -> str:
        self.flush_input()
        self.send(command)
        return self.wait_for(prefix, timeout)

    def flush_input(self) -> None:
        self._pump()
        self._lines.clear()

    @staticmethod
    def delay_host(seconds: float) -> None:
        """sleep no host (não entra na fila do robô). Use `delay()` para a fila."""
        time.sleep(seconds)

    # --- configuração --------------------------------------------------------
    def configure_wheels(self, left_diameter_mm: Number, right_diameter_mm: Number,
                         track_mm: Number) -> None:
        """WP MT1 WD.. / WP MT2 WD.. / WP DW.."""
        self.set_wheel_diameter(1, left_diameter_mm)
        self.set_wheel_diameter(2, right_diameter_mm)
        self.set_wheel_distance(track_mm)

    def set_wheel_diameter(self, motor: int, diameter_mm: Number) -> None:
        if motor not in (1, 2):
            raise ValueError("motor deve ser 1 (esquerda) ou 2 (direita)")
        _check("diametro_mm", diameter_mm, 90.0, 110.0)
        self.send(f"WP MT{motor} WD{_num(diameter_mm)}")

    def set_wheel_distance(self, track_mm: Number) -> None:
        _check("distancia_mm", track_mm, 240.0, 280.0)
        self.send(f"WP DW{_num(track_mm)}")

    def set_proportional_gain(self, so: Optional[Number] = None, ca: Optional[Number] = None,
                              df: Optional[Number] = None, ri: Optional[Number] = None) -> None:
        """PG SO.. CA.. DF.. RI.. (percentuais, aceitam negativo)."""
        parts = []
        for name, val in (("SO", so), ("CA", ca), ("DF", df), ("RI", ri)):
            if val is not None:
                _check(name, val, -99.99, 99.99)
                parts.append(f"{name}{_num(val)}")
        if not parts:
            raise ValueError("informe ao menos um ganho")
        self.send("PG " + " ".join(parts))

    # fórmulas de calibração do manual ---------------------------------------
    @staticmethod
    def gain_straight(commanded_mm_for_1m: Number) -> float:
        """PG_SO = (valor_para_1m - 1000) / 10"""
        return (commanded_mm_for_1m - 1000) / 10

    @staticmethod
    def gain_pivot(commanded_deg_for_360: Number) -> float:
        """PG_CA = ((valor_para_360 - 360) * 100) / 360"""
        return (commanded_deg_for_360 - 360) * 100 / 360

    @staticmethod
    def gain_diff_angle(commanded_deg_for_360: Number) -> float:
        """PG_DF = ((valor_para_360 - 360) * 100) / 360"""
        return (commanded_deg_for_360 - 360) * 100 / 360

    @staticmethod
    def gain_diff_radius(commanded_mm_for_100mm_radius: Number) -> float:
        """PG_RI = valor_para_100mm - 100"""
        return commanded_mm_for_100mm_radius - 100

    def command_return(self, enable: bool, ident: Optional[str] = None) -> None:
        """CR1/CR0 geral ou '<ID> CR1/0' (MT0, MT3, LT, DO, AO, BZ, EL, DL, WP, PG)."""
        ident = (ident or "").upper()
        if ident and ident not in ("MT0", "MT3", "LT", "DO", "AO", "BZ", "EL", "DL", "WP", "PG"):
            raise ValueError(f"CR por comando não documentado para {ident!r}")
        self.send((f"{ident} " if ident else "") + f"CR{1 if enable else 0}")

    # --- movimento: modo fixo ------------------------------------------------
    def wheels_enable(self, on: bool) -> None:
        self.send(f"MT0 E{1 if on else 0}")

    def move(self, distance_mm: Number, accel_ms: int = 1000, decel_ms: int = 1000,
             speed_cm_s: int = 10, wait: bool = False, wait_timeout: float = 120.0) -> None:
        """MT0 D<±mm> AT DT V. Positivo = frente, negativo = ré."""
        _check("distance_mm", abs(distance_mm), 0, 65000)
        _check("accel_ms", accel_ms, 0, 60000)
        _check("decel_ms", decel_ms, 0, 60000)
        _check("speed_cm_s", speed_cm_s, 0, 25)
        self.send(f"MT0 D{_num(distance_mm)} AT{accel_ms} DT{decel_ms} V{speed_cm_s}")
        if wait:
            self.wait_for("CR OK MT0", wait_timeout)

    def turn(self, angle_deg: Number, direction: str, accel_ms: int = 500, decel_ms: int = 500,
             speed_cm_s: int = 10, wait: bool = False, wait_timeout: float = 120.0) -> None:
        """Curva sobre o próprio eixo: MT0 D<graus> R|L AT DT V."""
        _check("angle_deg", angle_deg, 0, 400)
        _check("accel_ms", accel_ms, 0, 60000)
        _check("decel_ms", decel_ms, 0, 60000)
        _check("speed_cm_s", speed_cm_s, 0, 25)
        self.send(f"MT0 D{_num(angle_deg)} {_dir_flag(direction)} AT{accel_ms} DT{decel_ms} V{speed_cm_s}")
        if wait:
            self.wait_for("CR OK MT0", wait_timeout)

    def turn_differential(self, angle_deg: Number, direction: str, inner_radius_mm: int,
                          speed_cm_s: int = 5, accel_ms: Optional[int] = None,
                          decel_ms: Optional[int] = None, wait: bool = False,
                          wait_timeout: float = 120.0) -> None:
        """Curva diferencial: MT0 D<graus> DF R|L RI<mm> [AT DT] V."""
        _check("angle_deg", angle_deg, 0, 400)
        _check("inner_radius_mm", inner_radius_mm, 10, 9999)
        _check("speed_cm_s", speed_cm_s, 0, 25)
        cmd = f"MT0 D{_num(angle_deg)} DF {_dir_flag(direction)} RI{int(inner_radius_mm)}"
        if accel_ms is not None:
            cmd += f" AT{_check('accel_ms', accel_ms, 0, 60000)}"
        if decel_ms is not None:
            cmd += f" DT{_check('decel_ms', decel_ms, 0, 60000)}"
        cmd += f" V{speed_cm_s}"
        self.send(cmd)
        if wait:
            self.wait_for("CR OK MT0", wait_timeout)

    def motor3_enable(self, on: bool) -> None:
        self.send(f"MT3 E{1 if on else 0}")

    def motor3_move(self, total_pulses: int, pulses_per_s: int, accel_ms: int = 1000,
                    decel_ms: int = 1000) -> None:
        """MT3 AT DT TP PS (800 pulsos/rev). TP negativo = reverso."""
        _check("total_pulses", abs(total_pulses), 0, 500000)
        _check("pulses_per_s", pulses_per_s, 0, 2000)
        _check("accel_ms", accel_ms, 0, 60000)
        _check("decel_ms", decel_ms, 0, 60000)
        self.send(f"MT3 AT{accel_ms} DT{decel_ms} TP{int(total_pulses)} PS{int(pulses_per_s)}")

    def wheel_status(self, timeout: Optional[float] = None) -> Dict[str, Union[str, float]]:
        """MT0 MS -> {'kind': 'straight'|'turn_right'|'turn_left', 'value': mm|graus}."""
        line = self.query("MT0 MS", "MT0 MS", timeout)
        m = re.search(r"D([+-])(R|L)?(\d+),(\d+)", line)
        if not m:
            raise ValueError(f"status inesperado: {line!r}")
        sign, turn, ip, fp = m.groups()
        val = float(f"{ip}.{fp}")
        if turn == "R":
            return {"kind": "turn_right", "value": val, "raw": line}
        if turn == "L":
            return {"kind": "turn_left", "value": val, "raw": line}
        return {"kind": "straight", "value": -val if sign == "-" else val, "raw": line}

    # --- movimento: modo contínuo -------------------------------------------
    def continuous_enable(self, on: bool) -> None:
        self.send(f"MT0 ME{1 if on else 0}")

    def continuous_configure(self, accel_ms: int = 100, decel_ms: int = 100, speed_cm_s: int = 2,
                             differential: bool = False, inner_radius_mm: int = 50) -> None:
        _check("accel_ms", accel_ms, 0, 60000)
        _check("decel_ms", decel_ms, 0, 60000)
        _check("speed_cm_s", speed_cm_s, 0, 25)
        if differential:
            _check("inner_radius_mm", inner_radius_mm, 10, 9999)
            self.send(f"MT0 MC MD1 RI{int(inner_radius_mm)} AT{accel_ms} DT{decel_ms} V{speed_cm_s}")
        else:
            self.send(f"MT0 MC MD0 AT{accel_ms} DT{decel_ms} V{speed_cm_s}")

    _CONT = {"forward": "MF", "back": "MB", "left": "ML", "left_reverse": "ML-",
             "right": "MR", "right_reverse": "MR-", "pause": "MP"}

    def continuous_move(self, action: str) -> None:
        """action: forward | back | left | left_reverse | right | right_reverse | pause"""
        try:
            self.send(f"MT0 {self._CONT[action]}")
        except KeyError:
            raise ValueError(f"action deve ser um de {sorted(self._CONT)}")

    def learn_mode(self, on: bool) -> None:
        """MT0 MC LM1/LM0 (insira sleeps antes/depois, conforme o manual)."""
        self.send(f"MT0 MC LM{1 if on else 0}")

    # --- parada --------------------------------------------------------------
    def brake(self, motor: int = 0) -> None:
        """Break: desacelera conforme DT e limpa a fila (MT0 BC / MT3 BC)."""
        if motor not in (0, 3):
            raise ValueError("BC só vale para MT0 e MT3 (não para MT1/MT2)")
        self.send(f"MT{motor} BC")

    def kill(self) -> None:
        """KC: parada imediata + limpa a fila. SÓ EM EMERGÊNCIA (risco de dano irreversível)."""
        self.send("KC")

    # --- modo pulsos (MT1/MT2) ----------------------------------------------
    def pulse_enable(self, left: Optional[bool] = None, right: Optional[bool] = None) -> None:
        parts = []
        if left is not None:
            parts.append(f"MT1 E{1 if left else 0}")
        if right is not None:
            parts.append(f"MT2 E{1 if right else 0}")
        if not parts:
            raise ValueError("informe left e/ou right")
        self.send(" ".join(parts))

    def pulse_speed(self, left_pps: Optional[int] = None, right_pps: Optional[int] = None) -> None:
        """MT1 E1 PS<±> [MT2 E1 PS<±>]. Mesmo sinal = reta; sinais opostos = giro no eixo."""
        parts = []
        if left_pps is not None:
            parts.append(f"MT1 E1 PS{int(_check('left_pps', left_pps, -2000, 2000))}")
        if right_pps is not None:
            parts.append(f"MT2 E1 PS{int(_check('right_pps', right_pps, -2000, 2000))}")
        if not parts:
            raise ValueError("informe left_pps e/ou right_pps")
        self.send(" ".join(parts))

    # --- sensores ------------------------------------------------------------
    def read_sonar(self, n: int = 0) -> Dict[int, Optional[float]]:
        """SS0..SS8 -> {sensor: mm}. INF = acima de 4000 mm (FFFF); None = falha/desconectado (NULL)."""
        _check("n", n, 0, 8)
        line = self.query(f"SS{n}", "SS1" if n == 0 else f"SS{n}")
        out: Dict[int, Optional[float]] = {}
        for idx, val in re.findall(r"SS(\d)\s+(\w+)", line):
            out[int(idx)] = INF if val == "FFFF" else (None if val == "NULL" else int(val))
        return out

    def read_infrared(self) -> Optional[float]:
        """SI -> cm (20..150); INF se > 150 (FFF)."""
        line = self.query("SI", "SI")
        val = line.split()[1]
        return INF if val == "FFF" else int(val)

    def read_line_sensors(self) -> Dict[int, int]:
        """SL -> {1: 0|1, 2: ..., 3: ...}. 0 = reflexivo (branco), 1 = preto."""
        line = self.query("SL", "SL1")
        return {int(i): int(v) for i, v in re.findall(r"SL(\d)\s+([01])", line)}

    def read_digital(self, n: int = 0) -> Dict[int, int]:
        _check("n", n, 0, 8)
        line = self.query(f"DI{n}", "DI1" if n == 0 else f"DI{n}")
        return {int(i): int(v) for i, v in re.findall(r"DI(\d)\s+([01])", line)}

    def read_analog(self, n: int) -> Dict[str, Union[int, float, str]]:
        """AI1-2: 0-5 V; AI3-4: 0-10 V (mV no retorno); AI5-6: 4-20 mA (µA no retorno)."""
        _check("n", n, 1, 6)
        line = self.query(f"AI{n}", f"AI{n}")
        raw = int(line.split()[1])
        if n <= 4:
            return {"raw": raw, "raw_unit": "mV", "value": raw / 1000.0, "unit": "V"}
        return {"raw": raw, "raw_unit": "uA", "value": raw / 1000.0, "unit": "mA"}

    def read_accelerometer(self) -> Dict[str, float]:
        """SA -> {'AX','AY','AZ' (g), 'T' (°C), 'GX','GY','GZ' (°/s)}."""
        line = self.query("SA", "AX")
        pairs = re.findall(r"(AX|AY|AZ|GX|GY|GZ|T)(-?\d+(?:,\d+)?)", line)
        return {k: float(v.replace(",", ".")) for k, v in pairs}

    # --- saídas e periféricos -----------------------------------------------
    def digital_output(self, n: int, on: bool, timer_ms: Optional[int] = None) -> None:
        _check("n", n, 1, 8)
        cmd = f"DO{n} E{1 if on else 0}"
        if timer_ms is not None:
            cmd += f" TM{int(_check('timer_ms', timer_ms, 0, 60000))}"
        self.send(cmd)

    def pwm_output(self, n: int, on: bool, freq_hz: Optional[int] = None,
                   duty_pct: Optional[int] = None) -> None:
        _check("n", n, 1, 2)
        cmd = f"AO{n} E{1 if on else 0}"
        if freq_hz is not None:
            cmd += f" F{int(_check('freq_hz', freq_hz, 0, 1000))}"
        if duty_pct is not None:
            cmd += f" DC{int(_check('duty_pct', duty_pct, 0, 100))}"
        self.send(cmd)

    def led(self, r: int, g: int, b: int) -> None:
        for name, v in (("r", r), ("g", g), ("b", b)):
            _check(name, v, 0, 255)
        self.send(f"LT E1 RD{r} GR{g} BL{b}")

    def led_off(self) -> None:
        self.send("LT E0")

    def buzzer(self, on: bool) -> None:
        self.send(f"BZ E{1 if on else 0}")

    def uart_enable(self, on: bool) -> None:
        self.send(f"SC E{1 if on else 0}")

    def uart_send(self, text: str) -> None:
        """SC E1 SD<n> <texto> (até 15 bytes ASCII)."""
        data = text.encode("ascii")
        _check("len(text)", len(data), 0, 15)
        self.send(f"SC E1 SD{len(data)} {text}")

    def uart_read(self, timeout: Optional[float] = None) -> str:
        """SC RS -> caracteres recebidos (até 15)."""
        line = self.query("SC RS", "SC RS", timeout)
        return line[len("SC RS"):].strip()

    def elevator(self, action: str) -> None:
        """action: up | down | stop  (EL UP / EL DN / EL ST)."""
        mapping = {"up": "UP", "down": "DN", "stop": "ST"}
        try:
            self.send(f"EL {mapping[action.lower()]}")
        except KeyError:
            raise ValueError("action deve ser up, down ou stop")

    def delay(self, ms: int) -> None:
        """DL<ms>: atraso na FILA do robô (50..60000 ms)."""
        _check("ms", ms, 50, 60000)
        self.send(f"DL{int(ms)}")
