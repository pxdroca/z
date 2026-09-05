import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Cansadão Apostas",
  description: "Painel de apostas de tênis",
  // O ícone é a foto do tipster. Não é declarado aqui de propósito: o
  // Next detecta app/icon.png e app/apple-icon.png por convenção, gera
  // as variantes e injeta as tags — declarar à mão duplicaria.
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
