import { NextRequest, NextResponse } from "next/server";
import { autoConferirResultados, autoPromoteAoVivo, insertBetManual, listBets } from "@/lib/bets";
import {
  BET_STATUS_VALUES,
  ESPORTE_VALUES,
  RESULTADO_APOSTA_VALUES,
  type BetStatus,
  type Esporte,
  STATUS_VISIVEIS,
  type ResultadoAposta,
} from "@/lib/types";

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);

  const statusParam = searchParams.getAll("status");
  const pedidos: BetStatus[] = statusParam.filter((s): s is BetStatus =>
    (BET_STATUS_VALUES as readonly string[]).includes(s)
  );
  // Sem status na query, lista os visíveis (não a tabela inteira): assim
  // erro_extracao — mensagens que não eram tip — nunca aparece por
  // omissão do cliente, e não só quando a página lembra de filtrar.
  const status: BetStatus[] = pedidos.length > 0 ? pedidos : STATUS_VISIVEIS;

  const dateFrom = searchParams.get("from") ?? undefined;
  const dateTo = searchParams.get("to") ?? undefined;

  try {
    // Mesma ordem/efeito de main() no app.py: primeiro auto-promove
    // agendada->ao_vivo pelo relógio, depois lista, depois auto-confere o
    // resultado das que já encerraram e ainda estão pendentes.
    await autoPromoteAoVivo();
    const bets = await listBets({ status, dateFrom, dateTo });
    await autoConferirResultados(bets);
    return NextResponse.json({ bets });
  } catch (err) {
    console.error("GET /api/bets falhou:", err);
    return NextResponse.json({ error: "Erro ao consultar apostas." }, { status: 500 });
  }
}

/** Texto opcional: string não-vazia ou null. */
function textoOpcional(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const t = v.trim();
  return t === "" ? null : t;
}

/**
 * Cria uma aposta à mão. Complementa o pipeline automático em vez de
 * substituí-lo: serve para jogos que o SofaScore não confirma (liga fora do
 * endpoint de jogos do dia) e que por isso nunca sairiam de
 * "nao_encontrada" — ver insertBetManual em lib/bets.ts.
 */
export async function POST(request: NextRequest) {
  let body: Record<string, unknown>;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Corpo da requisição não é um JSON válido." }, { status: 400 });
  }

  const jogador1 = textoOpcional(body.jogador1);
  const jogador2 = textoOpcional(body.jogador2);
  // Os dois são obrigatórios numa aposta lançada à mão: quem digita sabe o
  // confronto. O "?" existe só pro caminho automático, quando o tipster
  // cita apenas o favorito e o SofaScore não confirma o adversário.
  if (!jogador1 || !jogador2) {
    return NextResponse.json({ error: "Informe os dois jogadores/times." }, { status: 400 });
  }

  let odd: number | null = null;
  if (body.odd !== undefined && body.odd !== null && body.odd !== "") {
    odd = Number(body.odd);
    if (!Number.isFinite(odd) || odd <= 1) {
      return NextResponse.json({ error: "Odd inválida (precisa ser maior que 1)." }, { status: 400 });
    }
  }

  let unidades = 1;
  if (body.unidades !== undefined && body.unidades !== null && body.unidades !== "") {
    unidades = Number(body.unidades);
    if (!Number.isFinite(unidades) || unidades <= 0) {
      return NextResponse.json({ error: "Unidades inválidas (precisa ser maior que 0)." }, { status: 400 });
    }
  }

  const esporte: Esporte = (ESPORTE_VALUES as readonly string[]).includes(String(body.esporte))
    ? (body.esporte as Esporte)
    : "tenis";

  // Default "nao_encontrada": é o status honesto para uma aposta que o
  // pipeline não confirmou num provedor. Quem lança sabe o resultado e
  // costuma já escolher encerrada+green/red no formulário.
  const status: BetStatus = (BET_STATUS_VALUES as readonly string[]).includes(String(body.status))
    ? (body.status as BetStatus)
    : "nao_encontrada";

  const resultado: ResultadoAposta = (RESULTADO_APOSTA_VALUES as readonly string[]).includes(
    String(body.resultado)
  )
    ? (body.resultado as ResultadoAposta)
    : "pendente";

  let dataHora: string | null = null;
  const dataBruta = textoOpcional(body.data_hora);
  if (dataBruta) {
    const d = new Date(dataBruta);
    if (Number.isNaN(d.getTime())) {
      return NextResponse.json({ error: "Data/hora inválida." }, { status: 400 });
    }
    dataHora = d.toISOString();
  }

  try {
    const bet = await insertBetManual({
      jogador1,
      jogador2,
      mercado: textoOpcional(body.mercado),
      odd,
      unidades,
      esporte,
      torneio: textoOpcional(body.torneio),
      data_hora: dataHora,
      status,
      resultado,
    });
    return NextResponse.json({ bet }, { status: 201 });
  } catch (err) {
    console.error("POST /api/bets falhou:", err);
    return NextResponse.json({ error: "Erro ao salvar a aposta." }, { status: 500 });
  }
}
