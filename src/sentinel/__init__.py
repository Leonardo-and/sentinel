"""Sentinel — rotinas de controle do robô SoBot (SOLIS Tecnologia).

    from sentinel import SoBot, load_config

    cfg = load_config()
    with SoBot(cfg.port, cfg.baud) as bot:
        bot.wheels_enable(True)
        bot.move(500, wait=True)
        bot.wheels_enable(False)

O driver completo está em :mod:`sentinel.sobot` e a configuração em
:mod:`sentinel.config`. Os dois periféricos opcionais ficam em módulos à parte,
porque cada um traz uma dependência pesada só dele: :mod:`sentinel.visao`
(webcam + OpenCV) e :mod:`sentinel.controle` (Logitech F710 + ``inputs``).
"""
from .config import (
    Config,
    ControleConfig,
    FaixaHSV,
    VisaoConfig,
    find_config_file,
    load_config,
    load_controle,
    load_visao,
)
from .sobot import DEFAULT_BAUD, DEFAULT_PORT, SoBot, SoBotError, SoBotTimeout

__version__ = "0.1.0"
__all__ = [
    "Config",
    "ControleConfig",
    "DEFAULT_BAUD",
    "DEFAULT_PORT",
    "FaixaHSV",
    "SoBot",
    "SoBotError",
    "SoBotTimeout",
    "VisaoConfig",
    "find_config_file",
    "load_config",
    "load_controle",
    "load_visao",
]
