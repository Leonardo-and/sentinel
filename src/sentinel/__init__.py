"""Sentinel — rotinas de controle do robô SoBot (SOLIS Tecnologia).

    from sentinel import SoBot, load_config

    cfg = load_config()
    with SoBot(cfg.port, cfg.baud) as bot:
        bot.wheels_enable(True)
        bot.move(500, wait=True)
        bot.wheels_enable(False)

O driver completo está em :mod:`sentinel.sobot` e a configuração em
:mod:`sentinel.config`.
"""
from .config import Config, find_config_file, load_config
from .sobot import DEFAULT_BAUD, DEFAULT_PORT, SoBot, SoBotError, SoBotTimeout

__version__ = "0.1.0"
__all__ = [
    "Config",
    "DEFAULT_BAUD",
    "DEFAULT_PORT",
    "SoBot",
    "SoBotError",
    "SoBotTimeout",
    "find_config_file",
    "load_config",
]
