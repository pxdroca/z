// Gera a imagem-resumo "jogos do dia" (formato vertical, pensado pra ser
// compartilhado no grupo do Telegram e ficar legível numa tela de celular
// — nunca horizontal). Desenha tudo num <canvas> no navegador: o projeto
// não tem nenhuma lib de imagem (canvas/sharp/puppeteer) instalada, e
// Canvas 2D nativo é suficiente pro layout simples de lista que precisamos.
import { grupoDaBet, type GrupoId } from "./agrupamento";
import { calcularEstatisticas } from "./estatisticas";
import type { Bet } from "./types";

const FUSO_PADRAO = "America/Sao_Paulo";

/** "Hoje" no fuso de Brasília — mesmo critério usado no filtro padrão do
 *  painel (app/page.tsx), pra bater com o que o usuário já vê como "hoje". */
function diaNoFuso(iso: string, fuso: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: fuso, year: "numeric", month: "2-digit", day: "2-digit" }).format(
    new Date(iso)
  );
}

/** Jogos de hoje, ordenados por horário — apostas sem data_hora (erro de
 *  extração, não encontrada) não têm "hoje" pra comparar, então ficam fora
 *  do resumo (não fazem sentido numa imagem organizada por horário). */
export function jogosDeHoje(bets: Bet[]): Bet[] {
  const hoje = new Intl.DateTimeFormat("en-CA", {
    timeZone: FUSO_PADRAO,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());

  return bets
    .filter((b) => b.data_hora && diaNoFuso(b.data_hora, FUSO_PADRAO) === hoje)
    .slice()
    .sort((a, b) => new Date(a.data_hora!).getTime() - new Date(b.data_hora!).getTime());
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
const HEADER_ALTURA = 220;
/** Faixa de estatísticas entre o header e a lista. */
const STATS_ALTURA = 150;
const FOOTER_ALTURA = 90;

/**
 * Desenha a faixa de estatísticas do dia (green, red, unidades, taxa de
 * acerto) logo abaixo do título.
 *
 * As unidades trazem o ROI como linha secundária, no lugar de virar um 5º
 * card: são a mesma informação (retorno) em unidades e em %, e 4 colunas
 * em 1080px deixam cada número com folga pra ser lido de longe — com 5 o
 * texto começaria a apertar.
 *
 * Os números vêm de calcularEstatisticas, o MESMO cálculo dos cards do
 * painel, pra imagem e tela nunca divergirem.
 */
function desenharEstatisticas(ctx: CanvasRenderingContext2D, jogos: Bet[], y: number): void {
  const stats = calcularEstatisticas(jogos);

  const colunas: { label: string; valor: string; sub: string | null; cor: string }[] = [
    { label: "GREEN", valor: String(stats.green), sub: null, cor: "#34d399" },
    { label: "RED", valor: String(stats.red), sub: null, cor: "#f87171" },
    {
      label: "UNIDADES",
      valor: `${stats.unidadesLiquidas >= 0 ? "+" : ""}${stats.unidadesLiquidas.toFixed(2)}u`,
      sub: stats.roi !== null ? `ROI ${stats.roi.toFixed(1)}%` : null,
      cor: stats.unidadesLiquidas >= 0 ? "#34d399" : "#f87171",
    },
    {
      label: "TAXA DE ACERTO",
      valor: stats.taxaAcerto !== null ? `${stats.taxaAcerto.toFixed(0)}%` : "—",
      sub: stats.green + stats.red > 0 ? `${stats.green}/${stats.green + stats.red}` : null,
      cor: "#a78bfa",
    },
  ];

  const larguraUtil = LARGURA - PAD * 2;
  const larguraCol = larguraUtil / colunas.length;
  const alturaCaixa = STATS_ALTURA - 34;

  colunas.forEach((col, i) => {
    const x = PAD + i * larguraCol;
    const cx = x + larguraCol / 2;

    // Cartão de vidro escuro, com um fio da cor de acento no topo — o
    // mesmo vocabulário dos cards de métrica do painel.
    ctx.fillStyle = "rgba(255,255,255,0.038)";
    roundRect(ctx, x + 6, y, larguraCol - 12, alturaCaixa, 18);
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.07)";
    ctx.lineWidth = 1;
    ctx.stroke();

    ctx.textAlign = "center";

    ctx.fillStyle = "#6b7280";
    ctx.font = "700 19px Inter, sans-serif";
    ctx.fillText(col.label, cx, y + 34);

    ctx.fillStyle = col.cor;
    ctx.font = "800 44px Inter, sans-serif";
    ctx.fillText(col.valor, cx, y + 84);

    if (col.sub) {
      ctx.fillStyle = "#6b7280";
      ctx.font = "500 20px Inter, sans-serif";
      ctx.fillText(col.sub, cx, y + 108);
    }
  });

  ctx.textAlign = "left";
}

/** Desenha o resumo do dia num canvas novo e devolve o elemento pronto
 *  (o chamador decide o que fazer com ele: preview, toBlob, download). */
export function desenharResumoDoDia(bets: Bet[]): HTMLCanvasElement {
  const jogos = jogosDeHoje(bets);
  // Altura mínima 16:9 "invertido" (retrato, tipo Stories) — em dias com
  // poucos jogos a lista sozinha não preenche o suficiente pra imagem
  // parecer vertical; o espaço sobrando fica como respiro abaixo do rodapé
  // em vez de deixar a imagem quase quadrada.
  const alturaConteudo =
    HEADER_ALTURA + STATS_ALTURA + Math.max(jogos.length, 1) * LINHA_ALTURA + FOOTER_ALTURA;
  const alturaMinimaRetrato = Math.round((LARGURA * 16) / 9);
  const altura = Math.max(alturaConteudo, alturaMinimaRetrato);

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

  // --- faixa de estatísticas (logo abaixo do título) ---
  desenharEstatisticas(ctx, jogos, HEADER_ALTURA - 24);

  // Início da lista, já descontando a faixa de estatísticas.
  const listaY = HEADER_ALTURA + STATS_ALTURA;

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

    // ícone do esporte, num círculo colorido à esquerda — cor própria por
    // esporte (âmbar/tênis, azul/basquete) pra ser reconhecível de relance,
    // sem precisar ler o nome do confronto.
    const corEsporte = bet.esporte === "basquete" ? "#60a5fa" : "#fbbf24";
    const fundoEsporte = bet.esporte === "basquete" ? "rgba(96, 165, 250, 0.14)" : "rgba(251, 191, 36, 0.14)";
    ctx.fillStyle = fundoEsporte;
    ctx.beginPath();
    ctx.arc(PAD + 34, cyCentro, 34, 0, Math.PI * 2);
    ctx.fill();
    desenharIconeEsporte(ctx, bet.esporte, PAD + 34, cyCentro, 21, corEsporte);

    const xTexto = PAD + 92;
    const larguraBadge = 190;
    const larguraTexto = LARGURA - PAD - xTexto - larguraBadge - 24;

    // horário — 30px (era 22) e em text-secondary (era muted): é a
    // primeira coisa que se procura na imagem ("que jogo é agora?") e
    // ficava menor E mais apagado que o mercado, que é secundário.
    ctx.fillStyle = "#a1a7b3";
    ctx.font = "700 30px 'JetBrains Mono', monospace";
    ctx.fillText(bet.data_hora ? formatarHora(bet.data_hora) : "--:--", xTexto, cyCentro - 24);

    // confronto (jogo)
    ctx.fillStyle = "#eceef1";
    ctx.font = "700 32px Inter, sans-serif";
    ctx.fillText(truncar(ctx, bet.jogo, larguraTexto), xTexto, cyCentro + 8);

    // mercado
    ctx.fillStyle = "#a1a7b3";
    ctx.font = "500 24px Inter, sans-serif";
    ctx.fillText(truncar(ctx, bet.mercado || "Mercado não identificado", larguraTexto), xTexto, cyCentro + 40);

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
