"use client";

import { useEffect, useState } from "react";
import { ESPORTE_VALUES, type Esporte, type ResultadoAposta } from "@/lib/types";
import { PlusIcon, XIcon } from "./icons";
import styles from "./NovaApostaDialog.module.css";

/**
 * Formulário de aposta manual.
 *
 * Existe porque o pipeline automático não cobre toda liga: quando o
 * SofaScore não tem o jogo (ex: NBL Blitz, pré-temporada australiana de
 * basquete), a aposta nunca sai de "nao_encontrada" e antes disso não havia
 * como registrá-la ou corrigi-la pelo painel — a única escrita possível era
 * editar o resultado de uma aposta já existente.
 *
 * Escreve pelo POST /api/bets, que insere com mensagem_id NULL (a marca de
 * "não veio do Telegram").
 */
export function NovaApostaDialog({ onCriada }: { onCriada: () => void }) {
  const [aberto, setAberto] = useState(false);
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  const [jogador1, setJogador1] = useState("");
  const [jogador2, setJogador2] = useState("");
  const [torneio, setTorneio] = useState("");
  const [mercado, setMercado] = useState("");
  const [odd, setOdd] = useState("");
  const [unidades, setUnidades] = useState("1");
  const [esporte, setEsporte] = useState<Esporte>("tenis");
  const [resultado, setResultado] = useState<ResultadoAposta>("pendente");
  const [dataHora, setDataHora] = useState("");

  // Esc fecha, como em qualquer diálogo.
  useEffect(() => {
    if (!aberto) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setAberto(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [aberto]);

  function limpar() {
    setJogador1("");
    setJogador2("");
    setTorneio("");
    setMercado("");
    setOdd("");
    setUnidades("1");
    setEsporte("tenis");
    setResultado("pendente");
    setDataHora("");
    setErro(null);
  }

  async function salvar() {
    if (!jogador1.trim()) {
      setErro("Informe pelo menos o jogador/time 1.");
      return;
    }
    setSalvando(true);
    setErro(null);
    try {
      // Um resultado já decidido implica jogo encerrado — evita a aposta
      // nascer incoerente (resultado green com status "não encontrada").
      const status = resultado === "pendente" ? "nao_encontrada" : "encerrada";
      const resp = await fetch("/api/bets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          jogador1,
          jogador2,
          torneio,
          mercado,
          odd,
          unidades,
          esporte,
          resultado,
          status,
          data_hora: dataHora,
        }),
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        setErro(data.error ?? "Falha ao salvar a aposta.");
        return;
      }
      limpar();
      setAberto(false);
      onCriada();
    } catch {
      setErro("Falha de rede ao salvar a aposta.");
    } finally {
      setSalvando(false);
    }
  }

  return (
    <>
      <button
        className={styles.trigger}
        onClick={() => setAberto(true)}
        aria-label="Lançar aposta manualmente"
        title="Lançar aposta manualmente"
      >
        <PlusIcon size={15} />
      </button>

      {aberto ? (
        <div className={styles.overlay} onMouseDown={() => setAberto(false)}>
          <div
            className={styles.dialog}
            role="dialog"
            aria-modal="true"
            aria-label="Nova aposta"
            onMouseDown={(e) => e.stopPropagation()}
          >
            <div className={styles.header}>
              <h2 className={styles.titulo}>Nova aposta</h2>
              <button className={styles.fechar} onClick={() => setAberto(false)} aria-label="Fechar">
                <XIcon size={15} />
              </button>
            </div>

            <p className={styles.ajuda}>
              Para jogos que o pipeline não encontrou automaticamente.
            </p>

            <div className={styles.grid}>
              <label className={styles.campo}>
                <span className={styles.label}>Esporte</span>
                <select
                  className={styles.input}
                  value={esporte}
                  onChange={(e) => setEsporte(e.target.value as Esporte)}
                >
                  {ESPORTE_VALUES.map((e) => (
                    <option key={e} value={e}>
                      {e === "tenis" ? "Tênis" : "Basquete"}
                    </option>
                  ))}
                </select>
              </label>

              <label className={styles.campo}>
                <span className={styles.label}>Data / hora do jogo</span>
                <input
                  type="datetime-local"
                  className={styles.input}
                  value={dataHora}
                  onChange={(e) => setDataHora(e.target.value)}
                />
              </label>

              <label className={`${styles.campo} ${styles.campoLargo}`}>
                <span className={styles.label}>
                  {esporte === "basquete" ? "Time 1" : "Jogador 1"} *
                </span>
                <input
                  className={styles.input}
                  value={jogador1}
                  onChange={(e) => setJogador1(e.target.value)}
                  placeholder={esporte === "basquete" ? "Illawarra Hawks" : "Nome do jogador"}
                />
              </label>

              <label className={`${styles.campo} ${styles.campoLargo}`}>
                <span className={styles.label}>
                  {esporte === "basquete" ? "Time 2" : "Jogador 2"}
                </span>
                <input
                  className={styles.input}
                  value={jogador2}
                  onChange={(e) => setJogador2(e.target.value)}
                  placeholder={esporte === "basquete" ? "Adelaide 36ers" : "Adversário (opcional)"}
                />
              </label>

              <label className={`${styles.campo} ${styles.campoLargo}`}>
                <span className={styles.label}>Mercado</span>
                <input
                  className={styles.input}
                  value={mercado}
                  onChange={(e) => setMercado(e.target.value)}
                  placeholder="Mais de 198.5 pontos"
                />
              </label>

              <label className={`${styles.campo} ${styles.campoLargo}`}>
                <span className={styles.label}>Torneio</span>
                <input
                  className={styles.input}
                  value={torneio}
                  onChange={(e) => setTorneio(e.target.value)}
                  placeholder="NBL Blitz (pré-temporada)"
                />
              </label>

              <label className={styles.campo}>
                <span className={styles.label}>Odd</span>
                <input
                  className={styles.input}
                  value={odd}
                  onChange={(e) => setOdd(e.target.value)}
                  inputMode="decimal"
                  placeholder="1.95"
                />
              </label>

              <label className={styles.campo}>
                <span className={styles.label}>Unidades</span>
                <input
                  className={styles.input}
                  value={unidades}
                  onChange={(e) => setUnidades(e.target.value)}
                  inputMode="decimal"
                  placeholder="1"
                />
              </label>

              <label className={`${styles.campo} ${styles.campoLargo}`}>
                <span className={styles.label}>Resultado</span>
                <select
                  className={styles.input}
                  value={resultado}
                  onChange={(e) => setResultado(e.target.value as ResultadoAposta)}
                >
                  <option value="pendente">Pendente</option>
                  <option value="green">Green</option>
                  <option value="red">Red</option>
                  <option value="void">Void</option>
                  <option value="cashout">Cashout</option>
                </select>
              </label>
            </div>

            {erro ? <div className={styles.erro}>{erro}</div> : null}

            <div className={styles.acoes}>
              <button className={styles.cancelar} onClick={() => setAberto(false)} disabled={salvando}>
                Cancelar
              </button>
              <button className={styles.salvar} onClick={salvar} disabled={salvando}>
                {salvando ? "Salvando..." : "Salvar aposta"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
