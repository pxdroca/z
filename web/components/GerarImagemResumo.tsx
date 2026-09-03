"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { canvasParaBlob, desenharResumoDoDia } from "@/lib/imagemResumo";
import type { Bet } from "@/lib/types";
import { CopyIcon, DownloadIcon, ImageIcon, ShareIcon, XIcon } from "./icons";
import styles from "./GerarImagemResumo.module.css";

const NOME_ARQUIVO = "jogos-do-dia.png";

/**
 * O navegador atual consegue copiar imagem e compartilhar arquivo?
 *
 * Checa capacidade, não navegador. Suporte a imagem no clipboard exige
 * ClipboardItem + contexto seguro (o Firefox não implementa write de
 * imagem), e compartilhar ARQUIVO é mais restrito que compartilhar texto —
 * por isso o teste é canShare({files}) com um File de verdade, e não a
 * mera existência de navigator.share.
 *
 * Roda no servidor também (é o initializer de um useState num componente
 * que faz SSR), onde `navigator` não existe — daí os guards.
 */
function detectarCapacidades(): { copiar: boolean; compartilhar: boolean } {
  if (typeof navigator === "undefined") return { copiar: false, compartilhar: false };

  const copiar =
    typeof ClipboardItem !== "undefined" && typeof navigator.clipboard?.write === "function";

  let compartilhar = false;
  try {
    const teste = new File([new Blob([""], { type: "image/png" })], "t.png", { type: "image/png" });
    compartilhar = typeof navigator.canShare === "function" && navigator.canShare({ files: [teste] });
  } catch {
    compartilhar = false;
  }

  return { copiar, compartilhar };
}

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
  // Feedback curto das ações ("Copiada!", "Falhou") — some sozinho.
  const [aviso, setAviso] = useState<string | null>(null);
  const urlAnteriorRef = useRef<string | null>(null);
  // Guarda o blob: copiar e compartilhar precisam dele, não do object URL.
  const blobRef = useRef<Blob | null>(null);
  const avisoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const gerar = useCallback(async () => {
    setGerando(true);
    setAberto(true);
    setAviso(null);
    try {
      const canvas = desenharResumoDoDia(bets);
      const blob = await canvasParaBlob(canvas);
      blobRef.current = blob;
      const url = URL.createObjectURL(blob);
      if (urlAnteriorRef.current) URL.revokeObjectURL(urlAnteriorRef.current);
      urlAnteriorRef.current = url;
      setImagemUrl(url);
    } finally {
      setGerando(false);
    }
  }, [bets]);

  const mostrarAviso = useCallback((texto: string) => {
    setAviso(texto);
    if (avisoTimerRef.current) clearTimeout(avisoTimerRef.current);
    avisoTimerRef.current = setTimeout(() => setAviso(null), 2600);
  }, []);

  /**
   * Copia o PNG para a área de transferência.
   *
   * Suporte a imagem no clipboard é irregular: exige ClipboardItem e
   * contexto seguro (https ou localhost), e o Firefox não implementa
   * clipboard.write para imagem. Por isso a checagem é de capacidade, não
   * de navegador — onde não dá, o botão nem aparece.
   */
  const copiar = useCallback(async () => {
    const blob = blobRef.current;
    if (!blob) return;
    try {
      await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
      mostrarAviso("Imagem copiada!");
    } catch {
      mostrarAviso("Não foi possível copiar aqui — use Baixar.");
    }
  }, [mostrarAviso]);

  /**
   * Abre a folha de compartilhamento nativa (WhatsApp, Telegram etc).
   *
   * navigator.share só existe em contexto seguro e, na prática, quase só
   * em mobile; canShare({files}) é a checagem correta porque compartilhar
   * ARQUIVO é mais restrito que compartilhar texto/link.
   */
  const compartilhar = useCallback(async () => {
    const blob = blobRef.current;
    if (!blob) return;
    const arquivo = new File([blob], NOME_ARQUIVO, { type: blob.type });
    try {
      await navigator.share({ files: [arquivo], title: "Jogos do dia" });
    } catch (err) {
      // O usuário cancelar a folha de compartilhamento é AbortError — não
      // é erro, e mostrar "falhou" nesse caso seria mentira.
      if ((err as Error)?.name !== "AbortError") {
        mostrarAviso("Não foi possível compartilhar aqui.");
      }
    }
  }, [mostrarAviso]);

  // Libera o object URL e o timer quando o componente desmonta (evita
  // vazar memória entre navegações da SPA).
  useEffect(() => {
    return () => {
      if (urlAnteriorRef.current) URL.revokeObjectURL(urlAnteriorRef.current);
      if (avisoTimerRef.current) clearTimeout(avisoTimerRef.current);
    };
  }, []);

  // Detecção de capacidade avaliada de forma lazy no primeiro render do
  // cliente (o initializer do useState só roda no navegador, onde
  // `navigator` existe). Os botões aparecem apenas onde a API funciona de
  // verdade — Firefox não escreve imagem no clipboard, e desktop em geral
  // não tem folha de compartilhamento de arquivo.
  const [capacidades] = useState(detectarCapacidades);

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

      {/* Portal pro <body> — mesma razão do NovaApostaDialog: no mobile
          este componente vive dentro do dock flutuante, cujo z-index e
          reset de fundo prendiam o modal na barra (era por isso que
          clicar fora não fechava: o "fora" estava dentro do dock). */}
      {aberto
        ? createPortal(
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
              <div className={styles.acoes}>
                {aviso ? <div className={styles.aviso}>{aviso}</div> : null}
                <div className={styles.acoesBotoes}>
                  {capacidades.copiar ? (
                    <button className={styles.acaoSecundaria} onClick={copiar}>
                      <CopyIcon size={15} />
                      Copiar
                    </button>
                  ) : null}
                  {capacidades.compartilhar ? (
                    <button className={styles.acaoSecundaria} onClick={compartilhar}>
                      <ShareIcon size={15} />
                      Compartilhar
                    </button>
                  ) : null}
                  <a href={imagemUrl} download={NOME_ARQUIVO} className={styles.baixarButton}>
                    <DownloadIcon size={15} />
                    Baixar
                  </a>
                </div>
              </div>
            ) : null}
          </div>
        </div>,
            document.body
          )
        : null}
    </>
  );
}
