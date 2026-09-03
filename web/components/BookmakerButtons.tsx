import { LOGO_BETANO, LOGO_SUPERBET } from "@/lib/bookmakerLogos";
import type { Bet } from "@/lib/types";
import styles from "./BetCard.module.css";

// Port de BOOKMAKER_BADGES em app.py — mesmas cores de marca, mesmo
// tratamento especial pra bet365 (wordmark de texto em vez de SVG, ver
// bookmakerLogos.ts para o porquê).
const BOOKMAKER_BADGES: Record<string, { logo?: string; wordmark?: boolean; cor: string }> = {
  superbet: { logo: LOGO_SUPERBET, cor: "#e2001a" },
  betano: { logo: LOGO_BETANO, cor: "#ff5000" },
  bet365: { wordmark: true, cor: "#027b5b" },
};

export function BookmakerButtons({ links }: { links: Bet["links"] }) {
  const slugs = Object.keys(links);
  if (slugs.length === 0) {
    return <div className={styles.bookmakerVazio}>Nenhuma casa de apostas configurada/encontrada.</div>;
  }

  return (
    <div className={styles.bookmakerRow}>
      {slugs.map((slug) => {
        const info = links[slug];
        const badge = BOOKMAKER_BADGES[slug];
        const cor = badge?.cor ?? "#6b7280";

        let conteudo: React.ReactNode;
        if (badge?.wordmark) {
          // Bug real corrigido aqui: className="accent" (string literal)
          // nunca batia com o seletor ".bookmakerWordmarkBet365 .accent"
          // do CSS Module (que vira uma classe com hash em build) — o
          // "365" nunca recebia a cor amarela, ficava branco igual o
          // "bet" (confirmado via devtools em produção). styles.accent é
          // o nome de classe real gerado pelo Module.
          conteudo = (
            <span className={styles.bookmakerWordmarkBet365}>
              bet<span className={styles.accent}>365</span>
            </span>
          );
        } else if (badge?.logo) {
          // data URI base64 embutido (não uma imagem remota) — next/image
          // não traz benefício real aqui e exigiria width/height fixos
          // além do que o CSS já controla.
          // eslint-disable-next-line @next/next/no-img-element
          conteudo = <img className={styles.bookmakerLogoImg} src={badge.logo} alt={info.nome || "?"} />;
        } else {
          conteudo = <span className={styles.bookmakerFallback}>{(info.nome || "?").slice(0, 3).toUpperCase()}</span>;
        }

        return (
          <a
            key={slug}
            className={styles.bookmakerBtn}
            style={{ background: cor }}
            href={info.url}
            target="_blank"
            rel="noopener noreferrer"
          >
            {conteudo}
          </a>
        );
      })}
    </div>
  );
}
