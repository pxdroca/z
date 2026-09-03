"use client";

import { SearchIcon, XIcon } from "./icons";
import styles from "./SearchInput.module.css";

/**
 * Busca client-side sobre as apostas já carregadas (ver filtrarPorBusca em
 * lib/busca.ts) — não faz request nova ao servidor.
 */
export function SearchInput({ valor, onChange }: { valor: string; onChange: (novo: string) => void }) {
  return (
    <div className={styles.wrapper}>
      <span className={styles.icone}>
        <SearchIcon size={14} />
      </span>
      <input
        className={styles.input}
        type="search"
        value={valor}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Pesquisar..."
        aria-label="Pesquisar apostas por jogador, torneio ou mercado"
      />
      {valor ? (
        <button className={styles.limpar} onClick={() => onChange("")} aria-label="Limpar pesquisa" type="button">
          <XIcon size={13} />
        </button>
      ) : null}
    </div>
  );
}
