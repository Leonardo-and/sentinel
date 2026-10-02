"""Rotina do Sentinel: o robô anda para frente e para.

Ajuste os parâmetros abaixo e rode:

    .venv/bin/python main.py

Com VERBOSO ligado você vê cada comando enviado (>>) e cada resposta (<<).
"""
from sentinel import SoBot, load_config

CFG = load_config()

DISTANCIA_MM = 500        # distância a percorrer; negativo = ré
VELOCIDADE_CM_S = 10      # 0-25 (guia §3.4)
ACEL_MS = 1000            # 0-60000
DESACEL_MS = 1000
VERBOSO = True


def main() -> None:
    with SoBot(CFG.port, CFG.baud, verbose=VERBOSO) as bot:
        bot.configure_wheels(*CFG.wheel_params())

        ganhos = CFG.pg_gains()
        if ganhos:
            bot.set_proportional_gain(**ganhos)

        bot.command_return(True, "MT0")
        # MT0 CR1 responde na hora; sem esta leitura o wait=True do move()
        # consumiria o ack do CR1 e voltaria antes do robô andar.
        bot.wait_for("CR OK MT0")

        bot.wheels_enable(True)
        try:
            bot.move(
                DISTANCIA_MM,
                accel_ms=ACEL_MS,
                decel_ms=DESACEL_MS,
                speed_cm_s=VELOCIDADE_CM_S,
                wait=True,
            )
        finally:
            bot.wheels_enable(False)


if __name__ == "__main__":
    main()
