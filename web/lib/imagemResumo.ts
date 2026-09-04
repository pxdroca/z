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
 * Idade máxima de uma aposta SEM horário confirmado para ela entrar no
 * resumo do dia.
 *
 * 24h: cobre a tip que o tipster manda de madrugada para o jogo do dia
 * seguinte (as de 04/09 foram lançadas 23:28 e 23:42 de 03/09, hora de
 * Brasília) sem alcançar aposta antiga que segue em aberto — a múltipla
 * de odd 129 de 03/09, por exemplo, que sem esse teto aparecia no resumo
 * de 04/09 como "pendente".
 */
const JANELA_SEM_HORARIO = 24 * 60 * 60 * 1000;

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

  const agora = Date.now();

  const doDia = bets.filter((b) => {
    if (b.data_hora) return diaNoFuso(b.data_hora, FUSO_PADRAO) === hoje;
    if (!b.criado_em) return false;
    // Sem horário confirmado: vale pela idade da aposta.
    return agora - new Date(b.criado_em).getTime() <= JANELA_SEM_HORARIO;
  });

  const instante = (b: Bet): number =>
    b.data_hora ? new Date(b.data_hora).getTime() : Number.POSITIVE_INFINITY;

  return doDia.slice().sort((a, b) => instante(a) - instante(b));
}

function formatarHora(iso: string): string {
  return new Intl.DateTimeFormat("pt-BR", { timeZone: FUSO_PADRAO, hour: "2-digit", minute: "2-digit" }).format(
    new Date(iso)
  );
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
const LINHA_ALTURA = 132;
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

  const celulas: { label: string; valor: string; cor: string }[] = [
    { label: "GREEN", valor: String(stats.green), cor: "#34d399" },
    { label: "RED", valor: String(stats.red), cor: "#f87171" },
    {
      label: "UNIDADES",
      valor: `${stats.unidadesLiquidas >= 0 ? "+" : ""}${stats.unidadesLiquidas.toFixed(2)}u`,
      cor: stats.unidadesLiquidas >= 0 ? "#34d399" : "#f87171",
    },
    {
      label: "ACERTO",
      valor: stats.taxaAcerto !== null ? `${stats.taxaAcerto.toFixed(0)}%` : "—",
      cor: "#a78bfa",
    },
  ];

  // Bloco 2x2 ancorado na borda direita, alinhado com o topo do título.
  const larguraCel = 190;
  const alturaCel = 82;
  const gap = 10;
  const xBase = LARGURA - PAD - (larguraCel * 2 + gap);

  celulas.forEach((col, i) => {
    const x = xBase + (i % 2) * (larguraCel + gap);
    const cy = y + Math.floor(i / 2) * (alturaCel + gap);
    const cx = x + larguraCel / 2;

    ctx.fillStyle = "rgba(255,255,255,0.038)";
    roundRect(ctx, x, cy, larguraCel, alturaCel, 16);
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.07)";
    ctx.lineWidth = 1;
    ctx.stroke();

    ctx.textAlign = "center";

    // Offsets a partir de cy (o topo DESTA célula), não de y. Sem a linha
    // secundária, o número passa a ficar centralizado na altura da caixa.
    ctx.fillStyle = "#6b7280";
    ctx.font = "700 16px Inter, sans-serif";
    ctx.fillText(col.label, cx, cy + 28);

    ctx.fillStyle = col.cor;
    ctx.font = "800 34px Inter, sans-serif";
    ctx.fillText(col.valor, cx, cy + 66);
  });

  ctx.textAlign = "left";
}

/** Desenha o resumo do dia num canvas novo e devolve o elemento pronto
 *  (o chamador decide o que fazer com ele: preview, toBlob, download). */
export function desenharResumoDoDia(bets: Bet[]): HTMLCanvasElement {
  const jogos = jogosDeHoje(bets);
  // A imagem acompanha o conteúdo: nada de piso de 16:9. O piso existia
  // pra "garantir formato vertical", mas com poucas apostas ele deixava
  // metade da imagem vazia embaixo do rodapé (6 apostas ocupavam ~1150px
  // numa imagem de 1920). Com 1080 de largura, qualquer lista de 3+
  // apostas já sai naturalmente em retrato.
  const altura = HEADER_ALTURA + Math.max(jogos.length, 1) * LINHA_ALTURA + FOOTER_ALTURA;

  const canvas = document.createElement("canvas");
  const escala = 2; // desenha em 2x e escala via CSS/atributo — texto nítido em telas retina
  canvas.width = LARGURA * escala;
  canvas.height = altura * escala;
  const ctx = canvas.getContext("2d")!;
  ctx.scale(escala, escala);

  // --- fundo (mesmo tom do painel dark) ---
  const fundo = ctx.createLinearGradient(0, 0, 0, altura);
  fundo.addColorStop(0, "#111418");
  fundo.addColorStop(1, "#0d0f11");
  ctx.fillStyle = fundo;
  ctx.fillRect(0, 0, LARGURA, altura);

  // --- header ---
  const hojeLabel = new Intl.DateTimeFormat("pt-BR", {
    timeZone: FUSO_PADRAO,
    day: "2-digit",
    month: "long",
    year: "numeric",
  }).format(new Date());

  ctx.fillStyle = "#d7f24d";
  ctx.font = "700 30px Inter, sans-serif";
  ctx.textBaseline = "alphabetic";
  ctx.fillText("CANSADÃO APOSTAS", PAD, 76);

  ctx.fillStyle = "#eceef1";
  ctx.font = "800 52px Inter, sans-serif";
  ctx.fillText("Jogos do dia", PAD, 140);

  ctx.fillStyle = "#a1a7b3";
  ctx.font = "500 26px Inter, sans-serif";
  const dataCapitalizada = hojeLabel.charAt(0).toUpperCase() + hojeLabel.slice(1);
  ctx.fillText(dataCapitalizada, PAD, 178);

  // --- estatísticas: grid 2x2 à direita do título ---
  desenharEstatisticas(ctx, jogos, 56);

  const listaY = HEADER_ALTURA;

  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(PAD, listaY - 8);
  ctx.lineTo(LARGURA - PAD, listaY - 8);
  ctx.stroke();

  if (jogos.length === 0) {
    ctx.fillStyle = "#6b7280";
    ctx.font = "500 28px Inter, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("Nenhum jogo hoje.", LARGURA / 2, listaY + LINHA_ALTURA / 2);
    ctx.textAlign = "left";
  }

  // --- linhas, 1 por jogo ---
  jogos.forEach((bet, i) => {
    const y = listaY + i * LINHA_ALTURA;
    const grupo = grupoDaBet(bet);
    const cor = COR_POR_GRUPO[grupo];

    // faixa de fundo alternada (zebra) — ajuda a separar linhas visualmente
    if (i % 2 === 1) {
      ctx.fillStyle = "rgba(255,255,255,0.02)";
      ctx.fillRect(0, y, LARGURA, LINHA_ALTURA);
    }

    const cyCentro = y + LINHA_ALTURA / 2;

    // Ícone do esporte num círculo colorido à esquerda, na COR REAL DA
    // BOLA: laranja pro basquete, amarelo-limão pro tênis. Antes o
    // basquete era azul, que não lembra nada da bola. As duas cores são
    // distinguíveis entre si e não colidem com as dos badges de resultado
    // (verde/vermelho/âmbar do "ao vivo" ficam à direita, longe do ícone).
    const corEsporte = bet.esporte === "basquete" ? "#f97316" : "#d4e34a";
    const fundoEsporte =
      bet.esporte === "basquete" ? "rgba(249, 115, 22, 0.16)" : "rgba(212, 227, 74, 0.14)";
    ctx.fillStyle = fundoEsporte;
    ctx.beginPath();
    ctx.arc(PAD + 34, cyCentro, 34, 0, Math.PI * 2);
    ctx.fill();
    desenharIconeEsporte(ctx, bet.esporte, PAD + 34, cyCentro, 21, corEsporte);

    const xTexto = PAD + 92;
    const larguraBadge = 190;
    const larguraTexto = LARGURA - PAD - xTexto - larguraBadge - 24;

    // Sem horário confirmado, a linha não desenha nada no lugar dele: um
    // "--:--" só chama atenção pra informação que falta. O bloco de texto
    // (jogo + mercado) sobe pra ocupar o espaço e fica centrado na linha,
    // em vez de deixar um vazio no topo.
    const temHora = Boolean(bet.data_hora);
    const deslocamento = temHora ? 0 : 16;

    if (temHora) {
      // horário — 26px e em text-secondary. Era 22px/muted (menor e mais
      // apagado que o mercado, que é secundário); 30px passou do ponto e
      // competia com o nome do confronto.
      ctx.fillStyle = "#a1a7b3";
      ctx.font = "700 26px 'JetBrains Mono', monospace";
      ctx.fillText(formatarHora(bet.data_hora!), xTexto, cyCentro - 24);
    }

    // confronto (jogo)
    ctx.fillStyle = "#eceef1";
    ctx.font = "700 32px Inter, sans-serif";
    ctx.fillText(encaixarConfronto(ctx, bet.jogo, larguraTexto), xTexto, cyCentro + 8 - deslocamento);

    // mercado
    ctx.fillStyle = "#a1a7b3";
    ctx.font = "500 24px Inter, sans-serif";
    ctx.fillText(
      truncar(ctx, bet.mercado || "Mercado não identificado", larguraTexto),
      xTexto,
      cyCentro + 40 - deslocamento,
    );

    // badge de resultado, à direita
    const badgeW = 172;
    const badgeH = 52;
    const badgeX = LARGURA - PAD - badgeW;
    const badgeY = cyCentro - badgeH / 2;
    ctx.fillStyle = cor.fundo;
    roundRect(ctx, badgeX, badgeY, badgeW, badgeH, badgeH / 2);
    ctx.fill();
    ctx.fillStyle = cor.texto;
    ctx.font = "700 24px Inter, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(cor.label, badgeX + badgeW / 2, badgeY + badgeH / 2 + 1);
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";

    // separador fino entre linhas
    if (i < jogos.length - 1) {
      ctx.strokeStyle = "rgba(255,255,255,0.05)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(PAD, y + LINHA_ALTURA);
      ctx.lineTo(LARGURA - PAD, y + LINHA_ALTURA);
      ctx.stroke();
    }
  });

  // --- footer ---
  const footerY = altura - FOOTER_ALTURA / 2;
  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  ctx.beginPath();
  ctx.moveTo(PAD, altura - FOOTER_ALTURA);
  ctx.lineTo(LARGURA - PAD, altura - FOOTER_ALTURA);
  ctx.stroke();

  // Green/red agora vivem na faixa de estatísticas do topo; repetir aqui
  // seria a mesma contagem duas vezes na mesma imagem. O rodapé fica com
  // o que o topo não diz: quantos jogos a lista tem e quantos ainda estão
  // em aberto.
  const nAoVivo = jogos.filter((b) => grupoDaBet(b) === "ao_vivo").length;
  const nPendente = jogos.filter((b) => grupoDaBet(b) === "pendentes").length;

  const partes: string[] = [`${jogos.length} ${jogos.length === 1 ? "jogo" : "jogos"}`];
  if (nAoVivo > 0) partes.push(`${nAoVivo} ao vivo`);
  if (nPendente > 0) partes.push(`${nPendente} em aberto`);

  ctx.font = "600 26px Inter, sans-serif";
  ctx.textBaseline = "middle";
  ctx.fillStyle = "#6b7280";
  ctx.fillText(partes.join("  ·  "), PAD, footerY);
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
