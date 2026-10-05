"""Rotina do Sentinel: o robô anda, vira, curva e dá ré.

Ajuste a sequência em ``PASSOS`` e rode:

    .venv/bin/python main.py

Com VERBOSO ligado você vê cada comando enviado (>>) e cada resposta (<<).

Há também um simulador. Com ``--simular`` nada sai para a serial: o robô é
uma placa virtual, a pista é um TOML e a câmera desenha o que a câmera real
veria. É o jeito de testar uma rotina antes de soltar no pátio.

    python main.py --simular --painel              # a rotina, no navegador
    python main.py --simular --pista pista.toml    # numa pista sua
    python main.py --simular --velocidade 8        # oito vezes mais rápido

E dois utilitários de diagnóstico, que não Talking com o robô:

    python main.py --debug-visao --cor vermelho   # calibra a faixa de uma cor
    python main.py --listar-botoes                 # descobre os botões do F710
"""
from __future__ import annotations

import argparse
import sys

from sentinel import SoBot, load_config

CFG = load_config()

#: A rotina, passo a passo. Edite aqui e rode: é este arquivo que o robô executa.
#:
#: ``("mover", mm)`` vira ``MT0 D<mm>``: negativo é ré. ``("virar", graus, lado)``
#: vira ``MT0 D<graus> R|L``. Só entra ``move``/``turn`` aqui — nada de sensor,
#: garra ou ímã — então a mesma sequência roda no robô e no simulador.
PASSOS: tuple[tuple[object, ...], ...] = (
    ("mover", 700),           # reto para a frente
    ("virar", 90, "right"),   # meia-volta para a direita
    ("mover", 500),           # frente de novo
    ("virar", 45, "left"),    # curva de 45°, exercita o diferencial
    ("mover", -400),          # ré
    ("mover", 600),           # frente, e acabou
)

VELOCIDADE_CM_S = 10      # 0-25 (guia §3.4)
ACEL_MS = 1000            # 0-60000. Rampa generosa: vale mais no robô real.
DESACEL_MS = 1000
VERBOSO = True


def main() -> None:
    """A rotina de verdade: serial, robô físico."""
    with SoBot(CFG.port, CFG.baud, verbose=VERBOSO) as bot:
        _movimentos(bot)


def _movimentos(bot: SoBot) -> None:
    """Roda a sequência de :data:`PASSOS`, sem saber se o bot é real ou simulado."""
    bot.configure_wheels(*CFG.wheel_params())

    ganhos = CFG.pg_gains()
    if ganhos:
        bot.set_proportional_gain(**ganhos)

    bot.command_return(True, "MT0")
    # MT0 CR1 responde na hora; sem esta leitura o wait=True do move()
    # consumiria o ack do CR1 e voltaria antes do robô andar.
    bot.wait_for("CR OK MT0")

    rampas = {
        "accel_ms": ACEL_MS,
        "decel_ms": DESACEL_MS,
        "speed_cm_s": VELOCIDADE_CM_S,
        "wait": True,
    }

    bot.wheels_enable(True)
    try:
        for passo in PASSOS:
            if passo[0] == "mover":
                bot.move(passo[1], **rampas)
            elif passo[0] == "virar":
                bot.turn(passo[1], passo[2], **rampas)
            else:
                raise ValueError(f"passo desconhecido: {passo!r}")
    finally:
        bot.wheels_enable(False)


# ----------------------------------------------------------------------------
# simulação
# ----------------------------------------------------------------------------
def simular(
    pista: str | None = None,
    velocidade: float = 1.0,
    painel: bool = False,
    abrir: bool = False,
    porta: int = 8765,
    esperar: float | None = None,
) -> None:
    """Roda a mesma rotina contra uma placa virtual.

    O que muda em relação ao robô real: a porta vira uma placa em memória, o
    tempo pode ser acelerado e a câmera (se você usar a visão) desenha a cena.
    O que **não** muda: os comandos, a ordem deles e as respostas — é o mesmo
    ``SoBot`` falando com outra coisa do outro lado da serial.
    """
    from sentinel.simulacao import Painel, PlacaVirtual, carregar, exemplo

    arena = carregar(pista) if pista else exemplo()
    print(f"[sim] {arena.largura:.0f}x{arena.altura:.0f} mm · "
          f"{len(arena.paredes)} paredes · {len(arena.objetos)} objetos · "
          f"{len(arena.alvos)} alvos")
    with PlacaVirtual(arena, velocidade=velocidade) as placa:
        contexto = None
        if painel:
            contexto = Painel(placa, porta=porta)
            print(f"[painel] {contexto.url}")
            if abrir:
                contexto.abrir()
        try:
            with SoBot(serial_obj=placa, verbose=VERBOSO) as bot:
                _movimentos(bot)
                if contexto is not None:
                    contexto.esperar_rodando(esperar)
        finally:
            if contexto is not None:
                contexto.close()


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
    """Roteia entre a rotina do robô, o simulador e os utilitários."""
    parser = argparse.ArgumentParser(
        description="Rotinas do Sentinel (SoBot). Sem argumentos, roda no robô.")
    parser.add_argument("--debug-visao", action="store_true",
                        help="olha a câmera e mede a cor pedida (não move o robô)")
    parser.add_argument("--cor", default="vermelho",
                        help="cor para o --debug-visao (padrão: vermelho)")
    parser.add_argument("--listar-botoes", action="store_true",
                        help="mostra os códigos de botão do Logitech F710")
    grupo = parser.add_argument_group("simulação")
    grupo.add_argument("--simular", action="store_true",
                       help="roda contra uma placa virtual, sem tocar no robô")
    grupo.add_argument("--pista", metavar="ARQUIVO",
                       help="TOML da pista simulada (padrão: a de demonstração)")
    grupo.add_argument("--velocidade", type=float, default=1.0, metavar="X",
                       help="multiplicador do tempo na simulação (padrão: 1)")
    grupo.add_argument("--painel", action="store_true",
                       help="abre o painel web da simulação")
    grupo.add_argument("--abrir", action="store_true",
                       help="abre o painel no navegador (implica --painel)")
    grupo.add_argument("--porta", type=int, default=8765,
                       help="porta do painel (padrão: 8765)")
    grupo.add_argument("--esperar", type=float, default=None, metavar="S",
                       help="com --painel, segura o servidor por S segundos e sai")
    args = parser.parse_args(argv)

    try:
        if args.listar_botoes:
            listar_botoes()
        elif args.debug_visao:
            depurar_visao(args.cor)
        elif args.simular or args.painel or args.abrir:
            simular(args.pista, args.velocidade,
                    painel=args.painel or args.abrir, abrir=args.abrir,
                    porta=args.porta, esperar=args.esperar)
        else:
            main()
    except KeyboardInterrupt:
        print("\ninterrompido pelo usuário")
        sys.exit(130)


if __name__ == "__main__":
    cli()
