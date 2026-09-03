// Autenticação simples: uma senha única compartilhada (não contas
// individuais — decisão do usuário; o painel Streamlit anterior era
// público sem senha nenhuma, então basta uma barreira mínima). O cookie guarda
// uma assinatura HMAC-SHA256 de um valor fixo + a senha, não a senha em
// si nem um segredo derivável — comparação em tempo constante evita
// timing attack na checagem.
import { createHmac, timingSafeEqual } from "crypto";

const COOKIE_NAME = "cansadao_auth";
const PAYLOAD = "autenticado";

function sign(secret: string): string {
  return createHmac("sha256", secret).update(PAYLOAD).digest("hex");
}

export function checkPassword(senhaDigitada: string): boolean {
  const senhaCorreta = process.env.PANEL_PASSWORD;
  if (!senhaCorreta) return false;
  return senhaDigitada === senhaCorreta;
}

export function makeAuthCookieValue(): string {
  const secret = process.env.PANEL_PASSWORD ?? "";
  return sign(secret);
}

export function isAuthCookieValid(cookieValue: string | undefined): boolean {
  const secret = process.env.PANEL_PASSWORD;
  if (!secret || !cookieValue) return false;
  const esperado = sign(secret);
  const a = Buffer.from(cookieValue);
  const b = Buffer.from(esperado);
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

export const AUTH_COOKIE_NAME = COOKIE_NAME;
