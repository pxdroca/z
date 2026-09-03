import { NextRequest, NextResponse } from "next/server";
import { autoConferirResultados, autoPromoteAoVivo, listBets } from "@/lib/bets";
import { BET_STATUS_VALUES, type BetStatus } from "@/lib/types";

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);

  const statusParam = searchParams.getAll("status");
  const status: BetStatus[] = statusParam.filter((s): s is BetStatus =>
    (BET_STATUS_VALUES as readonly string[]).includes(s)
  );

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
