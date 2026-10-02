"""Visão computacional: acha o bloco de uma cor e diz onde ele está na imagem.

A câmera é uma webcam USB plugged na Raspberry (``[visao.dispositivo]``). O
reconhecimento é segmentação por HSV: a cor vira uma máscara binária e o
centroide dessa máscara é a posição do alvo no quadro.

    from sentinel import load_visao
    from sentinel.visao import VisaoCor

    with VisaoCor(load_visao()) as cam:
        d = cam.detectar("vermelho")
        if d.achou:
            print(d.desvio)   # px; positivo = à direita do centro

Nada aqui é específico do robô: este módulo só fala com a câmera. Quem decide
o que fazer com o desvio é a rotina.

Para calibrar uma cor (e conferir se a faixa pegou o que deve), use a CLI::

    python main.py --debug-visao --cor vermelho

Ela imprime a área e o desvio de cada frame e salva o quadro com a máscara
sobreposta em ``/tmp/sentinel-visao-<cor>.png``.

As faixas do padrão são chute genérico: matiz e saturação mudam com a luz da
sala. Meça com o pallet real no chão.
"""
from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .config import FaixaHSV, VisaoConfig


@dataclass(frozen=True)
class Deteccao:
    """Resultado de um frame. ``achou=False`` zera a geometria."""

    achou: bool
    cx: float = 0.0              # centroide em px, origem no canto superior esquerdo
    cy: float = 0.0
    area: int = 0                # px da máscara
    largura: int = 0             # largura do quadro, para o desvio
    estavel: bool = False        # veio nos N frames seguidos que o filtro exige

    @property
    def desvio(self) -> float:
        """Deslocamento horizontal do alvo em relação ao centro, em px.

        Negativo = à esquerda do centro do quadro; positivo = à direita.
        Sem alvo vale 0: sem detecção não há direção para seguir.
        """
        if not self.achou:
            return 0.0
        return self.cx - self.largura / 2.0


def _cv2() -> Any:
    """Importa o OpenCV na hora; sem ele, o resto do pacote continua funcionando."""
    try:
        import cv2
    except ImportError as erro:
        raise RuntimeError(
            "OpenCV não encontrado. Na Raspberry: "
            'pip install -e ".[visao]"  (ou apt install python3-opencv python3-numpy)'
        ) from erro
    return cv2


def _numpy() -> Any:
    """Importa o numpy na hora; o OpenCV já o traz, mas a dependência é dele."""
    try:
        import numpy
    except ImportError as erro:
        raise RuntimeError("numpy não encontrado: instale com pip install -e '.[visao]'") from erro
    return numpy


class VisaoCor:
    """Câmera + faixa HSV + filtro de área e de estabilidade.

    Uma instância guarda o histórico de detecções usado por :meth:`amostrar`;
    trocar de cor zera esse histórico (veja :meth:`selecionar`).
    """

    def __init__(self, cfg: VisaoConfig, captura: Any = None):
        self.cfg = cfg
        self._captura = captura
        self._historico: list[bool] = []
        self._atual: str | None = None
        self._avisou_resolucao = False

    # --- ciclo de vida -------------------------------------------------------
    def abrir(self) -> VisaoCor:
        """Abre a webcam e aplica a resolução pedida. Idempotente."""
        if self._captura is not None:
            return self
        cv2 = _cv2()
        captura = cv2.VideoCapture(self.cfg.dispositivo)
        if not captura.isOpened():
            raise RuntimeError(
                f"não consegui abrir /dev/video{self.cfg.dispositivo} "
                "(câmera plugged? outro programa usando?)"
            )
        captura.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.largura)
        captura.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.altura)
        self._captura = captura
        return self

    def fechar(self) -> None:
        if self._captura is not None:
            self._captura.release()
            self._captura = None

    def __enter__(self) -> VisaoCor:
        return self.abrir()

    def __exit__(self, *exc: object) -> None:
        self.fechar()

    # --- leitura -------------------------------------------------------------
    def cor(self) -> str:
        """Cor sob observação; ``"?"`` antes da primeira seleção."""
        return self._atual or "?"

    def selecionar(self, cor: str) -> FaixaHSV:
        """Fixa a cor observada e zera o histórico de estabilidade."""
        self._atual = cor.strip().lower()
        self._historico.clear()
        return self.cfg.faixa(self._atual)

    def detectar(self, cor: str | None = None) -> Deteccao:
        """Lê um frame da câmera e devolve a detecção *bruta*, sem estabilidade."""
        alvo = cor or self._atual
        if alvo is None:
            raise ValueError("informe a cor ou chame selecionar() antes")
        if alvo.strip().lower() != self._atual:
            self.selecionar(alvo)
        self.abrir()

        ok, frame = self._captura.read()
        if not ok or frame is None:
            return Deteccao(achou=False, largura=self.cfg.largura)
        self._checar_resolucao(frame)
        return self.medir(frame)

    def medir(self, frame: Any) -> Deteccao:
        """Mede um frame já capturado — mesmo caminho usado com a câmera viva.

        Aceita qualquer imagem BGR que o OpenCV leia (numpy, arquivo, vídeo),
        o que permite calibrar uma faixa com uma foto salva.
        """
        cv2 = _cv2()
        altura, largura = frame.shape[:2]
        mascara = self._mascara(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV))
        momentos = cv2.moments(mascara, binaryImage=True)
        area = int(momentos["m00"])
        if not self.cfg.area_min <= area <= self.cfg.area_max:
            return Deteccao(achou=False, largura=largura)
        return Deteccao(
            achou=True,
            cx=momentos["m10"] / area,
            cy=momentos["m01"] / area,
            area=area,
            largura=largura,
        )

    def amostrar(self, cor: str | None = None) -> Deteccao:
        """Como :meth:`detectar`, mas só confirma depois de N frames seguidos.

        O ``achou`` devolvido já vem filtrado; ``estavel`` diz se a detecção foi
        confirmada ou ainda está acumulando frames.
        """
        deteccao = self.detectar(cor)
        janela = self.cfg.frames_estaveis
        self._historico.append(deteccao.achou)
        if len(self._historico) > janela:
            self._historico.pop(0)
        estavel = deteccao.achou and len(self._historico) == janela and all(self._historico)
        return Deteccao(
            achou=estavel,
            cx=deteccao.cx,
            cy=deteccao.cy,
            area=deteccao.area,
            largura=deteccao.largura,
            estavel=estavel,
        )

    # --- diagnóstico ---------------------------------------------------------
    def depurar(
        self,
        cor: str,
        caminho: str | None = None,
        intervalo: float = 0.2,
    ) -> Iterator[tuple[Deteccao, Any]]:
        """Gera ``(deteção, frame anotado)`` para calibrar a faixa de uma cor.

        Salva o quadro em ``caminho`` (por padrão
        ``/tmp/sentinel-visao-<cor>.png``) a cada iteração, para você olhar
        exatamente o que a máscara pegou. Interrompa com Ctrl-C.
        """
        self.selecionar(cor)
        self.abrir()
        cv2 = _cv2()
        destino = caminho or f"/tmp/sentinel-visao-{self.cor()}.png"
        while True:
            ok, frame = self._captura.read()
            if not ok or frame is None:
                if intervalo > 0:
                    time.sleep(intervalo)
                continue
            self._checar_resolucao(frame)
            deteccao = self.medir(frame)
            yield deteccao, self._anotar(cv2, frame, deteccao, destino)
            if intervalo > 0:
                time.sleep(intervalo)

    # --- internos ------------------------------------------------------------
    def _checar_resolucao(self, frame: Any) -> None:
        """Avisa uma vez se a câmera entregou um quadro diferente do pedido."""
        altura, largura = frame.shape[:2]
        if not self._avisou_resolucao and (largura, altura) != (self.cfg.largura, self.cfg.altura):
            # A câmera ignorou a resolução pedida; avisar evita ler um desvio em
            # pixels de outro quadro e errar o rumo.
            print(f"[visao] câmera em {largura}x{altura}, pedido "
                  f"{self.cfg.largura}x{self.cfg.altura}", file=sys.stderr)
            self._avisou_resolucao = True

    def _mascara(self, hsv: Any) -> Any:
        """Aplica a faixa da cor atual e limpa ruídos com abertura/fechamento.

        A faixa vira uma máscara por ``cv2.inRange``; quando ela envolve o zero
        do matiz (vermelho) são duas máscaras somadas com OR.
        """
        cv2 = _cv2()
        np = _numpy()
        mascara = None
        for baixo, alto in self.cfg.faixa(self.cor()).limites():
            parte = cv2.inRange(hsv, np.array(baixo, dtype="uint8"),
                                np.array(alto, dtype="uint8"))
            mascara = parte if mascara is None else cv2.bitwise_or(mascara, parte)
        for tamanho, operacao in ((self.cfg.abrir_kernel, cv2.MORPH_OPEN),
                                  (self.cfg.fechar_kernel, cv2.MORPH_CLOSE)):
            if tamanho:
                nucleo = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (tamanho, tamanho))
                mascara = cv2.morphologyEx(mascara, operacao, nucleo)
        return mascara

    def _anotar(self, cv2: Any, frame: Any, deteccao: Deteccao, destino: str) -> Any:
        """Desenha a vertical do centro e o centroide; salva o quadro anotado."""
        saida = frame.copy()
        altura, largura = saida.shape[:2]
        escala = max(largura, altura) / 320.0
        cv2.line(saida, (largura // 2, 0), (largura // 2, altura), (255, 255, 0), 1)
        if deteccao.achou:
            cv2.circle(saida, (int(deteccao.cx), int(deteccao.cy)),
                       max(int(6 * escala), 3), (0, 0, 255), 2)
        rotulo = "sem alvo"
        if deteccao.achou:
            rotulo = f"{deteccao.area}px  desvio={deteccao.desvio:+.0f}px"
        cv2.putText(saida, rotulo, (4, max(int(14 * escala), 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45 * escala, (255, 255, 255), 1)
        try:
            cv2.imwrite(destino, saida)
        except cv2.error:
            pass
        return saida
