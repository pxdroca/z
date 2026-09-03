"use client";

import { useState } from "react";
import type { Bet, BetStatus, ResultadoAposta } from "@/lib/types";
import { RESULTADO_LABEL, STATUS_LABEL } from "@/lib/labels";
import { BookmakerButtons } from "./BookmakerButtons";
import { StatusResultEditor } from "./StatusResultEditor";
import { AlertIcon, CheckIcon, CoinIcon, DotIcon, HourglassIcon, MinusIcon, PencilIcon, XIcon } from "./icons";
import styles from "./BetCard.module.css";

/** Cor de acento por resultado da aposta — usada só no ícone e no texto
 *  do status e no nome do vencedor, nunca como fundo cheio do card. */
const COR_RESULTADO: Record<ResultadoAposta, string> = {
  green: "var(--green)",
  cashout: "var(--amber)",
  red: "var(--red)",
  void: "var(--neutral)",
  pendente: "var(--amber)",
};

function ResultadoIcon({ resultado, size = 13 }: { resultado: ResultadoAposta; size?: number }) {
  const cor = COR_RESULTADO[resultado];
  switch (resultado) {
    case "green":
      return <CheckIcon color={cor} size={size} />;
    case "red":
      return <XIcon color={cor} size={size} />;
    case "void":
      return <MinusIcon color={cor} size={size} />;
    case "cashout":
      return <CoinIcon color={cor} size={size} />;
    default:
      return <HourglassIcon color={cor} size={size} />;
  }
}

function StatusPartidaIcon({ status }: { status: BetStatus }) {
  switch (status) {
    case "ao_vivo":
      return <DotIcon color="var(--red)" size={6} pulse />;
    case "agendada":
      return <DotIcon color="var(--neutral)" size={5} />;
    case "nao_encontrada":
    case "erro_extracao":
      return <AlertIcon color="var(--text-muted)" size={11} />;
    default:
      return null;
  }
}

function formatarData(isoString: string | null): string {
  if (!isoString) return "—";
  const d = new Date(isoString);
  const dia = String(d.getDate()).padStart(2, "0");
  const mes = String(d.getMonth() + 1).padStart(2, "0");
  const hora = String(d.getHours()).padStart(2, "0");
  const min = String(d.getMinutes()).padStart(2, "0");
  return `${dia}/${mes} · ${hora}:${min}`;
}

/** "6-3, 2-6, 2-6" -> "6–3 · 2–6 · 2–6" (traço meia-risca + separador
 *  ponto, como na direção visual). Puramente cosmético. */
function formatarPlacar(placar: string): string {
  return placar
    .split(",")
    .map((s) => s.trim().replace("-", "–"))
    .join(" · ");
}

export function BetCard({
  bet,
  onUpdate,
}: {
  bet: Bet;
  onUpdate: (id: number, patch: { status?: BetStatus; resultado?: ResultadoAposta }) => Promise<void>;
}) {
  const [editando, setEditando] = useState(false);
  const [salvando, setSalvando] = useState(false);

  const temPlacarAoVivo = bet.status === "ao_vivo" && Boolean(bet.placar_final);
  const temResultadoFinal = bet.status === "encerrada" && Boolean(bet.placar_final);
  const corResultado = COR_RESULTADO[bet.resultado];

  async function handleChangeStatus(novo: BetStatus) {
    setSalvando(true);
    try {
      await onUpdate(bet.id, { status: novo });
    } finally {
      setSalvando(false);
    }
  }

  async function handleChangeResultado(novo: ResultadoAposta) {
    setSalvando(true);
    try {
      await onUpdate(bet.id, { resultado: novo });
    } finally {
      setSalvando(false);
    }
  }

  return (
    <div>
      {/* data-resultado alimenta o glow ambiental do canto (ver
          BetCard.module.css). Fica num atributo em vez de classe pra o
          CSS resolver a cor sozinho — nenhuma lógica de cor no TSX. */}
      <div
        className={editando ? `${styles.card} ${styles.cardComEditor}` : styles.card}
        data-resultado={bet.resultado}
      >
        {/* Lensing de borda do vidro (ver BetCard.module.css) — puramente
            decorativo, precisa de elemento próprio por causa do mask. */}
        <span className={styles.lensing} aria-hidden="true" />
        <div className={styles.topRow}>
          <span className={styles.statusInline} style={{ color: corResultado }}>
            <ResultadoIcon resultado={bet.resultado} />
            {RESULTADO_LABEL[bet.resultado]}
          </span>

          <div className={styles.acoes}>
            <span
              className={
                bet.status === "ao_vivo"
                  ? `${styles.statusPartida} ${styles.statusPartidaAoVivo}`
                  : styles.statusPartida
              }
            >
              <StatusPartidaIcon status={bet.status} />
              {STATUS_LABEL[bet.status]}
            </span>
            <button
              className={styles.editButton}
              onClick={() => setEditando((v) => !v)}
              aria-label={editando ? "Fechar edição" : "Editar status/resultado"}
              title={editando ? "Fechar edição" : "Editar status/resultado"}
            >
              {editando ? <XIcon size={14} /> : <PencilIcon size={14} />}
            </button>
          </div>
        </div>

        <div className={styles.jogo}>{bet.jogo}</div>
        <div className={styles.torneio}>{bet.torneio || "Torneio não identificado"}</div>

        <div className={styles.label}>Mercado</div>
        <div className={styles.mercadoValor}>{bet.mercado || "Mercado não identificado"}</div>

        <div className={styles.infoRow}>
          <div>
            <div className={styles.label}>Odd</div>
            <div className={styles.infoValor}>{bet.odd !== null ? bet.odd.toFixed(2) : "—"}</div>
          </div>
          <div>
            <div className={styles.label}>Unidades</div>
            <div className={styles.infoValor}>{bet.unidades.toFixed(1)}</div>
          </div>
          <div>
            <div className={styles.label}>Data / Hora</div>
            <div className={styles.infoValor}>{formatarData(bet.data_hora)}</div>
          </div>
        </div>

        {temPlacarAoVivo ? (
          <div className={styles.placarAoVivo}>
            <span className={styles.placarAoVivoLabel}>Parcial</span>
            <span className={styles.resultadoPlacar}>{formatarPlacar(bet.placar_final!)}</span>
          </div>
        ) : null}

        {temResultadoFinal ? (
          <div className={styles.resultadoBox}>
            <div className={styles.label}>Resultado</div>
            <div className={styles.resultadoLinha} style={{ color: corResultado }}>
              <ResultadoIcon resultado={bet.resultado} size={12} />
              {bet.vencedor_partida || "?"} venceu
            </div>
            <div className={styles.resultadoPlacar}>{formatarPlacar(bet.placar_final!)}</div>
          </div>
        ) : null}

        <BookmakerButtons links={bet.links} />
      </div>

      {editando ? (
        <StatusResultEditor
          status={bet.status}
          resultado={bet.resultado}
          onChangeStatus={handleChangeStatus}
          onChangeResultado={handleChangeResultado}
          disabled={salvando}
        />
      ) : null}
    </div>
  );
}
