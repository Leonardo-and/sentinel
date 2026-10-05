"""Simulação do Sentinel: rode a rotina no PC e veja o robô sem o robô.

Este pacote monta uma **placa virtual** que fala o mesmo protocolo da Placa de
Controle e Potência do SoBot. Como o driver :class:`~sentinel.sobot.SoBot`
aceita um ``serial_obj``, a sua rotina roda **igual** — os mesmos comandos, os
mesmos ``wait_for``, os mesmos ``ERROR`` — só que do outro lado do cabo está o
simulador.

    from sentinel.simulacao import Arena, PlacaVirtual

    placa = PlacaVirtual(Arena(largura=3000, altura=2000))
    bot = SoBot(serial_obj=placa, verbose=True)   # em vez de /dev/ttyACM0
    bot.move(1000, wait=True)

O que a simulação cobre: a cinemática das rodas (reta, pivô e curva
diferencial, com os ganhos ``PG``), os 8 sonares por raycast, os sensores de
linha ``SL``, infravermelho, entradas digitais, LED, buzzer, elevador, relés e
o atraso na fila ``DL``. Um :mod:`~sentinel.simulacao.camera` opcional gera o
quadro de uma webcam virtual para o :class:`~sentinel.visao.VisaoCor`.

Não é o firmware: é cinemático puro, sem inércia nem escorregamento. Serve
para pegar erro de lógica, de rota e de calibração antes de sair pro pátio.
"""

from .camera import CameraVirtual
from .cinematica import Ganhos, Pose
from .painel import Painel, abrir_painel
from .pista import (
    Alvo,
    Arena,
    CameraArena,
    Faixa,
    Montagem,
    Objeto,
    Parede,
    RoboArena,
    Sonar,
    achar_arquivo,
    carregar,
    exemplo,
    padrao_pista,
    salvar,
)
from .placa import PlacaVirtual, Relogio, RelogioFake, RelogioReal

__all__ = [
    "Alvo",
    "Arena",
    "CameraArena",
    "CameraVirtual",
    "Faixa",
    "Ganhos",
    "Montagem",
    "Objeto",
    "Painel",
    "Parede",
    "PlacaVirtual",
    "Pose",
    "Relogio",
    "RelogioFake",
    "RelogioReal",
    "RoboArena",
    "Sonar",
    "achar_arquivo",
    "carregar",
    "exemplo",
    "padrao_pista",
    "abrir_painel",
    "salvar",
]
