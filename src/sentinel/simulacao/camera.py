"""A webcam virtual: um quadro da arena como a câmera do robô veria.

:class:`CameraVirtual` tem a mesma superfície que um ``cv2.VideoCapture``
(``read``, ``release``, ``isOpened``), então entra direto no
:class:`~sentinel.visao.VisaoCor`:

    from sentinel import load_visao
    from sentinel.simulacao import CameraVirtual, PlacaVirtual, exemplo
    from sentinel.visao import VisaoCor

    placa = PlacaVirtual(exemplo())
    with VisaoCor(load_visao(), captura=CameraVirtual(placa)) as cam:
        d = cam.detectar("vermelho")

E o melhor: a detecção usa as **suas** faixas HSV de verdade. Se o pallet
vermelho da pista está dentro do ``[visao.cores.vermelho]``, o ``VisaoCor``
acha — e o desvio em pixels sai com o sinal certo. Dá para calibrar a faixa
contra o pallet simulado antes de sair com a câmera na mão.

A projeção é a de uma câmera de perspectiva: raio por pixel, do ponto da
lente até o chão ou a primeira parede/objeto. É por isso que o pallet perto
da câmera sai grande e o do fundo sai pequeno — a mesma perspectiva que faz
o ``desvio`` mudar conforme o robô se aproxima.
"""

from __future__ import annotations

import math
from typing import Any

from .pista import CORES_BGR, Arena

ALTURA_PAREDE_MM = 600.0  # parede alta o bastante para encher o quadro
ALTURA_OBJETO_MM = 150.0  # a "cabeça" do pallet, para o robô pegar
DISTANCIA_OBJETO_MM = 150.0  # recuo da lente até o centro do pallet
COR_PAREDE = "cinza"  # parede não casa com nenhuma faixa de cor
CEU_BGR = (170, 180, 160)  # cinza azulado: também não casa


def _cv2() -> Any:
    """Importa o OpenCV e o numpy na hora; sem eles, o resto do pacote funciona."""
    try:
        import cv2
        import numpy
    except ImportError as erro:
        raise RuntimeError(
            'OpenCV/numpy não encontrados. Para simular a câmera: pip install -e ".[visao]"'
        ) from erro
    return cv2, numpy


class CameraVirtual:
    """Uma webcam que aponta para a arena, montada na pose do robô.

    Guarda o último quadro e a pose em que ele foi gerado: enquanto o robô não
    se move, ``read`` devolve a mesma imagem — sem reprojetar tudo a cada
    chamada, o que importa numa malha de visão que roda a cada laço.
    """

    def __init__(
        self,
        placa: Any,
        largura: int = 320,
        altura: int = 240,
        altura_parede: float = ALTURA_PAREDE_MM,
        altura_objeto: float = ALTURA_OBJETO_MM,
        recuo: float = DISTANCIA_OBJETO_MM,
    ):
        self.placa = placa
        self.largura = int(largura)
        self.altura = int(altura)
        self.altura_parede = altura_parede
        self.altura_objeto = altura_objeto
        self.recuo = recuo
        self._quadro: Any = None
        self._pose: tuple[float, float, float] | None = None

    # --- superfície de VideoCapture ------------------------------------------
    def isOpened(self) -> bool:  # noqa: N802 — nome que o OpenCV usa
        """Sempre ``True``: não há device para falhar."""
        return True

    def set(self, _prop: int, _valor: float) -> bool:  # noqa: N802
        """Aceita e ignora — a resolução já é a que o ``VisaoConfig`` pediu."""
        return True

    def release(self) -> None:
        """Solta o quadro guardado (o ``VisaoCor`` chama no ``fechar``)."""
        self._quadro = None
        self._pose = None

    def read(self) -> tuple[bool, Any]:
        """Devolve ``(ok, quadro)`` no mesmo formato do OpenCV: BGR uint8."""
        try:
            return True, self.imagem()
        except RuntimeError:
            return False, None

    # --- o desenho ------------------------------------------------------------
    def imagem(self, forcar: bool = False) -> Any:
        """Renderiza (ou reaproveita) o quadro da câmera nesta pose."""
        chave = self._chave()
        if not forcar and self._quadro is not None and self._pose == chave:
            return self._quadro
        quadro = self._renderizar(self.placa.pose)
        self._quadro, self._pose = quadro, chave
        return quadro

    def _chave(self) -> tuple:
        """O que invalida o quadro: a pose **e** o que a câmera enxerga.

        Só a pose não basta: o robô pega e solta o pallet com a garra, sem dar
        um passo, e a imagem tem que mudar. O caminho de volta é a lista de
        objetos no chão — ela muda quando algo é levantado ou solto.
        """
        pose = self.placa.pose
        return (
            round(pose.x, 1),
            round(pose.y, 1),
            round(pose.angulo, 3),
            tuple(o.id for o in self.placa.objetos_no_chao()),
        )

    def _renderizar(self, pose) -> Any:
        cv2, np = _cv2()
        arena: Arena = self.placa.arena
        config = arena.robo.camera

        altura, largura = self.altura, self.largura
        # Focal em pixels: metade da largura / tangente da metade do FOV.
        f = (largura / 2.0) / math.tan(config.fov * math.pi / 360.0)

        # Câmera olhando para a frente: x cresce à direita, y para baixo.
        x_cam = (np.arange(largura, dtype=np.float32) + 0.5 - largura / 2.0) / f
        y_cam = (np.arange(altura, dtype=np.float32) + 0.5 - altura / 2.0) / f
        xc = np.broadcast_to(x_cam, (altura, largura))
        yc = np.broadcast_to(y_cam[:, None], (altura, largura))
        zc = np.ones_like(yc)

        # Inclinação: gira o cone para baixo em torno do eixo da lente.
        t = config.tilt * math.pi / 180.0
        cos_t, sen_t = math.cos(t), math.sin(t)
        yc2 = yc * cos_t + zc * sen_t
        zc2 = -yc * sen_t + zc * cos_t

        # Da câmera para o mundo: o rumo do robô vira o eixo de frente, e o
        # "baixo" da câmera vira o -Z (para cima no mundo).
        a = pose.angulo
        frente_x, frente_y = math.cos(a), math.sin(a)
        direita_x, direita_y = -math.sin(a), math.cos(a)
        wx = xc * direita_x + zc2 * frente_x
        wy = xc * direita_y + zc2 * frente_y
        wz = -yc2

        # Origem da lente, recuada do centro do corpo, na altura configurada.
        ox = pose.x + self.recuo * frente_x
        oy = pose.y + self.recuo * frente_y
        oz = config.altura

        quadro = np.empty((altura, largura, 3), dtype=np.uint8)
        quadro[:, :] = CEU_BGR

        # Chão: onde o raio desce até Z=0. Onde ele nem desce, fica o céu.
        descendo = wz < -1e-6
        with np.errstate(divide="ignore", invalid="ignore"):
            t_chao = np.where(descendo, -oz / wz, np.inf)
        quadro[np.isfinite(t_chao)] = CORES_BGR.get(arena.cor_piso, CORES_BGR["branco"])

        # Paredes e objetos: cada segmento 2D extrudado em altura. O raio acerta
        # o segmento e ainda precisa estar entre o chão e o topo.
        for x1, y1, x2, y2, cor, topo in self._cenario(arena):
            t = self._atingir(np, x1, y1, x2, y2, ox, oy, wx, wy, wz, oz, topo)
            mais_perto = t < t_chao
            if mais_perto.any():
                quadro[mais_perto] = CORES_BGR.get(cor, CORES_BGR["cinza"])
                t_chao = np.where(mais_perto, t, t_chao)

        return quadro

    def _cenario(self, arena: Arena):
        """Os segmentos 3D da cena: paredes e objetos que continuam no chão."""
        for parede in arena.paredes:
            yield (parede.x1, parede.y1, parede.x2, parede.y2, COR_PAREDE, self.altura_parede)
        for objeto in self.placa.objetos_no_chao():
            cantos = objeto.lados
            for indice in range(4):
                inicio, fim = cantos[indice], cantos[(indice + 1) % 4]
                yield (inicio[0], inicio[1], fim[0], fim[1], objeto.cor, self.altura_objeto)

    @staticmethod
    def _atingir(np, x1, y1, x2, y2, ox, oy, wx, wy, wz, oz, topo):
        """Distância (array) em que cada raio acerta o segmento extrudado.

        Vetorial de propósito: percorrer 76 mil pixels em Python custaria
        segundos por quadro, e a malha de visão chama isso a cada laço.
        """
        ex, ey = x2 - x1, y2 - y1
        # Cruzamentos 2D: t do raio e u ao longo do segmento.
        den = wx * ey - wy * ex
        com_den = np.abs(den) > 1e-9
        den_seguro = np.where(com_den, den, 1.0)
        rx, ry = x1 - ox, y1 - oy
        t = (rx * ey - ry * ex) / den_seguro
        u = (rx * wy - ry * wx) / den_seguro
        z = oz + t * wz
        valido = com_den & (t > 0.0) & (u >= 0.0) & (u <= 1.0) & (z >= 0.0) & (z <= topo)
        return np.where(valido, t, np.inf)
