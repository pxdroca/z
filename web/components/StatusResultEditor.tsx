"use client";

import { BET_STATUS_VALUES, RESULTADO_APOSTA_VALUES, type BetStatus, type ResultadoAposta } from "@/lib/types";
import { STATUS_LABEL, RESULTADO_LABEL } from "@/lib/labels";
import styles from "./StatusResultEditor.module.css";

export function StatusResultEditor({
  status,
  resultado,
  onChangeStatus,
  onChangeResultado,
  disabled = false,
}: {
  status: BetStatus;
  resultado: ResultadoAposta;
  onChangeStatus: (novo: BetStatus) => void;
  onChangeResultado: (novo: ResultadoAposta) => void;
  disabled?: boolean;
}) {
  return (
    <div className={styles.editor}>
      <select
        className={styles.select}
        value={status}
        disabled={disabled}
        onChange={(e) => onChangeStatus(e.target.value as BetStatus)}
        aria-label="Atualizar status"
      >
        {BET_STATUS_VALUES.map((s) => (
          <option key={s} value={s}>
            {STATUS_LABEL[s]}
          </option>
        ))}
      </select>
      <select
        className={styles.select}
        value={resultado}
        disabled={disabled}
        onChange={(e) => onChangeResultado(e.target.value as ResultadoAposta)}
        aria-label="Resultado da aposta"
      >
        {RESULTADO_APOSTA_VALUES.map((r) => (
          <option key={r} value={r}>
            {RESULTADO_LABEL[r]}
          </option>
        ))}
      </select>
    </div>
  );
}
