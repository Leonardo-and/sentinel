"""A pista da simulação: paredes, objetos, alvos e faixas, tudo em milímetros.

A arena é um retângulo com paredes (segmentos), objetos (retângulos coloridos,
dos quais o robô pode segurar pallet), alvos de entrega e faixas pretas para os
sensores de linha. Quem manda é o ``pista.toml``, versionado no git:

    [pista]
    largura = 3000.0
    altura = 2000.0
    cor_piso = "branco"

    [[pista.paredes]]
    x1 = 0.0  y1 = 0.0  x2 = 3000.0  y2 = 0.0

    [[pista.objetos]]
    id = "pallet1"
    x = 500.0  y = 800.0
    largura = 250.0  altura = 180.0
    cor = "vermelho"

    [[pista.alvos]]
    nome = "prateleira_azul"
    x = 2400.0  y = 1600.0
    cor = "azul"

    [[pista.faixas]]        # faixa preta: o que o SL enxerga
    x1 = 0.0  y1 = 900.0  x2 = 3000.0  y2 = 900.0  largura = 40.0

    [montagem]             # onde o robô começa, no canto superior esquerdo
    x = 300.0  y = 300.0  angulo = 0.0

    [robo.camera]
    altura = 250.0          # acima do piso
    tilt = 25.0            # graus para baixo, em relação ao horizonte
    fov = 70.0             # abertura horizontal

    [robo.sonares]         # id = [x, y, ângulo]; ângulo 0 = frente, + = esquerda
    1 = [175.0, 140.0, 35.0]

    [robo.linha]           # id = [x, y]; 1 = esquerda, 2 = centro, 3 = direita
    1 = [110.0, 55.0]

O sistema é o mesmo do ``sentinel.toml``: chaves desconhecidas e valores fora de
faixa levantam ``ValueError`` na hora de carregar, em vez de o simulador
inventar uma pista silenciosamente diferente da que você editou.

Para salvar, o TOML é escrito na mão (:func:`salvar`) — o ``tomllib`` do
Python só lê, e puxar o ``tomli-w`` só para gravar um arquivo seria uma
dependência a mais num projeto que hoje só depende do ``pyserial``.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from math import hypot
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # ``Pose`` vive na cinemática, que importa este módulo.
    from sentinel.simulacao.cinematica import Pose
from pathlib import Path
from typing import Any

ARQUIVO_PADRAO = "pista.toml"
ENV_PISTA = "SENTINEL_PISTA"

# Segmento ``(x1, y1, x2, y2)`` em milímetros — a primitiva de colisão e sonar.
Segmento = tuple[float, float, float, float]

# Cores que o simulador sabe pintar, em BGR (a ordem do OpenCV). O mesmo nome
# usado em ``[visao.cores.*]``, para o objeto da pista e a cor que a rotina
# persegue serem a mesma palavra.
CORES_BGR: dict[str, tuple[int, int, int]] = {
    "vermelho": (0, 0, 220),
    "laranja": (0, 110, 240),
    "amarelo": (0, 220, 235),
    "verde": (0, 180, 40),
    "ciano": (220, 200, 0),
    "azul": (220, 70, 10),
    "magenta": (200, 20, 190),
    "roxo": (140, 40, 120),
    "branco": (245, 245, 245),
    "preto": (30, 30, 30),
    "cinza": (150, 150, 150),
}

# Largura do cone do sonar, em graus (Apostila §7.4: ~15°).
CONE_SONAR_GRAUS = 15.0
ALCANCE_SONAR_MM = 4000.0


# ----------------------------------------------------------------------------
# validação
# ----------------------------------------------------------------------------
def _conhecidas(dados: dict[str, Any], permitidas: set[str] | tuple[str, ...], secao: str) -> None:
    """Rejeita chave que o schema não conhece, nomeando a seção."""
    desconhecidas = set(dados) - set(permitidas)
    if desconhecidas:
        raise ValueError(f"chave(s) desconhecida(s) em [{secao}]: {sorted(desconhecidas)}")


def _num(dados: dict[str, Any], chave: str, padrao: float) -> float:
    """Lê um float aceitando o formato do robô (``99,6``) e o do TOML (``99.6``)."""
    valor = dados.get(chave, padrao)
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str)):
        raise ValueError(f"{chave}={valor!r} não é número")
    try:
        return float(str(valor).replace(",", "."))
    except ValueError:
        raise ValueError(f"{chave}={valor!r} não é número") from None


def _texto(dados: dict[str, Any], chave: str, padrao: str) -> str:
    valor = dados.get(chave, padrao)
    if not isinstance(valor, str):
        raise ValueError(f"{chave}={valor!r} não é texto")
    return valor.strip()


def _cor(valor: str, onde: str) -> str:
    """Normaliza o nome da cor e confere que o simulador sabe pintar."""
    cor = valor.strip().lower()
    if cor not in CORES_BGR:
        raise ValueError(
            f"{cor!r} em {onde} não é uma cor do simulador (conhecidas: {sorted(CORES_BGR)})"
        )
    return cor


def _booleano(dados: dict[str, Any], chave: str, padrao: bool) -> bool:
    valor = dados.get(chave, padrao)
    if not isinstance(valor, bool):
        raise ValueError(f"{chave}={valor!r} não é verdadeiro/falso")
    return valor


# ----------------------------------------------------------------------------
# geometria da pista
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Parede:
    """Segmento que bloqueia movimento e sonar. Espessura de 40 mm."""

    x1: float
    y1: float
    x2: float
    y2: float
    espessura: float = 40.0

    @property
    def comprimento(self) -> float:
        return ((self.x2 - self.x1) ** 2 + (self.y2 - self.y1) ** 2) ** 0.5


@dataclass(frozen=True)
class Objeto:
    """Retângulo colorido no chão. ``pegavel`` diz se o robô pode levantá-lo."""

    x: float
    y: float
    largura: float = 250.0
    altura: float = 180.0
    cor: str = "vermelho"
    id: str = ""
    pegavel: bool = True

    def __post_init__(self) -> None:
        if self.largura <= 0 or self.altura <= 0:
            raise ValueError(
                f"objeto {self.id or self.cor} com tamanho "
                f"{self.largura}x{self.altura} (tem de ser positivo)"
            )
        object.__setattr__(self, "cor", _cor(self.cor, "objeto"))

    @property
    def lados(self) -> tuple[tuple[float, float], ...]:
        """Os quatro cantos, em sentido anti-horário a partir de cima-esquerda."""
        meia_x, meia_y = self.largura / 2, self.altura / 2
        return (
            (self.x - meia_x, self.y - meia_y),
            (self.x - meia_x, self.y + meia_y),
            (self.x + meia_x, self.y + meia_y),
            (self.x + meia_x, self.y - meia_y),
        )

    def distancia_de(self, ponto: tuple[float, float] | Pose) -> float:
        """Distância do ponto ao retângulo (0 se estiver dentro).

        É a medida que importa para a garra: o robô encosta no pallet, não
        alinha o centro dele com o centro do pallet.
        """
        px, py = (ponto.x, ponto.y) if hasattr(ponto, "x") else tuple(ponto)
        dx = max(abs(px - self.x) - self.largura / 2, 0.0)
        dy = max(abs(py - self.y) - self.altura / 2, 0.0)
        return hypot(dx, dy)


@dataclass(frozen=True)
class Alvo:
    """Prateleira de entrega: um ponto onde o robô larga o que está segurando."""

    x: float
    y: float
    cor: str
    nome: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "cor", _cor(self.cor, "alvo"))


@dataclass(frozen=True)
class Faixa:
    """Faixa preta no chão, para os sensores de linha ``SL``.

    O trecho entre ``(x1, y1)`` e ``(x2, y2)``, com ``largura`` mm de espessura
    perpendicular. Fora dela o piso vale branco (``SL`` devolve 0).
    """

    x1: float
    y1: float
    x2: float
    y2: float
    largura: float = 40.0

    def __post_init__(self) -> None:
        if self.largura <= 0:
            raise ValueError(f"faixa com largura={self.largura} (tem de ser positivo)")

    @property
    def comprimento(self) -> float:
        return ((self.x2 - self.x1) ** 2 + (self.y2 - self.y1) ** 2) ** 0.5


@dataclass(frozen=True)
class Montagem:
    """Pose inicial do robô. ``angulo`` em graus, 0 = apontando para +x."""

    x: float = 200.0
    y: float = 200.0
    angulo: float = 0.0

    @classmethod
    def de_dict(cls, dados: dict[str, Any]) -> Montagem:
        _conhecidas(dados, ("x", "y", "angulo"), "montagem")
        return cls(
            x=_num(dados, "x", cls.x),
            y=_num(dados, "y", cls.y),
            angulo=_num(dados, "angulo", cls.angulo),
        )


# ----------------------------------------------------------------------------
# montagem do robô (inferida do manual — ajuste para o seu)
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Sonar:
    """Um dos oito sonares, no referencial do robô (``x`` frente, ``y`` direita).

    O ângulo é em graus, positivo no sentido horário da tela (para a direita do
    robô), que é a mesma convenção de :class:`~sentinel.simulacao.cinematica.Pose`.
    """

    id: int
    x: float
    y: float
    angulo: float
    desligado: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.id <= 8:
            raise ValueError(f"sonar id={self.id} fora de 1-8")


# Posições tiradas da figura do Guia §5.3 — (inferido), por isso ficam no
# ``pista.toml``: meça no robô e ajuste se divergir.
SONARES_PADRAO: tuple[Sonar, ...] = (
    Sonar(1, 175.0, -140.0, -35.0),  # canto frontal esquerdo
    Sonar(2, 210.0, 0.0, 0.0),  # frente central
    Sonar(3, 175.0, 140.0, 35.0),  # canto frontal direito
    Sonar(4, 0.0, 165.0, 90.0),  # lateral direita
    Sonar(5, -150.0, 130.0, 135.0),  # canto traseiro direito
    Sonar(6, -200.0, 0.0, 180.0),  # traseira central
    Sonar(7, -150.0, -130.0, -135.0),  # canto traseiro esquerdo
    Sonar(8, 0.0, -165.0, -90.0),  # lateral esquerda
)

# Três sensores que ``SL`` devolve (a PCI tem cinco; 4 e 5 vão para D1/D2,
# guia §3.13). 1 = esquerda, 2 = centro, 3 = direita — e, como no robô,
# a esquerda é ``y`` negativo.
LINHA_PADRAO: dict[int, tuple[float, float]] = {
    1: (110.0, -55.0),
    2: (110.0, 0.0),
    3: (110.0, 55.0),
}


@dataclass(frozen=True)
class CameraArena:
    """Câmera virtual: posição e abertura da lente, para gerar o quadro da visão."""

    altura: float = 250.0  # acima do piso, mm
    tilt: float = 25.0  # graus para baixo a partir do horizonte
    fov: float = 70.0  # abertura horizontal, graus

    def __post_init__(self) -> None:
        for nome, valor, teto in (
            ("altura", self.altura, 2000.0),
            ("tilt", self.tilt, 89.0),
            ("fov", self.fov, 179.0),
        ):
            if not -teto <= valor <= teto:
                raise ValueError(f"câmera {nome}={valor} fora de [-{teto}, {teto}]")

    @classmethod
    def de_dict(cls, dados: dict[str, Any]) -> CameraArena:
        _conhecidas(dados, ("altura", "tilt", "fov"), "robo.camera")
        return cls(
            altura=_num(dados, "altura", cls.altura),
            tilt=_num(dados, "tilt", cls.tilt),
            fov=_num(dados, "fov", cls.fov),
        )


@dataclass(frozen=True)
class RoboArena:
    """A parte do simulado que é do robô, não da pista.

    Os defaults vêm do manual (roda de 100 mm, corpo octogonal) e do que a
    Apostila diz da placa. Como a montagem dos sonares é *inferida*, ela é
    sobrescrevível no ``pista.toml`` — meça no robô e ajuste.
    """

    raio_corpo: float = 200.0  # colisão: círculo circunscrito ao octógono
    raio_pegagem: float = 250.0  # distância em que o elevador pega o objeto
    altura_elevador: float = 400.0  # curso do garfo/eletroímão, mm
    duracao_elevador: float = 7.0  # segundos de curso completo (guia §3.22)
    sonares: tuple[Sonar, ...] = SONARES_PADRAO
    linha: dict[int, tuple[float, float]] = field(default_factory=lambda: dict(LINHA_PADRAO))
    camera: CameraArena = CameraArena()

    def __post_init__(self) -> None:
        if self.raio_corpo <= 0 or self.raio_pegagem <= 0:
            raise ValueError("raio_corpo e raio_pegagem têm de ser positivos")
        if self.altura_elevador <= 0 or self.duracao_elevador <= 0:
            raise ValueError("o elevador precisa de curso e duração positivos")

    @classmethod
    def de_dict(cls, dados: dict[str, Any]) -> RoboArena:
        _conhecidas(
            dados,
            (
                "raio_corpo",
                "raio_pegagem",
                "altura_elevador",
                "duracao_elevador",
                "sonares",
                "linha",
                "camera",
            ),
            "robo",
        )
        base = cls()
        sonares = _sonares_de_dict(dados.get("sonares", {}), base.sonares)
        linha = _linha_de_dict(dados.get("linha", {}), base.linha)
        camera = CameraArena.de_dict(dados.get("camera", {}))
        return cls(
            raio_corpo=_num(dados, "raio_corpo", base.raio_corpo),
            raio_pegagem=_num(dados, "raio_pegagem", base.raio_pegagem),
            altura_elevador=_num(dados, "altura_elevador", base.altura_elevador),
            duracao_elevador=_num(dados, "duracao_elevador", base.duracao_elevador),
            sonares=sonares,
            linha=linha,
            camera=camera,
        )


def _pares_floats(bruto: Any, onde: str) -> list[float]:
    """``[175.0, 140.0, 35.0]`` → três floats, com erroamdo nome se não couber."""
    if not isinstance(bruto, (list, tuple)):
        raise ValueError(f"{onde}={bruto!r} não é uma lista de números")
    saida = []
    for item in bruto:
        if isinstance(item, bool) or not isinstance(item, (int, float, str)):
            raise ValueError(f"{onde} tem {item!r}, que não é número")
        try:
            saida.append(float(str(item).replace(",", ".")))
        except ValueError:
            raise ValueError(f"{onde} tem {item!r}, que não é número") from None
    return saida


def _sonares_de_dict(bruto: Any, padrao: tuple[Sonar, ...]) -> tuple[Sonar, ...]:
    """Lê ``[robo.sonares]`` (``id = [x, y, ângulo]``) sobre o padrão do manual.

    Uma chave que não é número é entendida como nome: ``SS2 = [210, 0, 0]``.
    Aceitar os dois jeitos evita ter que decorar se é ``1`` ou ``"SS1"``.
    """
    if not isinstance(bruto, dict):
        raise ValueError("[robo.sonares] precisa ser uma tabela")
    if not bruto:
        return padrao
    por_id = {s.id: s for s in padrao}
    for chave, valor in bruto.items():
        try:
            numero = int(str(chave).strip().upper().removeprefix("SS"))
        except ValueError:
            raise ValueError(f"[robo.sonares] chave {chave!r} não é um id de 1 a 8") from None
        if not 1 <= numero <= 8:
            raise ValueError(f"[robo.sonares] id={numero} fora de 1-8")
        posicao = _pares_floats(valor, f"[robo.sonares] {chave}")
        desligado = False
        if len(posicao) == 4:
            desligado = bool(posicao[3])
            posicao = posicao[:3]
        if len(posicao) != 3:
            raise ValueError(
                f"[robo.sonares] {chave} precisa de [x, y, ângulo] "
                f"(opcionalmente um 4º valor para desligar o sensor)"
            )
        por_id[numero] = Sonar(numero, posicao[0], posicao[1], posicao[2], desligado)
    return tuple(por_id[i] for i in sorted(por_id))


def _linha_de_dict(
    bruto: Any, padrao: dict[int, tuple[float, float]]
) -> dict[int, tuple[float, float]]:
    """Lê ``[robo.linha]`` (``id = [x, y]``) sobre o padrão do manual."""
    if not isinstance(bruto, dict):
        raise ValueError("[robo.linha] precisa ser uma tabela")
    if not bruto:
        return dict(padrao)
    saida = dict(padrao)
    for chave, valor in bruto.items():
        try:
            numero = int(str(chave).strip().upper().removeprefix("SL"))
        except ValueError:
            raise ValueError(f"[robo.linha] chave {chave!r} não é um id de 1 a 3") from None
        if numero not in (1, 2, 3):
            raise ValueError(f"[robo.linha] id={numero} fora de 1-3 (o SL devolve 3)")
        posicao = _pares_floats(valor, f"[robo.linha] {chave}")
        if len(posicao) != 2:
            raise ValueError(f"[robo.linha] {chave} precisa de [x, y]")
        saida[numero] = (posicao[0], posicao[1])
    return saida


# ----------------------------------------------------------------------------
# a arena
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Arena:
    """A pista inteira: onde o robô nasce, o que ele vê e o que o impede."""

    largura: float = 3000.0
    altura: float = 2000.0
    cor_piso: str = "branco"
    paredes: tuple[Parede, ...] = ()
    objetos: tuple[Objeto, ...] = ()
    alvos: tuple[Alvo, ...] = ()
    faixas: tuple[Faixa, ...] = ()
    montagem: Montagem = Montagem()
    robo: RoboArena = field(default_factory=RoboArena)

    def __post_init__(self) -> None:
        if self.largura <= 0 or self.altura <= 0:
            raise ValueError(f"pista {self.largura}x{self.altura} (tem de ser positivo)")
        object.__setattr__(self, "cor_piso", _cor(self.cor_piso, "pista.cor_piso"))
        vistos: set[str] = set()
        for objeto in self.objetos:
            if objeto.id and objeto.id in vistos:
                raise ValueError(f"id de objeto repetido: {objeto.id!r}")
            vistos.add(objeto.id)

    # --- consultas ----------------------------------------------------------
    def dentro(self, x: float, y: float, margem: float = 0.0) -> bool:
        """O ponto está dentro do retângulo da pista, com folga opcional?"""
        return margem <= x <= self.largura - margem and margem <= y <= self.altura - margem

    def obstaculos_de(self, objetos: Iterable[Objeto]) -> tuple[Segmento, ...]:
        """Segmentos que bloqueiam movimento e sonar: paredes + lados dos objetos.

        Recebe a lista de objetos **que continuam no chão** — um pallet que o
        robô já segurou não atrapalha mais ninguém, então some daqui.
        """
        segmentos: list[Segmento] = [(p.x1, p.y1, p.x2, p.y2) for p in self.paredes]
        for objeto in objetos:
            cantos = objeto.lados
            for indice in range(4):
                inicio, fim = cantos[indice], cantos[(indice + 1) % 4]
                segmentos.append((inicio[0], inicio[1], fim[0], fim[1]))
        return tuple(segmentos)

    def obstaculos(self) -> tuple[Segmento, ...]:
        """Atalho para :meth:`obstaculos_de` com todos os objetos."""
        return self.obstaculos_de(self.objetos)

    def objeto_por_id(self, identificador: str) -> Objeto | None:
        return next((o for o in self.objetos if o.id == identificador), None)

    def mais_proximo(self, x: float, y: float, cor: str | None = None) -> Objeto | None:
        """Objeto (do tipo ``cor``, se pedido) cujo centro está mais perto do ponto."""
        candidatos = [o for o in self.objetos if cor is None or o.cor == cor]
        if not candidatos:
            return None
        return min(candidatos, key=lambda o: (o.x - x) ** 2 + (o.y - y) ** 2)

    def substitui_objeto(self, objeto: Objeto) -> Arena:
        """Cópia da arena com ``objeto`` trocado (ou removido, se ``None``)."""
        restantes = tuple(o for o in self.objetos if o.id != objeto.id)
        if objeto is not None:
            restantes = restantes + (objeto,)
        return replace(self, objetos=restantes)


# ----------------------------------------------------------------------------
# leitura do TOML
# ----------------------------------------------------------------------------
def achar_arquivo(start: Path | None = None) -> Path | None:
    """Caminho do ``pista.toml`` a usar, ou ``None`` se não houver.

    A busca é ``$SENTINEL_PISTA`` → ``pista.toml`` no diretório atual e nos
    pastas acima, como no ``sentinel.toml``.
    """
    do_env = os.environ.get(ENV_PISTA)
    if do_env:
        caminho = Path(do_env).expanduser()
        if not caminho.is_file():
            raise FileNotFoundError(f"{ENV_PISTA} aponta para arquivo inexistente: {caminho}")
        return caminho

    atual = (start or Path.cwd()).resolve()
    for pasta in (atual, *atual.parents):
        candidato = pasta / ARQUIVO_PADRAO
        if candidato.is_file():
            return candidato
    return None


def _parede_de_dict(dados: dict[str, Any]) -> Parede:
    _conhecidas(dados, ("x1", "y1", "x2", "y2", "espessura"), "pista.paredes")
    return Parede(
        x1=_num(dados, "x1", 0.0),
        y1=_num(dados, "y1", 0.0),
        x2=_num(dados, "x2", 0.0),
        y2=_num(dados, "y2", 0.0),
        espessura=_num(dados, "espessura", Parede.espessura),
    )


def _objeto_de_dict(dados: dict[str, Any]) -> Objeto:
    _conhecidas(dados, ("id", "x", "y", "largura", "altura", "cor", "pegavel"), "pista.objetos")
    return Objeto(
        x=_num(dados, "x", 0.0),
        y=_num(dados, "y", 0.0),
        largura=_num(dados, "largura", Objeto.largura),
        altura=_num(dados, "altura", Objeto.altura),
        cor=_texto(dados, "cor", Objeto.cor),
        id=_texto(dados, "id", ""),
        pegavel=_booleano(dados, "pegavel", Objeto.pegavel),
    )


def _alvo_de_dict(dados: dict[str, Any]) -> Alvo:
    _conhecidas(dados, ("nome", "x", "y", "cor"), "pista.alvos")
    return Alvo(
        x=_num(dados, "x", 0.0),
        y=_num(dados, "y", 0.0),
        cor=_texto(dados, "cor", ""),
        nome=_texto(dados, "nome", ""),
    )


def _faixa_de_dict(dados: dict[str, Any]) -> Faixa:
    _conhecidas(dados, ("x1", "y1", "x2", "y2", "largura"), "pista.faixas")
    return Faixa(
        x1=_num(dados, "x1", 0.0),
        y1=_num(dados, "y1", 0.0),
        x2=_num(dados, "x2", 0.0),
        y2=_num(dados, "y2", 0.0),
        largura=_num(dados, "largura", Faixa.largura),
    )


def _tabelas(dados: dict[str, Any], chave: str, conversor: Any, secao: str) -> tuple[Any, ...]:
    bruto = dados.get(chave, [])
    if not isinstance(bruto, list):
        raise ValueError(f"[[{secao}]] precisa ser uma lista de tabelas")
    return tuple(conversor(item) for item in bruto)


def carregar(path: Path | str | None = None) -> Arena:
    """Lê a pista do TOML; sem arquivo, devolve a arena de demonstração.

    A arena de demonstração é uma sala de 3 × 2 m com um pallet vermelho no
    meio, uma prateleira azul no canto e uma faixa preta atravessando — o
    mínimo para a rotina inicial não bater em parede logo no primeiro teste.
    """
    caminho = Path(path) if path is not None else achar_arquivo()
    if caminho is None or not caminho.is_file():
        return exemplo()
    with open(caminho, "rb") as arquivo:
        dados = tomllib.load(arquivo)

    secao = dados.get("pista", {})
    if not isinstance(secao, dict):
        raise ValueError("[pista] precisa ser uma tabela")
    _conhecidas(
        secao, {"largura", "altura", "cor_piso", "paredes", "objetos", "alvos", "faixas"}, "pista"
    )

    base = exemplo()
    arena = Arena(
        largura=_num(secao, "largura", base.largura),
        altura=_num(secao, "altura", base.altura),
        cor_piso=_texto(secao, "cor_piso", base.cor_piso),
        paredes=_tabelas(secao, "paredes", _parede_de_dict, "pista.paredes"),
        objetos=_tabelas(secao, "objetos", _objeto_de_dict, "pista.objetos"),
        alvos=_tabelas(secao, "alvos", _alvo_de_dict, "pista.alvos"),
        faixas=_tabelas(secao, "faixas", _faixa_de_dict, "pista.faixas"),
        montagem=Montagem.de_dict(dados.get("montagem", {})),
        robo=RoboArena.de_dict(dados.get("robo", {})),
    )
    return arena


# ----------------------------------------------------------------------------
# escrita do TOML (à mão: o tomllib só lê)
# ----------------------------------------------------------------------------
def _fmt(valor: float) -> str:
    """Float para o TOML: inteiro sem ``.0``, decimal com ponto."""
    if valor == int(valor):
        return str(int(valor))
    return repr(round(float(valor), 3))


def _linha(chave: str, valor: Any) -> str:
    if isinstance(valor, bool):
        return f"{chave} = {'true' if valor else 'false'}"
    if isinstance(valor, str):
        return f'{chave} = "{valor}"'
    if isinstance(valor, float):
        return f"{chave} = {_fmt(valor)}"
    if isinstance(valor, (list, tuple)):
        return f"{chave} = [{', '.join(_fmt(float(v)) for v in valor)}]"
    return f"{chave} = {valor}"


def _dump(arena: Arena) -> str:
    """Serializa a arena no mesmo schema que :func:`carregar` lê."""
    linhas: list[str] = [
        "# Pista do simulador. Gerado pelo painel (botão Salvar);",
        "# editar à mão funciona igual. Medidas em milímetros.",
        "",
        "[pista]",
        _linha("largura", arena.largura),
        _linha("altura", arena.altura),
        _linha("cor_piso", arena.cor_piso),
        "",
    ]
    tabelas = (
        ("paredes", arena.paredes, ("x1", "y1", "x2", "y2", "espessura")),
        ("alvos", arena.alvos, ("nome", "x", "y", "cor")),
        ("faixas", arena.faixas, ("x1", "y1", "x2", "y2", "largura")),
    )
    for nome, itens, campos in tabelas:
        for item in itens:
            linhas.append(f"[[pista.{nome}]]")
            linhas.extend(_linha(campo, getattr(item, campo)) for campo in campos)
            linhas.append("")
    for objeto in arena.objetos:
        linhas.append("[[pista.objetos]]")
        linhas.extend(
            _linha(campo, getattr(objeto, campo))
            for campo in ("id", "x", "y", "largura", "altura", "cor", "pegavel")
        )
        linhas.append("")

    montagem = arena.montagem
    linhas += [
        "[montagem]",
        _linha("x", montagem.x),
        _linha("y", montagem.y),
        _linha("angulo", montagem.angulo),
        "",
    ]

    robo = arena.robo
    linhas += [
        "[robo]",
        _linha("raio_corpo", robo.raio_corpo),
        _linha("raio_pegagem", robo.raio_pegagem),
        _linha("altura_elevador", robo.altura_elevador),
        _linha("duracao_elevador", robo.duracao_elevador),
        "",
    ]
    linhas += [
        "[robo.camera]",
        _linha("altura", robo.camera.altura),
        _linha("tilt", robo.camera.tilt),
        _linha("fov", robo.camera.fov),
        "",
    ]
    linhas += ["# id = [x, y, ângulo]; ângulo 0 = frente, positivo = à esquerda.", "[robo.sonares]"]
    linhas += [
        _linha(str(s.id), [s.x, s.y, s.angulo, 1.0 if s.desligado else 0.0]) for s in robo.sonares
    ]
    linhas += ["", "# id = [x, y]; 1 = esquerda, 2 = centro, 3 = direita.", "[robo.linha]"]
    linhas += [_linha(str(i), list(robo.linha[i])) for i in sorted(robo.linha)]
    linhas.append("")
    return "\n".join(linhas)


def salvar(arena: Arena, path: Path | str) -> Path:
    """Grava a arena no TOML e devolve o caminho usado."""
    destino = Path(path)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(_dump(arena), encoding="utf-8")
    return destino


def padrao_pista() -> str:
    """O ``pista.toml`` de demonstração, como texto (para escrever no disco)."""
    return _dump(exemplo())


# ----------------------------------------------------------------------------
# a arena de demonstração
# ----------------------------------------------------------------------------
def exemplo() -> Arena:
    """Sala de 3 × 2 m: paredes nas bordas, um pallet e uma prateleira.

    O pallet vermelho fica no meio do caminho e a prateleira azul no canto
    oposto — o cenário mínimo para a rota de pallet (guia §6.5) andar e largar
    alguma coisa, e para o desvio de sonar (guia §6.4) ter o que desviar.
    """
    largura, altura = 3000.0, 2000.0
    return Arena(
        largura=largura,
        altura=altura,
        cor_piso="branco",
        paredes=(
            Parede(0.0, 0.0, largura, 0.0),
            Parede(largura, 0.0, largura, altura),
            Parede(largura, altura, 0.0, altura),
            Parede(0.0, altura, 0.0, 0.0),
        ),
        objetos=(Objeto(1500.0, 700.0, 300.0, 220.0, "vermelho", "pallet1"),),
        alvos=(Alvo(2600.0, 1600.0, "azul", "prateleira_azul"),),
        faixas=(Faixa(0.0, 1000.0, largura, 1000.0, 40.0),),
        montagem=Montagem(300.0, 300.0, 0.0),
    )
