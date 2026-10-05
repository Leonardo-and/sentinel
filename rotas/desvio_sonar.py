"""Rota 2 — desvio de sonar: anda em frente e desvia do que aparece.

A receita do guia §6.2: ler os oito sonares, achar o obstáculo mais próximo
dentro do cone frontal e girar para o lado mais livre. Aqui a decisão é
discreta ( esquerda/direita ), porque o ``MT0`` em modo fixo não tem condicional
— quem decide é o Python, que é justamente o que a simulação deixa você
exercitar sem risco no pátio.

    python -m rotas.desvio_sonar --velocidade 4 --painel
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

from sentinel import SoBot, SoBotError, load_config
from sentinel.simulacao import Arena, Montagem, Parede, PlacaVirtual


def _sem_infinito(leituras: dict) -> dict:
    """Troca o ``INF`` do driver (um ``float`` gigante) por ``None``.

    ``read_sonar`` devolve ``INF`` para "acima de 4000 mm" e ``None`` para
    "falhou". A rota só precisa de um número: ausente é 9999, que é o pior
    caso do desvio.
    """
    return {i: (None if v is None or v >= 4000 else v) for i, v in leituras.items()}


@dataclass
class DesvioSonar:
    """Percorre a arena desviando de tudo que o sonar frontal enxergar."""

    distancia_mm: float = 300.0
    velocidade_cm_s: int = 12
    aceleracao_ms: int = 300
    desaceleracao_ms: int = 300
    #: Sonares que contam como "à frente" e os lados para os quais dá para fugir.
    cone_frente: tuple[int, ...] = (1, 2, 3)
    desvio_graus: int = 45
    max_desvios: int = 200
    _desvios: int = field(default=0, init=False)
    _ultimo: dict[int, float | None] = field(default_factory=dict, init=False)

    # --- a arena ------------------------------------------------------------
    def arena(self) -> Arena:
        """Corredor com dois pilares: sem obstáculo não há o que desviar."""
        return Arena(
            largura=2600.0,
            altura=1800.0,
            paredes=(
                Parede(1400.0, 0.0, 1400.0, 1100.0),  # fecha a frente e a direita
                Parede(2000.0, 1100.0, 2000.0, 1800.0),  # fecha a esquerda adiante
            ),
            montagem=Montagem(400.0, 900.0, 0.0),
        )

    # --- a decisão ----------------------------------------------------------
    def frontal(self) -> dict[int, float | None]:
        """Só os sonares da frente, com a distância em mm (``None`` = sem alvo)."""
        return {i: v for i, v in self._ultimo.items() if i in self.cone_frente}

    def lado_livre(self) -> str:
        """Qual lado está mais limpo: o menor alcance entre 4 e 8.

        O ``FF`` do manual vira ``None`` na simulação; ``9999`` aqui é
        "não viu nada", que é o pior caso para desviar.
        """
        direita = min(self._alcance(4), self._alcance(5))
        esquerda = min(self._alcance(8), self._alcance(7))
        return "left" if esquerda > direita else "right"

    def _alcance(self, sonar: int) -> float:
        valor = self._ultimo.get(sonar)
        return 9999.0 if valor is None else valor

    def precisa_desviar(self) -> bool:
        """Tem obstáculo à frente dentro de ``distancia_mm``?"""
        return any(
            valor is not None and valor <= self.distancia_mm
            for indice, valor in self._ultimo.items()
            if indice in self.cone_frente
        )

    # --- o movimento --------------------------------------------------------
    def _comum(self) -> dict:
        return {
            "accel_ms": self.aceleracao_ms,
            "decel_ms": self.desaceleracao_ms,
            "speed_cm_s": self.velocidade_cm_s,
        }

    def passo(self, bot: SoBot) -> str:
        """Um passo de decisão. Devolve o que fez, para o painel e o log."""
        self._ultimo = _sem_infinito(bot.read_sonar())
        if not self.precisa_desviar():
            bot.move(self.distancia_mm, wait=True, **self._comum())
            return "frente"
        lado = self.lado_livre()
        bot.move(max(100.0, self.distancia_mm / 2), wait=True, **self._comum())
        bot.turn(self.desvio_graus, lado, wait=True, **self._comum())
        self._desvios += 1
        return f"desviou para {lado}"

    def rodar(self, bot: SoBot, passos: int | None = None) -> int:
        """Anda até acabar os passos (ou os desvios permitidos)."""
        feitos = 0
        limite = passos if passos is not None else self.max_desvios
        while feitos < limite and self._desvios < self.max_desvios:
            self.passo(bot)
            feitos += 1
        return feitos


def main(velocidade: float = 1.0, passos: int | None = None, painel: bool = False) -> None:
    rota = DesvioSonar()
    with PlacaVirtual(rota.arena(), velocidade=velocidade) as placa:
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
                    rota.rodar(bot, passos)
                except SoBotError as erro:
                    print(f"[rota] o robô reclamou: {erro}")
                finally:
                    bot.wheels_enable(False)
                if contexto is not None:
                    contexto.esperar_rodando()
        finally:
            if contexto is not None:
                contexto.close()


def cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Desvio de sonar na simulação")
    parser.add_argument("--velocidade", type=float, default=1.0)
    parser.add_argument("--passos", type=int, default=None)
    parser.add_argument("--painel", action="store_true")
    args = parser.parse_args(argv)
    main(args.velocidade, args.passos, args.painel)


if __name__ == "__main__":
    cli()
