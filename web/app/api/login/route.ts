import { NextRequest, NextResponse } from "next/server";
import { AUTH_COOKIE_NAME, checkPassword, makeAuthCookieValue } from "@/lib/auth";

export async function POST(request: NextRequest) {
  let body: { senha?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Corpo inválido." }, { status: 400 });
  }

  if (typeof body.senha !== "string" || !checkPassword(body.senha)) {
    return NextResponse.json({ error: "Senha incorreta." }, { status: 401 });
  }

  const resposta = NextResponse.json({ ok: true });
  resposta.cookies.set(AUTH_COOKIE_NAME, makeAuthCookieValue(), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 30, // 30 dias
  });
  return resposta;
}
