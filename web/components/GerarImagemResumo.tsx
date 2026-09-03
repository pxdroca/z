"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { canvasParaObjectUrl, desenharResumoDoDia } from "@/lib/imagemResumo";
import type { Bet } from "@/lib/types";
import { DownloadIcon, ImageIcon, XIcon } from "./icons";
import styles from "./GerarImagemResumo.module.css";

/**
 * Botão de header que gera a imagem-resumo "jogos do dia" (ver
 * lib/imagemResumo.ts) e abre um preview em modal, com opção de baixar o
 * PNG. Formato sempre vertical (pensado pra tela de celular) — nunca
 * horizontal, por decisão de produto.
 */
export function GerarImagemResumo({ bets }: { bets: Bet[] }) {
  const [aberto, setAberto] = useState(false);
  const [imagemUrl, setImagemUrl] = useState<string | null>(null);
  const [gerando, setGerando] = useState(false);
  const urlAnteriorRef = useRef<string | null>(null);

  const gerar = useCallback(async () => {
    setGerando(true);
    setAberto(true);
    try {
      const canvas = desenharResumoDoDia(bets);
      const url = await canvasParaObjectUrl(canvas);
      if (urlAnteriorRef.current) URL.revokeObjectURL(urlAnteriorRef.current);
      urlAnteriorRef.current = url;
      setImagemUrl(url);
    } finally {
      setGerando(false);
    }
  }, [bets]);

  // Libera o object URL quando o componente desmonta (evita vazar memória
  // entre navegações da SPA).
  useEffect(() => {
    return () => {
      if (urlAnteriorRef.current) URL.revokeObjectURL(urlAnteriorRef.current);
    };
  }, []);

  function fechar() {
    setAberto(false);
  }

  return (
    <>
      <button
        className={styles.trigger}
        onClick={gerar}
        aria-label="Gerar imagem dos jogos do dia"
        title="Gerar imagem dos jogos do dia"
      >
        <ImageIcon size={15} />
      </button>

      {aberto ? (
        <div className={styles.overlay} onClick={fechar}>
          <div className={styles.painel} onClick={(e) => e.stopPropagation()}>
            <div className={styles.painelHeader}>
              <span className={styles.painelTitulo}>Jogos do dia</span>
              <button className={styles.fecharButton} onClick={fechar} aria-label="Fechar">
                <XIcon size={15} />
              </button>
            </div>

            <div className={styles.preview}>
              {gerando ? (
                <div className={styles.carregando}>Gerando imagem…</div>
              ) : imagemUrl ? (
                // eslint-disable-next-line @next/next/no-img-element -- imagem gerada em runtime (blob URL), não um asset estático
                <img src={imagemUrl} alt="Resumo dos jogos do dia" className={styles.imagem} />
              ) : null}
            </div>

            {imagemUrl && !gerando ? (
              <a href={imagemUrl} download="jogos-do-dia.png" className={styles.baixarButton}>
                <DownloadIcon size={15} />
                Baixar imagem
              </a>
            ) : null}
          </div>
        </div>
      ) : null}
    </>
  );
}
