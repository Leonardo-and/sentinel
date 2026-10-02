# AGENTS.md — Sentinel

> Este arquivo é o ponto de partida para agentes que trabalham neste repositório.

## O que é este projeto

**Sentinel** é o apelido dado a um **SoBot** (SOLIS Tecnologia) — uma plataforma de
robótica móvel (AGV/AMR) de ensino com motores de passo NEMA-23 nas rodas, redução
3:1, 8 sonares, infravermelho, acelerômetro, sensores de linha, fita LED RGB,
buzzer e I/O industrial.

O host é uma Raspberry Pi 4B (no kit) conectada por **serial USB** à Placa de
Controle e Potência do robô, em `/dev/ttyACM0` a **57600 baud**.

Este repositório não é uma biblioteca nem um framework: é um conjunto de
**rotinas**. Você edita um arquivo Python e o robô executa o que está escrito nele.

## Mapa do repositório

| Caminho | Papel |
|---|---|
| `main.py` | **A rotina.** É o arquivo que você edita e roda. Começa com "anda para frente e para". |
| `sentinel.toml` | Porta, baud e calibração (`WD`/`DW`/`PG`). Versionado — meça as rodas e corrija aqui. |
| `src/sentinel/sobot.py` | **O driver.** Talking completo com o robô, validado contra o Guia v0.4.11. Trate como código de terceiros: não edite sem necessidade. |
| `src/sentinel/config.py` | Leitura do `sentinel.toml` (`tomllib`). |
| `src/sentinel/__init__.py` | Reexporta `SoBot`, `SoBotError`, `SoBotTimeout`, `Config`, `load_config`. |
| `docs/SOBOT_AGENT_GUIDE.md` | **Guia operacional** derivado do *Guia de Referência dos Comandos v0.4.11* e da *Apostila SoBot v0.4.17*. Leia antes de inventar comando. |
| `docs/sobot_api.json` | Mesmo conteúdo do guia em formato estruturado. |
| `tests/` | 44 testes que rodam **sem robô**, via `FakeSerial` (`tests/conftest.py`). |

## Comandos

```bash
.venv/bin/python main.py               # roda a rotina
.venv/bin/python -m pytest -q          # testes (não precisa do robô)
.venv/bin/ruff check .                 # lint
```

Ambiente Python 3.11+ (testado em 3.14). O sistema é PEP 668 — **nunca** use
`pip install` direto; passe pelo venv. Recriar o ambiente do zero:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

## Como escrever uma rotina nova

Chame o driver direto — não crie camada de abstração por cima. O
`docs/SOBOT_AGENT_GUIDE.md` §6 traz as receitas oficiais (quadrado, seguidor de
linha, desvio de sonar, pallet); a §3 lista todos os comandos com faixas.

```python
from sentinel import SoBot, load_config

CFG = load_config()

with SoBot(CFG.port, CFG.baud, verbose=True) as bot:
    bot.configure_wheels(*CFG.wheel_params())

    bot.command_return(True, "MT0")
    bot.wait_for("CR OK MT0")     # obrigatório — ver gotcha abaixo
    bot.wheels_enable(True)
    try:
        bot.move(500, accel_ms=1000, decel_ms=1000, speed_cm_s=10, wait=True)
    finally:
        bot.wheels_enable(False)
```

Regras de estrutura:

1. `wheels_enable(True)` → comandos de movimento → `wheels_enable(False)` (guia §0.5).
2. `try/finally` para o `MT0 E0`: uma exceção no meio não pode deixar as rodas ligadas.
3. `verbose=True` durante o desenvolvimento: imprime `>> comando` e `<< resposta`.

### Gotcha: o ack do `CR1`

`CR` responde **imediatamente**, enquanto o movimento é **enfileirado** (guia §2.3).
Por isso, logo após `command_return(True, "MT0")` existe um `CR OK MT0` pendente na
serial. Se você não drenar essa linha, o `wait_for` do próximo `move(wait=True)`
consome o ack errado e retorna **antes de o robô andar** — a sincronia fica
falsa. Sempre:

```python
bot.command_return(True, "MT0")
bot.wait_for("CR OK MT0")      # consome o ack imediato
```

### Fila × imediato (guia §2.3)

- **Fila** (até 100): `WP`, `PG`, `MT0` em modo fixo, `MT3`, `MT0 MS`, `DO`/`AO`,
  `LT`, `BZ`, `SC`, `EL`, `DL`.
- **Imediato**: modo contínuo (`ME`/`MC`/`MF`...), `LM`, `BC`, `KC`, modo pulsos
  (`MT1`/`MT2`), todos os sensores, `CR`.

Consequências: `DL` atrasa **no robô**; `time.sleep` atrasa **o envio**. Comandos
enfileirados não podem ser cancelados — `BC`/`KC` limpam a fila. Em modo contínuo
não há fila: temporize no host.

## Protocolo (guia §2.2) — já aplicado pelo driver

- ASCII **maiúsculo**; tokens separados por **espaço**; **sem terminador** de linha.
- Decimais com **vírgula** (`WD99,6`, `PG SO1,35`); negativo colado (`D-1000`).
- Respostas terminam em `\n`.
- O driver **valida as faixas documentadas** e levanta `ValueError` em vez de
  deixar o robô clampar em silêncio. Não contorne os limites nos argumentos.
- `ERROR=xx` vira `SoBotError` (com `.code` e `.detail`); silêncio na serial vira
  `SoBotTimeout`.

Limites que mais importam: `V` 0–25 cm/s · `AT`/`DT` 0–60000 ms · distância
≤ 65000 mm · ângulo ≤ 360° (o Guia divergem entre 380 e 400 — guia §14.1) ·
raio interno 10–9999 mm · `PS` ≤ 2000 pulsos/s · `DL` 50–60000 ms · LED 0–255 ·
UART ≤ 15 bytes.

## Segurança — leia antes de mandar o robô andar

1. **`KC` é só em emergência extrema.** Pode causar dano mecânico e elétrico
   irreversível. Prefira `MT0 BC`. O botão físico de emergência atrás do robô corta
   100% da energia (guia §12).
2. **Nunca misture `MT0` com `MT1`/`MT2`** → `ERROR=02`. Desabilite um modo antes
   do outro.
3. **Nunca mande pulsos a uma roda só** com a outra travada ("curva proibida"):
   danifica a roda parada e quebra a precisão (guia §12).
4. **Rampas generosas.** `BC` com `DT` curto tem o mesmo risco do `KC`. O manual
   não define um `DT` mínimo seguro; os exemplos oficiais usam 100 ms a 5000 ms.
5. **Piso limpo, seco e sem fissuras.** Alimentação 24 V, mínimo 5 A.
6. **Interface auxiliar de pulsos: máximo 3,3 V.**
7. Calibre `WD`/`DW`/`PG` antes de qualquer trabalho que dependa de distância ou
   ângulo exato (guia §5). Ganho varia com piso, peso e inclinação.

## Lacunas conhecidas dos manuais (guia §14)

Não presuma comportamento nestas áreas — o driver não pode te salvar:

- Estado de `CR` ao ligar: **não documentado**. Sempre defina explicitamente.
- `AT`/`DT` omitidos no modo fixo: **não documentado**.
- Curva em ré (`D` negativo com `R`/`L`): **não documentado**.
- `DI`/`DO` ↔ relés `RLn`: mapeamento exato não documentado.
- Sensores de linha: a PCI tem 5, mas `SL` devolve 3 (os outros dois vão a `D1`/`D2`).
- Qual entrada lê o botão Start: **não documentado**.
- O driver expõe `DO5`–`DO8` para eletroímãos por inferência do repositório
  oficial; confirme no robô antes de confiar.

## Convenções de código

- Comentários e docstrings em **português**; nomes de símbolo seguindo o padrão do
  driver existente (inglês).
- `src/sentinel/sobot.py` é **importado verbatim** do projeto original e está
  fora do `ruff` de modernização de tipos (`per-file-ignores` no `pyproject.toml`).
  Não faça refactor nele por estilo; só corrija bug real, com teste.
- Antes de escrever um método novo no driver, **confirme o comando no
  `docs/SOBOT_AGENT_GUIDE.md` §3**. Não invente sintaxe.
- Qualquer mudança no driver vem acompanhada de teste em `tests/test_sobot.py`
  usando `FakeSerial`.