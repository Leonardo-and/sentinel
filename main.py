"""Rotina do Sentinel: o robô anda para frente e para.

Ajuste os parâmetros abaixo e rode:

    .venv/bin/python main.py

Com VERBOSO ligado você vê cada comando enviado (>>) e cada resposta (<<).

Também há dois utilitários de diagnóstico, que não Talking com o robô:

    python main.py --debug-visao --cor vermelho   # calibra a faixa de uma cor
    python main.py --listar-botoes                 # descobre os botões do F710
"""
from __future__ import annotations

import argparse
import sys

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


# ----------------------------------------------------------------------------
# diagnóstico (não fala com o robô)
# ----------------------------------------------------------------------------
def depurar_visao(cor: str) -> None:
    """Fica olhando a câmera e mostra o que a faixa de ``cor`` está pegando.

    Não trava o robô e não abre a serial: dá para calibrar com o pallet no
    chão, antes de qualquer movimento.
    """
    from sentinel import load_visao
    from sentinel.visao import VisaoCor

    cfg = load_visao()
    destino = f"/tmp/sentinel-visao-{cor}.png"
    print(f"[visao] {cfg.dispositivo} @ {cfg.largura}x{cfg.altura} · "
          f"área [{cfg.area_min}, {cfg.area_max}] px · {cfg.frames_estaveis} frames estáveis")
    print(f"[visao] quadro anotado em {destino} · Ctrl-C para sair\n")
    with VisaoCor(cfg) as cam:
        cabecalho = True
        for deteccao, _quadro in cam.depurar(cor, caminho=destino, intervalo=0.15):
            if cabecalho:
                print(f"{'desvio':>9} {'área':>7}  estado")
                cabecalho = False
            estado = "ALVO" if deteccao.achou else "sem alvo"
            print(f"{deteccao.desvio:+9.0f} {deteccao.area:7d}  {estado}", end="\r", flush=True)


def listar_botoes() -> None:
    """Despeja os códigos crus de cada botão do F710, para o sentinel.toml."""
    from sentinel import load_controle
    from sentinel.controle import ControleF710

    cfg = load_controle()
    with ControleF710(cfg) as controle:
        mapa = controle.mapa()
        print("mapeado agora no sentinel.toml:")
        for codigo, acao in mapa.items():
            print(f"  {codigo:<14} → {acao or '(ignorado)'}")
        print("\napertando um botão mostra o código dele (Ctrl-C para sair):")
        print("copie para [controle.botoes] ou [controle.parada]\n")
        while True:
            for evento in controle.eventos():
                print(f"  {evento.botao:<14} → {evento.acao or '(não mapeado)'}", flush=True)


def cli(argv: list[str] | None = None) -> None:
    """Roteia entre a rotina do robô e os utilitários de diagnóstico."""
    parser = argparse.ArgumentParser(
        description="Rotinas do Sentinel (SoBot). Sem argumentos, roda a rotina.")
    parser.add_argument("--debug-visao", action="store_true",
                        help="olha a câmera e mede a cor pedida (não move o robô)")
    parser.add_argument("--cor", default="vermelho",
                        help="cor para o --debug-visao (padrão: vermelho)")
    parser.add_argument("--listar-botoes", action="store_true",
                        help="mostra os códigos de botão do Logitech F710")
    args = parser.parse_args(argv)

    try:
        if args.listar_botoes:
            listar_botoes()
        elif args.debug_visao:
            depurar_visao(args.cor)
        else:
            main()
    except KeyboardInterrupt:
        print("\ninterrompido pelo usuário")
        sys.exit(130)


if __name__ == "__main__":
    cli()
