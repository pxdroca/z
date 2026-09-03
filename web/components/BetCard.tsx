"use client";

import { useState } from "react";
import type { Bet, BetStatus, ResultadoAposta } from "@/lib/types";
import { RESULTADO_CSS_CLASS, RESULTADO_LABEL, STATUS_CSS_CLASS, STATUS_LABEL } from "@/lib/labels";
import { BookmakerButtons } from "./BookmakerButtons";
import { StatusResultEditor } from "./StatusResultEditor";
import { AlertIcon, CheckIcon, CoinIcon, DotIcon, HourglassIcon, MinusIcon, PencilIcon, SquareIcon, TrophyIcon, XIcon } from "./icons";
import styles from "./BetCard.module.css";

const STATUS_CLASS_MAP: Record<string, string> = {
  "status-agendada": styles.statusAgendada,
  "status-ao-vivo": styles.statusAoVivo,
  "status-alerta": styles.statusAlerta,
  "status-encerrada": styles.statusEncerrada,
};

const RESULTADO_CLASS_MAP: Record<string, string> = {
  "resultado-green": styles.resultadoGreen,
  "resultado-red": styles.resultadoRed,
  "resultado-void": styles.resultadoVoid,
  "resultado-pendente": styles.resultadoPendente,
  "resultado-cashout": styles.resultadoCashout,
};

function StatusIcon({ status }: { status: BetStatus }) {
  switch (status) {
    case "agendada":
      return <DotIcon color="var(--green)" />;
    case "ao_vivo":
      // Pulso de "live" (halo transparente crescendo/desvanecendo atrás
      // do ponto sólido) — pedido do usuário, mesmo efeito de indicador
      // ao vivo comum em players de vídeo.
      return <DotIcon color="var(--red)" pulse />;
    case "encerrada":
      return <SquareIcon color="var(--muted)" />;
    default:
      return <AlertIcon color="var(--lime)" />;
  }
}

function ResultadoIcon({ resultado }: { resultado: ResultadoAposta }) {
  switch (resultado) {
    case "green":
      return <CheckIcon color="var(--green)" />;
    case "red":
      return <XIcon color="var(--red)" />;
    case "void":
      return <MinusIcon color="var(--muted)" />;
    case "cashout":
      return <CoinIcon color="var(--amber)" />;
    default:
      // Parada (usuário achou o giro contínuo pouco elaborado/não gostou
      // do resultado — reverte pra ícone estático até pensarmos numa
      // animação mais trabalhada).
      return <HourglassIcon color="var(--lime)" />;
  }
}

function formatarData(isoString: string | null): string {
  if (!isoString) return "não encontrada";
  const d = new Date(isoString);
  const dia = String(d.getDate()).padStart(2, "0");
  const mes = String(d.getMonth() + 1).padStart(2, "0");
  const hora = String(d.getHours()).padStart(2, "0");
  const min = String(d.getMinutes()).padStart(2, "0");
  return `${dia}/${mes} ${hora}:${min}`;
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
  const temPlacarFinal = bet.status === "encerrada" && Boolean(bet.placar_final);

  const cardClasse = [
    styles.card,
    editando ? styles.cardComEditor : "",
    temPlacarAoVivo ? styles.comPlacarAoVivo : "",
  ]
    .filter(Boolean)
    .join(" ");

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
      <div className={cardClasse}>
        <div className={styles.torneio}>{bet.torneio || "Torneio não identificado"}</div>

        <div className={styles.headerRow}>
          <div className={styles.jogo}>{bet.jogo}</div>
          <div className={styles.badgesRow}>
            <span className={`${styles.statusBadge} ${STATUS_CLASS_MAP[STATUS_CSS_CLASS[bet.status]]}`}>
              <StatusIcon status={bet.status} />
              {STATUS_LABEL[bet.status]}
            </span>
            <span className={`${styles.resultadoBadge} ${RESULTADO_CLASS_MAP[RESULTADO_CSS_CLASS[bet.resultado]]}`}>
              <ResultadoIcon resultado={bet.resultado} />
              {RESULTADO_LABEL[bet.resultado]}
            </span>
            <button
              className={styles.editButton}
              onClick={() => setEditando((v) => !v)}
              aria-label={editando ? "Fechar edição" : "Editar status/resultado"}
              title={editando ? "Fechar edição" : "Editar status/resultado"}
            >
              {editando ? <XIcon /> : <PencilIcon />}
            </button>
          </div>
        </div>

        <div className={styles.mercadoLabel}>Mercado</div>
        <div className={styles.mercadoValor}>{bet.mercado || "Mercado não identificado"}</div>

        <div className={styles.infoRow}>
          <div>
            <div className={styles.infoLabel}>Odd</div>
            <div className={styles.infoValor}>{bet.odd !== null ? bet.odd.toFixed(2) : "—"}</div>
          </div>
          <div>
            <div className={styles.infoLabel}>Unidades</div>
            <div className={styles.infoValor}>{bet.unidades.toFixed(1)}</div>
          </div>
          <div>
            <div className={styles.infoLabel}>Data/Hora</div>
            <div className={styles.infoValor}>{formatarData(bet.data_hora)}</div>
          </div>
        </div>

        {temPlacarAoVivo ? <div className={styles.placarAoVivo}>{bet.placar_final}</div> : null}

        {temPlacarFinal ? (
          <div className={styles.placarFinal}>
            <TrophyIcon color="var(--green)" />
            {bet.vencedor_partida || "?"} venceu <span className={styles.placarNumeros}>{bet.placar_final}</span>
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
