"""A placa virtual: o outro lado do cabo serial é este arquivo.

:class:`PlacaVirtual` implementa a mesma superfície que o driver espera de um
``serial.Serial`` (``write``, ``read``, ``in_waiting``, ``flush``, ``close``),
e por baixo roda o robô de verdade na arena: interpreta cada comando, enfileira
os que são de fila, move o robô no tempo e escreve as respostas no formato do
protocolo.

    from sentinel.simulacao import PlacaVirtual, exemplo
    from sentinel import SoBot

    placa = PlacaVirtual(exemplo())
    bot = SoBot(serial_obj=placa, verbose=True)
    bot.move(1000, wait=True)

Três coisas que a placa reproduz do firmware e que mudam o resultado da prévia:

1. **Fila × imediato.** Movimento em modo fixo, ``EL``, ``DO``, ``LT``, ``BZ``,
   ``SC`` e ``DL`` entram numa fila de 100 e rodam em sequência (guia §2.3).
   Já ``SS``, ``SL``, ``SI``, ``DI``, ``AI``, ``SA``, ``CR``, ``BC``, ``KC``,
   modo contínuo e modo pulsos respondem na hora.
2. **O ``CR`` responde quando o comando é executado**, não quando chega. Por
   isso o ``wait=True`` do ``move`` só volta depois que o robô andou — e por
   isso o ack imediato do ``CR1`` fica pendente na serial, que é exatamente o
   gotcha do AGENTS.md.
3. **Os erros valem**: ``ERROR=01`` em sintaxe ruim, ``ERROR=02`` ao misturar
   ``MT0`` com ``MT1``/``MT2``.

O relógio é injetável. Com :class:`RelogioFake` a simulação inteira roda em
microssegundos, o que deixa os testes rápidos e determinísticos — sem ``sleep``
real em lugar nenhum.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from math import ceil
from typing import Protocol

from . import cinematica as cin
from .cinematica import Ganhos, Pose
from .pista import ALCANCE_SONAR_MM, Arena, Objeto

LIMITE_FILA = 100  # comandos de ação armazenáveis (guia §2.3)
INF = float("inf")

# Tokens que iniciam um comando de identificação, do mais longo para o mais
# curto. ``SS0`` casa ``SS`` e deixa ``0`` como parâmetro; o mesmo vale para
# ``DI1``, ``AI2``, ``DO3``, ``AO1``, ``CR1``, ``LT``… Sem isso não dá para
# saber onde um comando acaba e o próximo começa.
CABECALHOS = (
    "MT0",
    "MT1",
    "MT2",
    "MT3",
    "BC",
    "KC",
    "SS",
    "SI",
    "SL",
    "DI",
    "AI",
    "SA",
    "DO",
    "AO",
    "LT",
    "BZ",
    "SC",
    "EL",
    "DL",
    "WP",
    "PG",
    "CR",
)
# Só estes respondem com ``CR OK <id>``; o resto é imediato ou não tem retorno.
IDS_COM_CR = ("MT0", "MT3", "LT", "DO", "AO", "BZ", "EL", "DL", "WP", "PG")

# Cabeçalhos que a placa sabe executar. Um token fora daqui nunca chega ao
# roteador: o parser o descarta antes, e aí o erro é de sintaxe (``01``).
EXECUTAVEIS = frozenset(
    {
        "MT0",
        "MT1",
        "MT2",
        "MT3",
        "BC",
        "KC",
        "CR",
        "SS",
        "SI",
        "SL",
        "DI",
        "AI",
        "SA",
        "DO",
        "AO",
        "LT",
        "BZ",
        "SC",
        "EL",
        "DL",
        "WP",
        "PG",
    }
)


# ----------------------------------------------------------------------------
# relógio
# ----------------------------------------------------------------------------
class Relogio(Protocol):
    """O que a placa precisa de um relógio: o tempo e a pausa."""

    def tempo(self) -> float:
        """Instante atual em segundos (monotônico)."""

    def dormir(self, segundos: float) -> None:
        """Espera ``segundos`` de tempo **simulado**."""


class RelogioReal:
    """Relógio de verdade, com o fator de velocidade da simulação."""

    def __init__(self, velocidade: float = 1.0):
        if velocidade <= 0:
            raise ValueError(f"velocidade={velocidade} tem de ser positiva")
        self.velocidade = velocidade

    def tempo(self) -> float:
        return time.monotonic()

    def dormir(self, segundos: float) -> None:
        # ``velocidade > 1`` roda a simulação mais rápido que o tempo real:
        # divide a espera, e o tempo simulado anda ``segundos`` mesmo assim.
        time.sleep(segundos / self.velocidade)


class RelogioFake:
    """Relógio que só anda quando alguém manda dormir. Para os testes."""

    def __init__(self) -> None:
        self._agora = 0.0

    def tempo(self) -> float:
        return self._agora

    def dormir(self, segundos: float) -> None:
        self._agora += max(0.0, segundos)


# ----------------------------------------------------------------------------
# parser
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Comando:
    """Um comando já separado em cabeçalho e parâmetros crus.

    ``MT0 D500 AT1000 V10`` vira ``Comando("MT0", ("D500", "AT1000", "V10"))``.
    O número que vem grudado no cabeçalho (``DO5``, ``DL500``, ``SS0``) fica
    como primeiro parâmetro, e ``ident`` guarda qual comando de identificação
    acompanha um ``CR`` (``MT0 CR1`` → ``ident="MT0"``).
    """

    cab: str
    params: tuple[str, ...] = ()
    bruto: str = ""
    ident: str = ""

    def param(self, nome: str, padrao: str | None = None) -> str | None:
        """Valor do parâmetro ``nome`` (``D``, ``AT``, ``V``…), sem o nome.

        Busca o primeiro token que começa pelo nome e devolve o resto. Não
        exige ponto de corte exato porque o driver monta sempre os nomes
        certinhos (``AT1000``) e o manual não usa prefixos ambíguos.
        """
        for token in self.params:
            if token.startswith(nome) and len(token) > len(nome):
                return token[len(nome) :]
        return padrao

    def flag(self, nome: str) -> bool:
        """O parâmetro ``nome`` está presente? (para ``DF``, ``R``, ``L``)."""
        return any(token == nome for token in self.params)

    def numero(self, nome: str, padrao: float | None = None) -> float | None:
        """Valor numérico do parâmetro, convertendo a vírgula decimal do robô."""
        bruto = self.param(nome)
        if bruto is None:
            return padrao
        try:
            return float(bruto.replace(",", "."))
        except ValueError:
            return padrao

    @property
    def tem_cr(self) -> bool:
        """Este comando pediu retorno?

        O parser **sempre** arranca o ``CR`` para um comando próprio (é
        imediato, não pode ficar preso na fila), então o ``CR1`` mora em
        ``ident`` do comando seguinte e nunca nos ``params``. O sinal está no
        próprio objeto ``CR``: :attr:`pede_ack`.
        """
        return self.pede_ack

    @property
    def pede_ack(self) -> bool:
        """Sou um ``CR1`` ligado? (``CR0`` é ``False``.)"""
        return self.cab == "CR" and bool(self.params) and self.params[0] == "1"


def separar(comando: str) -> list[Comando]:
    """Quebra um comando recebido em uma lista de :class:`Comando`.

    O driver manda cada comando como uma escrita só, mas alguns mandam dois
    grupos de uma vez (``MT1 E1 MT2 E1``, o pulso duplo). Cada cabeçalho
    reconhecível começa um comando novo; o resto é parâmetro.

    O número grudado no cabeçalho vira o primeiro parâmetro — ``DO5 E1`` fica
    ``("5", "E1")``, ``DL500`` fica ``("500",)`` — e um ``CR`` guarda no
    ``ident`` o comando que o qualifica (``MT0 CR1`` → ``ident="MT0"``).
    """
    saida: list[Comando] = []
    atual: list[str] = []
    ident = ""
    soltos: list[str] = []

    def fechar() -> None:
        nonlocal atual, ident
        if atual:
            saida.append(Comando(atual[0], tuple(atual[1:]), " ".join(atual), ident))
            atual = []

    for token in comando.split():
        cab = next((c for c in CABECALHOS if token == c or token.startswith(c)), None)
        if cab is None:
            # Token depois de um cabeçalho é parâmetro; antes, é lixo de
            # comando — e o parser precisa entregar isso ao roteador.
            (atual.append(token) if atual else soltos.append(token))
            continue
        resto = token[len(cab) :]
        if cab == "CR" and ident:
            # ``MT0 CR1``: o CR pertence ao comando que o antecede.
            fechar()
        elif cab != "CR":
            fechar()
            ident = token if cab in IDS_COM_CR else ""
        atual = [cab, resto] if resto else [cab]
    fechar()
    # Lixo que não virou comando vira **um** ``Comando`` sem cabeçalho, para o
    # roteador responder ``ERROR=00`` em vez de engolir a escrita em silêncio.
    if soltos:
        saida.insert(0, Comando("", tuple(soltos), " ".join(soltos), ""))
    return saida


# ----------------------------------------------------------------------------
# estado observável (o painel lê isto)
# ----------------------------------------------------------------------------
@dataclass
class Estado:
    """Um retrato do robô, para o painel desenhar e para o teste olhar."""

    pose: Pose
    vel_mm_s: float = 0.0
    motores_ligados: bool = False
    modo_pulso: bool = False
    modo_continuo: bool = False
    direcao_continua: str = ""  # MF/MB/ML/MR/…
    led: tuple[int, int, int] | None = None
    buzzer: bool = False
    elevador: float = 0.0  # 0 = baixo, 1 = alto
    imanes: bool = False
    relés: dict[int, bool] = field(default_factory=dict)
    segure: Objeto | None = None
    colidiu: bool = False
    fila: int = 0
    simulando: bool = True
    tempo: float = 0.0
    trilha: tuple[tuple[float, float], ...] = ()


# ----------------------------------------------------------------------------
# a placa
# ----------------------------------------------------------------------------
class PlacaVirtual:
    """Uma Placa de Controle e Potência que é software.

    Fala o protocolo do robô por um lado (``write``/``read``) e move um robô na
    arena por outro. Todo comando do guia §3 é reconhecido; o que não existe,
    vira ``ERROR=00``, como no firmware.
    """

    def __init__(
        self,
        arena: Arena | None = None,
        *,
        dist_rodas_mm: float = 262.0,
        relogio: Relogio | None = None,
        ganho_inicial: Ganhos | None = None,
        velocidade: float = 1.0,
        colidir: bool = True,
    ):
        self.arena = arena if arena is not None else Arena()
        self.dist_rodas_mm = dist_rodas_mm
        self.relogio: Relogio = relogio if relogio is not None else RelogioReal(velocidade)
        self.ganhos = ganho_inicial or Ganhos()
        self.colidir = colidir

        # --- o que o driver enxerga ------------------------------------------
        self._saida = bytearray()
        self._log: list[str] = []

        # --- estado da placa --------------------------------------------------
        self.pose = Pose.de_graus(
            self.arena.montagem.x, self.arena.montagem.y, self.arena.montagem.angulo
        )
        self._inicio = self.pose
        self._ganhos_wd: dict[int, float] = {1: 100.0, 2: 100.0}
        self._ganhos_dw = dist_rodas_mm
        self._cr = {"geral": False}
        self._fila: list[Comando] = []
        self._vel = 0.0
        self._motores = False
        self._pulso = False
        self._continuo = False
        self._direcao = ""
        self._vel_cont = 2.0  # cm/s, o padrão do manual (§3.6)
        self._dif_cont = False
        self._raio_cont = 50.0
        self._at_cont, self._dt_cont = 100, 100
        self._aprendendo = False
        self._aprendido = 0.0
        self._led: tuple[int, int, int] | None = None
        self._buzzer = False
        self._elevador = 0.0
        self._elevador_destino = 0.0
        self._imanes = False
        self._reles: dict[int, bool] = {}
        self._reles_timer: dict[int, float] = {}
        self._objetos: dict[str, Objeto] = {o.id: o for o in self.arena.objetos if o.id}
        self._segure: Objeto | None = None
        self._colidiu = False
        self._travado = False
        self._trilha: list[tuple[float, float]] = [(self.pose.x, self.pose.y)]
        self._ultimo_mov: tuple[str, float] = ("straight", 0.0)
        self._contador_do: dict[int, float] = {}
        self._parado = False  # BC/KC interrompeu o movimento atual
        self._tique = 0.01  # 10 ms: o passo do modo contínuo

        # --- o fio entre o host e o robô -------------------------------------
        # O ``write`` só enfileira: quem executa é a thread, senão o ``write``
        # travaria durante o movimento e o painel não veria nada acontecendo.
        self._lock = threading.RLock()
        self._entrada: deque[Comando] = deque()
        self._interromper = threading.Event()  # BC/KC acorda o movimento
        self._fim = threading.Event()  # pede a parada da thread
        self._thread = threading.Thread(target=self._loop, name="placa-virtual", daemon=True)
        self._thread.start()

    # --- superfície de serial ------------------------------------------------
    @property
    def in_waiting(self) -> int:
        """Bytes prontos para o driver ler. O rodízio do ``read_line``."""
        with self._lock:
            return len(self._saida)

    def write(self, dados: bytes) -> int:
        """Recebe um comando (o driver nunca manda terminador) e o enfileira.

        Devolve na hora: a execução é da thread, como no firmware, onde a UART
        recebe o próximo comando enquanto o movimento anterior ainda corre.
        """
        comando = dados.decode("ascii", "replace").strip()
        if not comando:
            return len(dados)
        comandos = separar(comando)
        with self._lock:
            self._registrar(f">> {comando}")
            self._entrada.extend(comandos)
        # ``BC``/``KC`` são a parada de emergência: no robô real a UART é
        # atendida mesmo com a MCU ocupada, então o corte tem de valer na hora.
        # Deixar na fila faria o ``KC`` esperar os 20 s do movimento terminar —
        # justamente o que ele existe para evitar.
        if any(c.cab in ("BC", "KC") for c in comandos):
            self._interromper.set()
        return len(dados)

    def read(self, n: int = 1) -> bytes:
        with self._lock:
            n = min(n, len(self._saida))
            dados, self._saida = bytes(self._saida[:n]), self._saida[n:]
        return dados

    def flush(self) -> None:
        """O driver chama antes de mandar; joga fora o que estiver na fila."""
        with self._lock:
            self._saida.clear()

    def close(self) -> None:
        """Encerra a simulação: para o robô e desliga os motores."""
        self._fim.set()
        self._interromper.set()
        self._thread.join(timeout=2.0)
        with self._lock:
            self._parar_movimento()
            self._motores = False
            self._fila.clear()
            self._entrada.clear()
            self._continuo = False
            self._vel = 0.0

    def __enter__(self) -> PlacaVirtual:
        return self

    def __exit__(self, *_excecao) -> None:
        self.close()

    # --- espera a placa (para testes e para o painel) -----------------------
    def _esperar(self, prefixo: str, timeout: float = 5.0) -> str:
        """Devolve a primeira linha da saída que começa por ``prefixo``.

        É o espelho de :meth:`SoBot.wait_for` para quem não tem um driver na
        frente — os testes esperam assim, e o painel usa para sincronizar.
        """
        fim = time.monotonic() + timeout
        with self._lock:
            while True:
                for linha in self._saida.decode("ascii", "replace").splitlines():
                    if linha.startswith(prefixo):
                        return linha
                if self._fim.is_set() or time.monotonic() >= fim:
                    raise TimeoutError(f"sem resposta {prefixo!r} em {timeout}s")
                self._lock.release()
                try:
                    time.sleep(0.005)
                finally:
                    self._lock.acquire()

    def _esperar_muita(self, tempo: float = 0.15) -> None:
        """Deixa a thread rodar um pouco, sem esperar linha nenhuma.

        Para os comandos que não têm resposta própria (fila, periféricos) e
        para dar tempo de o movimento acontecer antes de conferir o estado.
        """
        time.sleep(tempo)

    # --- a thread do robô ----------------------------------------------------
    def _pop_proximo(self) -> Comando | None:
        """Tira o próximo comando da entrada, com ``BC``/``KC`` na frente.

        A parada de emergência não espera a vez dela na fila: se o host mandou
        ``MT0 D5000`` e logo depois ``KC``, o ``KC`` tem de ser executado antes
        de o movimento começar. Sem esta prioridade o movimento de 100 s
        rodaria inteiro antes de o ``KC`` ser lido.

        E o que sobrar na fila **antes** do ``BC``/``KC`` vai fora junto: a
        parada limpa a fila (guia §3.7), então um ``MT0`` que ainda estava
        esperando não pode arrancar depois do freio.
        """
        for i, cmd in enumerate(self._entrada):
            if cmd.cab in ("BC", "KC"):
                del self._entrada[i]
                self._entrada.clear()
                return cmd
        return self._entrada.popleft() if self._entrada else None

    def _loop(self) -> None:
        """Executa o que chega, na ordem, e avança o modo contínuo."""
        while not self._fim.is_set():
            with self._lock:
                comando = self._pop_proximo()
                if comando is not None:
                    # O sinal de parada é sempre do comando atual: um ``BC``
                    # antigo não pode travar o próximo movimento.
                    self._interromper.clear()
                    self._travado = False
                    self._executar(comando)
                elif self._continuo:
                    self._passo_continuo()
            if comando is None:
                # Sem trabalho: dorme um tique curtinho para o painel ter
                # tempo de desenhar o robô andando.
                self._fim.wait(0.01)

    # --- leitura do estado ---------------------------------------------------
    @property
    def log(self) -> tuple[str, ...]:
        """Tudo que entrou e saiu da serial, para o painel rolar."""
        return tuple(self._log)

    def estado(self) -> Estado:
        """Retrato atual — é o que o painel transforma em desenho."""
        with self._lock:
            return self._estado()

    def _estado(self) -> Estado:
        return Estado(
            pose=self.pose,
            vel_mm_s=self._vel,
            motores_ligados=self._motores,
            modo_pulso=self._pulso,
            modo_continuo=self._continuo,
            direcao_continua=self._direcao,
            led=self._led,
            buzzer=self._buzzer,
            elevador=self._elevador,
            imanes=self._imanes,
            relés=dict(self._reles),
            segure=self._segure,
            colidiu=self._colidiu,
            fila=len(self._fila),
            tempo=self.relogio.tempo(),
            trilha=tuple(self._trilha),
        )

    # --- descritivos dos sensores --------------------------------------------
    def objetos_no_chao(self) -> list[Objeto]:
        """Os objetos que ainda estão no chão; o que está na garra não conta."""
        carregado = self._segure.id if self._segure is not None else None
        return [o for o in self._objetos.values() if o.id != carregado]

    def leitura_sonares(self) -> dict[int, float | None]:
        """``{1..8}`` em mm; ``None`` quando não há nada no alcance (``FFFF``)."""
        no_chao = self.objetos_no_chao()
        return {
            s.id: cin.alcance_sonar(self.pose, s, self.arena, no_chao)
            for s in self.arena.robo.sonares
        }

    def leitura_linha(self) -> dict[int, int]:
        """``{1..3}`` em 0/1 — o que o ``SL`` devolve (guia §3.11)."""
        return cin.sensores_de_linha(self.pose, self.arena)

    def leitura_infra(self) -> float:
        """Distância do infra em **cm**, saturada em 150 (``SI FFF``)."""
        return cin.leitura_infravermelho(self.pose, self.arena)

    def leitura_digital(self) -> dict[int, int]:
        """``DI1..DI8`` em 0/1, incluindo o que o robô switched."""
        leitura = cin.digital(self.pose, self.arena)
        return {n: leitura.get(n, 0) for n in range(1, 9)}

    # =========================================================================
    # execução
    # =========================================================================
    def _registrar(self, linha: str) -> None:
        self._log.append(linha)
        if len(self._log) > 400:
            del self._log[:100]

    def _responder(self, linha: str) -> None:
        self._registrar(f"<< {linha}")
        self._saida.extend(f"{linha}\n".encode("ascii", "replace"))

    def _erro(self, codigo: str, detalhe: str = "") -> None:
        self._responder(f"ERROR={codigo}" + (f" {detalhe}" if detalhe else ""))

    def _executar(self, cmd: Comando) -> None:
        """Roteia um comando. Imediato responde agora; de fila, enfileira."""
        cab = cmd.cab
        if cab == "":
            # Token que nem é cabeçalho: o manual chama isso de comando
            # desconhecido, ``ERROR=00``.
            self._erro("00", cmd.bruto)
            return
        # CR é sempre imediato e muda o estado do ack antes de tudo.
        if cab == "CR":
            self._cr_geral(cmd)
            return
        if cab == "SS":
            self._responder(self._linha_sonares(cmd))
            return
        if cab == "SL":
            self._responder(self._linha_linha())
            return
        if cab == "SI":
            self._responder(self._linha_infravermelho())
            return
        if cab == "DI":
            self._responder(self._linha_digital(cmd))
            return
        if cab == "AI":
            self._responder(self._linha_analogica(cmd))
            return
        if cab == "SA":
            self._responder(self._linha_acelerometro())
            return
        if cab == "KC":
            self._parar_movimento()
            self._fila.clear()
            self._vel = 0.0
            self._responder("CR OK KC")
            return
        if cab == "BC":
            self._bc(cmd)
            return
        if cab == "MT1" or cab == "MT2":
            self._pulso_roda(cmd)
            return

        # daqui pra frente é fila — exceto o modo contínuo, que é imediato.
        if cab == "MT0":
            if self._pulso:
                self._erro("02", cmd.bruto)
                return
            self._rotas_mt0(cmd)
            return
        if cab in ("MT3", "EL", "DO", "AO", "LT", "BZ", "SC", "DL", "WP", "PG"):
            self._fila.append(cmd)
            self._rodar_fila()
            return
        # Cabeçalho reconhecido mas sem rota: ``ERROR=01`` (sintaxe inválida).
        self._erro("01", cab)

    # --- CR ------------------------------------------------------------------
    def _cr_geral(self, cmd: Comando) -> None:
        """Liga/desliga o retorno. O ack sai **na hora**, é imediato (guia §2.3)."""
        # ``separar`` já tirou o nome: ``CR1`` sobra como ``params[0] == "1"``.
        valor = cmd.params[0] if cmd.params else None
        if valor not in ("0", "1"):
            self._erro("01", "CR")
            return
        alvo = cmd.ident or "geral"
        self._cr[alvo if alvo in IDS_COM_CR else "geral"] = valor == "1"
        self._responder(f"CR OK {alvo}")

    # --- BREAK / KILL --------------------------------------------------------
    def _bc(self, cmd: Comando) -> None:
        alvo = cmd.bruto.split()[0] if cmd.bruto else "MT0"
        self._parar_movimento()
        self._vel = 0.0
        if alvo == "MT3":
            return
        # MT0 BC no modo fixo limpa a fila (guia §3.7); no contínuo, pausa.
        if not self._continuo:
            self._fila.clear()
        self._direcao = ""

    def _parar_movimento(self) -> None:
        # Marca o movimento corrente como interrompido e acorda o sono.
        self._travado = True
        self._vel = 0.0
        self._interromper.set()

    # --- modo pulsos (MT1/MT2) -----------------------------------------------
    def _pulso_roda(self, cmd: Comando) -> None:
        if self._motores:
            self._erro("02", cmd.bruto)
            return
        ligar = cmd.param("E")
        if ligar is not None:
            self._pulso = ligar == "1"
            if not self._pulso:
                self._vel = 0.0

    # --- MT0: movimento fixo, habilitação e modo contínuo --------------------
    def _rotas_mt0(self, cmd: Comando) -> None:
        """Decide o que um ``MT0`` faz: habilita, move, pergunta ou contínua."""
        # ``MT0 E1/E0`` liga e desliga os motores.
        if cmd.param("E") is not None:
            self._motores = cmd.param("E") == "1"
            return
        # ``MT0 MS`` pergunta o último movimento.
        if "MS" in cmd.params:
            self._responder(self._linha_status())
            return
        # Modo contínuo: ME, MC, MF/MB/ML/MR/MP — tudo imediato (guia §2.3).
        if any(p.startswith("ME") or p.startswith("MC") for p in cmd.params):
            self._continuo_comando(cmd)
            return
        if any(p in ("MF", "MB", "ML", "MR", "MP", "ML-", "MR-") for p in cmd.params):
            self._direcao = next(
                p for p in cmd.params if p in ("MF", "MB", "ML", "MR", "MP", "ML-", "MR-")
            )
            if self._direcao == "MP":
                self._vel = 0.0
            return
        # ``MT0 MC LM1`` liga o modo aprendizado, que é imediato.
        if any(p in ("LM1", "LM0") for p in cmd.params):
            self._aprendendo = any(p == "LM1" for p in cmd.params)
            return
        # O resto (D, DF, RI) é movimento em modo fixo: vai para a fila.
        if cmd.param("D") is not None:
            self._fila.append(cmd)
            self._rodar_fila()

    def _continuo_comando(self, cmd: Comando) -> None:
        me = cmd.param("ME")
        if me is not None:
            self._continuo = me == "1"
            if not self._continuo:
                self._direcao = ""
                self._vel = 0.0
            return
        self._config_continuo(cmd)

    def _config_continuo(self, cmd: Comando) -> None:
        if cmd.param("MD") == "1":
            self._dif_cont = True
            self._raio_cont = cmd.numero("RI", self._raio_cont)
        elif cmd.param("MD") == "0":
            self._dif_cont = False
        self._at_cont = int(cmd.numero("AT", self._at_cont))
        self._dt_cont = int(cmd.numero("DT", self._dt_cont))
        self._vel_cont = cmd.numero("V", self._vel_cont)

    # --- modo contínuo: um tique de movimento --------------------------------
    def _passo_continuo(self) -> None:
        """Avança a pose no modo contínuo. Roda a cada tique da thread.

        No contínuo não há fila: quem temporiza é o host (guia §2.3), e aqui o
        tempo do tique faz esse papel. O ``MT0 E1`` **não** é exigido: os
        exemplos oficiais do guia ligam o contínuo sem ele.
        """
        if self._direcao in ("", "MP"):
            self._vel = 0.0
            return
        # O tique é fixo e pequeno: 10 ms, o mesmo intervalo do laço.
        dt = self._tique
        dir_ = self._direcao
        destino: Pose | None = None
        if dir_ in ("MF", "MB"):
            # Translação: ``V`` é cm/s (guia §3.6).
            sinal = 1.0 if dir_ == "MF" else -1.0
            self._vel = sinal * self._vel_cont * 10.0
            destino = cin.ir_para_frente(self.pose, sinal * self._vel_cont * 10.0 * dt)
        else:
            # Rotação: o guia não diz a unidade (lacuna §14), então ``V`` é lido
            # como **graus/s** para frente. ``ML-``/``MR-`` invertem o sentido.
            base = dir_.rstrip("-")
            graus = self._vel_cont * dt
            if dir_.endswith("-"):
                graus = -graus
            self._vel = graus / dt
            if base == "ML":
                destino = cin.girar_no_eixo(self.pose, -graus)
            else:
                destino = cin.girar_no_eixo(self.pose, graus)

        if self._dif_cont and dir_ in ("ML", "ML-", "MR", "MR-"):
            destino = cin.girar_diferencial(
                self.pose, destino.angulo - self.pose.angulo, self._raio_cont, self._ganhos_dw
            )
        if destino is None:
            return
        if self.colidir:
            travado = cin.primeiro_travamento(
                self.pose, destino, self.arena, self.objetos_no_chao()
            )
            if travado is not None:
                self._vel = 0.0
                self._direcao = "MP"  # bateu: pausa, como o firmware faria
                self._colidiu = True
                destino = travado
        else:
            self._colidiu = False
        self.pose = destino
        self._trilha.append((self.pose.x, self.pose.y))
        if len(self._trilha) > 2000:
            del self._trilha[:500]

    # --- a fila --------------------------------------------------------------
    def _rodar_fila(self) -> None:
        """Executa a fila em sequência, esvaziando inteira.

        A thread é quem chama isto, então dá para drainar: no firmware a fila
        também roda sozinha, sem esperar o host mandar o próximo comando. Mas
        se um ``BC``/``KC`` chegou no meio do caminho, a fila é **abandonada**:
        o comando de parada está na fila de entrada e é o próximo a rodar.
        """
        while self._fila and not self._fim.is_set() and not self._interromper.is_set():
            self._rodar_comando(self._fila.pop(0))

    def _dormir_ate(self, segundos: float) -> bool:
        """Dorme, mas acorda se vier ``BC``/``KC`` ou se a placa for fechada.

        Devolve ``True`` se dormiu o tempo todo, ``False`` se foi interrompido.
        Sem isso o ``KC`` ficaria preso atrás de um ``move`` de 20 segundos. O
        sono é fatiado para o ``wait(True)`` do driver acordar no meio do
        caminho, e o tempo simulado continua somando os mesmos ``segundos``.
        """
        if segundos <= 0:
            return True
        # **Não** limpa o sinal aqui: quem limpa é a thread, uma vez por comando
        # (``_loop``), antes de começar a executá-lo. Limpar dentro do sono
        # apagaria um ``BC`` que chegou pelo ``write`` no instante anterior.
        restante = segundos
        while restante > 0 and not self._fim.is_set():
            self.relogio.dormir(min(restante, 0.05))
            restante -= min(restante, 0.05)
            if self._interromper.is_set():
                return False
        return not self._interromper.is_set()

    def _dormir_livre(self, segundos: float) -> bool:
        """Como :meth:`_dormir_ate`, mas **sem segurar** o ``_lock``.

        O firmware não tem este problema: quem atende a UART é o mesmo
        processador do movimento, então nunca há disputa. Aqui o lock protege
        só o estado, e segurá-lo durante o sono deixaria o painel cego (não
        conseguiria ler a pose no meio do trajeto) e o host esperando. Como é
        um ``RLock`` reentrante, cada passo é reescrito com o lock tomado e o
        sono acontece fora dele.
        """
        self._lock.release()
        try:
            return self._dormir_ate(segundos)
        finally:
            self._lock.acquire()

    def _rodar_comando(self, cmd: Comando) -> None:
        cab = cmd.cab
        # Cada handler devolve ``True`` se o comando merece ack. Quem já
        # respondeu sozinho (``MT0 MS``, ``SS``…) devolve ``False`` — senão
        # sairiam duas linhas e o próximo ``wait_for`` pegaria a errada.
        ack = {
            "MT0": self._mt0_fixo,
            "MT3": self._mt3,
            "WP": self._wp,
            "PG": self._pg,
            "DO": self._do,
            "AO": self._ao,
            "LT": self._lt,
            "BZ": self._bz,
            "SC": self._sc,
            "EL": self._el,
            "DL": self._dl,
        }[cab](cmd)

        # O ack sai **depois** de executado (guia §5.16). Ligado o ``CR``,
        # vale para todos os comandos seguintes daquele tipo.
        if ack and self._cr_para(cab):
            self._responder(f"CR OK {self._ident_ack(cab, cmd)}")

    def _cr_para(self, cab: str) -> bool:
        return self._cr.get(cab, self._cr.get("geral", False))

    def _lado(self, cmd: Comando) -> float:
        """``R``/``L`` do comando viram ``+1``/``-1`` (direita = ângulo +).

        ``DF`` sozinho **não** tem lado: quem diz o lado é o ``R`` ou o ``L`` que
        viaja junto (``MT0 D300 DF L RI100``), e sem os dois o firmware assume
        a direita. O ``D`` negativo é ré, não curva invertida — quem cuida do
        sinal da distância é a cinemática.
        """
        if "L" in cmd.params:
            return -1.0
        return 1.0

    def _ident_ack(self, cab: str, cmd: Comando) -> str:
        if cab == "DO":
            return f"DO{self._indice(cmd)}"
        if cab == "AO":
            return f"AO{self._indice(cmd)}"
        return cab

    # --- MT0 em modo fixo -----------------------------------------------------
    def _mt0_fixo(self, cmd: Comando) -> bool:
        """Executa um ``MT0`` de fila. Devolve ``True`` se ele merece ack.

        Só o **movimento** (``D…``) confirma. ``E1``/``E0`` é parametrização e
        ``MS`` já respondeu sozinho — nenhum dos dois gera ``CR OK MT0``, e é
        justo por isso que o ``CR1`` fica com um ack pendente na serial.
        """
        if cmd.param("E") is not None:  # MT0 E1 / MT0 E0
            self._motores = cmd.param("E") == "1"
            return False
        if "MS" in cmd.params:  # MT0 MS — já respondeu
            self._responder(self._linha_status())
            return False
        if cmd.param("D") is None:  # MT0 BC/KC/sozinho
            return False
        if not self._motores:  # desligado: o firmware ignora
            return False

        distancia = cmd.numero("D", 0.0) or 0.0
        at_ms = int(cmd.numero("AT", self._at_cont))
        dt_ms = int(cmd.numero("DT", self._dt_cont))
        v_cm_s = int(cmd.numero("V", self._vel_cont))

        # Ganhos PG corrigem o que o robô realmente faz (guia §3.2).
        lado = self._lado(cmd)
        if cmd.flag("DF"):
            # Com ``DF`` o ``D`` são **graus**, não mm (guia §3.5): o raio vem
            # do ``RI`` e o comprimento do arco sai do ângulo.
            graus = self.ganhos.curva(abs(distancia), self.ganhos.df)
            ri = self.ganhos.raio_interno(cmd.numero("RI", 50.0))
            destino = cin.girar_diferencial(self.pose, lado * graus, ri, self._ganhos_dw)
            tipo = "turn_right" if lado > 0 else "turn_left"
            # O tempo é o do **arco**, não o da corda: 300° de curva levam bem
            # mais tempo que a linha reta entre os mesmos dois pontos.
            comprimento = (
                abs(ri + self._ganhos_dw / 2.0) * abs(lado * graus) * cin.RAD_POR_GRAU
            )
        elif "R" in cmd.params or "L" in cmd.params:
            # Curva no próprio eixo: o corpo gira em volta de si, caminho
            # nenhum — o tempo sai do quanto girar.
            graus = self.ganhos.curva(abs(distancia), self.ganhos.ca)
            destino = cin.girar_no_eixo(self.pose, lado * graus)
            tipo = "turn_right" if lado > 0 else "turn_left"
            comprimento = abs(self._ganhos_dw / 2.0) * abs(lado * graus) * cin.RAD_POR_GRAU
        else:
            destino = cin.ir_para_frente(self.pose, self.ganhos.reta(distancia))
            tipo = "straight"
            comprimento = abs(self.pose.distancia_ate(destino))

        # Colisão: bate e para na última pose livre.
        if self.colidir:
            travado = cin.primeiro_travamento(
                self.pose, destino, self.arena, self.objetos_no_chao()
            )
            self._colidiu = travado is not None
            if travado is not None:
                destino = travado
                comprimento *= self.pose.distancia_ate(destino) / max(comprimento, 1e-9)

        # O ``MT0 MS`` devolve o que foi **comandado**, não o que foi andado.
        self._ultimo_mov = (tipo, distancia)
        # O tempo simulado é o do caminho realmente percorrido — se bateu numa
        # parede, andou só até a parede, não os 500 mm pedidos.
        duracao = cin.duracao_ms(comprimento, v_cm_s, at_ms, dt_ms) / 1000.0
        self._andar_para(destino, duracao)
        return True

    def _andar_para(self, destino: Pose, duracao: float) -> bool:
        """Move o robô até ``destino`` em fatias, soltando o lock no meio.

        Um movimento fixo é longo (2 s a 25 cm/s) e o painel precisa ver o robô
        andando, não um salto do ponto A ao ponto B: por isso o trajeto é
        percorrido em passos de ``_tique``, cada um reescrevendo a pose com o
        lock tomado. É o que permite o painel mostrar o movimento em tempo
        real e o ``write`` do host não ficar bloqueado.

        Devolve ``True`` se foi até o fim, ``False`` se um ``BC``/``KC``
        cortou o caminho.
        """
        if duracao <= 0:
            self.pose = destino
            return True
        passos = max(1, ceil(duracao / self._tique))
        fatia = duracao / passos
        inicio = self.pose
        for passo in range(1, passos + 1):
            frac = passo / passos
            self.pose = Pose(
                inicio.x + (destino.x - inicio.x) * frac,
                inicio.y + (destino.y - inicio.y) * frac,
                inicio.angulo + (destino.angulo - inicio.angulo) * frac,
            )
            if not self._dormir_livre(fatia):
                self._travado = True
                break
        self.pose = destino if not self._travado else self.pose
        self._trilha.append((self.pose.x, self.pose.y))
        if len(self._trilha) > 2000:
            del self._trilha[:500]
        return not self._travado

    def _linha_status(self) -> str:
        tipo, valor = self._ultimo_mov
        if tipo == "straight":
            return f"MT0 MS D{valor:+08.2f}".replace(".", ",")
        letra = "R" if tipo == "turn_right" else "L"
        return f"MT0 MS D+{letra}{valor:07.2f}".replace(".", ",")

    # --- MT3 ------------------------------------------------------------------
    def _mt3(self, cmd: Comando) -> bool:
        if cmd.param("E") is not None:
            return True  # MT3 E1/E0
        pulsos = cmd.numero("TP")
        if pulsos is None or not self._motores:
            return False
        pps = cmd.numero("PS", 100.0) or 100.0
        self._dormir_ate(abs(pulsos) / max(pps, 1.0))
        return True

    # --- WP / PG --------------------------------------------------------------
    def _wp(self, cmd: Comando) -> bool:
        dw = cmd.numero("DW")
        if dw is not None:
            self._ganhos_dw = dw
            self.dist_rodas_mm = dw
        return True

    def _pg(self, cmd: Comando) -> bool:
        self.ganhos = replace(
            self.ganhos,
            so=cmd.numero("SO", self.ganhos.so) or 0.0,
            ca=cmd.numero("CA", self.ganhos.ca) or 0.0,
            df=cmd.numero("DF", self.ganhos.df) or 0.0,
            ri=cmd.numero("RI", self.ganhos.ri) or 0.0,
        )
        return True

    # --- periféricos ----------------------------------------------------------
    def _indice(self, cmd: Comando) -> int:
        """O número que vem grudado no cabeçalho (``DO5`` → 5, ``DL500`` → 500)."""
        if cmd.params and cmd.params[0].lstrip("-").isdigit():
            return int(cmd.params[0])
        return int(re.sub(r"\D", "", cmd.bruto) or 0)

    def _do(self, cmd: Comando) -> bool:
        numero = self._indice(cmd)
        if not 1 <= numero <= 8:
            self._erro("01", "DO")
            return False
        ligar = cmd.param("E") == "1"
        self._reles[numero] = ligar
        # Relés 5-8 são os eletroímãs do Magnetic-Lift (guia §6.5).
        if numero >= 5:
            self._imanes = ligar
            if not ligar:
                self._soltar()
        timer = cmd.numero("TM")
        if timer is not None and ligar:
            self._reles_timer[numero] = self.relogio.tempo() + timer / 1000.0
        self._tentar_pegar()
        return True

    def _ao(self, cmd: Comando) -> bool:
        return True  # PWM: só interessa se a rotina mexer; estado ignorado

    def _lt(self, cmd: Comando) -> bool:
        if cmd.param("E") == "0":
            self._led = None
            return True
        r = int(cmd.numero("RD", 0.0) or 0.0)
        g = int(cmd.numero("GR", 0.0) or 0.0)
        b = int(cmd.numero("BL", 0.0) or 0.0)
        self._led = (r, g, b)
        return True

    def _bz(self, cmd: Comando) -> bool:
        self._buzzer = cmd.param("E") == "1"
        return True

    def _sc(self, cmd: Comando) -> bool:
        return True  # UART: sem dispositivo externo ligado no simulador

    def _el(self, cmd: Comando) -> bool:
        acao = cmd.bruto.split()[-1] if len(cmd.bruto.split()) > 1 else "ST"
        if acao == "UP":
            self._elevador_destino = 1.0
            self._dormir_ate(self.arena.robo.duracao_elevador)
            self._elevador = 1.0
        elif acao == "DN":
            self._elevador_destino = 0.0
            self._dormir_ate(self.arena.robo.duracao_elevador)
            self._elevador = 0.0
            self._tentar_pegar()
        else:
            self._elevador_destino = self._elevador
        return True

    def _dl(self, cmd: Comando) -> bool:
        self._dormir_ate(self._indice(cmd) / 1000.0)
        return True

    # --- objetos / elevador ---------------------------------------------------
    def _tentar_pegar(self) -> None:
        """Pega o objeto no chão quando o elevador está baixo e os ímãs ligados."""
        if not self._imanes or self._elevador > 0.5 or self._segure is not None:
            return
        raio = self.arena.robo.raio_pegagem
        melhor, melhor_dist = None, raio
        for objeto in self._objetos.values():
            if not objeto.pegavel:
                continue
            # Mede até a **borda** do objeto, não até o centro: um pallet de
            # 300 × 220 tem o centro a 110 mm da quina, e medir pelo centro
            # faria o robô precisar de um vão impossível (o corpo dele já bate
            # na quina antes). O guia §6.5 pega encostado, não alinhado.
            dist = objeto.distancia_de(self.pose)
            if dist < melhor_dist:
                melhor, melhor_dist = objeto, dist
        if melhor is not None:
            self._segure = melhor

    def _soltar(self) -> None:
        if self._segure is not None and not self._imanes:
            self._segure = None

    # =========================================================================
    # respostas dos sensores
    # =========================================================================
    def _linha_sonares(self, cmd: Comando) -> str:
        leituras = self.leitura_sonares()
        numero = self._indice(cmd)
        if numero == 0:
            partes = [self._sonar_texto(i, leituras.get(i)) for i in range(1, 9)]
            return " ".join(partes)
        return self._sonar_texto(numero, leituras.get(numero))

    @staticmethod
    def _sonar_texto(numero: int, valor: float | None) -> str:
        if valor is None:
            return f"SS{numero} FFFF"
        return f"SS{numero} {int(min(valor, ALCANCE_SONAR_MM)):04d}"

    def _linha_linha(self) -> str:
        leitura = self.leitura_linha()
        return " ".join(f"SL{i} {leitura.get(i, 0)}" for i in (1, 2, 3))

    def _linha_infravermelho(self) -> str:
        valor = self.leitura_infra()
        if valor >= 150.0:
            return "SI FFF"
        return f"SI {int(valor):03d}"

    def _linha_digital(self, cmd: Comando) -> str:
        leitura = self.leitura_digital()
        numero = self._indice(cmd)
        if numero == 0:
            return " ".join(f"DI{i} {leitura.get(i, 0)}" for i in range(1, 9))
        return f"DI{numero} {leitura.get(numero, 0)}"

    def _linha_analogica(self, cmd: Comando) -> str:
        numero = self._indice(cmd)
        # Sem sensor analógico na arena: devolve o fundo de escala em mV/µA.
        if numero <= 2:
            return f"AI{numero} 0000"
        if numero <= 4:
            return f"AI{numero} 00000"
        return f"AI{numero} 004000"

    def _linha_acelerometro(self) -> str:
        return "AX0,00 AY0,00 AZ0,91 T25,0 GX0,0 GY0,0 GZ0,0"
