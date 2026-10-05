"""Rota 1 — percurso fixo: um quadrado com ``MT0`` de curva e reta.

A receita mais simples do guia §6.1, na forma que a simulação consome: a
pista é só uma arena, e o quadrado é uma lista de movimentos. Serve para
conferir se a cinemática fecha (a soma das quatro retas tem que devolver o
robô ao ponto de partida) — e é o primeiro exemplo para abrir no painel.

    python -m rotas.percurso_fixo --velocidade 4
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from sentinel import SoBot, load_config
from sentinel.simulacao import Arena, Montagem, PlacaVirtual


@dataclass
class PercursoFixo:
    """Um quadrado de ``lado_mm``, andado a ``velocidade_cm_s``."""

    lado_mm: float = 800.0
    velocidade_cm_s: int = 15
    aceleracao_ms: int = 500
    desaceleracao_ms: int = 500

    def __post_init__(self) -> None:
        if self.lado_mm <= 0:
            raise ValueError(f"lado_mm={self.lado_mm} tem de ser positivo")

    @property
    def comandos(self) -> int:
        """Quantos ``MT0`` de movimento o quadrado usa."""
        return 4

    def arena(self) -> Arena:
        """Arena com folga para o quadrado inteiro caber sem bater."""
        folga = 400.0
        return Arena(
            largura=self.lado_mm + 2 * folga,
            altura=self.lado_mm + 2 * folga,
            montagem=Montagem(folga, folga),
        )

    def rodar(self, bot: SoBot) -> None:
        """Executa o quadrado. Assuma o ``wheels_enable(True)`` já feito."""
        comum = {
            "accel_ms": self.aceleracao_ms,
            "decel_ms": self.desaceleracao_ms,
            "speed_cm_s": self.velocidade_cm_s,
            "wait": True,
        }
        for _ in range(3):
            bot.move(self.lado_mm, **comum)
            bot.turn(90, "right", **comum)
        bot.move(self.lado_mm, **comum)


def main(velocidade: float = 1.0, painel: bool = False) -> None:
    """Roda o quadrado numa simulação avulsa, com o painel opcional."""
    percurso = PercursoFixo()
    with PlacaVirtual(percurso.arena(), velocidade=velocidade) as placa:
        contexto = None
        if painel:
            from sentinel.simulacao import Painel

            contexto = Painel(placa, porta=0)
            print(f"[painel] {contexto.url}")
            contexto.abrir()
        try:
            with SoBot(serial_obj=placa, verbose=True) as bot:
                bot.configure_wheels(*load_config().wheel_params())
                bot.command_return(True, "MT0")
                bot.wait_for("CR OK MT0")
                bot.wheels_enable(True)
                try:
                    percurso.rodar(bot)
                finally:
                    bot.wheels_enable(False)
                if contexto is not None:
                    contexto.esperar_rodando()
        finally:
            if contexto is not None:
                contexto.close()


def cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Quadrado de 800 mm na simulação")
    parser.add_argument("--velocidade", type=float, default=1.0,
                        help="multiplicador do tempo (2 = duas vezes mais rápido)")
    parser.add_argument("--painel", action="store_true", help="abre o painel web")
    args = parser.parse_args(argv)
    main(args.velocidade, args.painel)


if __name__ == "__main__":
    cli()
