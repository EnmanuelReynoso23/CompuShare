import type { AggregateCapacity } from '../red';

interface Props {
  capacity: AggregateCapacity;
  totalNodes: number;
}

const Stat = ({ label, value }: { label: string; value: string }) => (
  <div>
    <div className="text-[9px] uppercase tracking-[0.2em] text-paper-faint">{label}</div>
    <div className="text-sm font-display">{value}</div>
  </div>
);

const NetworkCapacityBar = ({ capacity, totalNodes }: Props) => {
  if (totalNodes === 0) {
    return (
      <div className="mx-4 sm:mx-8 mt-4 sm:mt-5 border border-line rounded-sm px-4 sm:px-5 py-3 shrink-0 text-xs text-paper-faint">
        Nadie está donando cómputo todavía — la red está vacía.
      </div>
    );
  }

  const memoryLabel =
    capacity.nodesReportingMemory > 0
      ? `~${capacity.totalMemoryGb} GB${capacity.nodesReportingMemory < totalNodes ? ` (${capacity.nodesReportingMemory}/${totalNodes} nodos con dato)` : ''}`
      : 'Safari/iPhone no expone este dato';

  return (
    <div className="mx-4 sm:mx-8 mt-4 sm:mt-5 border border-line rounded-sm px-4 sm:px-5 py-3 shrink-0">
      <div className="text-[9px] uppercase tracking-[0.25em] text-paper-faint mb-2">
        Capacidad total de la red — suma de los {totalNodes} nodo{totalNodes === 1 ? '' : 's'} conectado{totalNodes === 1 ? '' : 's'}
      </div>
      <div className="grid grid-cols-2 sm:flex sm:flex-wrap gap-x-4 gap-y-3 sm:gap-x-10 sm:gap-y-2">
        <Stat label="Núcleos CPU totales" value={String(capacity.totalCores)} />
        <Stat label="RAM total donada" value={memoryLabel} />
        <Stat label="Tareas resueltas por la red" value={String(capacity.totalTasksCompleted)} />
      </div>
    </div>
  );
};

export default NetworkCapacityBar;
