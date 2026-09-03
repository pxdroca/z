import type { ReactNode } from "react";
import { Sparkline } from "./Sparkline";
import styles from "./MetricsGrid.module.css";

export interface MetricItem {
  icon?: ReactNode;
  label: string;
  valor: string;
  /** Informação secundária abaixo do número (ex: "ROI 12.3%"). */
  delta?: string | null;
  /** Cor de acento do card (ícone + sparkline). Uma CSS custom property. */
  color?: string;
  /** Fundo do quadradinho do ícone — a variante -soft da cor de acento. */
  colorSoft?: string;
  /** Série real pro micrográfico; sem ela o card só não mostra sparkline. */
  serie?: number[];
}

/**
 * Cards de estatística em grid — pequenos "insights" de analytics, não
 * caixas grandes e coloridas: fundo de vidro escuro, cor só como acento
 * no ícone e no sparkline, número como elemento dominante.
 */
export function MetricsGrid({ itens }: { itens: MetricItem[] }) {
  return (
    <div className={styles.grid}>
      {itens.map((item) => (
        <div key={item.label} className={styles.card}>
          <div className={styles.topo}>
            {item.icon ? (
              <div className={styles.iconeBox} style={{ background: item.colorSoft }}>
                {item.icon}
              </div>
            ) : null}
            <span className={styles.label}>{item.label}</span>
          </div>

          <div className={styles.baixo}>
            <div className={styles.valores}>
              <div className={styles.valor} style={item.color ? { color: item.color } : undefined}>
                {item.valor}
              </div>
              {item.delta ? <div className={styles.delta}>{item.delta}</div> : null}
            </div>
            {item.serie && item.color ? (
              <div className={styles.spark}>
                <Sparkline valores={item.serie} color={item.color} />
              </div>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}
