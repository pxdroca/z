"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { STATUS_VISIVEIS, type BetStatus } from "@/lib/types";
import { STATUS_LABEL } from "@/lib/labels";
import { ChevronDownIcon, FilterIcon } from "./icons";
import styles from "./FilterPopover.module.css";

export interface Filtro {
  status: BetStatus[];
  from: string;
  to: string;
}

const CONSULTA_MOBILE = "(max-width: 640px)";

/**
 * Painel de filtros (status da partida + período) que abre a partir do
 * botão no canto superior direito. O botão "Atualizar" vive fora daqui,
 * como ação própria no header (ver page.tsx).
 */
export function FilterPopover({ filtro, onChange }: { filtro: Filtro; onChange: (f: Filtro) => void }) {
  const [aberto, setAberto] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const painelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickFora(e: MouseEvent) {
      const alvo = e.target as Node;
      // Confere o wrapper (onde está o botão) E o painel: no mobile o
      // painel é renderizado por portal no <body>, então já não é
      // descendente do wrapper — sem esta segunda checagem, clicar dentro
      // do próprio painel o fecharia.
      const dentroDoBotao = ref.current?.contains(alvo);
      const dentroDoPainel = painelRef.current?.contains(alvo);
      if (!dentroDoBotao && !dentroDoPainel) setAberto(false);
    }
    document.addEventListener("mousedown", handleClickFora);
    return () => document.removeEventListener("mousedown", handleClickFora);
  }, []);

  /**
   * No mobile o painel é renderizado no <body> por portal.
   *
   * Motivo: o botão vive no dock flutuante, que usa `transform` pra se
   * centralizar. Um ancestral transformado passa a ser o bloco contêiner
   * de qualquer `position: fixed` descendente — então o painel, apesar de
   * fixed com left/right: 0.75rem, era medido contra o dock e saía com
   * 199px em vez da largura da tela (medido). O portal o tira desse
   * contexto.
   *
   * No desktop fica no lugar: ali o painel é `position: absolute` ancorado
   * no botão, e movê-lo pro body exigiria calcular a posição via JS sem
   * nenhum ganho.
   *
   * Começa `false` (o mesmo que o servidor renderiza) e é resolvido no
   * primeiro efeito, pra não haver divergência de hidratação.
   */
  const [mobile, setMobile] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia(CONSULTA_MOBILE);
    const aplicar = () => setMobile(mq.matches);
    aplicar();
    mq.addEventListener("change", aplicar);
    return () => mq.removeEventListener("change", aplicar);
  }, []);

  function toggleStatus(s: BetStatus) {
    const jaSelecionado = filtro.status.includes(s);
    const novoStatus = jaSelecionado ? filtro.status.filter((x) => x !== s) : [...filtro.status, s];
    onChange({ ...filtro, status: novoStatus });
  }

  const comPortalSeMobile = (node: React.ReactNode): React.ReactNode =>
    mobile && typeof document !== "undefined" ? createPortal(node, document.body) : node;

  return (
    <div className={styles.wrapper} ref={ref}>
      <button
        className={aberto ? `${styles.trigger} ${styles.triggerAberto}` : styles.trigger}
        onClick={() => setAberto((v) => !v)}
        aria-expanded={aberto}
        aria-label="Filtros"
      >
        <FilterIcon size={15} />
        {/* Rótulo e chevron somem no mobile (ver CSS): lá o botão vive no
            dock flutuante, onde precisa ser quadrado como os vizinhos —
            com texto, ele sozinho ocupava metade da barra. */}
        <span className={styles.rotulo}>Filtros</span>
        <span className={aberto ? `${styles.chevron} ${styles.chevronAberto}` : styles.chevron}>
          <ChevronDownIcon size={13} />
        </span>
      </button>

      {aberto ? comPortalSeMobile(
        <div className={styles.panel} ref={painelRef}>
          <div className={styles.sectionLabel}>Status</div>
          <div className={styles.statusOptions}>
            {/* Só os status visíveis: erro_extracao são mensagens que não
                eram tip nenhuma e saíram do painel, então oferecer o
                checkbox seria oferecer um filtro que não mostra nada útil. */}
            {STATUS_VISIVEIS.map((s) => {
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
