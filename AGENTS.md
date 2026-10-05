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

E há um **simulador**: as mesmas rotinas, o mesmo `SoBot`, outra coisa do outro
lado da serial. `--simular` troca o robô por uma placa em memória e abre um
painel web no navegador. É o caminho para testar cinemática, garra e sensores sem
gasto de energia e sem risco no pátio — inclusive no seu notebook, sem Pi e sem
câmera.

## Mapa do repositório

| Caminho | Papel |
|---|---|
| `main.py` | **A rotina.** É o arquivo que você edita e roda. A rotina padrão é a tabela `PASSOS`: reta, meia-volta, reta, curva de 45°, ré e reta — só `move`/`turn`, sem sensor nem garra. Também tem a CLI de diagnóstico (`--debug-visao`, `--listar-botoes`). |
| `sentinel.toml` | Porta, baud, calibração (`WD`/`DW`/`PG`), faixas de cor e mapa de botões. Versionado — meça as rodas e corrija aqui. |
| `src/sentinel/sobot.py` | **O driver.** Talking completo com o robô, validado contra o Guia v0.4.11. Trate como código de terceiros: não edite sem necessidade. |
| `src/sentinel/config.py` | Leitura do `sentinel.toml` (`tomllib`): `Config` (robô), `FaixaHSV`/`VisaoConfig` (câmera), `ControleConfig` (F710). |
| `src/sentinel/visao.py` | Câmera USB + OpenCV:acha o bloco de uma cor e diz o desvio em px. Import de `cv2` é preguiçoso. |
| `src/sentinel/controle.py` | Logitech F710 via `inputs`: botões viram cores e parada. Não-bloqueante, só borda de subida. |
| `src/sentinel/__init__.py` | Reexporta `SoBot`, `SoBotError`, `SoBotTimeout`, `Config`, `load_config`, `load_visao`, `load_controle`. |
| `docs/SOBOT_AGENT_GUIDE.md` | **Guia operacional** derivado do *Guia de Referência dos Comandos v0.4.11* e da *Apostila SoBot v0.4.17*. Leia antes de inventar comando. |
| `pista.toml` | A pista simulada, versionada: tamanho, paredes, objetos, alvos, faixas de cor, montagem e a geometria do robô. Gerado pelo painel ("salvar") e editável à mão. |
| `rotas/percurso_fixo.py` | Receitas prontas e rodáveis: quadrado de 800 mm, desvio de sonar e ciclo de pallet. `python -m rotas.<nome> --help`. |
| `src/sentinel/simulacao/` | O simulador. `pista.py` (arena + TOML), `cinematica.py` (movimento, sensores, colisão), `placa.py` (a placa serial virtual), `camera.py` (quadro falso para `VisaoCor`), `painel.py`/`painel.html` (HTTP + canvas). |
| `docs/sobot_api.json` | Mesmo conteúdo do guia em formato estruturado. |
| `tests/` | Testes que rodam **sem robô**: `FakeSerial` (`tests/conftest.py`), placa virtual, câmera falsa e gamepad falso. O guard `nao_escreve_no_repo` impede que um teste reescreva `pista.toml`/`sentinel.toml`. |

## Comandos

```bash
.venv/bin/python main.py                          # roda a rotina no robô
.venv/bin/python main.py --simular                # a mesma rotina, simulada
.venv/bin/python main.py --simular --painel       # + o painel web no navegador
.venv/bin/python main.py --simular --velocidade 8 # 8x mais rápido que o real
.venv/bin/python main.py --simular --pista pista.toml --painel --abrir
.venv/bin/python main.py --debug-visao --cor azul # calibra a câmera (não move o robô)
.venv/bin/python main.py --listar-botoes          # descobre os botões do F710
.venv/bin/python -m pytest -q                     # testes (não precisa do robô)
.venv/bin/ruff check .                            # lint

.venv/bin/python -m rotas.percurso_fixo --painel  # as três receitas
.venv/bin/python -m rotas.desvio_sonar --velocidade 4
.venv/bin/python -m rotas.pallet --velocidade 8
```

Ambiente Python 3.11+ (testado em 3.14). O sistema é PEP 668 — **nunca** use
`pip install` direto; passe pelo venv. Recriar o ambiente do zero:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

A webcam e o F710 são **extras**, porque trazem dependência pesada e nem toda
rotina precisa deles: `visao = ["opencv-python-headless", "numpy"]` e
`controle = ["inputs"]`. Na Pi: `pip install -e ".[dev,visao,controle]"`, e o
`inputs` precisa de permissão de leitura em `/dev/input`
(`sudo usermod -aG input pi`, depois logout).

Nada de visão ou controle importa `cv2`/`inputs` no topo do módulo: o import é
preguiçoso, dentro da função, com mensagem de erro que aponta o
`pip install` certo. Assim o pacote importa (e os testes rodam) numa máquina sem
câmera.

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

## O simulador — o que ele ensina e o que ele esconde

`--simular` não é um robô de brincadeira: é a mesma placa, com fila, acks e
elevador de 7 s. As armadilhas abaixo apareceram todas na prática.

1. **A placa é assíncrona.** `write()` devolve assim que enfileira; quem executa
   é uma thread. `wheels_enable(True)`, `elevator("up")` e `digital_output()`
   não têm ack — só `MT0` tem. Para sincronizar, use o ack de um movimento
   (`wait=True`) ou espere a condição; **não durma um tempo chutado**.
2. **`RelogioFake` vs `RelogioReal`.** O elevador leva 7 s por curso. Num teste
   com relógio real isso são 7 s de teste; use `RelogioFake` ou
   `RelogioReal(25)`.
3. **`move(wait=True)` promete movimento, não posição.** A placa para por
   colisão *antes* do alvo, com a granularidade da sondagem (25 mm). Calcule a
   geometria a partir de onde o robô parou, não de onde você mandou — foi assim
   que a rota de pallet errou o alvo por 275 mm.
4. **`movimento(..., wait=False)` move de fato, em increments visíveis.** A
   alternativa é não mandar nada e o robô fica parado: `wait=False` não é
   "assíncrono", é "não espera o fim". Temporize no host.
5. **`BC`/`KC` limpam a fila inteira.** Ao usá-los, todos os movimentos
   pendentes atrás deles desaparecem — inclusive um `move` que já estava a
   caminho.
6. **`INF` do sonar não é distância.** `read_sonar` devolve `INF` (acima de
   4000 mm) e `None` (falhou); trate os dois como "não viu nada".
7. **O painel não comanda o robô.** Ele observa. Editar a montagem muda a pose
   inicial da *próxima* execução, não a de agora.
8. **O painel salva em disco e exige caminho explícito.** Sem `caminho` e sem
   uma pista já carregada, o POST dá 400 em vez de chutar `pista.toml` — um
   chute ali sobrescreveria a pista versionada.
9. **Um teste nunca escreve no repo.** `painel.html` faz parte do pacote:
   `pyproject.toml` tem `package-data` com ele. Não sirva o HTML de um caminho
   relativo ao repo — use `importlib.resources` ou o caminho do pacote.

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
- No simulador, a verdade é o comportamento do guia: quando um comando é de
  fila, simule de fila; quando é imediato, de imediato. A simulação que "funciona"
  divergindo do firmware é pior que nenhuma.