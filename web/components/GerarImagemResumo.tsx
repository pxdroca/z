"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { contarPorGrupo, GRUPO_LABEL, GRUPOS, grupoDaBet, type GrupoId } from "@/lib/agrupamento";
import { canvasParaBlob, desenharResumoDoDia } from "@/lib/imagemResumo";
import type { Bet } from "@/lib/types";
import { CopyIcon, DownloadIcon, ImageIcon, ShareIcon, XIcon } from "./icons";
import styles from "./GerarImagemResumo.module.css";

const NOME_ARQUIVO = "jogos-do-dia.png";

/** Quais grupos entram na imagem. Multi-seleção: cada chip marca ou
 *  desmarca um grupo, e "Todas" alterna entre tudo marcado e nada
 *  marcado. Antes era um filtro por vez (rádio), o que impedia
 *  combinações óbvias como "green + red" (o dia fechado, sem o que ainda
 *  está em jogo). Ver lib/agrupamento.ts para os grupos. */
type Selecao = Set<GrupoId>;

/** Ordem dos chips: primeiro o que já saiu, depois o que está em jogo. */
const ORDEM_CHIPS: GrupoId[] = ["green", "red", "void", "ao_vivo", "pendentes"];

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

  // Começa com todos os grupos marcados — o mesmo conteúdo que o antigo
  // "Todas" gerava por padrão.
  const [selecao, setSelecao] = useState<Selecao>(() => new Set(GRUPOS));

  const gerar = useCallback(async (alvo: Selecao) => {
    setAberto(true);
    setAviso(null);

    // Nada marcado: a imagem sairia só com cabeçalho e rodapé, e os
    // botões de copiar/baixar entregariam um arquivo vazio. Descarta a
    // prévia e deixa o modal pedir uma seleção.
    if (alvo.size === 0) {
      if (urlAnteriorRef.current) URL.revokeObjectURL(urlAnteriorRef.current);
      urlAnteriorRef.current = null;
      blobRef.current = null;
      setImagemUrl(null);
      setGerando(false);
      return;
    }

    setGerando(true);
    try {
      const selecionadas = bets.filter((b) => alvo.has(grupoDaBet(b)));
      const canvas = desenharResumoDoDia(selecionadas);
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

  // Contagem por grupo pra mostrar no chip: sem isso o usuário só
  // descobre que um grupo está vazio depois de marcar e ver a imagem.
  const contagemPorGrupo = useMemo(() => contarPorGrupo(bets), [bets]);

  const todosMarcados = selecao.size === GRUPOS.length;

  // O próximo conjunto é calculado a partir de `selecao` (não dentro do
  // updater do setState): gerar() faz trabalho de verdade — desenha o
  // canvas e cria um object URL — e um updater precisa ser puro, senão em
  // StrictMode isso roda duas vezes por clique.
  const aplicar = useCallback(
    (proximo: Selecao) => {
      setSelecao(proximo);
      void gerar(proximo);
    },
    [gerar],
  );

  const alternarGrupo = useCallback(
    (g: GrupoId) => {
      const proximo = new Set(selecao);
      if (proximo.has(g)) proximo.delete(g);
      else proximo.add(g);
      aplicar(proximo);
    },
    [aplicar, selecao],
  );

  const alternarTodos = useCallback(() => {
    aplicar(todosMarcados ? new Set() : new Set(GRUPOS));
  }, [aplicar, todosMarcados]);

  function fechar() {
    setAberto(false);
  }

  return (
    <>
      <button
        className={styles.trigger}
        onClick={() => gerar(selecao)}
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

            {/* Filtro multi-seleção: cada chip liga/desliga um grupo e a
                imagem é regerada. Mesmo agrupamento do painel, então
                "Green" aqui e "Green" lá significam a mesma coisa. */}
            <div className={styles.filtros}>
              <button
                className={todosMarcados ? `${styles.chip} ${styles.chipAtivo}` : styles.chip}
                onClick={alternarTodos}
                aria-pressed={todosMarcados}
                title={todosMarcados ? "Desmarcar todos" : "Marcar todos"}
              >
                Todas
              </button>
              <span className={styles.separadorChips} aria-hidden="true" />
              {ORDEM_CHIPS.map((g) => {
                const marcado = selecao.has(g);
                return (
                  <button
                    key={g}
                    className={marcado ? `${styles.chip} ${styles.chipAtivo}` : styles.chip}
                    onClick={() => alternarGrupo(g)}
                    aria-pressed={marcado}
                  >
                    {GRUPO_LABEL[g]}
                    <span className={styles.contagemChip}>{contagemPorGrupo[g]}</span>
                  </button>
                );
              })}
            </div>

            <div className={styles.preview}>
              {gerando ? (
                <div className={styles.carregando}>Gerando imagem…</div>
              ) : selecao.size === 0 ? (
                <div className={styles.carregando}>Marque ao menos um filtro.</div>
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
                    <button className={styles.acaoSecundaria} onClick={copiar} aria-label="Copiar imagem" title="Copiar">
                      <CopyIcon size={16} />
                      <span className={styles.rotuloAcao}>Copiar</span>
                    </button>
                  ) : null}
                  {capacidades.compartilhar ? (
                    <button
                      className={styles.acaoSecundaria}
                      onClick={compartilhar}
                      aria-label="Compartilhar imagem"
                      title="Compartilhar"
                    >
                      <ShareIcon size={16} />
                      <span className={styles.rotuloAcao}>Compartilhar</span>
                    </button>
                  ) : null}
                  <a
                    href={imagemUrl}
                    download={NOME_ARQUIVO}
                    className={styles.baixarButton}
                    aria-label="Baixar imagem"
                    title="Baixar"
                  >
                    <DownloadIcon size={16} />
                    <span className={styles.rotuloAcao}>Baixar</span>
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
