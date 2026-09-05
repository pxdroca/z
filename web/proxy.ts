// Checagem otimista de senha (cookie assinado, sem consulta a banco) —
// ver lib/auth.ts. "middleware.ts" foi renomeado pra "proxy.ts" no Next.js
// 16 (mesma funcionalidade, arquivo/exports renomeados).
import { NextRequest, NextResponse } from "next/server";
import { AUTH_COOKIE_NAME, isAuthCookieValid } from "@/lib/auth";

const ROTAS_PUBLICAS = ["/login", "/api/login"];

export default function proxy(request: NextRequest) {
  const path = request.nextUrl.pathname;
  const ehPublica = ROTAS_PUBLICAS.some((r) => path === r || path.startsWith(`${r}/`));

  const cookie = request.cookies.get(AUTH_COOKIE_NAME)?.value;
  const autenticado = isAuthCookieValid(cookie);

  if (!ehPublica && !autenticado) {
    if (path.startsWith("/api/")) {
      return NextResponse.json({ error: "Não autenticado." }, { status: 401 });
    }
    return NextResponse.redirect(new URL("/login", request.url));
  }

  if (path === "/login" && autenticado) {
    return NextResponse.redirect(new URL("/", request.url));
  }

  return NextResponse.next();
}

export const config = {
  // Arquivos estáticos ficam de fora da checagem de senha.
  //
  // A extensão no fim é o que libera o conteúdo de public/: sem isso
  // /tipster.jpg levava 307 pro /login, e a foto do tipster não aparecia
  // justamente NA tela de login (a página que a exibe). Vale também pros
  // ícones que o Next gera por convenção (icon.png, apple-icon.png).
  //
  // Não é um vazamento: são imagens de marca, não dado de aposta — e
  // qualquer rota de dados (/api/*) segue protegida.
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:png|jpg|jpeg|gif|svg|ico|webp|avif)$).*)",
  ],
};
