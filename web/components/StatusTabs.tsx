"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { GRUPOS, GRUPO_LABEL, type GrupoId } from "@/lib/agrupamento";
import styles from "./StatusTabs.module.css";

/** Cor do indicador de cada grupo — usado só na bolinha, nunca como fundo. */
const COR_GRUPO: Record<GrupoId, string> = {
  ao_vivo: "var(--red)",
  green: "var(--green)",
  red: "var(--red)",
  pendentes: "var(--amber)",
  void: "var(--neutral)",
};

export type FiltroGrupo = GrupoId | "todas";

/**
 * Segmented control pra filtrar a lista por grupo (client-side, sobre as
 * apostas já carregadas — não refaz busca no servidor). "Todas" mostra
 * tudo agrupado; escolher um grupo mostra só aquela seção.
 */
export function StatusTabs({
  ativo,
  onChange,
  contagens,
  total,
  children,
}: {
  ativo: FiltroGrupo;
  onChange: (novo: FiltroGrupo) => void;
  contagens: Record<GrupoId, number>;
  total: number;
  /** Slot à direita (ex: controle de ordenação) — mantém o alinhamento. */
  children?: React.ReactNode;
}) {
  const trilhaRef = useRef<HTMLDivElement>(null);
  // Posição/tamanho da pílula que desliza. As tabs têm larguras
  // diferentes (o rótulo e a contagem variam), então não dá pra calcular
  // por índice — é preciso medir o botão ativo.
  const [pilula, setPilula] = useState<{ left: number; width: number } | null>(null);

  const medir = useCallback(() => {
    const trilha = trilhaRef.current;
    if (!trilha) return;
    const alvo = trilha.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!alvo) return;
    // offsetLeft é relativo à trilha e já desconta o scroll horizontal
    // dela, que existe no mobile.
    setPilula({ left: alvo.offsetLeft, width: alvo.offsetWidth });
  }, []);

  // useLayoutEffect: mede antes da pintura, pra pílula nunca aparecer um
  // frame na posição errada ao trocar de aba.
  useLayoutEffect(medir, [medir, ativo, total, contagens]);

  // As larguras mudam com o tamanho da fonte/janela e quando a fonte
  // (Inter, via link externo) termina de carregar.
  useEffect(() => {
    const trilha = trilhaRef.current;
    if (!trilha) return;
    const ro = new ResizeObserver(medir);
    ro.observe(trilha);
    for (const filho of Array.from(trilha.children)) ro.observe(filho);
    return () => ro.disconnect();
  }, [medir]);

  return (
    <div className={styles.wrapper}>
      <div className={styles.tabs} role="tablist" aria-label="Filtrar por situação" ref={trilhaRef}>
        {/* Pílula única que desliza entre as opções, em vez de cada tab
            acender o próprio fundo. Fica atrás dos botões (z-index) e é
            decorativa — o estado real continua no aria-selected. */}
        {pilula ? (
          <span
            className={styles.pilula}
            style={{ transform: `translateX(${pilula.left}px)`, width: `${pilula.width}px` }}
            aria-hidden="true"
          />
        ) : null}

        <button
          role="tab"
          aria-selected={ativo === "todas"}
          className={ativo === "todas" ? `${styles.tab} ${styles.tabAtiva}` : styles.tab}
          onClick={() => onChange("todas")}
        >
          Todas
          <span className={styles.count}>{total}</span>
        </button>

        {GRUPOS.map((g) => (
          <button
            key={g}
            role="tab"
            aria-selected={ativo === g}
            className={ativo === g ? `${styles.tab} ${styles.tabAtiva}` : styles.tab}
            onClick={() => onChange(g)}
          >
            <span className={styles.dot} style={{ background: COR_GRUPO[g] }} />
            {GRUPO_LABEL[g]}
            <span className={styles.count}>{contagens[g]}</span>
          </button>
        ))}
      </div>

      {children}
    </div>
  );
}
