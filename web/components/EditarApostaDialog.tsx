"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { RESULTADO_LABEL, STATUS_LABEL } from "@/lib/labels";
import {
  BET_STATUS_VALUES,
  ESPORTE_VALUES,
  RESULTADO_APOSTA_VALUES,
  type Bet,
  type BetStatus,
  type Esporte,
  type ResultadoAposta,
} from "@/lib/types";
import { XIcon } from "./icons";
import styles from "./NovaApostaDialog.module.css";

const FUSO_PADRAO = "America/Sao_Paulo";

/**
 * Converte o `data_hora` da aposta (ISO em UTC) para o valor que um
 * `<input type="datetime-local">` espera: "YYYY-MM-DDTHH:mm" no horário
 * de BRASÍLIA.
 *
 * Não dá pra usar `toISOString().slice(0,16)` — isso mostraria UTC, e o
 * usuário digitaria pensando no horário local, gravando um jogo 3h
 * deslocado. É o mesmo erro que já colocou jogos de manhã como "ao vivo
 * de madrugada" neste projeto.
 */
function paraInputLocal(iso: string | null): string {
  if (!iso) return "";
  const partes = new Intl.DateTimeFormat("sv-SE", {
    timeZone: FUSO_PADRAO,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date(iso));
  const p = (t: string) => partes.find((x) => x.type === t)?.value ?? "";
  return `${p("year")}-${p("month")}-${p("day")}T${p("hour")}:${p("minute")}`;
}

/** Caminho inverso: o que o usuário digitou (horário de Brasília) vira
 *  ISO em UTC, que é o que a API e o banco guardam. */
function paraIsoUtc(local: string): string | null {
  if (!local) return null;
  // O "-03:00" fixo é o fuso de Brasília, que não tem mais horário de
  // verão desde 2019 — o mesmo pressuposto do resto do projeto.
  return new Date(`${local}:00-03:00`).toISOString();
}

/**
 * Edição completa de uma aposta.
 *
 * Antes o lápis do card só trocava status e resultado; corrigir um
 * confronto errado, um horário ou uma odd exigia mexer no banco à mão.
 * Aqui todo campo que o painel exibe pode ser corrigido por quem vê o
 * erro na tela.
 *
 * Reaproveita o CSS do NovaApostaDialog: são o mesmo tipo de formulário,
 * e duplicar o arquivo só faria as duas telas divergirem com o tempo.
 */
export function EditarApostaDialog({
  bet,
  aberto,
  onFechar,
  onSalva,
}: {
  bet: Bet;
  aberto: boolean;
  onFechar: () => void;
  onSalva: () => void;
}) {
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  const [jogador1, setJogador1] = useState(bet.jogador1 ?? "");
  const [jogador2, setJogador2] = useState(bet.jogador2 ?? "");
  const [torneio, setTorneio] = useState(bet.torneio ?? "");
  const [mercado, setMercado] = useState(bet.mercado ?? "");
  const [odd, setOdd] = useState(bet.odd != null ? String(bet.odd) : "");
  const [unidades, setUnidades] = useState(String(bet.unidades ?? 1));
  const [esporte, setEsporte] = useState<Esporte>((bet.esporte as Esporte) ?? "tenis");
  const [status, setStatus] = useState<BetStatus>(bet.status);
  const [resultado, setResultado] = useState<ResultadoAposta>(bet.resultado);
  const [dataHora, setDataHora] = useState(paraInputLocal(bet.data_hora));
  const [placar, setPlacar] = useState(bet.placar_final ?? "");
  const [vencedor, setVencedor] = useState(bet.vencedor_partida ?? "");

  useEffect(() => {
    if (!aberto) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onFechar();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [aberto, onFechar]);

  async function salvar() {
    if (!jogador1.trim()) {
      setErro("O primeiro jogador/time não pode ficar vazio.");
      return;
    }
    setSalvando(true);
    setErro(null);
    try {
      const resp = await fetch(`/api/bets/${bet.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          jogador1,
          jogador2,
          torneio,
          mercado,
          odd,
          unidades,
          esporte,
          status,
          resultado,
          data_hora: paraIsoUtc(dataHora),
          placar_final: placar,
          vencedor_partida: vencedor,
        }),
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        setErro(data.error ?? "Falha ao salvar as alterações.");
        return;
      }
      onFechar();
      onSalva();
    } catch {
      setErro("Falha de rede ao salvar as alterações.");
    } finally {
      setSalvando(false);
    }
  }

  if (!aberto) return null;

  // Portal pro <body> pela mesma razão do NovaApostaDialog: no mobile o
  // card pode estar sob camadas com transform/overflow próprios, que
  // prendem um modal renderizado ali dentro.
  return createPortal(
    <div className={styles.overlay} onMouseDown={onFechar}>
      <div
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-label={`Editar aposta #${bet.id}`}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className={styles.header}>
          <h2 className={styles.titulo}>Editar aposta</h2>
          <button className={styles.fechar} onClick={onFechar} aria-label="Fechar">
            <XIcon size={15} />
          </button>
        </div>

        <p className={styles.ajuda}>
          Corrija qualquer campo. Deixar em branco apaga o valor.
        </p>

        <div className={styles.grid}>
          <label className={styles.campo}>
            <span className={styles.label}>Jogador/Time 1</span>
            <input
              className={styles.input}
              value={jogador1}
              onChange={(e) => setJogador1(e.target.value)}
              placeholder="Ex: Taylor Fritz"
            />
          </label>

          <label className={styles.campo}>
            <span className={styles.label}>Jogador/Time 2</span>
            <input
              className={styles.input}
              value={jogador2}
              onChange={(e) => setJogador2(e.target.value)}
              placeholder="Ex: Francisco Cerundolo"
            />
          </label>

          <label className={styles.campoLargo}>
            <span className={styles.label}>Torneio</span>
            <input
              className={styles.input}
              value={torneio}
              onChange={(e) => setTorneio(e.target.value)}
              placeholder="Ex: US Open (M) - Terceira Rodada"
            />
          </label>

          <label className={styles.campoLargo}>
            <span className={styles.label}>Mercado</span>
            <input
              className={styles.input}
              value={mercado}
              onChange={(e) => setMercado(e.target.value)}
              placeholder="Ex: Cerundolo vencer a partida"
            />
          </label>

          <label className={styles.campo}>
            <span className={styles.label}>Odd</span>
            <input
              className={styles.input}
              type="number"
              step="0.01"
              min="1"
              value={odd}
              onChange={(e) => setOdd(e.target.value)}
              placeholder="Ex: 2.75"
            />
          </label>

          <label className={styles.campo}>
            <span className={styles.label}>Unidades</span>
            <input
              className={styles.input}
              type="number"
              step="0.1"
              min="0"
              value={unidades}
              onChange={(e) => setUnidades(e.target.value)}
            />
          </label>

          <label className={styles.campoLargo}>
            <span className={styles.label}>Data e hora (Brasília)</span>
            <input
              className={styles.input}
              type="datetime-local"
              value={dataHora}
              onChange={(e) => setDataHora(e.target.value)}
            />
          </label>

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
            <span className={styles.label}>Status da partida</span>
            <select
              className={styles.input}
              value={status}
              onChange={(e) => setStatus(e.target.value as BetStatus)}
            >
              {BET_STATUS_VALUES.map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABEL[s]}
                </option>
              ))}
            </select>
          </label>

          <label className={styles.campo}>
            <span className={styles.label}>Resultado da aposta</span>
            <select
              className={styles.input}
              value={resultado}
              onChange={(e) => setResultado(e.target.value as ResultadoAposta)}
            >
              {RESULTADO_APOSTA_VALUES.map((r) => (
                <option key={r} value={r}>
                  {RESULTADO_LABEL[r]}
                </option>
              ))}
            </select>
          </label>

          <label className={styles.campo}>
            <span className={styles.label}>Placar final</span>
            <input
              className={styles.input}
              value={placar}
              onChange={(e) => setPlacar(e.target.value)}
              placeholder="Ex: 6-4, 3-6, 6-2"
            />
          </label>

          <label className={styles.campoLargo}>
            <span className={styles.label}>Vencedor da partida</span>
            <input
              className={styles.input}
              value={vencedor}
              onChange={(e) => setVencedor(e.target.value)}
              placeholder="Nome de quem venceu"
            />
          </label>
        </div>

        {erro ? <p className={styles.erro}>{erro}</p> : null}

        <div className={styles.acoes}>
          <button className={styles.cancelar} onClick={onFechar} disabled={salvando}>
            Cancelar
          </button>
          <button className={styles.salvar} onClick={salvar} disabled={salvando}>
            {salvando ? "Salvando…" : "Salvar"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
