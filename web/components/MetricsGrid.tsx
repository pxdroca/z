import type { ReactNode } from "react";
import styles from "./MetricsGrid.module.css";

export interface MetricItem {
  icon?: ReactNode;
  label: string;
  valor: string;
  delta?: string | null;
}

/**
 * Port de _metrics_grid() em app.py — HTML/CSS puro lá (via st.markdown),
 * componente React de verdade aqui. `destaque` aumenta o peso tipográfico
 * (usado só pro grupo Green/Red/Unidades) sem cards com fundo/borda —
 * hierarquia só por tamanho/peso de fonte, minimalista (pedido do usuário).
 */
export function MetricsGrid({ itens, destaque = false }: { itens: MetricItem[]; destaque?: boolean }) {
  return (
    <div className={destaque ? `${styles.grid} ${styles.gridDestaque}` : styles.grid}>
      {itens.map((item) => (
        <div key={item.label} className={styles.cell}>
          <div className={styles.label}>
            {item.icon}
            <span>{item.label}</span>
          </div>
          <div className={styles.valor}>{item.valor}</div>
          {item.delta ? <div className={styles.delta}>{item.delta}</div> : null}
        </div>
      ))}
    </div>
  );
}
