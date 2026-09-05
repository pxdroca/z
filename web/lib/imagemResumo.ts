// Gera a imagem-resumo "jogos do dia" (formato vertical, pensado pra ser
// compartilhado no grupo do Telegram e ficar legível numa tela de celular
// — nunca horizontal). Desenha tudo num <canvas> no navegador: o projeto
// não tem nenhuma lib de imagem (canvas/sharp/puppeteer) instalada, e
// Canvas 2D nativo é suficiente pro layout simples de lista que precisamos.
import { grupoDaBet, type GrupoId } from "./agrupamento";
import { calcularEstatisticas } from "./estatisticas";
import type { Bet } from "./types";

const FUSO_PADRAO = "America/Sao_Paulo";

/**
 * Quanto ANTES da virada do dia uma tip sem horário ainda conta como
 * sendo daquele dia.
 *
 * A aposta sem `data_hora` pertence ao dia em que foi enviada, mas o
 * tipster manda as bets da madrugada na véspera (as de 04/09 saíram
 * 23:28 e 23:42 de 03/09, hora de Brasília). Esta folga cobre esse
 * padrão sem puxar a tip de dois dias atrás.
 */
const JANELA_SEM_HORARIO = 6 * 60 * 60 * 1000;

/** "Hoje" no fuso de Brasília — mesmo critério usado no filtro padrão do
 *  painel (app/page.tsx), pra bater com o que o usuário já vê como "hoje". */
function diaNoFuso(iso: string, fuso: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: fuso, year: "numeric", month: "2-digit", day: "2-digit" }).format(
    new Date(iso)
  );
}

/**
 * Apostas do dia, ordenadas por horário.
 *
 * Entram duas coisas: as com jogo marcado para hoje E as que foram
 * LANÇADAS hoje sem horário conhecido. O segundo caso existe porque
 * `data_hora` só é preenchido quando alguma fonte confirma o confronto —
 * uma aposta de liga que nenhuma fonte cobre (a de basquete da CBA
 * chinesa), uma múltipla (que não tem um confronto único) ou uma tip cujo
 * nome o OCR truncou ficam sem horário para sempre.
 *
 * Bug real (04/09/2026): o filtro exigia `data_hora`, e por isso 4 das 8
 * apostas já decididas do dia não apareciam na imagem — inclusive a ÚNICA
 * red e o green do basquete. O resumo mostrava 4 greens e nenhum red, o
 * que dá uma leitura errada do dia. Uma aposta com resultado decidido
 * pertence ao resumo, tenha horário ou não.
 *
 * Para as sem horário o critério NÃO pode ser "criada hoje": o tipster
 * manda as tips de madrugada, e as duas desse caso foram lançadas 23:28 e
 * 23:42 de 03/09 (Brasília) para jogos de 04/09 — "criada hoje" as
 * excluiria de novo. Também não pode ser "entra tudo": isso arrastava
 * para a imagem a múltipla de odd 129 de ontem, ainda pendente.
 *
 * O critério é uma JANELA a partir de quando a aposta foi lançada
 * (JANELA_SEM_HORARIO): cobre a tip da madrugada anterior sem alcançar
 * as apostas antigas que continuam em aberto.
 *
 * As sem horário vão para o fim da lista (a imagem é organizada por
 * horário; sem ele, não há onde encaixá-las no meio).
 */
export function jogosDeHoje(bets: Bet[]): Bet[] {
  const hoje = new Intl.DateTimeFormat("en-CA", {
    timeZone: FUSO_PADRAO,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());

  // Que dia a imagem está retratando? Normalmente hoje — mas quando a
  // lista recebida (já filtrada pelo painel) é de OUTRO dia, é esse dia
  // que vale. Sem isso, filtrar o painel por 05/09 às 22h de 04/09
  // gerava uma imagem vazia: a função recalculava "hoje" e descartava
  // tudo que o usuário tinha acabado de selecionar.
  const diasComJogo = new Set(
    bets.filter((b) => b.data_hora).map((b) => diaNoFuso(b.data_hora!, FUSO_PADRAO)),
  );
  const diaAlvo = diasComJogo.has(hoje)
    ? hoje
    // Sem jogo hoje na lista: usa o dia mais frequente entre os que há.
    : [...diasComJogo].sort()[0] ?? hoje;

  const doDia = bets.filter((b) => {
    if (b.data_hora) return diaNoFuso(b.data_hora, FUSO_PADRAO) === diaAlvo;
    if (!b.criado_em) return false;
    // Sem horário confirmado, a aposta pertence ao dia em que foi
    // ENVIADA — não a uma janela de horas a partir de agora.
    //
    // A janela deslizante fazia toda aposta sem horário reaparecer no
    // resumo do dia seguinte enquanto estivesse dentro das 24h, e é o que
    // enchia a seção "horário não encontrado" com jogos de dias
    // diferentes. Ancorar no dia de envio resolve: a tip de madrugada
    // (mandada 23:28 de 03/09 para o jogo de 04/09) continua entrando no
    // dia certo por causa da tolerância abaixo.
    const criadoEm = new Date(b.criado_em);
    if (diaNoFuso(b.criado_em, FUSO_PADRAO) === diaAlvo) return true;
    // Tolerância: tip mandada na véspera, perto da virada, é do dia
    // seguinte (o tipster manda as bets de madrugada por volta das 23h).
    const inicioDoDia = new Date(`${diaAlvo}T00:00:00-03:00`).getTime();
    const antes = inicioDoDia - criadoEm.getTime();
    return antes > 0 && antes <= JANELA_SEM_HORARIO;
  });

  const instante = (b: Bet): number =>
    b.data_hora ? new Date(b.data_hora).getTime() : Number.POSITIVE_INFINITY;

  return doDia.slice().sort((a, b) => instante(a) - instante(b));
}

/** Qual dia a imagem está retratando (YYYY-MM-DD no fuso de Brasília).
 *  Mesmo critério de jogosDeHoje — o cabeçalho tem que mostrar a data do
 *  conteúdo, não "hoje": filtrando o painel por 05/09 às 22h de 04/09, o
 *  título dizia "04 DE SET" com jogos de 05. */
export function diaRetratado(bets: Bet[]): string {
  const hoje = new Intl.DateTimeFormat("en-CA", {
    timeZone: FUSO_PADRAO,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  const dias = new Set(
    bets.filter((b) => b.data_hora).map((b) => diaNoFuso(b.data_hora!, FUSO_PADRAO)),
  );
  return dias.has(hoje) ? hoje : [...dias].sort()[0] ?? hoje;
}

function formatarHora(iso: string): string {
  return new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO_PADRAO, hour: "2-digit", minute: "2-digit" }).format(
    new Date(iso)
  );
}

/** Hora do dia (0-23) no fuso de Brasília — o agrupamento por período tem
 *  que usar o MESMO fuso do horário exibido, senão um jogo das 23h de
 *  Brasília cairia na "manhã" do dia seguinte em UTC. */
function horaNoFuso(iso: string): number {
  const hh = new Intl.DateTimeFormat("en-GB", {
    timeZone: FUSO_PADRAO,
    hour: "2-digit",
    hour12: false,
  }).format(new Date(iso));
  return Number(hh);
}

/**
 * Períodos do dia. As faixas seguem o horário de Brasília e o perfil real
 * das tips: o circuito asiático joga de madrugada (a tip dos Lions foi
 * 02:43), o europeu à tarde e o americano à noite.
 *
 * "sem_horario" é um período à parte, não um balde onde sobra tudo: uma
 * aposta sem `data_hora` (liga que nenhuma fonte cobre, múltipla, nome
 * truncado pelo OCR) não pertence a nenhuma hora do dia, e colocá-la em
 * "noite" mentia sobre quando o jogo acontece.
 */
type PeriodoId = "manha" | "tarde" | "noite" | "sem_horario";

interface Periodo {
  id: PeriodoId;
  label: string;
  /** Cor da luz difusa no canto superior esquerdo do bloco. */
  luz: string;
  /** Cor do rótulo e do ícone do período. */
  cor: string;
}

const PERIODOS: Periodo[] = [
  { id: "manha", label: "MANHÃ", luz: "52, 211, 153", cor: "#34d399" },
  { id: "tarde", label: "TARDE", luz: "251, 191, 36", cor: "#fbbf24" },
  { id: "noite", label: "NOITE", luz: "96, 165, 250", cor: "#60a5fa" },
  { id: "sem_horario", label: "HORÁRIO NÃO ENCONTRADO", luz: "156, 163, 175", cor: "#9ca3af" },
];

function periodoDaBet(bet: Bet): PeriodoId {
  if (!bet.data_hora) return "sem_horario";
  const h = horaNoFuso(bet.data_hora);
  // Madrugada conta como manhã: 01:55 e 02:43 são "cedo", não uma quarta
  // seção — e o tipster trata as duas coisas como a mesma tanda de tips.
  //
  // Noite a partir das 18h (não 17h): 17:00 e 17:30 ainda são tarde.
  if (h < 12) return "manha";
  if (h < 18) return "tarde";
  return "noite";
}

interface BlocoPeriodo {
  periodo: Periodo;
  jogos: Bet[];
}

/** Agrupa na ordem de PERIODOS, omitindo os períodos vazios (uma seção
 *  vazia só gastaria altura da imagem). */
function agruparPorPeriodo(jogos: Bet[]): BlocoPeriodo[] {
  return PERIODOS.map((periodo) => ({
    periodo,
    jogos: jogos.filter((b) => periodoDaBet(b) === periodo.id),
  })).filter((bloco) => bloco.jogos.length > 0);
}

type CorGrupo = { texto: string; fundo: string; label: string };

const COR_POR_GRUPO: Record<GrupoId, CorGrupo> = {
  green: { texto: "#34d399", fundo: "rgba(52, 211, 153, 0.14)", label: "GREEN" },
  red: { texto: "#f87171", fundo: "rgba(248, 113, 113, 0.14)", label: "RED" },
  // Ao vivo em ÂMBAR, não no mesmo vermelho do red: na imagem os dois
  // badges ficam um embaixo do outro e, na mesma cor, "ao vivo" era lido
  // como aposta perdida. Âmbar também é o tom que o painel já usa pra
  // pendente/em andamento, então não introduz cor nova no sistema.
  ao_vivo: { texto: "#fbbf24", fundo: "rgba(251, 191, 36, 0.16)", label: "AO VIVO" },
  pendentes: { texto: "#9ca3af", fundo: "rgba(156, 163, 175, 0.12)", label: "PENDENTE" },
  void: { texto: "#9ca3af", fundo: "rgba(156, 163, 175, 0.12)", label: "VOID" },
};

// Ícone de esporte desenhado direto no canvas (mesmo traço fino usado nos
// componentes React, ver components/icons.tsx) — path 2D equivalente aos
// SVGs de TennisBallIcon/BasketballIcon, só que centrado em (0,0) com raio
// 1 pra escalar fácil com ctx.scale().
function desenharIconeEsporte(ctx: CanvasRenderingContext2D, esporte: string | null | undefined, cx: number, cy: number, raio: number, cor: string) {
  ctx.save();
  ctx.translate(cx, cy);
  ctx.scale(raio / 9, raio / 9); // paths desenhados numa grade de raio 9 (como os ícones SVG, viewBox 24 centrado em 12,12 -> raio 9)
  ctx.strokeStyle = cor;
  ctx.lineWidth = 1.6;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  ctx.beginPath();
  ctx.arc(0, 0, 9, 0, Math.PI * 2);
  ctx.stroke();

  if (esporte === "basquete") {
    ctx.beginPath();
    ctx.moveTo(-9, 0);
    ctx.lineTo(9, 0);
    ctx.moveTo(0, -9);
    ctx.lineTo(0, 9);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(-6.4, -6.4);
    ctx.bezierCurveTo(-4, -4.5, -2.8, -2, -2.8, 0);
    ctx.bezierCurveTo(-2.8, 2, -4, 4.5, -6.4, 6.4);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(6.4, -6.4);
    ctx.bezierCurveTo(4, -4.5, 2.8, -2, 2.8, 0);
    ctx.bezierCurveTo(2.8, 2, 4, 4.5, 6.4, 6.4);
    ctx.stroke();
  } else {
    ctx.beginPath();
    ctx.moveTo(-7.8, -4.2);
    ctx.bezierCurveTo(-4.5, -3, -2.7, 0.2, -3.8, 4.3);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(7.8, 4.2);
    ctx.bezierCurveTo(4.5, 3, 2.7, -0.2, 3.8, -4.3);
    ctx.stroke();
  }
  ctx.restore();
}

function truncar(ctx: CanvasRenderingContext2D, texto: string, larguraMax: number): string {
  if (ctx.measureText(texto).width <= larguraMax) return texto;
  let t = texto;
  while (t.length > 1 && ctx.measureText(`${t}…`).width > larguraMax) {
    t = t.slice(0, -1);
  }
  return `${t}…`;
}

/**
 * Reduz "Inaki Montes De La Torre" a "Montes De La Torre".
 *
 * Só o primeiro nome sai; partículas (de, la, dos…) ficam, porque elas
 * fazem parte do sobrenome composto e "Torre" sozinho não identifica
 * ninguém. Nomes de time de basquete ("Zhejiang Guangsha Lions") também
 * passam por aqui, e perder a cidade continua deixando o time
 * reconhecível.
 */
const _PARTICULAS = new Set(["de", "da", "do", "das", "dos", "la", "le", "van", "von", "del", "di"]);

function soSobrenome(nome: string): string {
  // "/" = duplas ("Borges N./Hijikata R."), já vem abreviado da fonte;
  // "(" = seleção ("Coreia do Sul (F)"), que não tem sobrenome. Cortar
  // qualquer um dos dois só destruiria a informação.
  if (nome.includes("/") || nome.includes("(")) return nome;

  const partes = nome.trim().split(/\s+/);
  if (partes.length <= 2) return nome;
  // Anda da esquerda pra direita descartando o(s) primeiro(s) nome(s) até
  // sobrarem duas palavras ou a próxima ser uma partícula (aí o resto é o
  // sobrenome composto inteiro).
  let i = 1;
  while (partes.length - i > 2 && !_PARTICULAS.has(partes[i].toLowerCase())) i++;
  return partes.slice(i).join(" ");
}

/**
 * Encaixa "A vs B" na largura disponível sem cortar com "…".
 *
 * Um nome cortado ("Inaki Montes vs Jack Pinnin…") não diz qual era a
 * aposta, então antes de truncar a linha tenta versões mais curtas:
 * nome completo → só sobrenomes. Truncar continua sendo o último
 * recurso, pra nunca estourar a largura.
 */
function encaixarConfronto(
  ctx: CanvasRenderingContext2D,
  jogo: string,
  larguraMax: number,
): string {
  if (ctx.measureText(jogo).width <= larguraMax) return jogo;

  const lados = jogo.split(" vs ");
  if (lados.length === 2) {
    const curto = lados.map(soSobrenome).join(" vs ");
    if (ctx.measureText(curto).width <= larguraMax) return curto;
    return truncar(ctx, curto, larguraMax);
  }

  return truncar(ctx, jogo, larguraMax);
}

/**
 * Ícone do status, desenhado à esquerda do rótulo.
 *
 * Existe porque o status perdeu a pílula de fundo: sem o container, a cor
 * sozinha teria que carregar o significado, e um ✓/✕ resolve isso sem
 * peso visual — além de continuar legível pra quem não distingue bem
 * verde de vermelho.
 */
function desenharIconeStatus(
  ctx: CanvasRenderingContext2D,
  grupo: GrupoId,
  cx: number,
  cy: number,
  cor: string,
): void {
  ctx.save();
  ctx.strokeStyle = cor;
  ctx.fillStyle = cor;
  ctx.lineWidth = 2.4;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  if (grupo === "green") {
    // check dentro de um círculo fino
    ctx.beginPath();
    ctx.arc(cx, cy, 11, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(cx - 5, cy);
    ctx.lineTo(cx - 1.5, cy + 3.5);
    ctx.lineTo(cx + 5.5, cy - 4);
    ctx.stroke();
  } else if (grupo === "red") {
    ctx.beginPath();
    ctx.arc(cx, cy, 11, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(cx - 4.5, cy - 4.5);
    ctx.lineTo(cx + 4.5, cy + 4.5);
    ctx.moveTo(cx + 4.5, cy - 4.5);
    ctx.lineTo(cx - 4.5, cy + 4.5);
    ctx.stroke();
  } else if (grupo === "ao_vivo") {
    // ponto central com duas ondas — sinal de transmissão
    ctx.beginPath();
    ctx.arc(cx, cy, 3.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.lineWidth = 2;
    for (const raio of [7.5, 11.5]) {
      ctx.beginPath();
      ctx.arc(cx, cy, raio, -Math.PI * 0.32, Math.PI * 0.32);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(cx, cy, raio, Math.PI * 0.68, Math.PI * 1.32);
      ctx.stroke();
    }
  } else if (grupo === "void") {
    // traço — "não valeu"
    ctx.lineWidth = 2.6;
    ctx.beginPath();
    ctx.moveTo(cx - 8, cy);
    ctx.lineTo(cx + 8, cy);
    ctx.stroke();
  } else {
    // pendente: círculo vazio
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(cx, cy, 8.5, 0, Math.PI * 2);
    ctx.stroke();
  }

  ctx.restore();
}

/** Ícones dos cards de métrica do topo. Próprios (não os de status): um ✓
 *  cabe no card de green, mas "unidades" e "acerto" pedem moedas e alvo. */
type IconeMetrica = "check" | "x" | "moedas" | "alvo";

function desenharIconeMetrica(
  ctx: CanvasRenderingContext2D,
  icone: IconeMetrica,
  cx: number,
  cy: number,
  cor: string,
): void {
  ctx.save();
  ctx.strokeStyle = cor;
  ctx.fillStyle = cor;
  ctx.lineWidth = 2;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  if (icone === "check") {
    ctx.beginPath();
    ctx.arc(cx, cy, 11, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(cx - 5, cy);
    ctx.lineTo(cx - 1.5, cy + 3.5);
    ctx.lineTo(cx + 5.5, cy - 4);
    ctx.stroke();
  } else if (icone === "x") {
    ctx.beginPath();
    ctx.arc(cx, cy, 11, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(cx - 4.5, cy - 4.5);
    ctx.lineTo(cx + 4.5, cy + 4.5);
    ctx.moveTo(cx + 4.5, cy - 4.5);
    ctx.lineTo(cx - 4.5, cy + 4.5);
    ctx.stroke();
  } else if (icone === "moedas") {
    // pilha de três discos vista de lado
    for (const dy of [5, 0, -5]) {
      ctx.beginPath();
      ctx.ellipse(cx, cy + dy, 9, 3.6, 0, 0, Math.PI * 2);
      ctx.stroke();
    }
  } else {
    // alvo: dois anéis e o centro
    ctx.beginPath();
    ctx.arc(cx, cy, 10.5, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx, cy, 5.5, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx, cy, 1.8, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.restore();
}

/**
 * Luz ambiente difusa no canto superior esquerdo de um bloco.
 *
 * Imita luz refletida na superfície do vidro, não uma borda neon: o brilho
 * nasce no canto, cobre um pedaço curto das bordas superior e esquerda, e
 * some em gradiente. Três camadas, da mais ampla à mais concentrada —
 * uma só ou fica fraca demais ou vira um halo com contorno visível.
 *
 * `luzRgb` é "r, g, b" (sem alfa) pra compor os rgba() aqui dentro.
 */
function desenharLuzDoCanto(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  raioCanto: number,
  luzRgb: string,
): void {
  // Halo POR FORA do card: a luz escapando pela quina, que o clip()
  // sozinho cortava — o brilho batia na borda e parava seco.
  //
  // Feito com shadowBlur sobre o próprio roundRect (e não com um
  // fillRect de gradiente, que desenhava um QUADRADO visível ao lado do
  // card arredondado — pior que o corte que vinha corrigir): a sombra
  // acompanha a forma real do card. O deslocamento negativo joga o
  // brilho pra cima e pra esquerda, que é de onde a luz vem.
  // Desenhado numa camada própria e depois esmaecido com um gradiente
  // radial: recortar a região do canto com rect()+clip() deixava uma
  // ARESTA RETA visível onde o corte terminava. Com a máscara radial o
  // brilho perde força gradualmente conforme se afasta da quina, sem
  // nenhuma borda dura.
  const camada = document.createElement("canvas");
  camada.width = Math.ceil(w) + BLOCO_GLOW * 2;
  camada.height = Math.ceil(h) + BLOCO_GLOW * 2;
  const cctx = camada.getContext("2d");
  if (cctx) {
    // Coordenadas locais da camada (a origem do card fica em BLOCO_GLOW).
    const lx = BLOCO_GLOW;
    const ly = BLOCO_GLOW;

    cctx.shadowColor = `rgba(${luzRgb}, 0.6)`;
    cctx.shadowBlur = BLOCO_GLOW * 2;
    cctx.shadowOffsetX = -3;
    cctx.shadowOffsetY = -3;
    cctx.strokeStyle = `rgba(${luzRgb}, 0.3)`;
    cctx.lineWidth = 1;
    roundRect(cctx, lx, ly, w, h, raioCanto);
    cctx.stroke();

    // Máscara: mantém o que está perto da quina, apaga o resto.
    cctx.globalCompositeOperation = "destination-in";
    const mascara = cctx.createRadialGradient(lx, ly, 0, lx, ly, BLOCO_GLOW * 9);
    mascara.addColorStop(0, "rgba(0,0,0,1)");
    mascara.addColorStop(0.55, "rgba(0,0,0,0.45)");
    mascara.addColorStop(1, "rgba(0,0,0,0)");
    cctx.fillStyle = mascara;
    cctx.fillRect(0, 0, camada.width, camada.height);

    ctx.drawImage(camada, x - BLOCO_GLOW, y - BLOCO_GLOW);
  }

  ctx.save();
  // Recorta no formato do card: o resto da luz vive DENTRO do vidro.
  roundRect(ctx, x, y, w, h, raioCanto);
  ctx.clip();

  // 1) brilho amplo e muito suave — o "ambiente". Alfa baixo de
  // propósito: o que dá a leitura de luz é o gradiente longo, não a
  // intensidade. Com 0.16 aqui a primeira linha do bloco virava uma faixa
  // colorida, e o card parecia ter fundo verde/âmbar em vez de reflexo.
  const amplo = ctx.createRadialGradient(x, y, 0, x, y, Math.min(w * 0.5, 380));
  amplo.addColorStop(0, `rgba(${luzRgb}, 0.075)`);
  amplo.addColorStop(0.4, `rgba(${luzRgb}, 0.022)`);
  amplo.addColorStop(1, `rgba(${luzRgb}, 0)`);
  ctx.fillStyle = amplo;
  ctx.fillRect(x, y, w, h);

  // 2) núcleo concentrado no canto — pequeno, só pra marcar de onde a luz
  // vem.
  const nucleo = ctx.createRadialGradient(x, y, 0, x, y, 110);
  nucleo.addColorStop(0, `rgba(${luzRgb}, 0.10)`);
  nucleo.addColorStop(1, `rgba(${luzRgb}, 0)`);
  ctx.fillStyle = nucleo;
  ctx.fillRect(x, y, w, h);

  ctx.restore();

  // 3) a borda iluminada, seguindo o CONTORNO do card.
  //
  // Antes eram dois fillRect (uma faixa no topo e outra na esquerda).
  // Faixa é reta e o card é arredondado: o clip() cortava as duas na
  // curva, e cada uma terminava em BICO — as "quinas com falha". Agora é
  // um stroke sobre o mesmo roundRect, com um gradiente diagonal que sai
  // do canto e some; a linha acompanha o arredondamento e não tem ponta.
  //
  // Fora do clip de propósito: o traço fica centrado na borda, metade
  // pra dentro e metade pra fora, o que é justamente o que dá a
  // impressão de luz na superfície do vidro.
  ctx.save();
  const alcanceBorda = Math.min(Math.max(w, h) * 0.6, 460);
  const linha = ctx.createLinearGradient(x, y, x + alcanceBorda, y + alcanceBorda);
  linha.addColorStop(0, `rgba(${luzRgb}, 0.75)`);
  linha.addColorStop(0.35, `rgba(${luzRgb}, 0.28)`);
  linha.addColorStop(1, `rgba(${luzRgb}, 0)`);
  ctx.strokeStyle = linha;
  ctx.lineWidth = 1.5;
  roundRect(ctx, x, y, w, h, raioCanto);
  ctx.stroke();
  ctx.restore();
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

const LARGURA = 1080; // formato vertical estilo Stories — 1080 é a largura padrão de export mobile
const PAD = 56;
/* Linhas mais compactas (era 132) pra segurar a altura total.
   Com 20 apostas a imagem chegava a 2160x6488 — 14 megapixels, proporção
   1:3 — e o Telegram/WhatsApp recomprimem isso ao enviar, encolhendo a
   imagem INTEIRA até o texto ficar ilegível. Era a "qualidade baixa"
   percebida: a perda não é do desenho, é da recompressão que a altura
   provoca. 108px mantém as duas linhas de texto folgadas. */
const LINHA_ALTURA = 108;
/** Cabeçalho de cada seção de período ("MANHÃ", "TARDE"...). */
const PERIODO_HEADER_ALTURA = 66;
/** Respiro entre o fim de um bloco de período e o cabeçalho do próximo. */
const PERIODO_GAP = 22;
const BLOCO_RAIO = 22;
/* Margem interna do canvas reservada pro brilho dos cantos poder
   extravasar sem ser cortado — ver desenharLuzDoCanto. */
const BLOCO_GLOW = 18;
/* Header mais alto porque agora abriga o grid 2x2 de estatísticas à
   direita do título (antes elas ficavam numa faixa própria de 150px
   abaixo, que custava altura e deixava um vazio ao lado do título). */
const HEADER_ALTURA = 246;
const FOOTER_ALTURA = 90;

/**
 * Desenha as estatísticas do dia num grid 2x2 à DIREITA do título, em vez
 * de uma faixa de 4 colunas abaixo dele.
 *
 * Ganha altura (a faixa custava 150px de imagem) e aproveita o vazio que
 * sobrava ao lado de "Jogos do dia". Cada célula é um cartão de vidro
 * escuro — mesmo vocabulário dos cards de métrica do painel.
 *
 * Cada célula mostra UM número e nada mais. Sem linhas secundárias: o ROI
 * era a mesma informação das unidades noutra unidade, e "3/13" repetia o
 * green e o red das células ao lado. A imagem é pra ser lida de relance
 * no Telegram — quatro números grandes comunicam melhor que quatro
 * números com legendas embaixo.
 *
 * Os números vêm de calcularEstatisticas, o MESMO cálculo dos cards do
 * painel, pra imagem e tela nunca divergirem.
 */
function desenharEstatisticas(ctx: CanvasRenderingContext2D, jogos: Bet[], y: number): void {
  const stats = calcularEstatisticas(jogos);

  // As séries são a evolução ao longo do dia, na ordem cronológica dos
  // jogos já decididos — é o que dá sentido a uma sparkline. Métrica sem
  // progressão natural (taxa de acerto com 1 aposta) fica sem gráfico.
  const decididos = jogos.filter((b) => {
    const g = grupoDaBet(b);
    return g === "green" || g === "red";
  });

  const serieGreen: number[] = [];
  const serieRed: number[] = [];
  const serieUnidades: number[] = [];
  const serieAcerto: number[] = [];
  let accGreen = 0;
  let accRed = 0;
  let accUnid = 0;
  for (const b of decididos) {
    const ganhou = grupoDaBet(b) === "green";
    if (ganhou) {
      accGreen += 1;
      accUnid += ((b.odd ?? 1) - 1) * (b.unidades ?? 1);
    } else {
      accRed += 1;
      accUnid -= b.unidades ?? 1;
    }
    serieGreen.push(accGreen);
    serieRed.push(accRed);
    serieUnidades.push(accUnid);
    serieAcerto.push((accGreen / (accGreen + accRed)) * 100);
  }

  const celulas: {
    label: string;
    valor: string;
    cor: string;
    icone: IconeMetrica;
    luz: string;
    serie?: number[];
  }[] = [
    { label: "GREEN", valor: String(stats.green), cor: "#34d399", icone: "check", luz: "52, 211, 153", serie: serieGreen },
    { label: "RED", valor: String(stats.red), cor: "#f87171", icone: "x", luz: "248, 113, 113", serie: serieRed },
    {
      label: "UNIDADES",
      valor: `${stats.unidadesLiquidas >= 0 ? "+" : ""}${stats.unidadesLiquidas.toFixed(2)}u`,
      cor: stats.unidadesLiquidas >= 0 ? "#34d399" : "#f87171",
      icone: "moedas",
      luz: stats.unidadesLiquidas >= 0 ? "52, 211, 153" : "248, 113, 113",
      serie: serieUnidades,
    },
    {
      label: "ACERTO",
      valor: stats.taxaAcerto !== null ? `${stats.taxaAcerto.toFixed(0)}%` : "—",
      cor: "#a78bfa",
      icone: "alvo",
      luz: "167, 139, 250",
      serie: serieAcerto,
    },
  ];

  // Bloco 2x2 ancorado na borda direita, alinhado com o topo do título.
  const larguraCel = 232;
  const alturaCel = 96;
  const gap = 12;
  const xBase = LARGURA - PAD - (larguraCel * 2 + gap);

  celulas.forEach((col, i) => {
    const x = xBase + (i % 2) * (larguraCel + gap);
    const cy = y + Math.floor(i / 2) * (alturaCel + gap);

    ctx.fillStyle = "rgba(255,255,255,0.038)";
    roundRect(ctx, x, cy, larguraCel, alturaCel, 18);
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.07)";
    ctx.lineWidth = 1;
    ctx.stroke();

    // Mesma luz difusa dos blocos de período, no tom da própria métrica —
    // é o que amarra os cards do topo ao resto da imagem.
    desenharLuzDoCanto(ctx, x, cy, larguraCel, alturaCel, 18, col.luz);

    // Ícone + rótulo na primeira linha; valor embaixo, à esquerda; a
    // sparkline ocupa a metade direita da linha do valor. O valor pode ser
    // largo ("+10.02u"), então a sparkline é estreita e ancorada na borda
    // direita — foi onde as duas colidiam na primeira versão.
    desenharIconeMetrica(ctx, col.icone, x + 26, cy + 28, col.cor);

    ctx.textAlign = "left";
    ctx.fillStyle = "#6b7280";
    ctx.font = "700 15px Inter, sans-serif";
    ctx.fillText(col.label, x + 46, cy + 33);

    ctx.fillStyle = col.cor;
    ctx.font = "800 34px Inter, sans-serif";
    ctx.fillText(col.valor, x + 22, cy + 76);

    // Micrográfico: a curva acumulada do dia nessa métrica, desenhada bem
    // discreta à direita do valor. Não tem eixo nem rótulo — é textura que
    // diz "subiu" ou "desceu", não um gráfico pra ler valor.
    if (col.serie && col.serie.length > 1) {
      const larguraValor = ctx.measureText(col.valor).width;
      const sparkX = Math.max(x + 34 + larguraValor, x + larguraCel - 84);
      const sparkW = x + larguraCel - 20 - sparkX;
      if (sparkW >= 34) {
        desenharMicrografico(ctx, col.serie, sparkX, cy + 50, sparkW, 22, col.cor);
      }
    }
  });

  ctx.textAlign = "left";
}

/**
 * Sparkline sem eixos: a forma da curva, nada mais.
 *
 * Normaliza a série na altura disponível e desenha uma linha fina com um
 * preenchimento quase invisível embaixo. Série constante (todos os
 * valores iguais) vira uma reta no meio, em vez de dividir por zero.
 */
function desenharMicrografico(
  ctx: CanvasRenderingContext2D,
  serie: number[],
  x: number,
  y: number,
  w: number,
  h: number,
  cor: string,
): void {
  const min = Math.min(...serie);
  const max = Math.max(...serie);
  const amplitude = max - min;

  const pontos = serie.map((v, i) => ({
    px: x + (i / (serie.length - 1)) * w,
    // amplitude 0 = série constante: fica na metade da altura.
    py: y + h - (amplitude === 0 ? h / 2 : ((v - min) / amplitude) * h),
  }));

  ctx.save();
  ctx.beginPath();
  ctx.moveTo(pontos[0].px, pontos[0].py);
  for (const p of pontos.slice(1)) ctx.lineTo(p.px, p.py);
  ctx.strokeStyle = cor;
  ctx.lineWidth = 1.8;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.globalAlpha = 0.85;
  ctx.stroke();

  // área sob a curva, quase imperceptível — dá peso ao traço sem virar
  // um gráfico preenchido.
  ctx.lineTo(pontos[pontos.length - 1].px, y + h);
  ctx.lineTo(pontos[0].px, y + h);
  ctx.closePath();
  ctx.globalAlpha = 0.1;
  ctx.fillStyle = cor;
  ctx.fill();
  ctx.restore();
}

/** Ícone do cabeçalho de período: sol para manhã/tarde, lua para noite,
 *  triângulo de aviso para as apostas sem horário. */
function desenharIconePeriodo(
  ctx: CanvasRenderingContext2D,
  id: PeriodoId,
  cx: number,
  cy: number,
  cor: string,
): void {
  ctx.save();
  ctx.strokeStyle = cor;
  ctx.fillStyle = cor;
  ctx.lineWidth = 1.8;
  ctx.lineCap = "round";

  if (id === "noite") {
    // lua crescente: disco cheio com um disco do fundo recortado em cima
    ctx.beginPath();
    ctx.arc(cx, cy, 9, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalCompositeOperation = "destination-out";
    ctx.beginPath();
    ctx.arc(cx + 5, cy - 4.5, 8, 0, Math.PI * 2);
    ctx.fill();
  } else if (id === "sem_horario") {
    // triângulo de aviso com "!"
    ctx.beginPath();
    ctx.moveTo(cx, cy - 9);
    ctx.lineTo(cx + 9.5, cy + 7.5);
    ctx.lineTo(cx - 9.5, cy + 7.5);
    ctx.closePath();
    ctx.stroke();
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(cx, cy - 2.5);
    ctx.lineTo(cx, cy + 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx, cy + 5, 1.1, 0, Math.PI * 2);
    ctx.fill();
  } else {
    // sol: disco e raios (tarde com raios mais curtos, pra diferenciar
    // da manhã sem inventar um terceiro símbolo)
    const raioDisco = 5;
    const raios = id === "manha" ? 9.5 : 8.5;
    ctx.beginPath();
    ctx.arc(cx, cy, raioDisco, 0, Math.PI * 2);
    ctx.stroke();
    for (let k = 0; k < 8; k++) {
      const ang = (k * Math.PI) / 4;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(ang) * (raioDisco + 2), cy + Math.sin(ang) * (raioDisco + 2));
      ctx.lineTo(cx + Math.cos(ang) * raios, cy + Math.sin(ang) * raios);
      ctx.stroke();
    }
  }

  ctx.restore();
}

/**
 * Uma linha de aposta dentro do card do período.
 *
 * Duas mudanças de acabamento em relação à versão anterior:
 *
 *  - o ícone do esporte não tem mais o disco colorido atrás. O ícone já é
 *    colorido (verde-limão pro tênis, laranja pro basquete) e o disco só
 *    somava uma caixa que competia com o card do período.
 *  - o status não tem mais pílula: é ícone + texto na cor do resultado.
 *    Sem o fundo a linha fica mais leve, e o ícone garante a leitura
 *    mesmo sem depender só da cor.
 */
function desenharLinhaDoJogo(
  ctx: CanvasRenderingContext2D,
  bet: Bet,
  y: number,
  blocoX: number,
): void {
  const grupo = grupoDaBet(bet);
  const cor = COR_POR_GRUPO[grupo];
  const cyCentro = y + LINHA_ALTURA / 2;

  // Horário à esquerda, em coluna própria — alinhado à direita pra que os
  // dois dígitos da hora fiquem sempre na mesma vertical.
  const xHora = blocoX + 24;
  const larguraHora = 74;
  ctx.textBaseline = "middle";
  ctx.textAlign = "right";
  if (bet.data_hora) {
    ctx.fillStyle = "#a1a7b3";
    ctx.font = "700 25px 'JetBrains Mono', monospace";
    ctx.fillText(formatarHora(bet.data_hora), xHora + larguraHora, cyCentro);
  } else {
    // Travessão CENTRADO na coluna do horário, não alinhado à direita como
    // ele: "—" é bem mais estreito que "09:39", e alinhar pela direita
    // jogava o traço pro fim da coluna, desencontrado da coluna de
    // horários logo acima.
    ctx.textAlign = "center";
    ctx.fillStyle = "#4b5563";
    ctx.font = "700 25px 'JetBrains Mono', monospace";
    ctx.fillText("—", xHora + larguraHora / 2, cyCentro);
  }
  ctx.textAlign = "left";

  // Ícone do esporte, SEM disco atrás: na cor real da bola (amarelo-limão
  // pro tênis, laranja pro basquete).
  const corEsporte = bet.esporte === "basquete" ? "#f97316" : "#d4e34a";
  const xIcone = xHora + larguraHora + 40;
  desenharIconeEsporte(ctx, bet.esporte, xIcone, cyCentro, 15, corEsporte);

  // Status à direita: mede o texto pra saber onde começa o conjunto
  // ícone+rótulo, e o texto do jogo usa o que sobrar.
  ctx.font = "700 22px Inter, sans-serif";
  const statusTextoW = ctx.measureText(cor.label).width;
  const statusW = 26 + 10 + statusTextoW; // ícone + gap + texto
  const statusX = LARGURA - PAD - 24 - statusW;

  const xTexto = xIcone + 30;
  const larguraTexto = statusX - xTexto - 24;

  // confronto
  ctx.fillStyle = "#eceef1";
  ctx.font = "700 29px Inter, sans-serif";
  ctx.fillText(encaixarConfronto(ctx, bet.jogo, larguraTexto), xTexto, cyCentro - 13);

  // mercado
  ctx.fillStyle = "#8b919c";
  ctx.font = "500 21px Inter, sans-serif";
  ctx.fillText(
    truncar(ctx, bet.mercado || "Mercado não identificado", larguraTexto),
    xTexto,
    cyCentro + 16,
  );

  // status: ícone + texto, sem nenhum fundo
  desenharIconeStatus(ctx, grupo, statusX + 13, cyCentro, cor.texto);
  ctx.fillStyle = cor.texto;
  ctx.font = "700 22px Inter, sans-serif";
  ctx.fillText(cor.label, statusX + 36, cyCentro + 1);

  ctx.textBaseline = "alphabetic";
}

/** Desenha o resumo do dia num canvas novo e devolve o elemento pronto
 *  (o chamador decide o que fazer com ele: preview, toBlob, download). */
export function desenharResumoDoDia(bets: Bet[]): HTMLCanvasElement {
  const jogos = jogosDeHoje(bets);
  const blocos = agruparPorPeriodo(jogos);

  // A imagem acompanha o conteúdo: nada de piso de 16:9. O piso existia
  // pra "garantir formato vertical", mas com poucas apostas ele deixava
  // metade da imagem vazia embaixo do rodapé (6 apostas ocupavam ~1150px
  // numa imagem de 1920). Com 1080 de largura, qualquer lista de 3+
  // apostas já sai naturalmente em retrato.
  //
  // Agora a altura soma, por bloco de período, o cabeçalho + as linhas —
  // mais o respiro entre blocos.
  const alturaLista = blocos.length
    ? blocos.reduce(
        (acc, b) => acc + PERIODO_HEADER_ALTURA + b.jogos.length * LINHA_ALTURA + PERIODO_GAP,
        0,
      )
    : LINHA_ALTURA;
  const altura = HEADER_ALTURA + alturaLista + FOOTER_ALTURA;

  const canvas = document.createElement("canvas");
  /* Escala do canvas: quantos pixels reais por unidade de layout.
   *
   * Era 2 (2160px de largura, ~11 megapixels com 20 apostas). Parece
   * "mais qualidade", mas produz o contrário no destino real: Telegram e
   * WhatsApp reduzem qualquer imagem grande na hora do envio, e quanto
   * maior o fator de redução, mais o texto se desfaz. 1.5 entrega 1620px
   * de largura — acima do que esses apps entregam ao destinatário, e
   * perto o bastante pra redução final ser suave em vez de destrutiva.
   *
   * O texto continua nítido: o desenho é vetorial, e 1.5x já dá
   * subpixel suficiente pra fonte de 21px do mercado. */
  const escala = 1.5;
  canvas.width = Math.round(LARGURA * escala);
  canvas.height = Math.round(altura * escala);
  const ctx = canvas.getContext("2d")!;
  ctx.scale(escala, escala);

  // --- fundo (mesmo tom do painel dark) ---
  const fundo = ctx.createLinearGradient(0, 0, 0, altura);
  fundo.addColorStop(0, "#111418");
  fundo.addColorStop(1, "#0d0f11");
  ctx.fillStyle = fundo;
  ctx.fillRect(0, 0, LARGURA, altura);

  // --- header ---
  // "04 SET · QUINTA-FEIRA": data curta e dia da semana em caixa alta,
  // que situa o resumo sem gastar a linha inteira com "de setembro de".
  const agora = new Date();
  // A data do cabeçalho é a do DIA RETRATADO, não a de hoje: com o painel
  // filtrado por outro dia, o título mostrava "04 DE SET" acima de jogos
  // de 05. O "T12:00" evita que o fuso jogue a data pro dia anterior.
  const dataDoResumo = new Date(`${diaRetratado(jogos)}T12:00:00-03:00`);
  const diaMes = new Intl.DateTimeFormat("pt-BR", {
    timeZone: FUSO_PADRAO, day: "2-digit", month: "short",
  }).format(dataDoResumo).replace(".", "").toUpperCase();
  const diaSemana = new Intl.DateTimeFormat("pt-BR", {
    timeZone: FUSO_PADRAO, weekday: "long",
  }).format(dataDoResumo).toUpperCase();

  ctx.fillStyle = "#d7f24d";
  ctx.font = "700 30px Inter, sans-serif";
  ctx.textBaseline = "alphabetic";
  ctx.fillText("CANSADÃO APOSTAS", PAD, 76);

  ctx.fillStyle = "#eceef1";
  ctx.font = "800 52px Inter, sans-serif";
  ctx.fillText("Jogos do dia", PAD, 140);

  ctx.fillStyle = "#6b7280";
  ctx.font = "700 22px Inter, sans-serif";
  ctx.fillText(`${diaMes}  ·  ${diaSemana}`, PAD, 176);

  // Linha de resumo embaixo da data — o mesmo trio do rodapé, aqui pra
  // quem lê só o topo da imagem já saber o tamanho do dia.
  const nAoVivoTopo = jogos.filter((b) => grupoDaBet(b) === "ao_vivo").length;
  const nAbertoTopo = jogos.filter((b) => grupoDaBet(b) === "pendentes").length;
  const resumo: string[] = [`${jogos.length} ${jogos.length === 1 ? "jogo" : "jogos"}`];
  if (nAoVivoTopo > 0) resumo.push(`${nAoVivoTopo} ao vivo`);
  if (nAbertoTopo > 0) resumo.push(`${nAbertoTopo} em aberto`);

  ctx.fillStyle = "#6b7280";
  ctx.font = "500 22px Inter, sans-serif";
  ctx.fillText(resumo.join("   •   "), PAD, 212);

  // --- estatísticas: grid 2x2 à direita do título ---
  desenharEstatisticas(ctx, jogos, 56);

  if (jogos.length === 0) {
    ctx.fillStyle = "#6b7280";
    ctx.font = "500 28px Inter, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("Nenhum jogo hoje.", LARGURA / 2, HEADER_ALTURA + LINHA_ALTURA / 2);
    ctx.textAlign = "left";
  }

  // --- blocos por período (MANHÃ / TARDE / NOITE / SEM HORÁRIO) ---
  let cursorY = HEADER_ALTURA;

  for (const { periodo, jogos: doPeriodo } of blocos) {
    // Cabeçalho do período: ícone + rótulo à esquerda, contagem à direita.
    const headerCy = cursorY + PERIODO_HEADER_ALTURA / 2;
    desenharIconePeriodo(ctx, periodo.id, PAD + 14, headerCy, periodo.cor);

    ctx.fillStyle = periodo.cor;
    ctx.font = "800 24px Inter, sans-serif";
    ctx.textBaseline = "middle";
    const xLabel = PAD + 38;
    ctx.fillText(periodo.label, xLabel, headerCy + 1);
    const larguraLabel = ctx.measureText(periodo.label).width;

    // Contagem numa cápsula discreta, logo APÓS o rótulo (não na borda
    // direita): assim ela lê como parte do título da seção — "MANHÃ, 5
    // jogos" — em vez de um número solto do outro lado da imagem.
    const cont = `${doPeriodo.length} ${doPeriodo.length === 1 ? "jogo" : "jogos"}`;
    ctx.font = "600 20px Inter, sans-serif";
    const contW = ctx.measureText(cont).width + 26;
    const contX = xLabel + larguraLabel + 14;
    ctx.fillStyle = "rgba(255,255,255,0.05)";
    roundRect(ctx, contX, headerCy - 15, contW, 30, 15);
    ctx.fill();
    ctx.fillStyle = "#6b7280";
    ctx.textAlign = "center";
    ctx.fillText(cont, contX + contW / 2, headerCy + 1);
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";

    // Card de vidro que envolve as linhas do período.
    const blocoY = cursorY + PERIODO_HEADER_ALTURA;
    const blocoH = doPeriodo.length * LINHA_ALTURA;
    const blocoX = PAD;
    const blocoW = LARGURA - PAD * 2;

    ctx.fillStyle = "rgba(255,255,255,0.022)";
    roundRect(ctx, blocoX, blocoY, blocoW, blocoH, BLOCO_RAIO);
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.lineWidth = 1;
    ctx.stroke();

    // A luz difusa do canto superior esquerdo, na cor do período.
    desenharLuzDoCanto(ctx, blocoX, blocoY, blocoW, blocoH, BLOCO_RAIO, periodo.luz);

    doPeriodo.forEach((bet, i) => {
      const y = blocoY + i * LINHA_ALTURA;
      desenharLinhaDoJogo(ctx, bet, y, blocoX);

      // separador fino entre linhas do MESMO bloco
      if (i < doPeriodo.length - 1) {
        ctx.strokeStyle = "rgba(255,255,255,0.05)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(blocoX + 24, y + LINHA_ALTURA);
        ctx.lineTo(blocoX + blocoW - 24, y + LINHA_ALTURA);
        ctx.stroke();
      }
    });

    cursorY = blocoY + blocoH + PERIODO_GAP;
  }

  // --- footer ---
  // Sem linha divisória: os blocos de período já delimitam a lista, e o
  // traço de ponta a ponta cortava a imagem num ponto onde não há
  // separação de conteúdo pra marcar.
  const footerY = altura - FOOTER_ALTURA / 2;

  // Rodapé centralizado e discreto: quando a imagem foi gerada, mais o
  // mesmo trio de contagens do topo. Sem repetir o nome da marca (já está
  // no cabeçalho) e sem ícone decorativo.
  const horaGeracao = new Intl.DateTimeFormat("pt-BR", {
    timeZone: FUSO_PADRAO, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(agora).replace(", ", " às ");

  const partes: string[] = [
    `Atualizado em ${horaGeracao}`,
    `${jogos.length} ${jogos.length === 1 ? "jogo" : "jogos"}`,
  ];
  if (nAoVivoTopo > 0) partes.push(`${nAoVivoTopo} ao vivo`);
  if (nAbertoTopo > 0) partes.push(`${nAbertoTopo} em aberto`);

  ctx.font = "500 22px Inter, sans-serif";
  ctx.textBaseline = "middle";
  ctx.textAlign = "center";
  ctx.fillStyle = "#6b7280";
  ctx.fillText(partes.join("  ·  "), LARGURA / 2, footerY);
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";

  return canvas;
}

/** Converte o canvas em blob PNG. O blob (e não só o object URL) é o que
 *  as APIs de clipboard e de compartilhamento exigem. */
export function canvasParaBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error("Falha ao gerar a imagem."));
        return;
      }
      resolve(blob);
    }, "image/png");
  });
}

/** Converte o canvas em blob PNG e devolve como object URL, pronto pra usar
 *  em <img src> (preview) ou num link de download. */
export async function canvasParaObjectUrl(canvas: HTMLCanvasElement): Promise<string> {
  return URL.createObjectURL(await canvasParaBlob(canvas));
}
