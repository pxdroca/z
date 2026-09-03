// Micrográfico de linha, SVG puro (sem biblioteca de gráficos — seria peso
// desproporcional pra isso). Deliberadamente discreto: sem eixos, sem
// grade, sem labels, sem tooltip — é um detalhe de textura no card de
// estatística, não um gráfico pra ler valores.

const LARGURA = 72;
const ALTURA = 24;

export function Sparkline({ valores, color }: { valores: number[]; color: string }) {
  // Menos de 2 pontos não formam linha — não desenha nada (o card
  // simplesmente fica sem o detalhe, sem buraco no layout).
  if (valores.length < 2) return null;

  const min = Math.min(...valores);
  const max = Math.max(...valores);
  const span = max - min || 1; // série constante: linha reta no meio

  const pontos = valores.map((v, i) => {
    const x = (i / (valores.length - 1)) * LARGURA;
    // 2px de respiro em cima/embaixo pra linha não encostar na borda
    const y = ALTURA - 2 - ((v - min) / span) * (ALTURA - 4);
    return [x, y] as const;
  });

  const linha = pontos.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${linha} L${LARGURA},${ALTURA} L0,${ALTURA} Z`;
  const gradId = `spark-${color.replace(/[^a-z0-9]/gi, "")}`;

  return (
    <svg width={LARGURA} height={ALTURA} viewBox={`0 0 ${LARGURA} ${ALTURA}`} fill="none" aria-hidden="true">
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.18" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gradId})`} />
      <path d={linha} stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.85" />
    </svg>
  );
}
