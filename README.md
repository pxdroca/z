# 🎾 Tennis Bet Monitor

Aplicação 100% gratuita e local que escuta um grupo privado do Telegram com
tips de apostas de tênis, extrai os dados dos prints/textos, confirma o
confronto oficial (via SofaScore) e tenta achar o link de cada casa de
apostas (Superbet, Betano, bet365 — configurável), entregando tudo em dois
lugares: uma notificação no seu Telegram privado (com um botão por casa) e
um painel web (Streamlit).

## Índice

1. [Visão geral do fluxo](#visão-geral-do-fluxo)
2. [Por que essa arquitetura (e não só "ler a Superbet")](#por-que-essa-arquitetura-e-não-só-ler-a-superbet)
3. [Estrutura de pastas](#estrutura-de-pastas)
4. [Passo a passo: credenciais do Telegram](#passo-a-passo-credenciais-do-telegram)
5. [Instalação](#instalação)
6. [Configuração (.env)](#configuração-env)
7. [Rodando o projeto](#rodando-o-projeto)
8. [Calibrando o extractor (OCR)](#calibrando-o-extractor-ocr)
9. [Calibrando o SofaScore (fonte do confronto oficial)](#calibrando-o-sofascore-fonte-do-confronto-oficial)
10. [Calibrando os adaptadores de casas de apostas](#calibrando-os-adaptadores-de-casas-de-apostas)
11. [Scraping, stealth e Termos de Uso — leia isto](#scraping-stealth-e-termos-de-uso--leia-isto)
12. [Limitações conhecidas](#limitações-conhecidas)

---

## Visão geral do fluxo

```
Grupo privado (Telegram)
        │  Telethon (userbot, sua conta pessoal)
        ▼
   listener.py  ──baixa print/texto──▶  extractor.py  ──jogadores/torneio/mercado/odd──▶
        │                                                                                  │
        │                                                                                  ▼
        │                                                                            matcher.py
        │                                                                     ┌────────────┴────────────┐
        │                                                                     ▼                          ▼
        │                                                          sofascore_client.py          bookmakers/*.py
        │                                                       (confirma jogo oficial:      (1 link por casa:
        │                                                        nomes, torneio, hora)         Superbet/Betano/bet365)
        ▼                                                                     └────────────┬────────────┘
   database.py  ◀──────────────────────────────── salva tudo no SQLite ───────────────────┘
        │
        ├──▶ notifier.py ──▶ Bot API do Telegram ──▶ seu chat privado (1 botão por casa)
        └──▶ app.py (Streamlit) ──▶ painel web com filtros e 1 botão por casa
```

## Por que essa arquitetura (e não só "ler a Superbet")

A primeira versão deste projeto tentava só raspar a Superbet direto. Ao
investigar problemas reais de implementação, veio à tona um padrão que vale
entender antes de mexer no código:

- A **Superbet** carrega os jogos via JavaScript (SPA React/Next.js) e
  parece bloquear por geolocalização/IP dependendo de onde você acessa.
- A **Betano** devolveu HTTP 403 (bloqueio anti-bot) já numa requisição
  simples, sem nem chegar a abrir navegador.
- O **bet365** é conhecido por ter uma das proteções anti-bot mais
  agressivas do mercado, e historicamente não tem URLs estáveis por
  partida pra usuários anônimos (usa roteamento interno por sessão).
- Agregadores de odds também não resolvem sozinhos: o **SofaScore** mostra
  odds de um único parceiro fixo (não necessariamente Superbet/Betano) e o
  **OddsAgora** (agregador brasileiro) bloqueou conexões de navegador
  automatizado nos testes, mesmo tendo URLs de partida previsíveis.

Ou seja: **não existe um atalho único** que resolva tudo de graça e sem
fricção. A solução adotada aqui separa o problema em duas partes:

1. **Confronto oficial** (nomes corretos, torneio, data/hora exata) vem do
   **SofaScore** (`sofascore_client.py`) — uma API interna não-documentada,
   mas amplamente usada pela comunidade, gratuita, sem bloqueio geográfico
   conhecido e que responde em JSON puro (não precisa de navegador).
2. **Link de cada casa de apostas** vem de um adaptador dedicado em
   `bookmakers/` (um arquivo por casa), que tenta um navegador "disfarçado"
   (stealth) pra achar o link exato da partida e, se não conseguir por
   qualquer motivo (bloqueio, seletor desatualizado, CAPTCHA...), cai
   automaticamente para um link aproximado (a página do torneio/dia
   naquela casa) — **o pipeline nunca quebra**, na pior das hipóteses você
   toca 1-2 vezes a mais dentro do app da casa.

## Estrutura de pastas

```
tennis_bet_monitor/
├── .env                    # suas credenciais reais (você cria a partir do .env.example)
├── .env.example            # template de configuração
├── requirements.txt        # dependências (todas gratuitas/open source)
├── config.py               # leitura centralizada do .env
├── models.py                # dataclasses compartilhados (Bet, ExtractedBet, MatchInfo)
├── nameutils.py             # normalização e fuzzy-match de nomes de jogadores
├── database.py               # camada SQLite (schema + CRUD)
├── extractor.py               # OCR (EasyOCR local ou Gemini free tier) + parsing
├── sofascore_client.py         # confronto oficial via SofaScore (nomes/torneio/hora)
├── matcher.py                   # orquestra sofascore_client + bookmakers/
├── bookmakers/
│   ├── base.py                    # navegador stealth + contrato comum (fallback automático)
│   ├── superbet.py                 # adaptador Superbet (mais testado/confiável dos 3)
│   ├── betano.py                    # adaptador Betano (confiança média/baixa — ver avisos)
│   └── bet365.py                     # adaptador bet365 (confiança mais baixa — ver avisos)
├── notifier.py               # envio da notificação formatada, 1 botão por casa (Bot API)
├── listener.py               # userbot Telethon, orquestra todo o pipeline
├── app.py                    # dashboard Streamlit
├── data/
│   └── apostas.db           # banco SQLite (criado automaticamente)
├── media/                    # prints baixados do Telegram (criado automaticamente)
└── logs/
    └── listener.log           # log do listener (criado automaticamente)
```

## Passo a passo: credenciais do Telegram

Você vai precisar de **duas** credenciais diferentes, para dois propósitos diferentes:

### A) `api_id` e `api_hash` (para o userbot Telethon ler o grupo)

1. Acesse **https://my.telegram.org** e faça login com o número de telefone
   da conta que já está no grupo de tips.
2. Clique em **"API development tools"**.
3. Preencha o formulário (pode usar qualquer nome de app, ex: "Tennis Bet Monitor")
   e clique em **"Create application"**.
4. Anote os valores de **`api_id`** (número) e **`api_hash`** (string longa) —
   eles vão para `TELEGRAM_API_ID` e `TELEGRAM_API_HASH` no `.env`.
5. Na primeira execução do `listener.py`, o Telethon vai pedir seu número de
   telefone e o código de confirmação recebido por SMS/Telegram — isso cria
   um arquivo de sessão local (`tennis_monitor_session.session`) e você não
   precisa logar de novo depois.

⚠️ **Trate `api_id`/`api_hash` e o arquivo `.session` como senha** — quem tiver
acesso a eles pode logar como você.

### B) Token do Bot (para receber a notificação privada)

1. No Telegram, procure por **@BotFather** e inicie uma conversa.
2. Envie `/newbot` e siga as instruções (nome e username do bot).
3. O BotFather vai devolver um token no formato `123456789:AAExemplo...` —
   isso vai para `TELEGRAM_BOT_TOKEN` no `.env`.
4. Descubra seu `chat_id` pessoal: procure por **@userinfobot** no Telegram,
   inicie uma conversa com ele e ele devolve seu ID numérico — isso vai para
   `TELEGRAM_BOT_CHAT_ID` no `.env`.
5. **Importante:** dê um `/start` na conversa com o SEU bot recém-criado
   (ou envie qualquer mensagem a ele) antes de rodar o projeto — bots não
   podem iniciar conversas, só responder a quem já falou com eles primeiro.

### Descobrindo o ID do grupo privado de tips (`TELEGRAM_SOURCE_CHAT`)

Depois de instalar as dependências (próxima seção), rode:

```bash
python listener.py --list-chats
```

Isso lista todos os seus chats com o ID de cada um. Encontre o grupo de tips
na lista e copie o ID (geralmente um número negativo tipo `-1001234567890`)
para `TELEGRAM_SOURCE_CHAT` no `.env`.

## Instalação

Requer **Python 3.10+**.

```bash
# 1) crie e ative um ambiente virtual (recomendado)
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2) instale as dependências
pip install -r requirements.txt

# 3) instale o navegador do Playwright (usado pelos adaptadores em bookmakers/)
playwright install chromium

# (Linux) o Playwright pode pedir dependências de sistema extras:
playwright install-deps chromium
```

Se preferir não usar venv, adicione `--break-system-packages` ao `pip install`
em sistemas que exigem (ex: distros mais recentes de Linux).

## Configuração (.env)

```bash
cp .env.example .env
```

Abra o `.env` e preencha, no mínimo:
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SOURCE_CHAT` (seção A acima)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_CHAT_ID` (seção B acima)

Os demais campos já têm valores padrão sensatos. Vale revisar antes de usar
de verdade:
- `BOOKMAKERS` — quais casas tentar (por padrão `superbet,betano,bet365`).
  Remova alguma se ela não te interessar ou estiver dando muito trabalho.
- `BETANO_BASE_URL`/`BETANO_TENNIS_PATH` e `BET365_BASE_URL`/`BET365_TENNIS_PATH`
  — **confirme manualmente** essas URLs no seu navegador (ver seção
  "Calibrando os adaptadores de casas de apostas").

## Rodando o projeto

Abra **dois terminais** (ambos com o venv ativado):

```bash
# Terminal 1 — escuta o Telegram e processa as tips
python listener.py
```

```bash
# Terminal 2 — sobe o painel web
streamlit run app.py
```

O Streamlit abre automaticamente em `http://localhost:8501`. Para
compartilhar com outros membros na sua rede local:

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

e compartilhe `http://SEU_IP_LOCAL:8501` com quem precisar (mesma rede Wi-Fi/LAN).

### Testando sem esperar uma tip nova

```bash
python listener.py --backfill 20
```

Isso reprocessa as últimas 20 mensagens do grupo, útil para validar o
pipeline (extractor → matcher → banco → notificação) sem precisar aguardar
alguém postar uma tip nova.

## Calibrando o extractor (OCR)

O `extractor.py` funciona em dois modos (`OCR_ENGINE` no `.env`):

- **`easyocr`** (padrão): 100% local. Na primeira execução baixa os modelos
  de reconhecimento (~200MB) — isso é normal e só acontece uma vez. Depois
  disso funciona offline.
- **`gemini`**: manda o print direto para a API Gemini (Free Tier) pedindo
  JSON estruturado — geralmente mais preciso com prints "bagunçados", porém
  depende de internet e de uma chave grátis em
  **https://aistudio.google.com/apikey**.

Independente do motor, o resultado passa por `parse_free_text()` (regex) para
estruturar jogadores/torneio/mercado/odd quando o motor não devolve JSON
pronto. **Os tipsters de cada grupo escrevem de um jeito diferente** — abra
`extractor.py` e ajuste as constantes `_PLAYERS_PATTERNS`, `_LABELLED_PATTERNS`,
`_ODD_PATTERNS`, `_TOURNAMENT_KEYWORDS` e `_MARKET_KEYWORDS` com base nos
textos reais do seu grupo. Rode `python listener.py --backfill 20` depois de
cada ajuste para validar rapidinho.

## Calibrando o SofaScore (fonte do confronto oficial)

`sofascore_client.py` foi escrito com base no formato de resposta
documentado pela comunidade (não é uma API oficial), e **não foi possível
validar ao vivo** a partir do ambiente onde este código foi gerado — a rede
usada lá recebeu HTTP 403 do SofaScore, o que costuma acontecer com IPs de
datacenter/nuvem, e não deve se repetir na sua rede doméstica/celular.

Antes de confiar no resultado, rode:

```bash
python sofascore_client.py --debug
# ou pra um dia específico:
python sofascore_client.py --debug 2026-09-01
```

Isso salva o JSON bruto do dia em `sofascore_debug.json`. Abra o arquivo e
confira se os campos batem com o que `_extract_event()` espera (em
`sofascore_client.py`): nome dos jogadores em `homeTeam.name`/`awayTeam.name`,
torneio em `tournament.name`, horário em `startTimestamp` (timestamp Unix).
Se algo estiver diferente, ajuste `_extract_event()` de acordo.

## Calibrando os adaptadores de casas de apostas

Cada casa em `bookmakers/` tem um nível de confiança diferente, do mais ao
menos testado:

| Casa | Confiança | O que fazer antes de usar |
|---|---|---|
| **Superbet** (`superbet.py`) | Média — investigada em detalhe: SPA React/Next.js, aceita `?day=YYYY-MM-DD`, pode ter geo-bloqueio fora do Brasil. | Ajustar `_SELECTORS` olhando o HTML real (veja abaixo). |
| **Betano** (`betano.py`) | Baixa/Média — uma requisição simples já recebeu HTTP 403 (proteção anti-bot na borda). | **Confirmar** `BETANO_BASE_URL`/`BETANO_TENNIS_PATH` no `.env` abrindo o site pelo navegador, e depois ajustar `_SELECTORS`. |
| **bet365** (`bet365.py`) | Baixa — mesmo domínio não pôde ser confirmado a partir do ambiente de geração deste código, e o bet365 é conhecido por não ter URLs estáveis por partida. | **Confirmar o domínio real** (`BET365_BASE_URL`) e o caminho da seção de tênis (`BET365_TENNIS_PATH`) manualmente, e não se surpreenda se o link exato quase nunca funcionar — o fallback (link aproximado) cobre isso. |

Como descobrir os seletores/paths reais de qualquer casa:

1. **Modo visual (mais fácil):**
   ```bash
   playwright codegen https://SITE_DA_CASA/caminho/de/tenis
   ```
   Isso abre um Chromium e gera código Python conforme você interage — útil
   para descobrir os seletores certos sem precisar ler HTML na mão.

2. **DevTools do navegador:** abra a seção de tênis da casa, aperte `F12`,
   clique com o botão direito num card de jogo → *Inspecionar* → copie o
   seletor CSS (ou o atributo `data-testid`/`data-qa`, se houver) e cole no
   dicionário `_SELECTORS` do respectivo arquivo em `bookmakers/`.

3. Rode com `PLAYWRIGHT_HEADLESS=false` no `.env` durante a calibração —
   assim você vê o navegador abrindo de verdade e consegue notar CAPTCHAs,
   bloqueios ou popups que atrapalham o scraping.

Se o link exato não for encontrado (site bloqueado, seletor desatualizado,
nomes não batem, CAPTCHA etc.), a aposta **não é perdida**: o botão daquela
casa aparece com um 📍 (em vez de 🎯) apontando para a página do torneio/dia,
e você pode reprocessar depois com `--backfill`.

## Scraping, stealth e Termos de Uso — leia isto

Os adaptadores em `bookmakers/` usam **`tf-playwright-stealth`** para
disfarçar sinais óbvios de automação (ex: a flag `navigator.webdriver`),
além de cabeçalhos e comportamento (atrasos aleatórios) que imitam um
navegador humano. Isso é **melhor esforço, não uma garantia**: proteções
comerciais mais fortes (Cloudflare, Akamai, PerimeterX etc.) podem detectar
e bloquear mesmo assim, e podem mudar a qualquer momento.

Pontos importantes:

- **Isso pode violar os Termos de Uso** das casas de apostas e/ou de fontes
  como o SofaScore. Este projeto não tenta contornar CAPTCHAs, autenticação,
  ou qualquer proteção "paga"/comercial de bot-management — só reduz sinais
  básicos de automação. A decisão de usar (ou não) é sua, ciente do risco.
- **Não rode isso em alta frequência.** O código já inclui atrasos
  aleatórios entre requisições; não remova isso nem rode em loop agressivo —
  além do risco de bloqueio, é uma questão de uso responsável dos servidores
  de terceiros.
- **Uso pessoal.** Este projeto foi pensado para consumo próprio (você lendo
  suas próprias tips e organizando as informações), não para redistribuir
  dados de odds em escala.
- Se uma casa bloquear completamente o scraping, o sistema **não trava**:
  ele só passa a usar o link aproximado (torneio/dia) pra aquela casa.

## Limitações conhecidas

- **Scraping é frágil por natureza.** Sites de apostas mudam o layout com
  frequência; espere reajustar `_SELECTORS` de tempos em tempos, em
  qualquer uma das 3 casas.
- **Betano e bet365 têm proteção anti-bot mais forte** que a Superbet —
  não se surpreenda se o link exato delas cair pro fallback na maioria das
  vezes. Isso foi documentado durante o desenvolvimento, não é um bug.
- **Userbot (Telethon) usa sua conta pessoal do Telegram.** Isso é necessário
  para ler grupos privados, mas significa que o arquivo de sessão
  (`*.session`) tem acesso à sua conta — mantenha-o em local seguro e nunca o
  compartilhe.
- **EasyOCR** roda bem em CPU, mas é mais lento que APIs em nuvem; se o seu
  grupo tem muitas tips por minuto e a extração ficar lenta, considere usar
  `OCR_ENGINE=gemini`.
- **Este projeto não faz apostas automaticamente** — ele apenas organiza a
  informação. Toda decisão de apostar é sua.
