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

## Desenvolvimento

```bash
.venv/bin/python -m pytest -q     # 44 testes, nenhum precisa do robô
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
