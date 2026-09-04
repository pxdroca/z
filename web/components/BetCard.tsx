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

/**
 * Formata a data/hora do jogo SEMPRE no horário de Brasília.
 *
 * getDate()/getHours() usam o fuso do NAVEGADOR, não o do usuário do
 * painel — bug real: um jogo gravado às 07:00 BRT aparecia como 06:00
 * num navegador em UTC-4. Todo o resto do sistema (filtro padrão em
 * page.tsx, imagem-resumo, pipeline Python) já fixa America/Sao_Paulo;
 * este era o único ponto que ainda dependia da máquina de quem olha.
 */
function formatarData(isoString: string | null): string {
  if (!isoString) return "—";
  const partes = new Intl.DateTimeFormat("pt-BR", {
    timeZone: "America/Sao_Paulo",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date(isoString));
  const p = (tipo: string) => partes.find((x) => x.type === tipo)?.value ?? "--";
  return `${p("day")}/${p("month")} · ${p("hour")}:${p("minute")}`;
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
  // Vencedor OU placar já bastam pra mostrar a área de resultado. Exigir
  // placar escondia o bloco inteiro no basquete: o 365scores não devolve
  // placar por quarto (só o vencedor), então a aposta encerrada ficava sem
  // nenhuma informação de resultado e o card saía mais baixo que os
  // vizinhos, desalinhando a linha do grid.
  const temResultadoFinal =
    bet.status === "encerrada" && Boolean(bet.placar_final || bet.vencedor_partida);
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
    // O wrapper é o item do grid (que usa grid-auto-rows: 1fr, ver
    // page.module.css) e precisa repassar a altura esticada pro card —
    // sem isso o card só cresce até o conteúdo e cards com menos
    // informação (ex: basquete sem placar por quarto) saíam mais baixos.
    <div className={styles.wrapper}>
      {/* data-resultado alimenta o glow ambiental do canto (ver
          BetCard.module.css). Fica num atributo em vez de classe pra o
          CSS resolver a cor sozinho — nenhuma lógica de cor no TSX. */}
      <div
        className={editando ? `${styles.card} ${styles.cardComEditor}` : styles.card}
        data-resultado={bet.resultado}
        /* data-status separado: "ao vivo" é estado da PARTIDA e o
           resultado dela ainda é "pendente", então sem isto a quina de
           um jogo ao vivo acenderia em cinza em vez de âmbar. */
        data-status={bet.status}
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
            {/* Só quando existe: no basquete o placar por quarto não vem
                do 365scores, e a linha ficaria vazia. */}
            {bet.placar_final ? (
              <div className={styles.resultadoPlacar}>{formatarPlacar(bet.placar_final)}</div>
            ) : null}
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
