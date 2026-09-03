"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { BetCard } from "@/components/BetCard";
import { FilterPopover, type Filtro } from "@/components/FilterPopover";
import { GerarImagemResumo } from "@/components/GerarImagemResumo";
import { MetricsGrid, type MetricItem } from "@/components/MetricsGrid";
import { NovaApostaDialog } from "@/components/NovaApostaDialog";
import { SearchInput } from "@/components/SearchInput";
import { StatusTabs, type FiltroGrupo } from "@/components/StatusTabs";
import { LayersIcon, RefreshIcon, TargetIcon, TrendDownIcon, TrendUpIcon } from "@/components/icons";
import { agruparBets, contarPorGrupo, GRUPO_LABEL, GRUPO_VAZIO } from "@/lib/agrupamento";
import { filtrarPorBusca } from "@/lib/busca";
import { calcularEstatisticas, calcularOddMedia, calcularSeries } from "@/lib/estatisticas";
import { PRIORIDADE_STATUS } from "@/lib/labels";
import { STATUS_VISIVEIS, type Bet, type BetStatus, type ResultadoAposta } from "@/lib/types";
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
    // Tudo menos erro_extracao. "nao_encontrada" ENTRA: é uma aposta real
    // que o pipeline só não conseguiu casar com um jogo (liga fora do
    // SofaScore, nome truncado pelo OCR) — sem ela no filtro padrão, uma
    // aposta lançada à mão pelo painel ficava invisível, que foi o que
    // aconteceu com a de basquete (#64).
    status: STATUS_VISIVEIS,
    from: formatarDataInput(hoje),
    to: formatarDataInput(ate),
  };
}

function horaAgora(): string {
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: FUSO_PADRAO,
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date());
}

export default function Home() {
  const [filtro, setFiltro] = useState<Filtro>(filtroPadrao);
  const [bets, setBets] = useState<Bet[]>([]);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const [busca, setBusca] = useState("");
  const [grupoAtivo, setGrupoAtivo] = useState<FiltroGrupo>("todas");
  const [atualizadoEm, setAtualizadoEm] = useState<string | null>(null);

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
      setAtualizadoEm(horaAgora());
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

  // A busca filtra só a exibição (client-side); as estatísticas seguem
  // refletindo o filtro de status/período, não o texto digitado.
  const apostasVisiveis = useMemo(
    () => filtrarPorBusca(apostasOrdenadas, busca),
    [apostasOrdenadas, busca]
  );

  const contagens = useMemo(() => contarPorGrupo(apostasVisiveis), [apostasVisiveis]);
  const grupos = useMemo(() => {
    const todos = agruparBets(apostasVisiveis);
    return grupoAtivo === "todas" ? todos : todos.filter((g) => g.id === grupoAtivo);
  }, [apostasVisiveis, grupoAtivo]);

  const stats = useMemo(() => calcularEstatisticas(apostasOrdenadas), [apostasOrdenadas]);
  const oddMedia = useMemo(() => calcularOddMedia(apostasOrdenadas), [apostasOrdenadas]);
  const series = useMemo(() => calcularSeries(apostasOrdenadas), [apostasOrdenadas]);

  const totalDecididas = stats.green + stats.red;
  const metricas: MetricItem[] = [
    {
      icon: <TrendUpIcon color="var(--green)" />,
      label: "Green",
      valor: String(stats.green),
      delta: totalDecididas > 0 ? `${((stats.green / totalDecididas) * 100).toFixed(1)}% do total` : null,
      color: "var(--green)",
      colorSoft: "var(--green-soft)",
      serie: series.green,
    },
    {
      icon: <TrendDownIcon color="var(--red)" />,
      label: "Red",
      valor: String(stats.red),
      delta: totalDecididas > 0 ? `${((stats.red / totalDecididas) * 100).toFixed(1)}% do total` : null,
      color: "var(--red)",
      colorSoft: "var(--red-soft)",
      serie: series.red,
    },
    {
      icon: <LayersIcon color="var(--blue)" />,
      label: "Unidades",
      valor: `${stats.unidadesLiquidas >= 0 ? "+" : ""}${stats.unidadesLiquidas.toFixed(2)}u`,
      delta: stats.roi !== null ? `ROI ${stats.roi.toFixed(1)}%` : null,
      color: "var(--blue)",
      colorSoft: "var(--blue-soft)",
      serie: series.unidades,
    },
    {
      icon: <TargetIcon color="var(--amber)" />,
      label: "Odd média",
      valor: oddMedia,
      delta: "Média das odds",
      color: "var(--amber)",
      colorSoft: "var(--amber-soft)",
      serie: series.odds,
    },
    {
      icon: <TargetIcon color="var(--purple)" />,
      label: "Taxa de acerto",
      valor: stats.taxaAcerto !== null ? `${stats.taxaAcerto.toFixed(1)}%` : "—",
      delta: totalDecididas > 0 ? `${stats.green} de ${totalDecididas} apostas` : null,
      color: "var(--purple)",
      colorSoft: "var(--purple-soft)",
      serie: series.taxaAcerto,
    },
  ];

  return (
    <div className={styles.container}>
      <header className={styles.header}>
        <div className={styles.headerTitulo}>
          <h1 className={styles.appHeader}>Cansadão Apostas</h1>
          <div className={styles.subtitulo}>
            Acompanhamento das suas apostas
            {atualizadoEm ? (
              <>
                <span className={styles.separador} />
                <span className={styles.atualizado}>
                  <span className={styles.pontoVivo} />
                  Última atualização: {atualizadoEm}
                </span>
              </>
            ) : null}
          </div>
        </div>

        <div className={styles.headerAcoes}>
          <SearchInput valor={busca} onChange={setBusca} />
          <button
            className={styles.refreshButton}
            onClick={() => buscarBets(filtro)}
            aria-label="Atualizar apostas"
            title="Atualizar"
          >
            <span className={carregando ? `${styles.refreshIcone} ${styles.refreshGirando}` : styles.refreshIcone}>
              <RefreshIcon size={15} />
            </span>
          </button>
          <NovaApostaDialog onCriada={() => buscarBets(filtro)} />
          <GerarImagemResumo bets={bets} />
          <FilterPopover filtro={filtro} onChange={setFiltro} />
        </div>
      </header>

      <MetricsGrid itens={metricas} />

      <StatusTabs
        ativo={grupoAtivo}
        onChange={setGrupoAtivo}
        contagens={contagens}
        total={apostasVisiveis.length}
      />

      {erro ? <div className={styles.aviso}>{erro}</div> : null}

      {carregando && bets.length === 0 ? (
        <div className={styles.aviso}>Carregando...</div>
      ) : apostasVisiveis.length === 0 ? (
        <div className={styles.aviso}>
          {busca
            ? `Nenhuma aposta encontrada para "${busca}".`
            : "Nenhuma aposta encontrada com os filtros atuais. Ajuste os filtros no botão de Filtros."}
        </div>
      ) : (
        grupos.map((grupo) => (
          <section key={grupo.id} className={styles.secao}>
            <div className={styles.secaoHeader}>
              <span className={`${styles.secaoDot} ${styles[`dot_${grupo.id}`]}`} />
              <span className={styles.secaoTitulo}>{GRUPO_LABEL[grupo.id]}</span>
              <span className={styles.secaoCount}>({grupo.apostas.length})</span>
            </div>

            {grupo.apostas.length === 0 ? (
              <div className={styles.secaoVazia}>{GRUPO_VAZIO[grupo.id]}</div>
            ) : (
              <div className={styles.cardsGrid}>
                {grupo.apostas.map((bet) => (
                  <BetCard key={bet.id} bet={bet} onUpdate={handleUpdateBet} />
                ))}
              </div>
            )}
          </section>
        ))
      )}
    </div>
  );
}
