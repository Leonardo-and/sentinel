# SoBot (SOLIS Tecnologia) — Guia de operação para agentes de IA

> **Fontes:** *Apostila SoBot 1ª Ed. 2025 v0.4.17* (citada como **Apostila**) e *Guia de Referência dos Comandos 2ª Ed. 2025 v0.4.11* (citado como **Guia**). Referências `[Guia §x]` / `[Apostila §x]` apontam para a seção de origem.
> **Convenção de confiança:** o que não tem marca vem direto dos manuais. **(inferido)** = dedução minha a partir dos manuais, confirmar no robô. **(não documentado)** = os manuais não dizem.
> **Arquivos irmãos:** `sobot_api.json` (catálogo estruturado, mesmas informações) e `src/sentinel/sobot.py` (driver Python testado contra todos os exemplos do Guia).

---

## 0. Resumo operacional (leia isto primeiro)

1. **Canal:** serial USB entre o computador de bordo (Raspberry Pi 4B) e a Placa de Controle e Potência. Python: `serial.Serial('/dev/ttyACM0', 57600, timeout=0, dsrdtr=False)`. [Apostila §10.2, Fig. 96–97]
2. **Formato:** ASCII **MAIÚSCULO**; tokens separados por **espaço**; **sem terminador** de linha nos exemplos; decimais com **vírgula** (`WD99,6`, `PG SO1,35`); negativo com `-` colado ao número (`D-1000`). [Guia §2, §4, §5.1]
3. **O primeiro token é o "comando de identificação"** (`WP`, `MT`, `PG`, `DO`, `AO`, `LT`, `BZ`, `SC`, `EL`). [Guia §4]
4. **Fila vs. imediato:** comandos de ação entram numa **fila de até 100** e rodam em sequência; modo contínuo, parada (BC/KC), modo pulsos, leituras de sensor e `CR` rodam **imediatamente**. [Guia §2 e notas de cada seção]
5. **Sequência padrão de movimento fixo:** `MT0 E1` → comandos de movimento → `MT0 E0`. [Guia §5.2.1]
6. **Saber que terminou:** habilite `MT0 CR1`; o robô devolve `CR OK MT0` quando o movimento é processado/executado. [Guia §2, §5.16]
7. **Erros:** `ERROR=00` (desconhecido), `ERROR=01 <ID>` (sintaxe), `ERROR=02 ...` (misturou `MT0` com `MT1/MT2`); o robô dá **2 beeps**. [Guia §6]
8. **Segurança:** `KC` e `BC` com desaceleração curta podem **danificar motor/driver de forma irreversível**; piso limpo e seco; nunca aplicar >3,3 V na interface auxiliar de pulsos. Use `KC` só em emergência. [Guia §5.2.3, Apostila §12]
9. **Limites que mais importam:** velocidade `V` 0–25 cm/s · `AT`/`DT` 0–60000 ms · distância ≤ 65000 mm · raio interno 10–9999 mm · `PS` ≤ 2000 pulsos/s · `DL` 50–60000 ms · LED 0–255 · UART ≤ 15 bytes.
10. **Antes de confiar em distâncias/ângulos:** calibrar rodas (`WP`) e ganho proporcional (`PG`) — ver §5.

---

## 1. O robô

### 1.1 Visão geral
SoBot é uma plataforma de robótica móvel (AGV/AMR) de ensino com **motores de passo NEMA-23** nas rodas, redução **3:1** (torque 15 → 45 kgf·cm), corpo octogonal, 8 sonares, 1 infravermelho, acelerômetro, placa de sensores de linha, fita LED RGB, buzzer, I/O industrial e visão computacional opcional. [Apostila §1, §7]

Versões/módulos: **Starter** · **Fork-Lift** (elevador com garfo de pallet) · **Magnetic-Lift** (elevador com 4 eletroímãs) · **Agri-Tech** (webcam + tanque agro com bombas/solenoides). [Apostila §9]

### 1.2 Arquitetura de controle
- **Placa de Controle e Potência SoBot:** gerencia todos os periféricos, recebe os comandos da API por serial USB.
- **Placa host** (Raspberry Pi 4B no kit; também Arduino, BeagleBone, ESP32, Jetson…): roda seu programa e envia comandos. Qualquer placa com serial USB serve. [Apostila §7.2, §11.1]
- Dá para controlar pulso/direção dos drivers por interface externa: **remover o jumper** e ligar a interface auxiliar; **máx. 3,3 V** nessas linhas; alimentar a interface pelas saídas auxiliares da própria placa. Com o jumper no lugar, o controle é pela placa. [Apostila §12.1.1]

### 1.3 Recursos da placa [Apostila §12.1.2]
| Recurso | Quantidade | Detalhes |
|---|---|---|
| Saídas de motor de passo | 3 | 1 = roda esquerda, 2 = roda direita, 3 = auxiliar (elevador, garra…) |
| Saídas digitais (relé) | 8 | contato seco 10 A |
| Saídas analógicas PWM | 2 | frequência e duty ajustáveis |
| Entradas digitais | 8 | |
| Entradas analógicas | 6 | 2× 0–5 V, 2× 0–10 V, 2× 4–20 mA |
| Sonar | 8 | HC-SR04, alcance 4 m |
| Infravermelho | 1 | Sharp GP2Y0A02YK0F, 20–150 cm |
| Sensores de linha | 5 (PCI) | fotoelétricos; leitura `SL` |
| Acelerômetro/giroscópio | 1 | integrado à placa |
| Buzzer, fita LED RGB | 1 / 1 | |
| UART | 1 | 9600 bps, sem paridade, 1 stop bit |
| Saídas auxiliares de alimentação | 5 V, 12 V, 24 V + USB aux | |

### 1.4 Dados mecânicos e cinemática [Apostila §7.1, §8.1]
- Passo do motor 1,8° · 200 passos/volta · micropasso 1/4 → **800 pulsos/volta do motor** → com redução 3:1 → **2400 pulsos/volta da roda**.
- Roda nominal **100 mm** (perímetro 314,159 mm) · distância entre rodas nominal **262 mm** (perímetro 823,09 mm).
- **0,130899 mm por pulso.** Pulsos para 1 m ≈ 7639 · pivô de 90° ≈ 1572 pulsos · diferencial 90° com raio interno 100 mm: roda interna ≈ 1199 e externa ≈ 4343 pulsos.
- Velocidade máx. da API 25 cm/s ≈ 1910 pulsos/s (consistente com o teto de 2000 pps) *(derivado)*.
- Fórmulas: `pulsos = distância / mm_por_pulso`; `freq_pulso = velocidade_mm_s / mm_por_pulso`; rampas por MRUV (`t = √(2·S/a)`).
- **Você não precisa calcular pulsos** usando a API de alto nível (`MT0 D... V...`); só no modo pulsos (`MT1/MT2`).

### 1.5 Sonar: posição dos 8 sensores *(inferido da figura do Guia §5.3 + código de exemplo)*
Com a frente do robô para a direita da figura: **SS1** canto frontal esquerdo · **SS2** frente central · **SS3** canto frontal direito · **SS4** lateral direita · **SS5** canto traseiro direito · **SS6** traseira central · **SS7** canto traseiro esquerdo · **SS8** lateral esquerda. O exemplo oficial `SoBot-Ultrasonic-Sensor` trata SS1–SS3 como "frontais" e SS4/SS8 como "laterais", o que bate com isso.

### 1.6 Energia e controles físicos [Apostila §7.6–7.7]
- Operação **24 V, mínimo 5 A**. Padrão Solis: 2 baterias 12 V/5 Ah em série. Alternativas: 2×12 V/7 Ah série, ou 4 baterias (2 séries em paralelo = 24 V/10 A). Para carregar, **remover** as baterias do robô e usar carregador adequado.
- Alimentação externa pelo **cordão umbilical** (conector HP-3MD na tampa superior); a **chave seletora** escolhe bateria × externa.
- Três controles traseiros: **chave seletora**; **botão de emergência** (NF, corta 100% da energia — só em emergência/acidente); **botão Start** (pulsador NA, usável por programação — a entrada correspondente **não é documentada**).

---

## 2. Protocolo de comunicação

### 2.1 Conexão
```python
import serial
usb = serial.Serial('/dev/ttyACM0', 57600, timeout=0, dsrdtr=False)
usb.flush()                       # espera a configuração (usado nos exemplos oficiais)
usb.write(b"DO1 E1")              # retorna 6 -> nenhum terminador é adicionado
while 1:
    line = usb.readline()         # respostas terminam em newline
    if line:
        print(line)
```
[Apostila §10.2 e repositórios oficiais] Com `timeout=0` o `readline()` não bloqueia: faça polling. Nos exemplos oficiais há `sleep(1)` entre comandos de configuração e `sleep(0.1)` antes de ler a resposta de um `SL`.

### 2.2 Regras de sintaxe [Guia §2, §4]
- Letras **maiúsculas**, caracteres ASCII.
- **Espaço** separa os parâmetros.
- O **comando de identificação** vem primeiro. Pode haver mais de um grupo na mesma string (`MT1 E1 PS500 MT2 E1 PS500`, `PG SO1,35 CA2,87 DF3,52 RI-5`).
- Decimais com **vírgula**; faixas excedidas normalmente são **limitadas ao máximo** pelo robô (o driver `sobot.py` prefere rejeitar com erro).
- "Utilize os comandos conforme os exemplos do capítulo 5 para evitar erros de sintaxe."

### 2.3 Modelo de execução [Guia §2]
- **Fila:** até **100** comandos de ação, executados sequencialmente.
- **Imediatos:** parametrização/leitura/status de sensores e os itens marcados abaixo.
- Se o retorno (`CR`) estiver habilitado, a resposta sai quando o comando é processado e finalizado.

| Execução | Comandos |
|---|---|
| **Fila (sequencial)** | `WP`, `PG`, `MT0` modo fixo (`D`, `E`…), `MT3`, `MT0 MS`, `DO`/`AO`/`LT`/`BZ`, `SC`/`SD`/`RS`, `EL`, `DL` |
| **Imediato** | modo contínuo (`ME`, `MC`, `MD`, `MF`, `MB`, `ML`, `MR`, `MP`), `LM`, `BC`, `KC`, modo pulsos (`MT1`/`MT2`), `SS`, `SI`, `SL`, `DI`, `AI`, `SA`, `CR`, e `DO` quando usa `TM` |

Consequência prática: **`DL` atrasa a execução da fila no robô; `sleep()` no host só atrasa o envio.** Em modo contínuo não há fila: temporize no host.

### 2.4 Prioridade contínuo × fixo [Guia §5.2]
- Contínuo ativo + chega comando fixo → o fixo só executa quando o contínuo for **pausado** (`MP`).
- Fixo em andamento + chega comando contínuo → o robô **desacelera** e passa ao contínuo **sem terminar o fixo**. *Atenção a problemas mecânicos/elétricos.*
- **Não misture `MT0` com `MT1/MT2`** (gera `ERROR=02`). Desabilite um modo (`E0`) antes de usar o outro *(inferido)*.

---

## 3. Referência de comandos por tarefa

Legenda: **[F]** fila · **[I]** imediato. Faixas entre colchetes.

### 3.1 Parametrizar rodas — `WP` [F] [Guia §5.1]
```
WP MT1 WD<diâmetro_mm>    # MT1 = roda esquerda (MOTOR 01)   WD [90,00–110,00]
WP MT2 WD<diâmetro_mm>    # MT2 = roda direita  (MOTOR 02)
WP DW<distância_mm>       # entre pontos de contato          DW [240,00–280,00]
```
Exemplo: `WP MT1 WD99,6` · `WP MT2 WD100,32` · `WP DW260,35`. Acima do máximo o robô usa o máximo. Valores medidos corrigem tolerâncias de fabricação.

### 3.2 Ganho proporcional — `PG` [F] [Guia §5.2.6]
`PG SO<%> CA<%> DF<%> RI<%>` (qualquer subconjunto, `PG` primeiro). Faixa 0–99,99 %, com `-` para ganho negativo.
- **SO** = distância em linha reta · **CA** = ângulo de curva no próprio eixo · **DF** = ângulo de curva diferencial · **RI** = raio interno da curva diferencial.
Procedimento e fórmulas na §5.

### 3.3 Retorno de execução — `CR` [I] [Guia §5.16]
| Comando | Efeito | Resposta quando executar |
|---|---|---|
| `CR1` / `CR0` | liga/desliga retorno **geral** | `CR OK <ID>` |
| `MT0 CR1` / `CR0` | só rodas | `CR OK MT0` |
| `MT3 CR1` / `CR0` | só motor 3 | `CR OK MT3` |
| `LT CR1` / `CR0` | fita LED | `CR OK LT` |
| `DO CR1` / `CR0` | saídas digitais | `CR OK DO1` … `DO8` |
| `AO CR1` / `CR0` | saídas PWM | `CR OK AO1` / `AO2` |
| `BZ CR1` / `CR0` | buzzer | `CR OK BZ` |
| `EL CR1` / `CR0` | elevador | `CR OK EL` |
| `DL CR1` / `CR0` | delay | `CR OK DL` |
| `WP CR1` / `CR0` | parâmetros das rodas | `CR OK WP` |
| `PG CR1` / `CR0` | ganho proporcional | `CR OK PG` |

**Não se aplica a `MT1`/`MT2`.** Estado de `CR` ao ligar: **(não documentado)** — sempre defina explicitamente.

### 3.4 Movimento — modo fixo (por distância/ângulo) [F] [Guia §5.2.1]
Habilitar: `MT0 E1` · Desabilitar: `MT0 E0`.

| Parâmetro | Código | Faixa | Observação |
|---|---|---|---|
| Distância | `D` | 0–65000,00 mm | `-` antes = ré; sem sinal = frente |
| Ângulo (com `R`/`L`) | `D` | 0–400,00° | texto do Guia cita 380; máx. >360° por acurácia |
| Aceleração | `AT` | 0–60000 ms | |
| Desaceleração | `DT` | 0–60000 ms | |
| Velocidade de cruzeiro | `V` | 0–25 cm/s | constante entre as rampas |
| Curva diferencial | `DF` | flag | sem `DF` = curva no próprio eixo |
| Direção da curva | `R` / `L` | flag | direita / esquerda |
| Raio interno | `RI` | 10–9999 mm | curva diferencial |

Modelos:
```
MT0 D<mm> AT<ms> DT<ms> V<cm/s>                          # reta (D negativo = ré)
MT0 D<graus> R|L AT<ms> DT<ms> V<cm/s>                   # pivô no próprio eixo
MT0 D<graus> DF R|L RI<mm> [AT<ms> DT<ms>] V<cm/s>       # curva diferencial
```
Exemplos oficiais:
```
MT0 E1
MT0 D1000 AT5000 DT5000 V10        # 1 m à frente, rampas 5 s, 10 cm/s
MT0 D-1000 AT5000 DT5000 V10       # 1 m para trás
MT0 D90 R AT500 DT500 V10          # 90° à direita no próprio eixo
MT0 D90 DF L RI100 V5              # 90° à esquerda, diferencial, raio interno 100 mm
MT0 E0
```
Curva diferencial: a roda externa percorre raio = `RI` + distância entre rodas; as duas terminam juntas.

### 3.5 Motor 3 (auxiliar) por pulsos [F] [Guia §5.2.1.5]
```
MT3 E1
MT3 AT<ms> DT<ms> TP<pulsos> PS<pps>    # TP ±0–500000 (− = reverso) · PS 0–2000 · 800 pulsos/rev fixos
MT3 E0
MT3 BC                                   # parada com desaceleração
```
Exemplo: `MT3 AT2000 DT2000 TP8000 PS800`.

### 3.6 Movimento — modo contínuo [I] [Guia §5.2.2]
Segue até novo comando. Habilitar: `MT0 ME1` · Desabilitar: `MT0 ME0`.

**Padrões se não configurar:** `AT`=100 ms, `DT`=100 ms, `V`=2 cm/s, raio interno = 50 mm.

Configuração:
```
MT0 MC MD0 AT100 DT100 V5               # curva no próprio eixo
MT0 MC MD1 RI100 AT100 DT100 V5         # curva diferencial, raio interno 100 mm
```
Movimento: `MT0 MF` frente · `MT0 MB` trás · `MT0 ML` esquerda · `MT0 ML-` esquerda em sentido reverso · `MT0 MR` direita · `MT0 MR-` direita em sentido reverso · `MT0 MP` pausa.

Usos típicos: joystick/teclado e navegação por sensores (sonar/linha) que anda até achar obstáculo. `MT0` precisa acompanhar os comandos contínuos.

### 3.7 Parar [I] [Guia §5.2.3]
| Comando | Efeito |
|---|---|
| `MT0 BC` / `MT3 BC` | **Break:** desacelera conforme `DT` e **limpa a fila**. No modo contínuo = pausa. |
| `KC` | **Kill:** para **imediatamente** e limpa a fila. **Só em emergência extrema.** |

Ambos **não valem para `MT1`/`MT2`**. `KC`, ou `BC` com `DT` baixo, pode causar **dano mecânico e elétrico irreversível** (inércia × peso). O Guia não define um `DT` mínimo seguro.

### 3.8 Status do movimento — `MT0 MS` [F] [Guia §5.2.4]
Devolve a distância/ângulo da **última** movimentação feita com `MT0` (fixo ou contínuo):
```
MT0 MS D+00500,00     # frente 500 mm
MT0 MS D-00500,00     # ré 500 mm
MT0 MS D+R0090,00     # curva direita 90°
MT0 MS D+L0090,00     # curva esquerda 90°
```
Máximo retornável `D+99999,00`, dependente de `WP`/`PG`.

### 3.9 Modo aprendizado (teach-in) — `LM` [I] [Guia §5.2.5]
Com `LM1`, movimentos para frente **somam** e para trás **subtraem** distância (e curvas somam/subtraem graus); `MT0 MS` entrega o **resultado líquido** e rearma o robô para aprender outro movimento. Use **atrasos** antes e depois de ligar o modo.
```
MT0 ME1 ; sleep(0.1) ; MT0 MC LM1 ; sleep(1)
MT0 MC AT100 DT100 V5
MT0 MF ; sleep(2) ; MT0 MB ; sleep(1) ; MT0 MP
MT0 MS                      # -> MT0 MS D+00050,00 (exemplo do manual)
MT0 ME0 ; MT0 MC LM0
```

### 3.10 Modo pulsos por segundo — `MT1`/`MT2` [I] [Guia §5.2.7]
Controle direto da frequência de pulso (`PS` ±0–2000 pps; `-` = reverso). MT1 = roda esquerda, MT2 = roda direita.
```
MT1 E1 ; MT1 E1 PS200 ; sleep(2) ; MT1 E0                 # motor 1 sozinho
MT1 E1 MT2 E1
MT1 E1 PS500  MT2 E1 PS500        # ambos à frente
MT1 E1 PS-500 MT2 E1 PS-500       # ambos para trás
MT1 E1 PS-500 MT2 E1 PS500        # giro à esquerda no eixo (sinais opostos)
MT1 E1 PS500  MT2 E1 PS200        # curva diferencial à direita (módulos diferentes)
MT1 E0 MT2 E0
```
Sem `CR`, sem `BC`/`KC`. Misturar com `MT0` → `ERROR=02`.

### 3.11 Sonar — `SS` [I] [Guia §5.3]
`SS0` = todos · `SS1`…`SS8` = individual.
Respostas: `SS1 0150` (150 mm) · `SS1 FFFF` (> 4000 mm) · `SS1 NULL` (falha/desconectado). Todos: `SS1 0150 SS2 0210 … SS8 1193` (sempre do 1 ao 8). Faixa 0–4000 mm; mínimo ≈ 2 cm; cone ≈ 15°. [Apostila §7.4]

### 3.12 Infravermelho — `SI` [I] [Guia §5.4]
`SI` → `SI 050` (50 cm) ou `SI FFF` (> 150 cm). Faixa 20–150 cm.

### 3.13 Sensores de linha — `SL` [I] [Guia §5.5]
`SL` → `SL1 0 SL2 1 SL3 0`. **0 = reflexivo (branco); 1 = não reflexivo (preto).** Nos repositórios oficiais compara-se o byte recebido com `49` (`'1'`, preto) e `48` (`'0'`, branco) nas posições 4, 10 e 16 da linha. A PCI tem 5 sensores; **4 e 5 ligam em D1/D2** [Apostila Tab. 5] → provavelmente lidos com `DI1`/`DI2` *(inferido)*.

### 3.14 Entradas digitais — `DI` [I] [Guia §5.6]
`DI0` (todas) · `DI1`…`DI8`. Resposta `DI1 1 DI2 1 DI3 0 … DI8 0` (0/1 = nível lógico).

### 3.15 Saídas digitais (relés) — `DO` [F; I com `TM`] [Guia §5.7]
`DO<1–8> E<0|1> [TM<0–60000 ms>]` — uma saída por comando. Exemplo: `DO1 E1` · `DO2 E1 TM2000` (liga e desliga sozinha em 2 s) · `DO1 E0`.

### 3.16 Entradas analógicas — `AI` [I] [Guia §5.8]
| Comando | Faixa | Unidade da resposta | Exemplo |
|---|---|---|---|
| `AI1`, `AI2` | 0–5 V | mV (4 dígitos) | `AI1 1550` = 1,55 V |
| `AI3`, `AI4` | 0–10 V | mV (5 dígitos) | `AI3 08100` = 8,1 V |
| `AI5`, `AI6` | 4–20 mA | µA (6 dígitos) | `AI5 009800` = 9,8 mA |

### 3.17 Saídas PWM — `AO` [F] [Guia §5.9]
`AO<1|2> E1 F<0–1000 Hz> DC<0–100 %>` · `AO<n> E0`. Exemplo: `AO1 E1 F1000 DC30`.

### 3.18 Fita LED RGB — `LT` [F] [Guia §5.10]
`LT E1 RD<0–255> GR<0–255> BL<0–255>` · `LT E0`. Exemplo: `LT E1 RD50 GR0 BL0` (vermelho). Intensidade máxima consome muita energia, sobretudo na bateria. Convenção dos exemplos oficiais: LED como indicador de estado da lógica (ex.: verde = andando, azul = desviando).

### 3.19 Buzzer — `BZ` [F] [Guia §5.11]
`BZ E1` liga (intermitente, oscilador interno) · `BZ E0` desliga.

### 3.20 Acelerômetro/giroscópio/temperatura — `SA` [I] [Guia §5.12]
`SA` → `AX0,00 AY0,00 AZ0,91 T32,6 GX10,0 GY-0,4 GZ-2,0`. Aceleração em **g** (±2 g), temperatura em **°C** (−40…85), velocidade angular em **°/s** (faixa ±25 °/s conforme o manual). Decimais com vírgula.

### 3.21 UART com dispositivo externo — `SC`/`SD`/`RS` [F] [Guia §5.13]
```
SC E1 SD5 HELLO     # habilita UART e envia 5 bytes ("HELLO")
SC E1 SD2 OK        # envia 2 bytes
SC RS               # lê o que chegou -> "SC RS 0123ABCD"
SC E0               # desabilita
```
UART: 9600 bps, sem paridade, 1 stop bit. Máx. **15 bytes**; mais que 15 recebidos não são lidos. Bytes recebidos só vão ao USB se a UART estiver habilitada; `CR`/`LF` recebidos também ficam no buffer; a leitura só enxerga caracteres que chegaram **depois** de enviar dados ao dispositivo externo.

### 3.22 Elevador — `EL` [F] [Guia §5.14]
`EL UP` sobe · `EL DN` desce · `EL ST` para. Anda até o **fim de curso** ou até `ST`. Para curso completo use `DL` (ex.: 7 s):
```
EL DN ; DL7000 ; EL UP ; DL7000 ; EL ST
```

### 3.23 Atraso na fila — `DL` [F] [Guia §5.15]
`DL<50–60000 ms>`. Fora da faixa é limitado ao limite.

---

## 4. Erros [Guia §6]
Qualquer erro → **2 beeps** na placa.

| Retorno | Significado | Como corrigir |
|---|---|---|
| `ERROR=00` | Comando completamente desconhecido | Conferir código/caixa alta |
| `ERROR=01 <ID>` | **Erro de sintaxe** no comando `<ID>` | Rever parâmetros e ordem do comando. IDs documentados: `MT0 MT1 MT2 MT3 SS DI DO AI AO LT BZ SC EL DL WP PG` |
| `ERROR=02 <cmd>` | Mistura de modos: `MT0` enquanto rodas em pulsos (`MT1/MT2`) ou vice-versa | Desabilitar um modo antes do outro |

Exemplos: `ERROR=01 MT0`, `ERROR=01 PG`, `ERROR=02 MT1 E1 MT2 E1`, `ERROR=02 MT0 E1`. `SI`, `SL`, `SA` e `MS` não têm erro listado.

---

## 5. Calibração (fazer antes de trabalho de precisão) [Guia §5.1, §5.2.6; Apostila §12.1]
Motivo: tolerâncias de fabricação/montagem e perdas em longas distâncias. O ganho varia com **piso, peso do robô e inclinação**. Use os mesmos `AT`/`DT`/`V` da operação normal.

1. **Rodas:** meça os diâmetros reais e a distância entre pontos de contato; envie `WP`.
2. **Reta (SO):** marque a partida e comande `MT0 D1000 …`. Ajuste o valor comandado até o robô andar **exatamente 1 m**. `PG_SO = (valor_para_1m − 1000) / 10`. Ex.: 1013,5 → `PG SO1,35`.
3. **Pivô (CA):** comande 360° no próprio eixo e ajuste até fechar a volta. `PG_CA = ((valor_para_360 − 360) × 100) / 360`. Ex.: 370,34° → `PG CA2,87`.
4. **Diferencial (DF e RI):** comande 360° com raio interno 100 mm. `PG_DF = ((valor_para_360 − 360) × 100) / 360`; `PG_RI = valor_para_100mm − 100`. Ex.: 372,68° e 95 mm → `PG DF3,52` e `PG RI-5`.
5. Aplique tudo: `PG SO1,35 CA2,87 DF3,52 RI-5`.

---

## 6. Receitas

### 6.1 Startup seguro
```
CR1                          # (opcional) confirmações gerais; ou "MT0 CR1"
WP MT1 WD<d1> ; WP MT2 WD<d2> ; WP DW<dist>
PG SO<..> CA<..> DF<..> RI<..>
LT E1 RD0 GR50 BL0           # indica "pronto" (verde)
MT0 E1
```
Ao terminar: `MT0 E0` · `LT E0`.

### 6.2 Percurso por comandos fixos (ex.: quadrado)
```
MT0 E1
MT0 D1000 AT1000 DT1000 V10
MT0 D90 R AT500 DT500 V10
MT0 D1000 AT1000 DT1000 V10
MT0 D90 R AT500 DT500 V10
... (repetir) ...
MT0 E0
```
Tudo isso entra na fila (máx. 100). Para sincronizar com o programa, habilite `MT0 CR1` e aguarde `CR OK MT0`, ou use `MT0 MS`.

### 6.3 Seguidor de linha (padrão do repositório oficial) [Apostila §14.2]
1. `LT E1 ...` (verde) · `MT0 MC AT100 DT100 V2` · `MT0 ME1` (com `sleep` entre cada).
2. Loop: enviar `SL`, `sleep(0.1)`, `readline()`, decidir:
   - sensor central em preto, laterais em branco → `MT0 MF` (frente);
   - lado esquerdo preto → corrigir para a esquerda (`MT0 ML`); lado direito preto → `MT0 MR`;
   - nenhum sensor vê a faixa → `MT0 MP`/parar.
3. Sinalizar cada estado na fita LED.

### 6.4 Desvio de obstáculos com sonar [Apostila §14.3]
1. Configuração igual à do seguidor; `usb.flush()`.
2. Loop: `SS0` → ler os 8; os 3 frontais (SS1–SS3) livres → `MT0 MF`; obstáculo → decidir rota olhando laterais (SS4/SS8) e traseiros e girar com `MT0 ML`/`MR`.
3. Tratar `FFFF` como "livre" e `NULL` como falha de sensor.

### 6.5 Pallet / eletroímãs [Apostila §14]
- Rodas: modo contínuo comandado por joystick (`MT0 MF/MB/ML/MR/MP`).
- Elevador: `EL UP` / `EL DN` / `EL ST`, com `DL` quando precisar do curso completo.
- Eletroímãs: saídas de relé RL5–RL8 → `DO5`…`DO8` *(inferido; ver repositório `SoBot-Control-Electromagnets`)*.

### 6.6 Agri-Tech [Apostila §14.4]
Webcam USB na Raspberry + visão computacional (OpenCV) → acionar bombas/solenoides por relés (`DO…`, provavelmente `DO1`–`DO4`, *(inferido)*) só sobre pontos vermelhos; contar pontos verdes/vermelhos.

### 6.7 Parada de emergência
1. Preferir `MT0 BC` (e `MT3 BC`) — desacelera.
2. `KC` só se o risco for maior que o dano ao hardware.
3. Em acidente: **botão de emergência** (corta toda a energia).

---

## 7. Instalação dos módulos [Apostila §9]
**Regra geral:** fazer as **ligações elétricas antes de fechar a tampa superior**.

- **Placa seguidora de faixa (Starter):** fixada com 3 parafusos Allen M3×12, espaçadores nylon M3×5, porcas e arruelas M3. Fios dos sensores **4 e 5 → D1 e D2**.
- **Elevador com pallet (Fork-Lift):** 12 fios = NO/C/NC dos relés **RL1–RL4** + **+24 V (vermelho)** e **GND (marrom)**. Ferramentas: chave combinada 7 mm, Allen 2,5.
- **Elevador com eletroímãs (Magnetic-Lift):** igual ao anterior + eletroímãs 1–4 nos contatos **NO de RL5–RL8**. Ferramentas: 7 mm, Allen 2,5, Phillips.
- **Agri-Tech:** webcam em porta USB da Raspberry; tanque agro com treliças de pulverizadores e engate (chave 8 mm, Allen 3 / 7 mm, Allen 2,5). Fiação: fio 1 GND; fios 2–5 = NO de RL1–RL4; fios 6–8 não usados; vermelho +24 V.
- Elevador e Agri-Tech ocupam **RL1–RL4**: não usar `DO1`–`DO4` para outra carga com esses módulos montados.
- Tampas: superior (acrílico), carenagem superior, carenagem inferior, tampa das baterias.

---

## 8. Acesso ao computador de bordo [Apostila §10]
- A Raspberry vem configurada para entrar numa rede Wi-Fi padrão Solis: **rede `SOLIS`, senha `solistec`** (compartilhe a conexão do PC como hotspot).
- Acesso remoto por **VNC Viewer** (por nome de host ou IP): usuário **`pi`**, senha **`raspberry`** (padrão de fábrica — **troque em produção**).
- Terminal: `python3` → `import serial` → `usb = serial.Serial('/dev/ttyACM0', 57600, timeout=0, dsrdtr=False)` → `usb.write(b"...")`. Editor usado nos exemplos: Visual Studio Code, Python 3.

## 9. Atualização de firmware [Apostila §16]
Software: **SoBot FW Update** (pedir à Solis). Só arquivo **.bin fornecido pela Solis**. Procedimento:
1. Com o robô **desligado**, remover a tampa superior e ligar a placa ao PC por **Micro USB**; ligar o robô.
2. **Check Version** → habilita **Update Mode** → confirmar apagamento da memória (**Yes**).
3. Quando o botão ficar verde, **apertar o botão de reset** da placa (posição na Fig. 112; versão de placa 1.1.2) com proteção antiestática → **Detect Device**.
4. **Select Firmware** (.bin) → **Update Firmware** → aguardar a mensagem de sucesso.
5. Reconectar o Micro USB entre Raspberry e placa; recolocar a tampa.
**Nunca desconectar USB nem cortar a energia durante a gravação** (dano irreversível).

## 10. Repositórios oficiais de exemplo (GitHub `SolisTecnologia`) [Apostila §13–14]
| Repositório | O que demonstra |
|---|---|
| `SoBot-Simple-Route` | Percurso com comandos fixos (`time`, `serial`) |
| `SoBot-Line-Follower` | Seguidor de faixa com SL1–SL3 + LED; para quando perde a faixa |
| `SoBot-Ultrasonic-Sensor` | Desvio de obstáculos com sonar (biblioteca própria `check_sonar`) |
| `SoBot-USB-Control` | Joystick USB → modo contínuo |
| `SoBot-Control-Pallet` | Elevador de pallet + controle Logitech F710 (`inputs`) |
| `SoBot-Control-Electromagnets` | Elevador + eletroímãs com F710 |
| `SoBot-Agri-Tech` | Visão computacional (`cv2`, `numpy`, `threading`, biblioteca `tracker`) |

Padrão de código dos exemplos: `from time import sleep` · `import serial` · abrir `usb` · `usb.flush()` · `LT E1 ...` · `sleep(1)` · `MT0 MC AT100 DT100 V2` · `sleep(1)` · `MT0 ME1` · `sleep(1)` · loop de sensores/decisão.
*Eu não baixei esses repositórios; só li as capturas de tela do Apostila.*

## 11. Desafios propostos (para validar um agente) [Apostila §15]
1) Contornar um obstáculo de 1 m² com comandos fixos e voltar à origem. 2) Seguir faixa com SL1–SL3 em modo contínuo; ao detectar faixa extra nos sensores 4–5, parar e voltar pela faixa principal. 3) Transportar contêineres em pallets do estoque à produção com comandos fixos. 4) Coletar mini-caixotes com eletroímãs e depositar por cor em 4 prateleiras (verde 1, azul 2, vermelho 3, amarelo 4), voltando ao início. 5) Visão computacional + módulo agro: contar pontos verdes/vermelhos e pulverizar só os vermelhos.

## 12. Segurança (consolidado)
- Piso **limpo, seco, sem fissuras/buracos**.
- Evitar a **"curva proibida"**: mandar pulsos a só uma roda e deixar a outra travada — danifica a roda parada e prejudica a precisão.
- `KC`/`BC` curto → dano irreversível possível. Sem `DT` mínimo documentado: use rampas generosas (os exemplos oficiais usam de 100 ms a 5000 ms conforme o caso).
- Emergência = botão físico traseiro. Alimentação 24 V ≥ 5 A. Máx. 3,3 V na interface auxiliar de pulsos.
- Manusear placas com proteção antiestática; fechar a tampa só depois das ligações elétricas.
- O fabricante descreve o SoBot como dispositivo de **aprendizagem**; não recomenda ligar equipamentos de terceiros não previstos nem uso comercial dos experimentos. [Apostila §2.1]

## 13. Lista completa dos 55 códigos [Guia §3]
`AI` Input Analog · `AO` Output Analog PWM · `AT` Acceleration Time · `BC` Break Command · `BL` Blue · `BZ` Buzzer · `CA` Curve Angle · `CR` Command Return · `D` Distance · `DC` Duty-Cycle · `DF` Differential Curve · `DI` Input Digital · `DL` Delay · `DN` Down · `DO` Output Digital · `DT` Deceleration Time · `DW` Distance Wheel · `E` Enable · `EL` Elevator · `F` Frequency (Hz) · `GR` Green · `KC` Kill Command · `L` Left · `LM` Learn Movements · `LT` Led Tape · `MB` Movement Back · `MC` Movement Continuous · `MD` Movement Differential · `ME` Movement Enable · `MF` Movement Forward · `ML` Movement Left · `MP` Movement Pause · `MR` Movement Right · `MS` Movement Status · `MT` Motor · `PG` Proportional Gain · `PS` Pulse Speed · `R` Right · `RD` Red · `RI` Radius Inner · `RS` Read Serial · `SA` Sensor Accelerometer · `SC` Serial Communication · `SD` Send Serial · `SI` Sensor Infrared · `SL` Sensor Line · `SO` Straight On · `SS` Sensor Sonar · `ST` Stop · `TM` Timer · `TP` Total Pulses · `UP` Up · `V` Velocity · `WD` Wheel Diameter · `WP` Wheel Parameters.

**Códigos que mudam de significado conforme o contexto:** `DF` = flag "curva diferencial" no movimento, mas **ganho %** dentro de `PG` · `RI` = raio interno em **mm** no movimento, mas **ganho %** dentro de `PG` · `E` = habilitar/desabilitar (motores, saídas, LED, buzzer, UART) · `D` = **mm** (reta) ou **graus** (curva) · `PS` = pulsos/s (motor 3 e modo pulsos) · `ST` só existe dentro de `EL` · `MT` = 0 (rodas, modo fixo/contínuo), 1/2 (rodas, modo pulsos), 3 (auxiliar); em `WP`, `MT1`/`MT2` escolhem a roda.

## 14. Inconsistências e lacunas nos manuais (o agente deve tratar com cautela)
1. **Ângulo máximo de curva:** texto do Guia diz 0–380; tabela diz 0–400,00. Use ≤ 360 por segurança.
2. **§5.2.1.4** está intitulado "curva no próprio eixo a esquerda", mas o exemplo usa curva **diferencial** (`DF L RI100`).
3. **Tabela 21** chama `RD` de "REED" (é *Red*). **Tabela 25** repete "irá se movimentar para cima" na descrição do `ST` (é só "parar").
4. **Teach-in:** o texto fala em resultante de **55 mm**, mas o retorno exibido é `D+00050,00`.
5. **Status:** §2 diz que verificação de status é imediata; §5.2.4 diz que `MT0 MS` é enfileirado.
6. **Sensores de linha:** 5 sensores na PCI, mas `SL` no exemplo devolve só 3; os sensores 4 e 5 ligam em D1/D2 (a Tab. 5 os chama de "saída digital").
7. **Não documentados:** estado de `CR` ao ligar · valores padrão de `AT`/`DT` no modo fixo quando omitidos · comportamento de `WD`/`DW` abaixo do mínimo · qual entrada lê o botão Start · `DT` mínimo seguro · mapeamento exato `RLn`↔`DOn` · comportamento de curva em ré (`D` negativo com `R`/`L`).
8. A Apostila repete numeração em §14 (14.1–14.3 duas vezes).
9. O Guia tem versão **v0.4.11** e a Apostila **v0.4.17**; confirme a versão do firmware da placa antes de assumir todos os comandos.
