"""Leitura do sentinel.toml, defaults e sobrescritas por ambiente."""
from __future__ import annotations

import pytest

from sentinel import Config, find_config_file, load_config

ARQUIVO = """\
[serial]
port = "/dev/ttyUSB0"
baud = 115200

[rodas]
wd_esquerda = 99.6
wd_direita = 100.32
dw = 260.35

[ganho]
so = 1.35
ri = -5.0
"""


@pytest.fixture
def projeto(tmp_path, monkeypatch):
    (tmp_path / "sentinel.toml").write_text(ARQUIVO)
    monkeypatch.delenv("SENTINEL_CONFIG", raising=False)
    monkeypatch.delenv("SENTINEL_PORT", raising=False)
    monkeypatch.delenv("SENTINEL_BAUD", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_le_o_arquivo(projeto):
    cfg = load_config()
    assert cfg.port == "/dev/ttyUSB0"
    assert cfg.baud == 115200
    assert cfg.wheel_params() == (99.6, 100.32, 260.35)


def test_ganhos_apenas_os_declarados(projeto):
    cfg = load_config()
    assert cfg.pg_gains() == {"so": 1.35, "ri": -5.0}


def test_encontra_o_arquivo_subindo_de_pasta(projeto):
    fundo = projeto / "rotinas" / "desvio"
    fundo.mkdir(parents=True)
    assert find_config_file(fundo) == projeto / "sentinel.toml"


def test_defaults_quando_nao_ha_arquivo(tmp_path, monkeypatch):
    monkeypatch.delenv("SENTINEL_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    assert load_config() == Config()


def test_ambiente_sobrescreve_arquivo(projeto, monkeypatch):
    monkeypatch.setenv("SENTINEL_PORT", "/dev/ttyACM1")
    assert load_config().port == "/dev/ttyACM1"


def test_env_aponta_para_arquivo_inexistente(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_CONFIG", str(tmp_path / "nao-existe.toml"))
    with pytest.raises(FileNotFoundError):
        load_config()
