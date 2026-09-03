"use client";

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
  return (
    <div className={styles.wrapper}>
      <div className={styles.tabs} role="tablist" aria-label="Filtrar por situação">
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
