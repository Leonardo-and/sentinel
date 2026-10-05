"""Fakes de serial para exercitar o driver sem robô."""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest

#: Arquivos versionados que nenhum teste tem o direito de reescrever. O painel
#: salva pista em disco, e um ``caminho`` esquecido escreve no diretório de
#: trabalho — foi assim que um teste apagou as paredes do ``pista.toml``.
ARQUIVOS_DO_REPO = ("pista.toml", "sentinel.toml", "pyproject.toml")


@pytest.fixture(autouse=True)
def nao_escreve_no_repo() -> Iterable[None]:
    """Repara e acusa se algum teste sujar um arquivo versionado.

    Guarda os bytes antes do teste em vez de ir ao git: ``pista.toml`` é
    novo e ainda não está no índice, e um guard que depende de histórico
    falha justo na primeira rodada.
    """
    raiz = Path(__file__).resolve().parent.parent
    antes = {nome: (raiz / nome).read_bytes() for nome in ARQUIVOS_DO_REPO
             if (raiz / nome).is_file()}
    yield
    sujos = []
    for nome, original in antes.items():
        caminho = raiz / nome
        if not caminho.is_file() or caminho.read_bytes() != original:
            caminho.write_bytes(original)
            sujos.append(nome)
    criados = [nome for nome in ARQUIVOS_DO_REPO
               if nome not in antes and (raiz / nome).is_file()]
    for nome in criados:
        (raiz / nome).unlink()
        sujos.append(nome)
    if sujos:
        pytest.fail(f"teste reescreveu {', '.join(sujos)} (restaurado)")


class FakeSerial:
    """Substitui um ``serial.Serial``.

    Cada chave de ``respostas`` casa com o início do comando enviado; o valor
    é devolvido como uma linha terminated por ``\\n``.
    """

    def __init__(self, respostas: dict[str, str] | None = None):
        self.comandos: list[str] = []
        self.respostas = dict(respostas or {})
        self.fechado = False
        self._buf = bytearray()

    @property
    def in_waiting(self) -> int:
        return len(self._buf)

    def write(self, data: bytes) -> int:
        comando = data.decode("ascii").strip()
        self.comandos.append(comando)
        for prefixo, resposta in self.respostas.items():
            if comando.startswith(prefixo):
                self._buf.extend(f"{resposta}\n".encode("ascii"))
                break
        return len(data)

    def read(self, n: int = 1) -> bytes:
        n = min(n, len(self._buf))
        dados, self._buf = bytes(self._buf[:n]), self._buf[n:]
        return dados

    def flush(self) -> None:
        self._buf.clear()

    def close(self) -> None:
        self.fechado = True


@pytest.fixture
def serial() -> FakeSerial:
    """Serial que responde ``CR OK MT0`` a qualquer comando iniciado por MT0."""
    return FakeSerial({"MT0": "CR OK MT0"})


@pytest.fixture
def make_serial():
    """Fábrica para casos que precisam de respostas próprias."""
    return FakeSerial


def comandos_de(serial: FakeSerial, prefixo: Iterable[str]) -> list[str]:
    """Comandos enviados cujo texto começa com um dos prefixos dados."""
    return [c for c in serial.comandos if c.startswith(tuple(prefixo))]
