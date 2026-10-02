# Sentinel

Rotinas de controle do **Sentinel**, um [SoBot](https://solis.com.br) da SOLIS
Tecnologia — plataforma de robótica móvel de ensino com motores de passo NEMA-23,
8 sonares, sensores de linha, infravermelho, acelerômetro, fita LED RGB e I/O
industrial.

O robô conversa por **serial USB** com o computador de bordo (Raspberry Pi 4B
no kit) em `/dev/ttyACM0`, 57600 baud.

## Instalação

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

O sistema é PEP 668: **nunca** rode `pip install` fora do venv.

Os periféricos opcionais (webcam, Logitech F710) vêm em extras separados, para
não obrigar a instalar OpenCV num projeto que só anda reto:

```bash
.venv/bin/pip install -e ".[dev,visao,controle]"
```

## Rodando

Edite `main.py` e execute. A rotina inicial anda 500 mm para frente e para:

```bash
.venv/bin/python main.py
```

Com `VERBOSO = True` você vê o protocolo inteiro na tela:

```
>> WP MT1 WD100
>> WP MT2 WD100
>> WP DW262
>> MT0 CR1
<< CR OK MT0
>> MT0 E1
>> MT0 D500 AT1000 DT1000 V10
<< CR OK MT0
>> MT0 E0
```

## Calibração

`sentinel.toml` guarda porta, baud e os parâmetros das rodas (`WD`/`DW`).
Os valores do arquivo são os **nominais de fábrica** — meça o diâmetro real das
rodas e a distância entre os pontos de contato antes de confiar em distâncias.
O ganho proporcional (`PG`) só entra depois de calibrado: veja
`docs/SOBOT_AGENT_GUIDE.md` §5.

## Visão e controle (periféricos)

O robô tem uma webcam USB e um Logitech F710 acoplados. Os dois ficam em
módulos à parte, cada um com sua dependência:

| Módulo | Dependência | Configuração |
|---|---|---|
| `src/sentinel/visao.py` | `opencv-python-headless`, `numpy` | `[visao]`, `[visao.cores.*]` |
| `src/sentinel/controle.py` | `inputs` | `[controle]`, `[controle.botoes]` |

Na Raspberry:

```bash
pip install -e ".[dev,visao,controle]"
sudo usermod -aG input pi      # o 'inputs' lê /dev/input; logout depois
```

Dois utilitários de diagnóstico, que **não movem o robô** e não abrem a serial:

```bash
python main.py --debug-visao --cor vermelho   # mede a cor e salva o quadro anotado
python main.py --listar-botoes                 # descobre os códigos do F710
```

`--debug-visao` imprime área e desvio a cada frame e salva
`/tmp/sentinel-visao-<cor>.png`. As faixas do `sentinel.toml` são chute
genérico: calibre com o pallet real, na luz real. O vermelho precisa de duas
faixas porque o matiz dele envolve o zero (em HSV do OpenCV, matiz vai de 0 a 179).

`--listar-botoes` é a fonte da verdade do mapa de botões: os nomes que a
biblioteca `inputs` emite (`BTN_SOUTH`, `BTN_START`, ...) não são o mesmo para
todo controle. Copie o que aparecer para `[controle.botoes]` ou
`[controle.parada]`.

## Desenvolvimento

```bash
.venv/bin/python -m pytest -q     # 105 testes, nenhum precisa do robô
.venv/bin/ruff check .            # lint
```

`src/sentinel/sobot.py` é o driver do protocolo, importado verbatim do projeto
original e validado contra o *Guia de Referência dos Comandos v0.4.11*. O guia
operacional completo está em `docs/SOBOT_AGENT_GUIDE.md` — inclusive as
receitas de rotas prontas.

## Segurança

`KC` e `BC` com desaceleração curta podem danificar motor e driver de forma
irreversível. Use `MT0 BC`; `KC` só em emergência extrema. O botão físico atrás
do robô corta 100% da energia. Piso limpo e seco, 24 V com no mínimo 5 A, e
nunca mande pulsos a uma roda só com a outra travada.

As regras completas estão em `docs/SOBOT_AGENT_GUIDE.md` §12 e em `AGENTS.md`.
