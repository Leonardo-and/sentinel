"""Leitura do Logitech F710: botões viram cores e parada de emergência.

O controle é lido com a biblioteca ``inputs`` (evdev por baixo), em modo
não-bloqueante: :meth:`ControleF710.eventos` só descreve o que mudou desde a
chamada anterior, e devolve vazio quando ninguém mexeu no controle. Isso deixa o
laço de movimento fazer as outras coisas (câmera, sensores) sem travar.

    from sentinel import load_controle
    from sentinel.controle import ControleF710

    with ControleF710(load_controle()) as controle:
        for evento in controle.eventos():
            if evento.acao == "parada":
                bot.brake()          # MT0 BC, nunca KC
            elif evento.acao:
                alvo = evento.acao    # "vermelho", "azul", ...

Os botões são identificados pelos códigos crus que o ``inputs`` emite
(``BTN_SOUTH``, ``BTN_START``, ...), e o mapa fica no ``sentinel.toml``:

    [controle.botoes]
    BTN_SOUTH = "vermelho"

Não confie no palpite dos nomes: descubra os do seu controle com
``python main.py --listar-botoes`` e copie o que aparecer.

Na Raspberry o ``inputs`` precisa de permissão de leitura em ``/dev/input``:
``sudo usermod -aG input pi`` e logout (ou reboot).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import ControleConfig

ACACAO_PARADA = "parada"


@dataclass(frozen=True)
class Evento:
    """Uma transição de botão. ``acao`` é ``"parada"``, uma cor, ou ``""``."""

    botao: str
    acao: str

    @property
    def parada(self) -> bool:
        return self.acao == ACACAO_PARADA


def _inputs() -> Any:
    """Importa a ``inputs`` na hora; sem ela, o resto do pacote funciona."""
    try:
        import inputs
    except ImportError as erro:
        raise RuntimeError(
            "biblioteca 'inputs' não encontrada. Na Raspberry: "
            'pip install -e ".[controle]"'
        ) from erro
    return inputs


class ControleF710:
    """Controle USB como uma sequência de eventos de borda (subida de botão)."""

    def __init__(self, cfg: ControleConfig, gamepad: Any = None):
        self.cfg = cfg
        self._gamepad = gamepad
        self._presente: dict[str, bool] = {}

    # --- ciclo de vida -------------------------------------------------------
    def abrir(self) -> ControleF710:
        """Abre o primeiro gamepad USB. Idempotente."""
        if self._gamepad is not None:
            return self
        controles = _inputs().devices.gamepads
        if not controles:
            raise RuntimeError(
                "nenhum controle USB encontrado — conectado? "
                "(no F710, o seletor precisa estar em X/D, não OFF)"
            )
        self._gamepad = controles[0]
        return self

    def fechar(self) -> None:
        # ``inputs`` não fecha nada: o gamepad vive enquanto o processo viver.
        self._gamepad = None

    def __enter__(self) -> ControleF710:
        return self.abrir()

    def __exit__(self, *exc: object) -> None:
        self.fechar()

    # --- leitura -------------------------------------------------------------
    def eventos(self) -> tuple[Evento, ...]:
        """Botões apertados (ou soltos) desde a última chamada.

        Devolve os eventos de borda na ordem em que o SO os entregou. Botão
        que não está no mapa volta com ``acao=""``, para o diagnóstico.
        """
        self.abrir()
        saida = []
        for evento in self._gamepad:
            if getattr(evento, "ev_type", None) not in ("KeyDown", "KeyUp"):
                continue
            codigo = str(getattr(evento, "code", "")).strip().upper()
            if not codigo.startswith("BTN"):
                continue
            pressionado = evento.ev_type == "KeyDown" and int(evento.state) == 1
            if self._presente.get(codigo) == pressionado:
                continue
            # O estado muda nos dois sentidos (para o botão de parada ser
            # solto); só a subida vira evento, porque parar é decisão de borda.
            self._presente[codigo] = pressionado
            if not pressionado:
                continue
            saida.append(Evento(botao=codigo, acao=self.cfg.acao(codigo) or ""))
        return tuple(saida)

    def segurando_parada(self) -> bool:
        """Verdadeiro enquanto um botão de parada estiver apertado.

        Complementa os eventos para o caso de o laço só perguntar se pode
        andar, sem percorrer os eventos a cada iteração.
        """
        return any(self._presente.get(c.strip().upper(), False) for c in self.cfg.parada)

    def mapa(self) -> dict[str, str]:
        """Mapa código → ação, para conferir a configuração de olho nu."""
        codigos = sorted({*self.cfg.botoes, *self.cfg.parada})
        return {codigo: (self.cfg.acao(codigo) or "") for codigo in codigos}

    @property
    def gamepad(self) -> Any | None:
        return self._gamepad
