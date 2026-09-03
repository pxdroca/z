import { NextRequest, NextResponse } from "next/server";
import { getBet, updateResultado, updateStatus } from "@/lib/bets";
import { BET_STATUS_VALUES, RESULTADO_APOSTA_VALUES } from "@/lib/types";

export async function PATCH(request: NextRequest, ctx: RouteContext<"/api/bets/[id]">) {
  const { id: idParam } = await ctx.params;
  const id = Number(idParam);
  if (!Number.isInteger(id)) {
    return NextResponse.json({ error: "id inválido." }, { status: 400 });
  }

  let body: { status?: unknown; resultado?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Corpo da requisição não é um JSON válido." }, { status: 400 });
  }

  const existente = await getBet(id);
  if (!existente) {
    return NextResponse.json({ error: "Aposta não encontrada." }, { status: 404 });
  }

  // Duas UPDATEs de coluna única em sequência (não monta SQL dinâmico
  // multi-coluna) — mesmo padrão de update_status/update_resultado
  // separados em database.py, só que expostos numa rota só.
  if (body.status !== undefined) {
    if (typeof body.status !== "string" || !(BET_STATUS_VALUES as readonly string[]).includes(body.status)) {
      return NextResponse.json({ error: "status inválido." }, { status: 400 });
    }
    await updateStatus(id, body.status as (typeof BET_STATUS_VALUES)[number]);
  }

  if (body.resultado !== undefined) {
    if (typeof body.resultado !== "string" || !(RESULTADO_APOSTA_VALUES as readonly string[]).includes(body.resultado)) {
      return NextResponse.json({ error: "resultado inválido." }, { status: 400 });
    }
    await updateResultado(id, body.resultado as (typeof RESULTADO_APOSTA_VALUES)[number]);
  }

  const atualizado = await getBet(id);
  return NextResponse.json({ bet: atualizado });
}
