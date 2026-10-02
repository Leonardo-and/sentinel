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

A mesma leitura alimenta os módulos de visão (``[visao]``, ``[visao.cores.*]``)
e de controle (``[controle]``), que têm funções próprias — :func:`load_config`
continua cuidando só do robô.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
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


def _ler_dados(path: Path | None = None) -> tuple[dict[str, Any], Path | None]:
    """Abre o TOML uma vez e devolve o conteúdo cru (ou ``{}``)."""
    caminho = path or find_config_file()
    if caminho is None:
        return {}, None
    with open(caminho, "rb") as fh:
        return tomllib.load(fh), caminho


def load_config(path: Path | None = None) -> Config:
    """Carrega a configuração; sem arquivo, devolve os valores nominais."""
    dados, _ = _ler_dados(path)

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


# ----------------------------------------------------------------------------
# visão: faixas HSV e parâmetros da câmera
# ----------------------------------------------------------------------------
# No OpenCV o matiz vai de 0 a 179 e o vermelho Involta o zero: para ele ser
# uma faixa contígua, h_min > h_max significa "de h_min até 179 e de 0 até h_max".
@dataclass(frozen=True)
class FaixaHSV:
    """Intervalo de matiz/saturação/valor que define uma cor na câmera."""

    h_min: int = 0
    h_max: int = 179
    s_min: int = 0
    s_max: int = 255
    v_min: int = 0
    v_max: int = 255

    def __post_init__(self) -> None:
        for nome, valor, teto in (("h_min", self.h_min, 179), ("h_max", self.h_max, 179),
                                  ("s_min", self.s_min, 255), ("s_max", self.s_max, 255),
                                  ("v_min", self.v_min, 255), ("v_max", self.v_max, 255)):
            if not 0 <= valor <= teto:
                raise ValueError(f"{nome}={valor} fora de [0, {teto}]")
        for nome, a, b in (("saturação", self.s_min, self.s_max),
                            ("valor", self.v_min, self.v_max)):
            if a > b:
                raise ValueError(f"{nome}: {a} > {b} (faixa invertida)")

    def contem(self, h: int, s: int, v: int) -> bool:
        """O pixel HSV (h, s, v) está dentro da faixa?"""
        if self.h_min <= self.h_max:
            dentro_h = self.h_min <= h <= self.h_max
        else:
            dentro_h = h >= self.h_min or h <= self.h_max
        return dentro_h and self.s_min <= s <= self.s_max and self.v_min <= v <= self.v_max

    def limites(self) -> list[tuple[list[int], list[int]]]:
        """Pares ``(baixo, alto)`` para ``cv2.inRange``.

        Uma faixa contínua vira um par só. O vermelho, que envolve o zero do
        matiz, vira dois: 0→``h_max`` e ``h_min``→179.
        """
        if self.h_min <= self.h_max:
            return [([self.h_min, self.s_min, self.v_min], [self.h_max, self.s_max, self.v_max])]
        return [([0, self.s_min, self.v_min], [self.h_max, self.s_max, self.v_max]),
                ([self.h_min, self.s_min, self.v_min], [179, self.s_max, self.v_max])]

    @classmethod
    def de_dict(cls, dados: dict[str, Any], base: FaixaHSV | None = None) -> FaixaHSV:
        """Constrói a faixa a partir de uma seção ``[visao.cores.<cor>]``.

        As chaves omitidas vêm de ``base`` (a faixa padrão daquela cor), para
        bastar escrever o que mudou — por exemplo só o ``s_min`` do vermelho.
        """
        chaves = ("h_min", "h_max", "s_min", "s_max", "v_min", "v_max")
        desconhecidas = set(dados) - set(chaves)
        if desconhecidas:
            raise ValueError(f"chave(s) desconhecida(s) na faixa: {sorted(desconhecidas)}")
        if base is None:
            base = cls()
        completo = {nome: getattr(base, nome) for nome in chaves}
        completo.update({k: int(v) for k, v in dados.items()})
        return cls(**completo)



# Pontos de partida, não valores medidos: calibre com ``main.py --debug-visao``.
CORES_PADRAO: dict[str, FaixaHSV] = {
    "vermelho": FaixaHSV(h_min=170, h_max=10, s_min=120, v_min=40),
    "amarelo": FaixaHSV(h_min=20, h_max=35, s_min=120, v_min=80),
    "verde": FaixaHSV(h_min=40, h_max=85, s_min=90, v_min=40),
    "azul": FaixaHSV(h_min=100, h_max=130, s_min=90, v_min=30),
    "ciano": FaixaHSV(h_min=80, h_max=99, s_min=90, v_min=40),
    "magenta": FaixaHSV(h_min=135, h_max=169, s_min=90, v_min=40),
    "branco": FaixaHSV(h_min=0, h_max=179, s_min=0, s_max=60, v_min=180),
    "preto": FaixaHSV(h_min=0, h_max=179, s_min=0, s_max=255, v_min=0, v_max=45),
}


@dataclass(frozen=True)
class VisaoConfig:
    """Câmera, faixa de detecção e limites de área do bloco de cor."""

    dispositivo: int = 0
    largura: int = 320
    altura: int = 240
    area_min: int = 300
    area_max: int = 60000
    frames_estaveis: int = 3
    abrir_kernel: int = 5
    fechar_kernel: int = 5
    cores: dict[str, FaixaHSV] = field(default_factory=lambda: dict(CORES_PADRAO))

    def __post_init__(self) -> None:
        if self.dispositivo < 0:
            raise ValueError(f"dispositivo={self.dispositivo} inválido")
        if self.largura <= 0 or self.altura <= 0:
            raise ValueError(f"resolução {self.largura}x{self.altura} inválida")
        if self.area_min <= 0 or self.area_max <= self.area_min:
            raise ValueError(f"área fora de faixa: [{self.area_min}, {self.area_max}]")
        if self.frames_estaveis < 1:
            raise ValueError(f"frames_estaveis={self.frames_estaveis} precisa ser >= 1")
        for nome, valor in (("abrir_kernel", self.abrir_kernel),
                             ("fechar_kernel", self.fechar_kernel)):
            if valor < 0 or valor % 2 == 0 and valor != 0:
                raise ValueError(f"{nome}={valor} precisa ser 0 ou um ímpar")

    def faixa(self, cor: str) -> FaixaHSV:
        """Faixa de uma cor conhecida; levanta ValueError se não existir."""
        try:
            return self.cores[cor.strip().lower()]
        except KeyError:
            raise ValueError(
                f"cor desconhecida: {cor!r} (conhecidas: {sorted(self.cores)})"
            ) from None


# ----------------------------------------------------------------------------
# controle: Logitech F710
# ----------------------------------------------------------------------------
# As chaves são os códigos crus que a biblioteca ``inputs`` emite (ex.: BTN_SOUTH).
# Descubra os seus com ``main.py --listar-botoes`` em vez de confiar no chute.
BOTOES_PADRAO: dict[str, str] = {
    "BTN_SOUTH": "vermelho",
    "BTN_EAST": "azul",
    "BTN_NORTH": "verde",
    "BTN_WEST": "amarelo",
}
PARADA_PADRAO: tuple[str, ...] = ("BTN_START", "BTN_SELECT")


@dataclass(frozen=True)
class ControleConfig:
    """Mapa botão → cor e botões de parada de emergência."""

    botoes: dict[str, str] = field(default_factory=lambda: dict(BOTOES_PADRAO))
    parada: tuple[str, ...] = PARADA_PADRAO

    def acao(self, codigo: str) -> str | None:
        """Cor asociada ao botão, ou ``"parada"``, ou ``None`` se não mapeado."""
        codigo = codigo.strip().upper()
        if codigo in {p.strip().upper() for p in self.parada}:
            return "parada"
        return self.botoes.get(codigo)

    def __post_init__(self) -> None:
        for codigo, cor in self.botoes.items():
            if not isinstance(cor, str) or not cor.strip():
                raise ValueError(f"botão {codigo!r} sem cor associada")
            if cor.strip() == "parada":
                raise ValueError("use [controle.parada] para parada, não [controle.botoes]")
        for codigo in self.parada:
            if not isinstance(codigo, str) or not codigo.strip():
                raise ValueError("botão de parada vazio em [controle.parada]")


def load_visao(path: Path | None = None) -> VisaoConfig:
    """Lê ``[visao]`` e ``[visao.cores.*]``; cores ausentes ficam no padrão."""
    dados, _ = _ler_dados(path)
    secao = dados.get("visao", {})
    if not isinstance(secao, dict):
        raise ValueError("[visao] precisa ser uma tabela")

    chaves = {f.name for f in fields(VisaoConfig)} - {"cores"}
    desconhecidas = set(secao) - chaves - {"cores"}
    if desconhecidas:
        raise ValueError(f"chave(s) desconhecida(s) em [visao]: {sorted(desconhecidas)}")

    cores = dict(CORES_PADRAO)
    for nome, faixa in secao.get("cores", {}).items():
        chave = nome.strip().lower()
        cores[chave] = FaixaHSV.de_dict(faixa, base=cores.get(chave))

    return VisaoConfig(
        dispositivo=int(secao.get("dispositivo", VisaoConfig.dispositivo)),
        largura=int(secao.get("largura", VisaoConfig.largura)),
        altura=int(secao.get("altura", VisaoConfig.altura)),
        area_min=int(secao.get("area_min", VisaoConfig.area_min)),
        area_max=int(secao.get("area_max", VisaoConfig.area_max)),
        frames_estaveis=int(secao.get("frames_estaveis", VisaoConfig.frames_estaveis)),
        abrir_kernel=int(secao.get("abrir_kernel", VisaoConfig.abrir_kernel)),
        fechar_kernel=int(secao.get("fechar_kernel", VisaoConfig.fechar_kernel)),
        cores=cores,
    )


def load_controle(path: Path | None = None) -> ControleConfig:
    """Lê ``[controle.botoes]`` e ``[controle.parada]``."""
    dados, _ = _ler_dados(path)
    secao = dados.get("controle", {})
    if not isinstance(secao, dict):
        raise ValueError("[controle] precisa ser uma tabela")
    desconhecidas = set(secao) - {"botoes", "parada"}
    if desconhecidas:
        raise ValueError(f"chave(s) desconhecida(s) em [controle]: {sorted(desconhecidas)}")

    botoes = dict(BOTOES_PADRAO)
    for codigo, cor in secao.get("botoes", {}).items():
        botoes[str(codigo).strip().upper()] = str(cor)
    parada = secao.get("parada", list(PARADA_PADRAO))
    if isinstance(parada, str):
        raise ValueError("[controle.parada] precisa ser uma lista de códigos")
    return ControleConfig(botoes=botoes, parada=tuple(str(p) for p in parada))
