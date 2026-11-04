interface Props {
  total: number;
  idle: number;
  busy: number;
}

const R = 26;
const CIRCUMFERENCE = 2 * Math.PI * R;

const CountBadge = ({ total, idle, busy }: Props) => {
  const pct = total > 0 ? busy / total : 0;

  return (
    <div className="flex items-center gap-3">
      <svg width="60" height="60" viewBox="0 0 64 64" className="shrink-0">
        <circle cx="32" cy="32" r={R} fill="none" stroke="var(--color-line)" strokeWidth="3" />
        <circle
          cx="32"
          cy="32"
          r={R}
          fill="none"
          stroke="var(--color-paper)"
          strokeWidth="3"
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={CIRCUMFERENCE * (1 - pct)}
          strokeLinecap="round"
          transform="rotate(-90 32 32)"
          style={{ transition: 'stroke-dashoffset 0.4s ease' }}
        />
        <text x="32" y="38" textAnchor="middle" fontSize="19" fontFamily="var(--font-display)" fontWeight="700" fill="var(--color-paper)">
          {total}
        </text>
      </svg>
      <div className="text-[10px] font-mono text-paper-faint leading-[1.4] tracking-wide">
        <div>{idle} LIBRES</div>
        <div>{busy} OCUPADOS</div>
      </div>
    </div>
  );
};

export default CountBadge;
