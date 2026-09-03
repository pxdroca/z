"use client";

import { useEffect, useRef, useState } from "react";
import { BET_STATUS_VALUES, type BetStatus } from "@/lib/types";
import { STATUS_LABEL } from "@/lib/labels";
import { SearchIcon } from "./icons";
import styles from "./FilterPopover.module.css";

export interface Filtro {
  status: BetStatus[];
  from: string;
  to: string;
}

/**
 * Port de _header_e_filtros() em app.py (st.popover) — painel que abre/fecha
 * sobre o conteúdo, mesma posição (canto superior direito), mesmo conteúdo
 * (multiselect de status + período + botão de atualizar).
 */
export function FilterPopover({ filtro, onChange, onRefresh }: { filtro: Filtro; onChange: (f: Filtro) => void; onRefresh: () => void }) {
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
      >
        <SearchIcon />
        Filtros
      </button>

      {aberto ? (
        <div className={styles.panel}>
          <div className={styles.sectionLabel}>Status</div>
          <div className={styles.statusOptions}>
            {BET_STATUS_VALUES.map((s) => {
              const ativo = filtro.status.includes(s);
              return (
                <label key={s} className={ativo ? styles.statusChip : `${styles.statusChip} ${styles.statusChipInativo}`}>
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

          <button className={styles.refreshButton} onClick={onRefresh}>
            Atualizar agora
          </button>
        </div>
      ) : null}
    </div>
  );
}
