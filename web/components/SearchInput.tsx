"use client";

import { useEffect, useRef, useState } from "react";
import { SearchIcon, XIcon } from "./icons";
import styles from "./SearchInput.module.css";

/**
 * Busca client-side sobre as apostas já carregadas (ver filtrarPorBusca em
 * lib/busca.ts) — não faz request nova ao servidor.
 *
 * Em repouso é só a lupa, do mesmo tamanho dos outros botões do header; ao
 * clicar, o campo cresce da direita pra esquerda. Fecha ao perder o foco,
 * mas só se estiver vazio — fechar com texto digitado esconderia o filtro
 * ativo e o usuário não entenderia por que a lista está curta.
 */
export function SearchInput({ valor, onChange }: { valor: string; onChange: (novo: string) => void }) {
  const [aberto, setAberto] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Foca só quando o usuário abre (não no primeiro render, senão a página
  // carregaria com o teclado do celular aberto).
  useEffect(() => {
    if (aberto) inputRef.current?.focus();
  }, [aberto]);

  function fecharSeVazio() {
    if (!valor) setAberto(false);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      onChange("");
      setAberto(false);
    }
  }

  return (
    <div className={aberto ? `${styles.wrapper} ${styles.wrapperAberto}` : styles.wrapper}>
      <button
        className={styles.lupa}
        onClick={() => setAberto(true)}
        aria-label="Pesquisar apostas"
        aria-expanded={aberto}
        title="Pesquisar"
        type="button"
        // Fechado, a lupa é o botão. Aberto, ela vira só o ícone dentro do
        // campo — clicar nela não deve roubar o foco de quem está digitando.
        tabIndex={aberto ? -1 : 0}
      >
        <SearchIcon size={15} />
      </button>

      <input
        ref={inputRef}
        className={styles.input}
        type="text"
        value={valor}
        onChange={(e) => onChange(e.target.value)}
        onBlur={fecharSeVazio}
        onKeyDown={onKeyDown}
        placeholder="Pesquisar..."
        aria-label="Pesquisar apostas por jogador, torneio ou mercado"
        aria-hidden={!aberto}
        tabIndex={aberto ? 0 : -1}
      />

      {aberto && valor ? (
        <button className={styles.limpar} onClick={() => onChange("")} aria-label="Limpar pesquisa" type="button">
          <XIcon size={13} />
        </button>
      ) : null}
    </div>
  );
}
