"use client";

import { useEffect, useRef, useState } from "react";
import { BET_STATUS_VALUES, type BetStatus } from "@/lib/types";
import { STATUS_LABEL } from "@/lib/labels";
import { ChevronDownIcon, FilterIcon } from "./icons";
import styles from "./FilterPopover.module.css";

export interface Filtro {
  status: BetStatus[];
  from: string;
  to: string;
}

/**
 * Painel de filtros (status da partida + período) que abre a partir do
 * botão no canto superior direito. O botão "Atualizar" vive fora daqui,
 * como ação própria no header (ver page.tsx).
 */
export function FilterPopover({ filtro, onChange }: { filtro: Filtro; onChange: (f: Filtro) => void }) {
  const [aberto, setAberto] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickFora(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setAberto(false);
      }
    }
    document.addEventListener("mousedown", handleClickFora);
    return () => document.removeEventListener("mousedown", handleClickFora);
  }, []);

  function toggleStatus(s: BetStatus) {
    const jaSelecionado = filtro.status.includes(s);
    const novoStatus = jaSelecionado ? filtro.status.filter((x) => x !== s) : [...filtro.status, s];
    onChange({ ...filtro, status: novoStatus });
  }

  return (
    <div className={styles.wrapper} ref={ref}>
      <button
        className={aberto ? `${styles.trigger} ${styles.triggerAberto}` : styles.trigger}
        onClick={() => setAberto((v) => !v)}
        aria-expanded={aberto}
      >
        <FilterIcon size={14} />
        Filtros
        <span className={aberto ? `${styles.chevron} ${styles.chevronAberto}` : styles.chevron}>
          <ChevronDownIcon size={13} />
        </span>
      </button>

      {aberto ? (
        <div className={styles.panel}>
          <div className={styles.sectionLabel}>Status</div>
          <div className={styles.statusOptions}>
            {BET_STATUS_VALUES.map((s) => {
              const ativo = filtro.status.includes(s);
              return (
                <label key={s} className={ativo ? `${styles.statusChip} ${styles.statusChipAtivo}` : styles.statusChip}>
                  <input type="checkbox" checked={ativo} onChange={() => toggleStatus(s)} />
                  {STATUS_LABEL[s]}
                </label>
              );
            })}
          </div>

          <div className={styles.sectionLabel}>Período (data do jogo)</div>
          <div className={styles.dateRow}>
            <input
              type="date"
              className={styles.dateInput}
              value={filtro.from}
              onChange={(e) => onChange({ ...filtro, from: e.target.value })}
            />
            <input
              type="date"
              className={styles.dateInput}
              value={filtro.to}
              onChange={(e) => onChange({ ...filtro, to: e.target.value })}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}
