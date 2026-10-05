"""Painel web da simulação: o robô, a pista e a serial, no navegador.

Um ``http.server`` da biblioteca padrão serve uma página com canvas e um
fluxo SSE com o estado — sem Flask, sem FastAPI, sem ``websockets``. Quem roda
isto é uma Raspberry com um display e rede fraca, e o pacote inteiro precisa
caber no ``pip install -e ".[dev,visao,controle]"``.

    from sentinel.simulacao import PlacaVirtual, painel

    p = PlacaVirtual(Arena(largura=3000, altura=2000))
    with painel(p, porta=8765) as painel_:
        painel_.abrir()          # abre no navegador
        ...                       # a rotina do robô roda aqui

O painel é **só leitura** quanto ao robô: ele observa, não manda comando. A
pista, essa sim é editável — dá para arrastar a montagem, os objetos e as
quinas de parede e gravar o resultado em ``pista.toml``, que é o arquivo que
a simulação lê no próximo ``--simular``.
"""

from __future__ import annotations

import json
import math
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any

from . import cinematica as cinem_mod
from . import pista as pista_mod
from .placa import PlacaVirtual

#: Cores que o editor oferece, na ordem em que a ferramenta "paleta" cicla.
PALETA = ("vermelho", "azul", "verde", "amarelo", "laranja", "preto", "branco")

GRAUS = 57.29577951308232


def _html() -> bytes:
    """Lê o ``painel.html`` que viaja dentro do pacote."""
    return files("sentinel.simulacao").joinpath("painel.html").read_bytes()


def _arena_para_json(arena: pista_mod.Arena) -> dict[str, Any]:
    """A pista no formato que o JavaScript consome (listas, não dataclasses)."""
    return {
        "largura": arena.largura,
        "altura": arena.altura,
        "cor_piso": arena.cor_piso,
        "paredes": [{"x1": p.x1, "y1": p.y1, "x2": p.x2, "y2": p.y2} for p in arena.paredes],
        "objetos": [
            {
                "id": o.id,
                "x": o.x,
                "y": o.y,
                "largura": o.largura,
                "altura": o.altura,
                "cor": o.cor,
                "pegavel": o.pegavel,
            }
            for o in arena.objetos
        ],
        "alvos": [
            {"x": a.x, "y": a.y, "cor": a.cor, "nome": a.nome} for a in arena.alvos
        ],
        "faixas": [
            {"x1": f.x1, "y1": f.y1, "x2": f.x2, "y2": f.y2, "largura": f.largura}
            for f in arena.faixas
        ],
        "montagem": {
            "x": arena.montagem.x,
            "y": arena.montagem.y,
            "angulo_graus": arena.montagem.angulo,
        },
        "robo": {
            "raio_corpo": arena.robo.raio_corpo,
            "raio_pegagem": arena.robo.raio_pegagem,
        },
        "sonares": [],
    }


def _estado_para_json(placa: PlacaVirtual, arquivo: str | None) -> dict[str, Any]:
    """Um retrato da placa em JSON puro — o que o SSE repete a cada tique."""
    estado = placa.estado()
    arena = placa.arena
    leitura = placa.leitura_sonares()
    pista_json = _arena_para_json(arena)
    pista_json["sonares"] = [
        {
            "id": s.id,
            # Coordenadas do mundo (x frente, y direita -> mundo)
            "xw": estado.pose.x
            + s.x * math.cos(estado.pose.angulo)
            - s.y * math.sin(estado.pose.angulo),
            "yw": estado.pose.y
            + s.x * math.sin(estado.pose.angulo)
            + s.y * math.cos(estado.pose.angulo),
            "angw_graus": (estado.pose.angulo + s.angulo * cinem_mod.RAD_POR_GRAU)
            * cinem_mod.GRAUS_POR_RAD,
            # Mantidos para compatibilidade
            "x": s.x,
            "y": s.y,
            "ang_graus": s.angulo,
            "alcance": leitura.get(s.id),
            "alcance_mm": pista_mod.ALCANCE_SONAR_MM,
            "cone_graus": pista_mod.CONE_SONAR_GRAUS,
        }
        for s in arena.robo.sonares
    ]
    return {
        "arquivo": arquivo,
        "paleta": list(PALETA),
        "pista": pista_json,
        "robo": {
            "x": estado.pose.x,
            "y": estado.pose.y,
            "ang_graus": estado.pose.angulo * GRAUS,
            "vel_mm_s": estado.vel_mm_s,
            "motores": estado.motores_ligados,
            "modo_pulso": estado.modo_pulso,
            "modo_continuo": estado.modo_continuo,
            "direcao_continua": estado.direcao_continua,
            "led": list(estado.led) if estado.led else None,
            "buzzer": estado.buzzer,
            "elevador": estado.elevador,
            "imenes": estado.imanes,
            "reles": {str(k): v for k, v in estado.relés.items()},
            "segure": estado.segure.id if estado.segure else None,
            "colidiu": estado.colidiu,
            "fila": estado.fila,
            "trilha": [list(p) for p in estado.trilha[-400:]],
        },
        "sensores": {
            "sonares": {str(k): v for k, v in leitura.items()},
            "linha": [placa.leitura_linha().get(i, 0) for i in (1, 2, 3)],
            "infra_cm": placa.leitura_infra(),
            "digital": [placa.leitura_digital()[i] for i in range(1, 9)],
        },
        "log": list(placa.log[-60:]),
        "tempo": estado.tempo,
        "velocidade": getattr(placa.relogio, "velocidade", 1.0),
    }


def _arena_de_json(bruto: dict[str, Any]) -> pista_mod.Arena:
    """Reconstrói a arena que veio do navegador (mesmos nomes do TOML)."""
    paredes = tuple(
        pista_mod.Parede(w["x1"], w["y1"], w["x2"], w["y2"]) for w in bruto.get("paredes", [])
    )
    objetos = tuple(
        pista_mod.Objeto(
            x=o["x"],
            y=o["y"],
            largura=o.get("largura", 250.0),
            altura=o.get("altura", 180.0),
            cor=o.get("cor", "vermelho"),
            id=o.get("id") or f"obj{i}",
            pegavel=bool(o.get("pegavel", True)),
        )
        for i, o in enumerate(bruto.get("objetos", []))
    )
    faixas = tuple(
        pista_mod.Faixa(f["x1"], f["y1"], f["x2"], f["y2"], f.get("largura", 40.0))
        for f in bruto.get("faixas", [])
    )
    alvos = tuple(
        pista_mod.Alvo(
            x=a["x"], y=a["y"], cor=a.get("cor", "vermelho"), nome=a.get("nome", "")
        )
        for a in bruto.get("alvos", [])
    )
    robo_bruta = bruto.get("robo", {})
    montagem_bruta = bruto.get("montagem", {})
    return pista_mod.Arena(
        largura=bruto.get("largura", 3000.0),
        altura=bruto.get("altura", 2000.0),
        cor_piso=bruto.get("cor_piso", "branco"),
        paredes=paredes,
        objetos=objetos,
        alvos=alvos,
        faixas=faixas,
        robo=pista_mod.RoboArena(
            raio_corpo=float(robo_bruta.get("raio_corpo", 200.0)),
            raio_pegagem=float(robo_bruta.get("raio_pegagem", 250.0)),
        ),
        montagem=pista_mod.Montagem(
            x=montagem_bruta.get("x", 500.0),
            y=montagem_bruta.get("y", 500.0),
            angulo=montagem_bruta.get("angulo_graus", 0.0),
        ),
    )


class _Handler(BaseHTTPRequestHandler):
    """Rota as três coisas que o painel precisa: a página, o estado e o TOML."""

    protocol_version = "HTTP/1.1"
    painel: Painel  # injetado pelo construtor do servidor

    # --- utilidades ----------------------------------------------------------
    def log_message(self, *_args) -> None:  # noqa: A003 — nome do base
        """Silencia o log de acesso: a 20 Hz ele inundaria o terminal."""
        return

    def _responder(self, corpo: bytes, tipo: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, dados: dict[str, Any], status: int = 200) -> None:
        self._responder(json.dumps(dados).encode("utf-8"), "application/json", status)

    # --- GET -----------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 — nome do base
        if self.path in ("/", "/index.html"):
            self._responder(_html(), "text/html; charset=utf-8")
            return
        if self.path == "/estado":
            self._json(self.painel.retrato())
            return
        if self.path == "/eventos":
            self._sse()
            return
        self._json({"erro": "rota desconhecida"}, 404)

    def _sse(self) -> None:
        """Fluxo de estado: o navegador desenha o que arrives, sem perguntar."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while not self.painel.fechando:
                carga = json.dumps(self.painel.retrato())
                self.wfile.write(f"data: {carga}\n\n".encode())
                self.wfile.flush()
                self.painel._tique.wait(self.painel.intervalo)
                self.painel._tique.clear()
        except (BrokenPipeError, ConnectionResetError, ValueError):
            # Fechou a aba ou o servidor: é o jeito normal de este fluxo acabar.
            return

    # --- POST ----------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802 — nome do base
        if self.path == "/pista":
            self._salvar()
            return
        if self.path == "/recarregar":
            self._recarregar()
            return
        if self.path == "/exemplo":
            self.painel.trocar_pista(pista_mod.exemplo(), None)
            self._json({"ok": True, "pista": _arena_para_json(self.painel.arena)})
            return
        self._json({"erro": "rota desconhecida"}, 404)

    def _salvar(self) -> None:
        try:
            bruto = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            # "arena" é o nome do POST; "pista" é o do GET. Aceita os dois, mas
            # não pode aceitar nenhum: sem isso, um erro de digitação no
            # cliente viraria uma pista sem parede nenhuma, sem reclamar.
            dados = bruto.get("arena", bruto.get("pista"))
            if not isinstance(dados, dict):
                self._json({"ok": False, "erro": "corpo sem a chave 'arena'"}, 400)
                return
            arena = _arena_de_json(dados)
            caminho = self.painel.salvar_pista(arena, bruto.get("caminho"))
        except (ValueError, TypeError, KeyError) as erro:
            self._json({"ok": False, "erro": f"pista inválida: {erro}"}, 400)
            return
        self._json({"ok": True, "caminho": str(caminho)})

    def _recarregar(self) -> None:
        # ``pista_mod.carregar`` é gentil e devolve a arena de exemplo quando
        # não acha o arquivo — no CLI isso é ótimo, no botão "recarregar"
        # seria mentira: o usuário acharia que está vendo o próprio arquivo.
        if self.painel.arquivo is None or not self.painel.arquivo.is_file():
            self._json(
                {"ok": False, "erro": f"não achei {self.painel.arquivo or 'nenhum pista.toml'}"},
                400,
            )
            return
        try:
            arena = pista_mod.carregar(self.painel.arquivo)
        except (OSError, ValueError) as erro:
            self._json({"ok": False, "erro": f"não li o arquivo: {erro}"}, 400)
            return
        self.painel.trocar_pista(arena, self.painel.arquivo)
        self._json(
            {
                "ok": True,
                "caminho": str(self.painel.arquivo or "pista de exemplo"),
                "pista": _arena_para_json(arena),
            }
        )


class Painel:
    """Servidor do painel em uma thread, observing a placa.

    Não é o dono da simulação: a placa continua rodando (e a thread dela,
    junto) se este objeto for destruído. O que o painel faz é **ler** o estado
    e **escrever** a pista.
    """

    def __init__(
        self,
        placa: PlacaVirtual,
        *,
        porta: int = 8765,
        host: str = "127.0.0.1",
        intervalo: float = 0.05,
        arquivo: Path | str | None = None,
    ):
        self.placa = placa
        self.intervalo = intervalo
        self.arquivo = Path(arquivo) if arquivo else pista_mod.achar_arquivo()
        self._tique = threading.Event()
        self.fechando = False
        self._lock = threading.Lock()
        handler = type("_PainelHandler", (_Handler,), {"painel": self})
        self._servidor = ThreadingHTTPServer((host, porta), handler)
        self._servidor.daemon_threads = True
        self._thread = threading.Thread(
            target=self._servidor.serve_forever, name="painel-web", daemon=True
        )
        self._thread.start()

    # --- o estado ------------------------------------------------------------
    @property
    def url(self) -> str:
        host, porta = self._servidor.server_address[:2]
        return f"http://{host}:{porta}/"

    @property
    def arena(self) -> pista_mod.Arena:
        """A pista **em uso** agora — a que a simulação está vendo."""
        return self.placa.arena

    def retrato(self) -> dict[str, Any]:
        """Um retrato em JSON, seguro para chamar de qualquer thread."""
        with self._lock:
            return _estado_para_json(self.placa, str(self.arquivo) if self.arquivo else None)

    # --- editar a pista ------------------------------------------------------
    def salvar_pista(self, arena: pista_mod.Arena, caminho: str | Path | None = None) -> Path:
        """Grava a arena editada e passa a simulação a usá-la.

        O destino é sempre explícito: sem ``caminho`` e sem um arquivo já
        conhecido, levanta ``ValueError``. Chutar ``pista.toml`` no diretório
        de trabalho sobrescreveria uma pista versionada — e o editor do
        navegador não tem como pedir confirmação de caminho.
        """
        destino = Path(caminho) if caminho else self.arquivo
        if destino is None:
            raise ValueError("diga onde salvar: nenhum caminho e nenhuma pista carregada")
        pista_mod.salvar(arena, destino)
        self.arquivo = destino
        self.trocar_pista(arena, destino)
        return destino

    def trocar_pista(self, arena: pista_mod.Arena, arquivo: Path | str | None = None) -> None:
        """Troca a arena no meio da simulação, sem derrubar o robô.

        Só o que é pista muda: a pose atual é preservada (não é um reset), e a
        montagem nova vira a origem do robô se o painel mandou outra.
        """
        with self._lock:
            self.placa.arena = arena
            self.placa._objetos.clear()  # recarrega os objetos da arena nova
            self.placa._objetos.update({o.id: o for o in arena.objetos if o.id})
            self.placa._segure = None
            if arquivo is not None:
                self.arquivo = Path(arquivo) if str(arquivo) else None
        self._tique.set()

    # --- vida ----------------------------------------------------------------
    def abrir(self) -> None:
        """Abre a página no navegador do usuário."""
        webbrowser.open(self.url)

    def esperar_rodando(self, timeout: float | None = None) -> None:
        """Segura o servidor no ar.

        Com ``timeout=None`` (o uso típico do ``main.py``) bloqueia até
        Ctrl-C; com um número, volta sozinho — é o que os testes usam, porque
        um teste que espera por tecla é um teste que trava a suite.
        """
        try:
            if timeout is None:
                while True:
                    time.sleep(0.5)
            else:
                time.sleep(timeout)
        except KeyboardInterrupt:
            pass

    def close(self) -> None:
        """Fecha o servidor e derruba as conexões SSE abertas."""
        self.fechando = True
        self._tique.set()
        self._servidor.shutdown()
        self._servidor.server_close()
        self._thread.join(timeout=2.0)

    def __enter__(self) -> Painel:
        return self

    def __exit__(self, *_excecao) -> None:
        self.close()


def abrir_painel(placa: PlacaVirtual, **kwargs) -> Painel:
    """Atalho: sobe o painel e já abre no navegador."""
    painel = Painel(placa, **kwargs)
    painel.abrir()
    return painel
