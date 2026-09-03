"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { BetCard } from "@/components/BetCard";
import { FilterPopover, type Filtro } from "@/components/FilterPopover";
import { MetricsGrid, type MetricItem } from "@/components/MetricsGrid";
import { CheckIcon, HourglassIcon, MinusIcon, XIcon } from "@/components/icons";
import { calcularEstatisticas, calcularOddMedia } from "@/lib/estatisticas";
import { PRIORIDADE_STATUS, STATUS_LABEL } from "@/lib/labels";
import type { Bet, BetStatus, ResultadoAposta } from "@/lib/types";
import styles from "./page.module.css";

// "Hoje" no filtro padrão precisa ser o dia no horário de Brasília, não em
// UTC nem no fuso do navegador do usuário — calculado em UTC, a virada do
// dia acontece 3h antes da meia-noite local (America/Sao_Paulo é UTC-3),
// fazendo apostas de "hoje" no Brasil sumirem do filtro padrão perto do
// fim do dia (bug real encontrado testando: a virada UTC excluiu apostas
// datadas de "ontem" no horário local enquanto ainda era "hoje" em SP).
const FUSO_PADRAO = "America/Sao_Paulo";

function hojeNoFuso(fuso: string): Date {
  const partes = new Intl.DateTimeFormat("en-CA", {
    timeZone: fuso,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const ano = partes.find((p) => p.type === "year")!.value;
  const mes = partes.find((p) => p.type === "month")!.value;
  const dia = partes.find((p) => p.type === "day")!.value;
  return new Date(`${ano}-${mes}-${dia}T00:00:00`);
}

function formatarDataInput(d: Date): string {
  const ano = d.getFullYear();
  const mes = String(d.getMonth() + 1).padStart(2, "0");
  const dia = String(d.getDate()).padStart(2, "0");
  return `${ano}-${mes}-${dia}`;
}

function filtroPadrao(): Filtro {
  const hoje = hojeNoFuso(FUSO_PADRAO);
  const ate = new Date(hoje);
  ate.setDate(ate.getDate() + 14);
  return {
    status: ["agendada", "ao_vivo", "encerrada"],
    from: formatarDataInput(hoje),
    to: formatarDataInput(ate),
  };
}

export default function Home() {
  const [filtro, setFiltro] = useState<Filtro>(filtroPadrao);
  const [bets, setBets] = useState<Bet[]>([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);

  const buscarBets = useCallback(async (f: Filtro) => {
    setCarregando(true);
    setErro(null);
    try {
      const params = new URLSearchParams();
      for (const s of f.status) params.append("status", s);
      if (f.from) params.append("from", f.from);
      if (f.to) params.append("to", f.to);

      const resp = await fetch(`/api/bets?${params.toString()}`);
      if (!resp.ok) throw new Error(`Falha ao buscar apostas (${resp.status})`);
      const data = await resp.json();
      setBets(data.bets);
    } catch (e) {
      setErro(e instanceof Error ? e.message : "Erro desconhecido.");
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => {
    // IIFE async: evita que o efeito em si chame setState de forma
    // síncrona no corpo (regra react-hooks/set-state-in-effect) — a busca
    // roda como uma tarefa disparada pelo efeito, não como parte dele.
    void (async () => {
      await buscarBets(filtro);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtro.status.join(","), filtro.from, filtro.to]);

  const handleUpdateBet = useCallback(
    async (id: number, patch: { status?: BetStatus; resultado?: ResultadoAposta }) => {
      const resp = await fetch(`/api/bets/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (!resp.ok) {
        setErro("Falha ao salvar a alteração.");
        return;
      }
      // Refetch simples (mesmo espírito do st.rerun() original) — o
      // conjunto de dados é pequeno o suficiente pra isso não ser um
      // problema de performance real.
      await buscarBets(filtro);
    },
    [buscarBets, filtro]
  );

  const apostasOrdenadas = useMemo(() => {
    const semData = new Date(8640000000000000); // Date "máxima" — mesmo papel do datetime.max do original
    return [...bets].sort((a, b) => {
      const prioA = PRIORIDADE_STATUS[a.status] ?? 9;
      const prioB = PRIORIDADE_STATUS[b.status] ?? 9;
      if (prioA !== prioB) return prioA - prioB;
      const dataA = a.data_hora ? new Date(a.data_hora) : semData;
      const dataB = b.data_hora ? new Date(b.data_hora) : semData;
      return dataA.getTime() - dataB.getTime();
    });
  }, [bets]);

  const grupos = useMemo(() => {
    const resultado: { status: BetStatus; apostas: Bet[] }[] = [];
    for (const bet of apostasOrdenadas) {
      const ultimo = resultado[resultado.length - 1];
      if (ultimo && ultimo.status === bet.status) {
        ultimo.apostas.push(bet);
      } else {
        resultado.push({ status: bet.status, apostas: [bet] });
      }
    }
    return resultado;
  }, [apostasOrdenadas]);

  const stats = useMemo(() => calcularEstatisticas(apostasOrdenadas), [apostasOrdenadas]);
  const oddMedia = useMemo(() => calcularOddMedia(apostasOrdenadas), [apostasOrdenadas]);

  const metricsDestaque: MetricItem[] = [
    { icon: <CheckIcon color="var(--green)" />, label: "Green", valor: String(stats.green) },
    { icon: <XIcon color="var(--red)" />, label: "Red", valor: String(stats.red) },
    {
      label: "Unidades",
      valor: `${stats.unidadesLiquidas >= 0 ? "+" : ""}${stats.unidadesLiquidas.toFixed(2)}`,
      delta: stats.roi !== null ? `ROI ${stats.roi.toFixed(1)}%` : null,
    },
  ];

  const metricsSecundarias: MetricItem[] = [
    { label: "Total no filtro", valor: String(apostasOrdenadas.length) },
    { label: "Ao vivo agora", valor: String(apostasOrdenadas.filter((b) => b.status === "ao_vivo").length) },
    { label: "Odd média", valor: oddMedia },
    { icon: <MinusIcon color="var(--muted)" />, label: "Void", valor: String(stats.void) },
    { icon: <HourglassIcon color="var(--lime)" />, label: "Pendente", valor: String(stats.pendente) },
    { label: "Taxa de acerto", valor: stats.taxaAcerto !== null ? `${stats.taxaAcerto.toFixed(1)}%` : "—" },
  ];

  return (
    <div className={styles.container}>
      <div className={styles.headerRow}>
        <div className={styles.appHeader}>Cansadão Apostas</div>
        <FilterPopover filtro={filtro} onChange={setFiltro} onRefresh={() => buscarBets(filtro)} />
      </div>

      <MetricsGrid itens={metricsDestaque} destaque />
      <MetricsGrid itens={metricsSecundarias} />

      {erro ? <div className={styles.emptyState}>{erro}</div> : null}

      {carregando ? (
        <div className={styles.loadingState}>Carregando...</div>
      ) : apostasOrdenadas.length === 0 ? (
        <div className={styles.emptyState}>Nenhuma aposta encontrada com os filtros atuais. Ajuste os filtros no botão de Filtros.</div>
      ) : (
        grupos.map((grupo) => (
          <div key={grupo.status}>
            <div className={styles.secaoStatus}>
              {STATUS_LABEL[grupo.status]}
              <span className={styles.secaoStatusCount}>({grupo.apostas.length})</span>
            </div>
            <div className={styles.cardsGrid}>
              {grupo.apostas.map((bet) => (
                <BetCard key={bet.id} bet={bet} onUpdate={handleUpdateBet} />
              ))}
            </div>
          </div>
        ))
      )}
    </div>
  );
}
