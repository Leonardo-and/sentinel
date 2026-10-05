"""Rota 3 — pallet: pega a carga, leva até a prateleira e solta.

A receita do guia §6.4, encadeando os três subsistemas que quase nunca andam
juntos: garra (ímã), elevador e rodas. O ponto chato é a ordem — desce, liga o
ímã, **sobe**, anda, desce e desliga. Escrever isso no robô real custa 14 s de
elevador por lado e medo de deixar a carga cair; aqui custa meio segundo e dá
para repetir até a cinemática ficar certa.

    python -m rotas.pallet --velocidade 8 --painel
"""

from __future__ import annotations

import argparse

from sentinel import SoBot, load_config
from sentinel.simulacao import Alvo, Arena, Montagem, Objeto, PlacaVirtual

#: saída do ímã da garra. O guia §3.17 não mapeia ``DO`` ↔ relé, e o repositório
#: oficial só sugere ``DO5`` — confirme no robô antes de confiar (AGENTS.md).
CANAL_IMA = 5

#: raio do robô na simulação, o mesmo que :mod:`cinematica` usa para colisão
RAIO_ROBO_MM = 200.0


class RotaPallet:
    """Busca o pallet, carrega em linha reta e solta na prateleira."""

    velocidade_cm_s = 12
    aceleracao_ms = 400
    desaceleracao_ms = 400
    afastar_mm = 350.0
    tolerancia_mm = 50.0

    def arena(self) -> Arena:
        """Pallet e prateleira alinhados no mesmo eixo, com a carga no meio."""
        return Arena(
            largura=3200.0,
            altura=2000.0,
            objetos=(Objeto(1100.0, 1000.0, 400.0, 300.0, "vermelho", "pallet1"),),
            alvos=(Alvo(2600.0, 1000.0, "vermelho", "prateleira1"),),
            montagem=Montagem(500.0, 1000.0, 0.0),
        )

    # --- a garra ------------------------------------------------------------
    def _comum(self) -> dict:
        return {
            "accel_ms": self.aceleracao_ms,
            "decel_ms": self.desaceleracao_ms,
            "speed_cm_s": self.velocidade_cm_s,
            "wait": True,
        }

    def desce_e_agarra(self, bot: SoBot) -> None:
        """Elevador desce, ímão liga, elevador sobe. As rodas não se movem."""
        bot.elevator("down")
        bot.digital_output(CANAL_IMA, True)
        bot.elevator("up")

    def solta(self, bot: SoBot) -> None:
        """Elevador desce, ímão desliga."""
        bot.elevator("down")
        bot.digital_output(CANAL_IMA, False)

    # --- a rota -------------------------------------------------------------
    def rodar(self, bot: SoBot, placa: PlacaVirtual | None = None) -> str:
        """Executa a rota e devolve um resumo.

        ``EL`` e ``DO`` são comandos de **fila** e não dão ack, então quem
        garante a ordem é a própria fila da placa. Não dá para dormir entre
        eles esperando o elevador acabar — quem controla o tempo do elevador é
        a placa, e ela faz isso sozinha (guia §2.3).

        Passe ``placa`` para a rota conferir onde o pallet caiu; sem ele a
        rota funciona igual e só não faz a última checagem.
        """
        arena = self.arena()
        origem = arena.montagem
        pallet = arena.objetos[0]
        prateleira = arena.alvos[0]
        borda = pallet.x - pallet.largura / 2  # 900 mm

        # 1. encosta no pallet. A placa trava a 200 mm da borda pela colisão,
        #    e a garra pega a 250 mm — então qualquer ``move`` que chegue lá
        #    funciona, e a posição final é promessa da placa, não do host.
        bot.move(borda - origem.x, **self._comum())
        self.desce_e_agarra(bot)

        # 2. recua para a garra não raspar no pallet
        bot.move(-self.afastar_mm, **self._comum())

        # 3. linha reta até a prateleira. O passo 1 termina na COLISÃO, um
        #    raio de robô antes da borda — não onde o ``move`` pediu. A conta
        #    tem de usar onde o robô parou, senão a entrega erra por esse tanto.
        saida = borda - RAIO_ROBO_MM - self.afastar_mm
        bot.move(prateleira.x - saida, **self._comum())

        # 4. entrega
        self.solta(bot)

        if placa is None:
            return "entregue (sem conferir)"
        erro = abs(placa.pose.x - prateleira.x)
        if erro > self.tolerancia_mm:
            raise AssertionError(
                f"o pallet caiu a {erro:.0f} mm do alvo (tolera {self.tolerancia_mm:.0f})"
            )
        return f"entregue a {erro:.0f} mm do alvo"


def main(velocidade: float = 1.0, painel: bool = False) -> None:
    rota = RotaPallet()
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
                    print("[rota]", rota.rodar(bot, placa))
                finally:
                    bot.wheels_enable(False)
                if contexto is not None:
                    contexto.esperar_rodando()
        finally:
            if contexto is not None:
                contexto.close()


def cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Pallet: pega, leva e solta")
    parser.add_argument("--velocidade", type=float, default=1.0,
                        help="multiplicador do tempo (o elevador leva 7 s por curso)")
    parser.add_argument("--painel", action="store_true")
    args = parser.parse_args(argv)
    main(args.velocidade, args.painel)


if __name__ == "__main__":
    cli()
