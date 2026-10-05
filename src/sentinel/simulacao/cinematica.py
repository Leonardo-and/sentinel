"""A matemática que faz o robô andar: pose, rampas, curvas, sensores e colisão.

Tudo aqui é cinemático puro — sem inércia, sem escorregamento, sem atrito. É a
mesma simplificação que o robô real já faz por dentro, já que ele também decide
*timestamps* e não forças: o ``MT0 D1000 AT1000 DT1000 V10`` vira "ande 1000 mm
com estas rampas", e o simulador só precisa saber **onde o robô estava antes e
onde ele está depois**.

O referencial é o mesmo do ``pista.toml``: ``x`` cresce para a **direita**,
``y`` para **baixo**, ângulo em **radianos** com 0 apontando para ``+x`` e
crescendo no sentido **horário** na tela. É a convenção natural de uma arena
vista de cima, e evita o sinal invertido que aparece quando se usa matemática
de sala de exposição num desenho visto de cima.

    >>> ir_para_frente(Pose(0, 0, 0), 500).x
    500.0

A pose do robô é no **centro do corpo**, e não na roda: assim a colisão é um
círculo em volta do ponto, e não um retângulo torto para girar.

O truque que vale mais aqui é :func:`aplicar_ganhos`. Os ganhos ``PG`` do
``sentinel.toml`` descrevem o quanto o robô erra a favor ou contra o
comando — com ``so = 1.35``, mandar 1000 mm faz o robô andar 1013,5. Aplicar
isso aqui mostra a rota caindo fora **antes** de você descobrir no pátio.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from .pista import ALCANCE_SONAR_MM, CONE_SONAR_GRAUS, Arena, Objeto, Segmento, Sonar

RAD_POR_GRAU = math.pi / 180.0
GRAUS_POR_RAD = 180.0 / math.pi


@dataclass(frozen=True)
class Pose:
    """Posição e direção do robô. ``angulo`` em radianos, 0 = frente (+x)."""

    x: float = 0.0
    y: float = 0.0
    angulo: float = 0.0

    @classmethod
    def de_graus(cls, x: float, y: float, angulo_graus: float) -> Pose:
        return cls(x, y, angulo_graus * RAD_POR_GRAU)

    @property
    def angulo_graus(self) -> float:
        return self.angulo * GRAUS_POR_RAD

    def girada(self, delta_rad: float) -> Pose:
        return Pose(self.x, self.y, self.angulo + delta_rad)

    def avancado(self, distancia_mm: float) -> Pose:
        """Some ``distancia_mm`` no rumo atual. Negativo = para trás."""
        return Pose(
            self.x + distancia_mm * math.cos(self.angulo),
            self.y + distancia_mm * math.sin(self.angulo),
            self.angulo,
        )

    def virado_para(self, alvo: tuple[float, float]) -> Pose:
        """Mesma posição, apontando para o ponto dado."""
        dx, dy = alvo[0] - self.x, alvo[1] - self.y
        return Pose(self.x, self.y, math.atan2(dy, dx))

    def distancia_ate(self, alvo: Pose | tuple[float, float]) -> float:
        dx = alvo.x - self.x if isinstance(alvo, Pose) else alvo[0] - self.x
        dy = alvo.y - self.y if isinstance(alvo, Pose) else alvo[1] - self.y
        return math.hypot(dx, dy)

    def para_local(self, ponto: tuple[float, float]) -> tuple[float, float]:
        """Ponto do mundo para o referencial do robô (x frente, y direita).

        ``y`` positivo é a **direita** do robô — igual ao ``+y`` da tela, já que
        o ângulo cresce no sentido horário. É a convenção que
        :class:`~sentinel.simulacao.pista.Sonar` e ``LINHA_PADRAO`` seguem.
        """
        dx, dy = ponto[0] - self.x, ponto[1] - self.y
        cos, sen = math.cos(self.angulo), math.sin(self.angulo)
        return dx * cos + dy * sen, -dx * sen + dy * cos

    def para_mundo(self, local: tuple[float, float]) -> tuple[float, float]:
        """Referencial do robô (``x`` frente, ``y`` direita) para o mundo."""
        cos, sen = math.cos(self.angulo), math.sin(self.angulo)
        return self.x + local[0] * cos - local[1] * sen, self.y + local[0] * sen + local[1] * cos


def normalizar_graus(graus: float) -> float:
    """Junta ``-90`` e ``270`` num só valor, em ``[-180, 180)``."""
    return (graus + 180.0) % 360.0 - 180.0


# ----------------------------------------------------------------------------
# rampas: quanto tempo um movimento leva
# ----------------------------------------------------------------------------
def duracao_ms(distancia_mm: float, v_cm_s: int, at_ms: int, dt_ms: int) -> float:
    """Tempo total de um movimento em modo fixo, em milissegundos.

    Reproduz o perfil trapezoidal do ``MT0``: acelera em ``AT`` até ``V``, segura
    o cruzeiro e desacelera em ``DT``. Quando a distância é curta demais para
    alcançar ``V``, as rampas se cruzam e vira um perfil triangular — o mesmo
    ``MRUV`` que o manual usa nas fórmulas de calibração (guia §1.4).
    """
    distancia = abs(float(distancia_mm))
    if distancia == 0.0 or v_cm_s <= 0:
        # ``V0`` não anda: o robô só gasta as rampas ligando e desligando.
        return float(at_ms + dt_ms)
    v_mm_s = float(v_cm_s) * 10.0
    d_acel = 0.5 * v_mm_s * (at_ms / 1000.0)
    d_desacel = 0.5 * v_mm_s * (dt_ms / 1000.0)
    if distancia >= d_acel + d_desacel:
        return float(at_ms + (distancia - d_acel - d_desacel) / v_mm_s * 1000.0 + dt_ms)

    # Triangular: resolve pelo pico de velocidade v' = sqrt(2·s/(1/a1 + 1/a2)).
    a1 = v_mm_s / (at_ms / 1000.0) if at_ms else math.inf
    a2 = v_mm_s / (dt_ms / 1000.0) if dt_ms else math.inf
    inverso = (1 / a1 if a1 != math.inf else 0.0) + (1 / a2 if a2 != math.inf else 0.0)
    if inverso <= 0.0:
        return 0.0
    v_pico = math.sqrt(2 * distancia / inverso)
    return 1000.0 * (
        (v_pico / a1 if a1 != math.inf else 0.0) + (v_pico / a2 if a2 != math.inf else 0.0)
    )


def caminho_no_tempo(
    distancia_mm: float, v_cm_s: int, at_ms: int, dt_ms: int, fracao: float
) -> float:
    """Quanto já andou quando ``fracao`` do tempo total passou (``0.0``-``1.0``).

    É a curva posição×tempo do perfil trapezoidal. Serve para o painel desenhar
    o robô deslizando no tempo certo — e para simular um ``V`` menor do que o
    do robô real, sem reescrever a cinemática.
    """
    fracao = max(0.0, min(1.0, fracao))
    if distancia_mm == 0.0 or v_cm_s <= 0:
        return 0.0
    sinal = 1.0 if distancia_mm > 0 else -1.0
    distancia = abs(float(distancia_mm))
    v_mm_s = float(v_cm_s) * 10.0
    at_s, dt_s = at_ms / 1000.0, dt_ms / 1000.0
    a1 = v_mm_s / at_s if at_s else math.inf
    a2 = v_mm_s / dt_s if dt_s else math.inf
    d_acel = 0.5 * v_mm_s * at_s
    d_desacel = 0.5 * v_mm_s * dt_s

    total = duracao_ms(distancia, v_cm_s, at_ms, dt_ms) / 1000.0
    t = fracao * total
    if distancia < d_acel + d_desacel:  # triangular
        inverso = (1 / a1 if a1 != math.inf else 0.0) + (1 / a2 if a2 != math.inf else 0.0)
        v_pico = math.sqrt(2 * distancia / inverso) if inverso else 0.0
        t1 = v_pico / a1 if a1 != math.inf else 0.0
        t2 = v_pico / a2 if a2 != math.inf else 0.0
        if t <= t1:
            return sinal * 0.5 * a1 * t * t
        return sinal * (distancia - 0.5 * a2 * (t1 + t2 - t) ** 2)

    if t <= at_s:
        return sinal * 0.5 * a1 * t * t
    if t <= total - dt_s:
        return sinal * (d_acel + v_mm_s * (t - at_s))
    restante = total - t
    return sinal * (distancia - 0.5 * a2 * restante * restante)


# ----------------------------------------------------------------------------
# ganhos PG: o quanto o robô erra o comando
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Ganhos:
    """Os quatro ganhos ``PG``, em % (mesma conversão do driver)."""

    so: float = 0.0
    ca: float = 0.0
    df: float = 0.0
    ri: float = 0.0

    @classmethod
    def de_dict(cls, ganhos: dict[str, float] | None) -> Ganhos:
        """Aceita o dicionário que :meth:`Config.pg_gains` devolve."""
        dados = ganhos or {}
        return cls(
            so=float(dados.get("so", 0.0)),
            ca=float(dados.get("ca", 0.0)),
            df=float(dados.get("df", 0.0)),
            ri=float(dados.get("ri", 0.0)),
        )

    def reta(self, distancia_mm: float) -> float:
        """Distância que o robô **realmente** percorre, dado o ganho ``SO``."""
        return distancia_mm * (1.0 + self.so / 100.0)

    def curva(self, graus: float, ganho: float) -> float:
        """Ângulo real de uma curva, dado o ganho ``CA`` ou ``DF``."""
        return graus * (1.0 + ganho / 100.0)

    def raio_interno(self, raio_mm: float) -> float:
        """Raio interno real, dado o ganho ``RI`` (``-5`` = raio 5 mm menor)."""
        return raio_mm + self.ri


def aplicar_ganhos(
    reta_mm: float = 0.0,
    pivo_graus: float = 0.0,
    diferencial_graus: float = 0.0,
    raio_interno_mm: float = 0.0,
    ganhos: Ganhos | None = None,
) -> tuple[float, float, float, float]:
    """Devolve os quatro valores **já corrigidos** pelo ``PG``.

    É o gancho mais útil da prévia: o que a rotina pede versus o que o robô,
    com a sua calibração real, realmente faria.
    """
    g = ganhos or Ganhos()
    return (
        g.reta(reta_mm),
        g.curva(pivo_graus, g.ca),
        g.curva(diferencial_graus, g.df),
        g.raio_interno(raio_interno_mm),
    )


# ----------------------------------------------------------------------------
# os três modelos de movimento do modo fixo
# ----------------------------------------------------------------------------
def ir_para_frente(pose: Pose, distancia_mm: float) -> Pose:
    """Reta: soma a distância no rumo atual. Positivo = frente, negativo = ré."""
    return pose.avancado(distancia_mm)


def girar_no_eixo(pose: Pose, graus: float) -> Pose:
    """Curva sobre o próprio eixo: muda o rumo, o ponto não sai do lugar."""
    return pose.girada(graus * RAD_POR_GRAU)


def girar_diferencial(
    pose: Pose, graus: float, raio_interno_mm: float, dist_rodas_mm: float
) -> Pose:
    """Curva em arco: o corpo descreve um círculo de raio ``ri + dw/2``.

    Numa curva à direita o centro do arco fica à **direita** do robô (é para lá
    que ele está virando); numa à esquerda, à esquerda. O ângulo do comando e o
    do corpo giram juntos, então basta girar a posição em torno do centro pelo
    mesmo ``delta`` que a direção.
    """
    raio = raio_interno_mm + dist_rodas_mm / 2.0
    if raio <= 0.0:
        return girar_no_eixo(pose, graus)
    delta = graus * RAD_POR_GRAU
    lado = 1.0 if delta >= 0.0 else -1.0  # +y local = direita do robô
    centro = pose.para_mundo((0.0, raio * lado))
    # Vetor do centro até o robô agora; ele é que gira.
    vx, vy = pose.x - centro[0], pose.y - centro[1]
    cos, sen = math.cos(delta), math.sin(delta)
    return Pose(
        centro[0] + vx * cos - vy * sen, centro[1] + vx * sen + vy * cos, pose.angulo + delta
    )


# ----------------------------------------------------------------------------
# colisão
# ----------------------------------------------------------------------------
def ponto_perto_de_segmento(px: float, py: float, segmento: Segmento, raio: float) -> bool:
    """O ponto está a menos de ``raio`` do segmento (arredondando as pontas)?"""
    x1, y1, x2, y2 = segmento
    dx, dy = x2 - x1, y2 - y1
    comprimento = dx * dx + dy * dy
    if comprimento == 0.0:
        return math.hypot(px - x1, py - y1) <= raio
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / comprimento))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy)) <= raio


def colide(
    pose: Pose,
    arena: Arena,
    raio_corpo: float | None = None,
    objetos: Iterable[Objeto] | None = None,
) -> bool:
    """A pose está dentro de uma parede, fora da pista ou sobre um objeto solto?

    ``objetos`` deixa passar só os que continuam no chão — o pallet que o robô
    segurou não é obstáculo para ele mesmo.
    """
    raio = arena.robo.raio_corpo if raio_corpo is None else raio_corpo
    if not arena.dentro(pose.x, pose.y, raio):
        return True
    no_chao = arena.objetos if objetos is None else list(objetos)
    return any(
        ponto_perto_de_segmento(pose.x, pose.y, segmento, raio)
        for segmento in arena.obstaculos_de(no_chao)
    )


def primeiro_travamento(
    pose: Pose,
    destino: Pose,
    arena: Arena,
    objetos: Iterable[Objeto] | None = None,
    passo_mm: float = 25.0,
) -> Pose | None:
    """A pose onde ``pose → destino`` bate em algo, ou ``None`` se o caminho é livre.

    Vai em passos de ``passo_mm`` e devolve a **última pose livre**. É o que faz
    o robô encostar na parede e parar, em vez de atravessar.
    """
    total = pose.distancia_ate((destino.x, destino.y))
    if total <= passo_mm:
        return destino if colide(destino, arena, objetos=objetos) else None
    passos = max(1, int(total / passo_mm))
    anterior = pose
    for passo in range(1, passos + 1):
        fracao = passo / passos
        intermediario = Pose(
            pose.x + (destino.x - pose.x) * fracao,
            pose.y + (destino.y - pose.y) * fracao,
            pose.angulo + (destino.angulo - pose.angulo) * fracao,
        )
        if colide(intermediario, arena, objetos=objetos):
            return anterior
        anterior = intermediario
    return None


# ----------------------------------------------------------------------------
# sensores
# ----------------------------------------------------------------------------
def intersecao_raio_segmento(
    ox: float, oy: float, dx: float, dy: float, segmento: Segmento
) -> float | None:
    """Distância do ponto ``(ox, oy)`` até o segmento, na direção ``(dx, dy)``.

    Devolve ``None`` quando o raio não acerta. Vetor de direção já normalizado.
    """
    x1, y1, x2, y2 = segmento
    ex, ey = x2 - x1, y2 - y1
    denominador = dx * ey - dy * ex
    if abs(denominador) < 1e-12:
        return None  # paralelo: ou nunca cruza, ou está colado no segmento
    rx, ry = x1 - ox, y1 - oy
    t = (rx * ey - ry * ex) / denominador
    u = (rx * dy - ry * dx) / denominador
    if t >= 0.0 and 0.0 <= u <= 1.0:
        return t
    return None


def alcance_sonar(
    pose: Pose,
    sonar: Sonar,
    arena: Arena,
    objetos: Iterable[Objeto] | None = None,
    alcance_mm: float = ALCANCE_SONAR_MM,
    cone_graus: float = CONE_SONAR_GRAUS,
) -> float | None:
    """Distância que o sonar enxerga, ou ``None`` se não há nada no alcance.

    O cone de ``cone_graus`` (≈15°, Apostila §7.4) é varrido em três raios —
    centro e as duas bordas — e vale o menor acerto, que é o que o sensor real
    faria com o feixe largo. Se nada for menor que ``alcance_mm``, devolve
    ``None``; o ``PlacaVirtual`` transforma em ``FFFF``.
    """
    if sonar.desligado:
        return None
    # Posição do sonar no mundo e seu rumo, a partir do referencial do robô.
    origem = pose.para_mundo((sonar.x, sonar.y))
    no_chao = arena.objetos if objetos is None else list(objetos)
    obstaculos = arena.obstaculos_de(no_chao)

    melhor = alcance_mm
    encontrou = False
    meia_cone = cone_graus / 2.0
    for amostra in (0.0, -meia_cone, meia_cone):
        angulo = pose.angulo + (sonar.angulo + amostra) * RAD_POR_GRAU
        dx, dy = math.cos(angulo), math.sin(angulo)
        for segmento in obstaculos:
            t = intersecao_raio_segmento(origem[0], origem[1], dx, dy, segmento)
            if t is not None and t <= melhor:
                melhor = t
                encontrou = True
    return melhor if encontrou else None


def sensores_de_linha(pose: Pose, arena: Arena) -> dict[int, int]:
    """``SL`` na pose atual: ``{1, 2, 3}``, com 1 = preto, 0 = branco.

    Cada sensor é um ponto no chão; 1 se ele (ou o raio do corpo, para não
    furar a faixa) está sobre alguma faixa preta. Fora de faixa — vale o piso.
    """
    saida = {}
    for indice, (lx, ly) in arena.robo.linha.items():
        no_mundo = pose.para_mundo((lx, ly))
        sobre = any(ponto_sobre_faixa(no_mundo[0], no_mundo[1], faixa) for faixa in arena.faixas)
        saida[indice] = 1 if sobre else 0
    return saida


def ponto_sobre_faixa(px: float, py: float, faixa) -> bool:
    """O ponto cai dentro da faixa (considerando a espessura)?"""
    x1, y1, x2, y2 = faixa.x1, faixa.y1, faixa.x2, faixa.y2
    dx, dy = x2 - x1, y2 - y1
    comprimento = math.hypot(dx, dy)
    if comprimento == 0.0:
        return math.hypot(px - x1, py - y1) <= faixa.largura / 2.0
    # Vetor unitário ao longo da faixa e o ponto projetado nele.
    ux, uy = dx / comprimento, dy / comprimento
    projecao = (px - x1) * ux + (py - y1) * uy
    if not 0.0 <= projecao <= comprimento:
        return False
    perpendicular = abs(-(px - x1) * uy + (py - y1) * ux)
    return perpendicular <= faixa.largura / 2.0


# O infravermelho fica na frente do corpo (Apostila §1.3). É um "sonar" de
# uso único: só a leitura frontal, e o resultado é sempre numérico.
INFRAMELHO = Sonar(2, 200.0, 0.0, 0.0)


def leitura_infravermelho(pose: Pose, arena: Arena, alcance_cm: int = 150) -> float:
    """``SI`` em cm: distância frontal ao obstáculo mais próximo.

    Diferente do sonar, o infravermelho é **sempre numérico** e vale 150 cm
    quando não há nada (guia §3.12). Usa a mesma varredura, só com um alcance
    bem menor. Atenção à unidade: ``SI`` responde em **cm**, mas o raycast
    trabalha em **mm** — a conversão é aqui, nas duas pontas.
    """
    limite_mm = float(alcance_cm) * 10.0
    valor = alcance_sonar(pose, INFRAMELHO, arena, alcance_mm=limite_mm, cone_graus=0.0)
    return float(alcance_cm) if valor is None else min(float(alcance_cm), valor / 10.0)


def digital(pose: Pose, arena: Arena) -> dict[int, int]:
    """``DI1``-``DI8``: 1 = nível alto.

    A PCI tem cinco sensores de linha e ``SL`` devolve três; os outros dois vão
    para ``D1``/``D2`` (guia §3.13, Apostila Tab. 5). Então os dois primeiros
    canais espelham o 4º e o 5º sensor de linha — os outros ficam em 0, que é
    o estado de uma entrada solta.
    """
    linha = sensores_de_linha(pose, arena)
    saida = {i: 0 for i in range(1, 9)}
    if 2 in linha:
        saida[1] = linha[2]
    if 3 in linha:
        saida[2] = linha[3]
    return saida
