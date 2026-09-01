# 🎾 Tennis Bet Monitor

Aplicação 100% gratuita que escuta um grupo privado do Telegram com tips de
apostas de tênis, extrai os dados dos prints/textos, confirma o confronto
oficial (via SofaScore) e tenta achar o link de cada casa de apostas
(Superbet, Betano, bet365 — configurável), entregando tudo em dois lugares:
uma notificação no seu Telegram privado (com um botão por casa) e um painel
web (Streamlit).

Pode rodar **100% na nuvem gratuita** (GitHub Actions + Neon + Streamlit
Community Cloud — ver [Deploy em produção](#deploy-em-produção-nuvem-100-gratuita))
ou **localmente** na sua máquina, com escuta em tempo real.

## Índice

1. [Visão geral do fluxo](#visão-geral-do-fluxo)
2. [Por que essa arquitetura (e não só "ler a Superbet")](#por-que-essa-arquitetura-e-não-só-ler-a-superbet)
3. [Estrutura de pastas](#estrutura-de-pastas)
4. [Passo a passo: credenciais do Telegram](#passo-a-passo-credenciais-do-telegram)
5. [Instalação](#instalação)
6. [Configuração (.env)](#configuração-env)
7. [Rodando localmente](#rodando-localmente)
8. [Deploy em produção (nuvem 100% gratuita)](#deploy-em-produção-nuvem-100-gratuita)
9. [Calibrando o extractor (OCR)](#calibrando-o-extractor-ocr)
10. [Calibrando o SofaScore (fonte do confronto oficial)](#calibrando-o-sofascore-fonte-do-confronto-oficial)
11. [Calibrando os adaptadores de casas de apostas](#calibrando-os-adaptadores-de-casas-de-apostas)
12. [Scraping, stealth e Termos de Uso — leia isto](#scraping-stealth-e-termos-de-uso--leia-isto)
13. [Limitações conhecidas](#limitações-conhecidas)

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
   database.py  ◀───────────────────────── salva tudo no Postgres (Neon) ──────────────────┘
        │
        ├──▶ notifier.py ──▶ Bot API do Telegram ──▶ seu chat privado (1 botão por casa)
        └──▶ app.py (Streamlit) ──▶ painel web com filtros e 1 botão por casa
```

Em produção, `listener.py` e `score_updater.py` não ficam rodando 24/7 —
eles rodam em modo "poll and exit" (`--poll-once`/`--once`), disparados por
cron do GitHub Actions a cada 5 minutos. Ver
[Deploy em produção](#deploy-em-produção-nuvem-100-gratuita).

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
├── .env                       # suas credenciais reais (você cria a partir do .env.example)
├── .env.example                # template de configuração
├── .github/workflows/            # cron do GitHub Actions (produção — ver seção de deploy)
│   ├── poll-listener.yml            # roda listener.py --poll-once a cada 5 min
│   └── score-updater.yml            # roda score_updater.py --once a cada 5 min
├── requirements.txt        # dependências (todas gratuitas/open source)
├── config.py               # leitura centralizada do .env
├── models.py                # dataclasses compartilhados (Bet, ExtractedBet, MatchInfo)
├── nameutils.py             # normalização e fuzzy-match de nomes de jogadores
├── database.py               # camada Postgres/Neon (schema + CRUD)
├── extractor.py               # OCR (EasyOCR local ou Gemini free tier) + parsing
├── sofascore_client.py         # confronto oficial via SofaScore (nomes/torneio/hora)
├── matcher.py                   # orquestra sofascore_client + bookmakers/
├── bookmakers/
│   ├── base.py                    # navegador stealth + contrato comum (fallback automático)
│   ├── superbet.py                 # adaptador Superbet (mais testado/confiável dos 3)
│   ├── betano.py                    # adaptador Betano (confiança média/baixa — ver avisos)
│   └── bet365.py                     # adaptador bet365 (confiança mais baixa — ver avisos)
├── notifier.py               # envio da notificação formatada, 1 botão por casa (Bot API)
├── listener.py               # userbot Telethon — escuta contínua (local) ou --poll-once (produção)
├── generate_session_string.py # utilitário local: gera a StringSession pro secret do GitHub Actions
├── app.py                    # dashboard Streamlit
├── media/                    # prints baixados do Telegram (criado automaticamente)
└── logs/
    └── listener.log           # log do listener (criado automaticamente, uso local)
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
5. Na primeira execução local do `listener.py`, o Telethon vai pedir seu
   número de telefone e o código de confirmação recebido por SMS/Telegram —
   isso cria um arquivo de sessão local (`tennis_monitor_session.session`) e
   você não precisa logar de novo depois. (Pra rodar em produção via GitHub
   Actions, veja `generate_session_string.py` na seção
   [Deploy em produção](#deploy-em-produção-nuvem-100-gratuita) — o runner
   não tem como fazer esse login interativo.)

⚠️ **Trate `api_id`/`api_hash`, o arquivo `.session` e a `TELEGRAM_SESSION_STRING`
como senha** — quem tiver acesso a eles pode logar como você.

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

### Descobrindo o grupo privado de tips (`TELEGRAM_SOURCE_CHAT`)

Depois de instalar as dependências (próxima seção), rode:

```bash
python listener.py --list-chats
```

Isso lista todos os seus chats com o ID e o nome de cada um.

- **Se o grupo é permanente** (mesmo grupo/ID todo dia): copie o ID
  (geralmente um número negativo tipo `-1001234567890`) para
  `TELEGRAM_SOURCE_CHAT` no `.env`.
- **Se o grupo é recriado periodicamente** (ex: um grupo novo por dia, com
  link liberado após pagamento, nome tipo "Cansadão Apostas 31/08"): use só
  o **prefixo fixo do nome** (ex: `Cansadão Apostas`, sem a data) em
  `TELEGRAM_SOURCE_CHAT`. O listener resolve automaticamente pro grupo mais
  recente com esse prefixo a cada execução (`_resolve_source_chat()` em
  `listener.py`) — nada pra atualizar manualmente quando um grupo novo for
  liberado, mesmo em produção via GitHub Actions.

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
- `DATABASE_URL` — connection string do Postgres. Crie um projeto grátis em
  **https://neon.tech**, crie um banco, e copie a "Connection string" do
  painel (algo como `postgresql://usuario:senha@ep-xxx.neon.tech/dbname`).
  Precisa estar preenchida mesmo pra rodar só localmente — `database.py`
  não usa mais SQLite.

Os demais campos já têm valores padrão sensatos. Vale revisar antes de usar
de verdade:
- `BOOKMAKERS` — quais casas tentar (por padrão `superbet,betano,bet365`).
  Remova alguma se ela não te interessar ou estiver dando muito trabalho.
- `BETANO_BASE_URL`/`BETANO_TENNIS_PATH` e `BET365_BASE_URL`/`BET365_TENNIS_PATH`
  — **confirme manualmente** essas URLs no seu navegador (ver seção
  "Calibrando os adaptadores de casas de apostas").

## Rodando localmente

Abra **dois terminais** (ambos com o venv ativado):

```bash
# Terminal 1 — escuta o Telegram em tempo real e processa as tips
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

Rodando localmente dessa forma, `listener.py` fica conectado 24/7 e processa
cada tip assim que ela chega (sem o atraso de até 5 min do modo produção
via cron — ver próxima seção). Você também precisa rodar `score_updater.py`
à parte pra acompanhar placar/resultado ao vivo:

```bash
# Terminal 3 — acompanha placar/resultado ao vivo
python score_updater.py
```

### Testando sem esperar uma tip nova

```bash
python listener.py --backfill 20
```

Isso reprocessa as últimas 20 mensagens do grupo, útil para validar o
pipeline (extractor → matcher → banco → notificação) sem precisar aguardar
alguém postar uma tip nova.

## Deploy em produção (nuvem 100% gratuita)

Em vez de manter sua máquina ligada 24/7, dá pra rodar tudo de graça na
nuvem — com uma diferença de UX importante: novas tips e atualizações de
placar chegam com até ~5 min de atraso (o intervalo do cron), em vez de
instantaneamente como no modo local.

**Por que funciona de graça:** o GitHub Actions cobra em minutos
arredondados pra cima; num repositório **privado** o limite gratuito
(2.000 min/mês) estoura numa cadência de 5 min. Em repositório **público**,
Actions em runners padrão é **ilimitado e gratuito** — por isso este
repositório precisa ser público pra essa arquitetura funcionar sem custo.
Segredos continuam protegidos mesmo assim, via GitHub Actions Secrets
(nunca ficam visíveis no código nem nos logs).

### Peças da produção

| Peça | Onde roda | O quê |
|---|---|---|
| Banco de dados | [Neon](https://neon.tech) (Postgres gratuito) | Substitui o SQLite local — persiste entre execuções efêmeras. |
| `listener.py --poll-once` | GitHub Actions (`.github/workflows/poll-listener.yml`), cron a cada 5 min | Busca mensagens novas desde a última execução (rastreado no Postgres, tabela `sync_state`) e processa cada uma. |
| `score_updater.py --once` | GitHub Actions (`.github/workflows/score-updater.yml`), cron a cada 5 min | Um ciclo de atualização de placar + retry de apostas não encontradas, e sai. |
| `app.py` (dashboard) | [Streamlit Community Cloud](https://streamlit.io/cloud) | Lê do mesmo Postgres, sempre acessível pelo link público. |

### Passo a passo

1. **Neon**: crie a conta, um projeto, copie a connection string —
   preencha `DATABASE_URL` no seu `.env` local e rode `python -c "from
   database import init_db; init_db()"` uma vez pra criar as tabelas.

2. **Sessão do Telegram para produção**: rode localmente
   ```bash
   python generate_session_string.py
   ```
   e guarde a string impressa (trate como senha).

3. **Torne o repositório público** (Settings → General → Danger Zone →
   Change visibility) — necessário pro cron de 5 min ser gratuito (ver
   acima). Se preferir manter o código privado, dá pra rodar os workflows
   mesmo assim, só que com uma cadência mais espaçada (ex: a cada 20-30 min)
   pra caber no limite gratuito de repositório privado — ajuste o `cron` nos
   dois arquivos em `.github/workflows/`.

4. **Cadastre os secrets** do repositório (Settings → Secrets and variables
   → Actions → New repository secret):
   - `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING` (do passo 2), `TELEGRAM_SOURCE_CHAT`
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_CHAT_ID`
   - `DATABASE_URL` (do passo 1)
   - `GEMINI_API_KEY` (recomendado trocar `OCR_ENGINE` pra `gemini` em
     produção — ver [Calibrando o extractor](#calibrando-o-extractor-ocr);
     já está assim por padrão no workflow `poll-listener.yml`)

   E as variáveis não-secretas (aba "Variables", mesma tela), se quiser
   customizar algo além do padrão: `BOOKMAKERS`, `SUPERBET_BASE_URL`,
   `BETANO_BASE_URL`, `BETANO_TENNIS_PATH`, `BET365_BASE_URL`,
   `BET365_TENNIS_PATH`, `TIMEZONE`.

5. **Dispare os workflows manualmente uma vez** (aba Actions → escolha o
   workflow → "Run workflow") pra validar antes de esperar o cron — confira
   os logs da execução.

6. **Publique o dashboard**: em [share.streamlit.io](https://share.streamlit.io),
   conecte o repositório, aponte pra `app.py`, e em "Secrets" (painel do
   app) cole:
   ```toml
   DATABASE_URL = "postgresql://usuario:senha@ep-xxx.neon.tech/dbname"
   ```
   O link público gerado é o que você compartilha com o grupo.

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
casa aponta para a página do torneio/dia em vez da partida específica (a
notificação do Telegram, essa sim, ainda marca 🎯 exato / 📍 aproximado em
cada botão). `score_updater.py` também retenta apostas que não acharam
confronto nenhum a cada ciclo — ver [Deploy em produção](#deploy-em-produção-nuvem-100-gratuita).

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
  para ler grupos privados, mas significa que o arquivo de sessão local
  (`*.session`) ou a `TELEGRAM_SESSION_STRING` (produção) têm acesso à sua
  conta — mantenha-os em local seguro e nunca os compartilhe.
- **Em produção (GitHub Actions), tips e placares chegam com até ~5 min de
  atraso** — o cron não é instantâneo como a escuta local em tempo real.
  Aceitável pra a maioria dos usos; se precisar de tempo real, rode
  `listener.py`/`score_updater.py` localmente em vez do modo cron.
- **EasyOCR** roda bem em CPU, mas baixa ~200MB de modelo a cada execução
  do GitHub Actions (o runner é uma máquina nova a cada vez, sem cache) —
  por isso o workflow de produção usa `OCR_ENGINE=gemini` por padrão
  (mais leve, precisa só de uma chave grátis). Localmente, `easyocr`
  continua sendo o padrão e funciona bem offline.
- **Este projeto não faz apostas automaticamente** — ele apenas organiza a
  informação. Toda decisão de apostar é sua.
