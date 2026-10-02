"""Configuração do Sentinel lida de ``sentinel.toml``.

Os nomes das chaves reproduzem os códigos do próprio robô (``WD``/``DW``/``PG``)
para que o arquivo seja legível junto com o *Guia de Referência dos Comandos*.

    [serial]
    port = "/dev/ttyACM0"
    baud = 57600

    [rodas]
    wd_esquerda = 100.0
    wd_direita = 100.0
    dw = 262.0

    [ganho]        # opcional: só preencher depois da calibração (guia §5)
    so = 0.0
    ca = 0.0
    df = 0.0
    ri = 0.0

A busca é: ``$SENTINEL_CONFIG`` → ``sentinel.toml`` no diretório atual e nos
pastas acima. Sem arquivo, valem os valores nominais de fábrica.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "sentinel.toml"
ENV_CONFIG_PATH = "SENTINEL_CONFIG"
ENV_PORT = "SENTINEL_PORT"
ENV_BAUD = "SENTINEL_BAUD"


@dataclass(frozen=True)
class Config:
    """Parâmetros de conexão e de kinematics do robô."""

    port: str = "/dev/ttyACM0"
    baud: int = 57600
    wd_esquerda: float = 100.0
    wd_direita: float = 100.0
    dw: float = 262.0
    so: float | None = None
    ca: float | None = None
    df: float | None = None
    ri: float | None = None

    def wheel_params(self) -> tuple[float, float, float]:
        """Argumentos prontos para ``SoBot.configure_wheels``."""
        return (self.wd_esquerda, self.wd_direita, self.dw)

    def pg_gains(self) -> dict[str, float]:
        """Ganhos definidos para ``SoBot.set_proportional_gain``; vazio se não houver."""
        return {k: v for k, v in (("so", self.so), ("ca", self.ca),
                                  ("df", self.df), ("ri", self.ri)) if v is not None}


def find_config_file(start: Path | None = None) -> Path | None:
    """Caminho do ``sentinel.toml`` a usar, ou ``None`` se não houver."""
    from_env = os.environ.get(ENV_CONFIG_PATH)
    if from_env:
        path = Path(from_env).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"{ENV_CONFIG_PATH} aponta para arquivo inexistente: {path}")
        return path

    atual = (start or Path.cwd()).resolve()
    for pasta in (atual, *atual.parents):
        candidato = pasta / CONFIG_FILENAME
        if candidato.is_file():
            return candidato
    return None


def load_config(path: Path | None = None) -> Config:
    """Carrega a configuração; sem arquivo, devolve os valores nominais."""
    caminho = path or find_config_file()
    dados: dict[str, Any] = {}
    if caminho is not None:
        with open(caminho, "rb") as fh:
            dados = tomllib.load(fh)

    serial = dados.get("serial", {})
    rodas = dados.get("rodas", {})
    ganho = dados.get("ganho", {})
    ganhos = {k: float(ganho[k]) for k in ("so", "ca", "df", "ri") if ganho.get(k) is not None}

    return Config(
        port=os.environ.get(ENV_PORT) or serial.get("port", Config.port),
        baud=int(os.environ.get(ENV_BAUD) or serial.get("baud", Config.baud)),
        wd_esquerda=float(rodas.get("wd_esquerda", Config.wd_esquerda)),
        wd_direita=float(rodas.get("wd_direita", Config.wd_direita)),
        dw=float(rodas.get("dw", Config.dw)),
        **ganhos,
    )
