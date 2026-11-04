import type { NodeRow } from '../red';

export type FlowPhase = 'connecting' | 'sent' | 'returning' | 'error';
export interface Flow {
  targetId: string;
  phase: FlowPhase;
}

interface Props {
  myId: string;
  nodes: NodeRow[];
  flow: Flow | null;
}

const CENTER = 200;
const RADIUS = 148;

function pointFor(index: number, count: number) {
  const angle = (2 * Math.PI * index) / count - Math.PI / 2;
  return {
    x: CENTER + RADIUS * Math.cos(angle),
    y: CENTER + RADIUS * Math.sin(angle),
  };
}

const NetworkView = ({ myId, nodes, flow }: Props) => {
  const others = nodes.filter((n) => n.$id !== myId);
  const target = flow ? others.find((n) => n.$id === flow.targetId) : null;

  return (
    <div className="w-full h-full flex flex-col">
      <div className="text-[11px] tracking-[0.2em] text-paper-faint uppercase mb-3 shrink-0">
        Simulador — cómo viaja una pregunta por la red
      </div>

      <div className="flex-1 min-h-0 rounded border border-line grid-surface flex items-center justify-center relative overflow-hidden">
        <svg viewBox="0 0 400 400" className="w-full h-full max-w-[520px] max-h-[520px]">
          {/* connecting lines */}
          {others.map((n, i) => {
            const p = pointFor(i, others.length || 1);
            const isTarget = target?.$id === n.$id;
            return (
              <line
                key={`line-${n.$id}`}
                x1={CENTER}
                y1={CENTER}
                x2={p.x}
                y2={p.y}
                stroke={isTarget ? 'var(--color-paper)' : 'var(--color-line-strong)'}
                strokeWidth={isTarget ? 1.5 : 1}
                strokeDasharray={n.activeTasks > 0 ? '0' : '3 4'}
              />
            );
          })}

          {/* traveling pulse */}
          {target && flow && (
            <circle r={4} fill="var(--color-paper)">
              <animateMotion
                key={`${flow.targetId}-${flow.phase}`}
                dur="0.9s"
                repeatCount={flow.phase === 'connecting' ? 'indefinite' : '1'}
                fill="freeze"
                path={
                  flow.phase === 'returning'
                    ? `M ${pointFor(others.findIndex((n) => n.$id === flow.targetId), others.length).x} ${pointFor(others.findIndex((n) => n.$id === flow.targetId), others.length).y} L ${CENTER} ${CENTER}`
                    : `M ${CENTER} ${CENTER} L ${pointFor(others.findIndex((n) => n.$id === flow.targetId), others.length).x} ${pointFor(others.findIndex((n) => n.$id === flow.targetId), others.length).y}`
                }
              />
            </circle>
          )}

          {/* error mark on the target node */}
          {target && flow?.phase === 'error' && (() => {
            const p = pointFor(others.findIndex((n) => n.$id === flow.targetId), others.length);
            return (
              <g stroke="var(--color-paper)" strokeWidth={1.5}>
                <line x1={p.x - 7} y1={p.y - 7} x2={p.x + 7} y2={p.y + 7} />
                <line x1={p.x - 7} y1={p.y + 7} x2={p.x + 7} y2={p.y - 7} />
              </g>
            );
          })()}

          {/* other nodes */}
          {others.map((n, i) => {
            const p = pointFor(i, others.length || 1);
            const isTarget = target?.$id === n.$id;
            return (
              <g key={n.$id}>
                <circle
                  cx={p.x}
                  cy={p.y}
                  r={isTarget ? 9 : 7}
                  fill={n.activeTasks > 0 ? 'var(--color-paper)' : 'var(--color-ink)'}
                  stroke="var(--color-paper)"
                  strokeWidth={1.5}
                />
                <text
                  x={p.x}
                  y={p.y + 20}
                  textAnchor="middle"
                  fontSize="9"
                  fill="var(--color-paper-dim)"
                  fontFamily="var(--font-mono)"
                >
                  {n.$id.substring(0, 6)}
                </text>
                <text
                  x={p.x}
                  y={p.y + 31}
                  textAnchor="middle"
                  fontSize="8"
                  fill="var(--color-line-strong)"
                  fontFamily="var(--font-mono)"
                >
                  {n.cores}c{n.deviceMemoryGb ? `/${n.deviceMemoryGb}gb` : ''}
                </text>
              </g>
            );
          })}

          {/* self, at center */}
          <circle cx={CENTER} cy={CENTER} r={11} fill="var(--color-paper)" />
          <text
            x={CENTER}
            y={CENTER + 26}
            textAnchor="middle"
            fontSize="9"
            fill="var(--color-paper-dim)"
            fontFamily="var(--font-mono)"
          >
            TÚ
          </text>
        </svg>

        {others.length === 0 && (
          <div className="absolute bottom-4 text-[11px] text-paper-faint font-mono">
            Sin otros nodos conectados todavía.
          </div>
        )}
      </div>
    </div>
  );
};

export default NetworkView;
