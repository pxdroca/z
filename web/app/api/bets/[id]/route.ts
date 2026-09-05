import { NextRequest, NextResponse } from "next/server";
import { getBet, updateBetCampos, type CamposEditaveis } from "@/lib/bets";
import { BET_STATUS_VALUES, ESPORTE_VALUES, RESULTADO_APOSTA_VALUES } from "@/lib/types";

/** Texto opcional: "" vira NULL (é como o banco representa "sem valor"),
 *  e qualquer outra coisa é aparada. Devolve `undefined` quando o campo
 *  não veio no corpo — o UPDATE ignora esses. */
function textoOpcional(valor: unknown, campo: string): string | null | undefined | Error {
  if (valor === undefined) return undefined;
  if (valor === null) return null;
  if (typeof valor !== "string") return new Error(`${campo} precisa ser texto.`);
  const limpo = valor.trim();
  return limpo === "" ? null : limpo;
}

/** Número opcional, aceitando "" como "apagar". Rejeita texto não
 *  numérico em vez de silenciosamente virar NaN. */
function numeroOpcional(
  valor: unknown,
  campo: string,
  { min, max }: { min?: number; max?: number } = {}
): number | null | undefined | Error {
  if (valor === undefined) return undefined;
  if (valor === null || valor === "") return null;
  const n = typeof valor === "number" ? valor : Number(valor);
  if (!Number.isFinite(n)) return new Error(`${campo} precisa ser um número.`);
  if (min !== undefined && n < min) return new Error(`${campo} não pode ser menor que ${min}.`);
  if (max !== undefined && n > max) return new Error(`${campo} não pode ser maior que ${max}.`);
  return n;
}

export async function PATCH(request: NextRequest, ctx: RouteContext<"/api/bets/[id]">) {
  const { id: idParam } = await ctx.params;
  const id = Number(idParam);
  if (!Number.isInteger(id)) {
    return NextResponse.json({ error: "id inválido." }, { status: 400 });
  }

  let body: Record<string, unknown>;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Corpo da requisição não é um JSON válido." }, { status: 400 });
  }

  const existente = await getBet(id);
  if (!existente) {
    return NextResponse.json({ error: "Aposta não encontrada." }, { status: 404 });
  }

  const campos: CamposEditaveis = {};
  const erro = (msg: string) => NextResponse.json({ error: msg }, { status: 400 });

  // --- enums: só valores conhecidos ---
  if (body.status !== undefined) {
    if (typeof body.status !== "string" || !(BET_STATUS_VALUES as readonly string[]).includes(body.status)) {
      return erro("status inválido.");
    }
    campos.status = body.status as CamposEditaveis["status"];
  }

  if (body.resultado !== undefined) {
    if (
      typeof body.resultado !== "string" ||
      !(RESULTADO_APOSTA_VALUES as readonly string[]).includes(body.resultado)
    ) {
      return erro("resultado inválido.");
    }
    campos.resultado = body.resultado as CamposEditaveis["resultado"];
  }

  if (body.esporte !== undefined) {
    if (typeof body.esporte !== "string" || !(ESPORTE_VALUES as readonly string[]).includes(body.esporte)) {
      return erro("esporte inválido.");
    }
    campos.esporte = body.esporte;
  }

  // --- textos ---
  // jogador1 é o único obrigatório: sem ele o card fica sem título.
  if (body.jogador1 !== undefined) {
    if (typeof body.jogador1 !== "string" || body.jogador1.trim() === "") {
      return erro("jogador1 não pode ficar vazio.");
    }
    campos.jogador1 = body.jogador1.trim();
  }

  // jogador2 é NOT NULL no banco (como jogador1), mas PODE ficar vazio —
  // é o caso da múltipla, que não tem um confronto único. Vazio vira ""
  // e não NULL, senão o UPDATE viola a constraint (erro real ao editar a
  // múltipla #131).
  if (body.jogador2 !== undefined) {
    if (body.jogador2 === null) {
      campos.jogador2 = "";
    } else if (typeof body.jogador2 !== "string") {
      return erro("jogador2 precisa ser texto.");
    } else {
      campos.jogador2 = body.jogador2.trim();
    }
  }

  for (const campo of ["torneio", "mercado", "placar_final", "vencedor_partida"] as const) {
    const v = textoOpcional(body[campo], campo);
    if (v instanceof Error) return erro(v.message);
    if (v !== undefined) campos[campo] = v;
  }

  // --- números ---
  const odd = numeroOpcional(body.odd, "odd", { min: 1 });
  if (odd instanceof Error) return erro(odd.message);
  if (odd !== undefined) campos.odd = odd;

  const unidades = numeroOpcional(body.unidades, "unidades", { min: 0 });
  if (unidades instanceof Error) return erro(unidades.message);
  // unidades é NOT NULL no banco — apagar o campo volta pro padrão de 1.
  if (unidades !== undefined) campos.unidades = unidades ?? 1;

  const eventId = numeroOpcional(body.sofascore_event_id, "sofascore_event_id", { min: 0 });
  if (eventId instanceof Error) return erro(eventId.message);
  if (eventId !== undefined) campos.sofascore_event_id = eventId === null ? null : Math.trunc(eventId);

  // --- data/hora ---
  // Chega como ISO (o formulário converte o horário local de Brasília).
  if (body.data_hora !== undefined) {
    if (body.data_hora === null || body.data_hora === "") {
      campos.data_hora = null;
    } else if (typeof body.data_hora !== "string" || Number.isNaN(Date.parse(body.data_hora))) {
      return erro("data_hora precisa ser uma data válida.");
    } else {
      campos.data_hora = new Date(body.data_hora).toISOString();
    }
  }

  if (Object.keys(campos).length === 0) {
    return NextResponse.json({ error: "Nenhum campo para atualizar." }, { status: 400 });
  }

  await updateBetCampos(id, campos);

  const atualizado = await getBet(id);
  return NextResponse.json({ bet: atualizado });
}
