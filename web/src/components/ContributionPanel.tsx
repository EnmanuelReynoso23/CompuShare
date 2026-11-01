interface Props {
  loading: boolean;
  loadProgress: string;
  cores: number;
  deviceMemoryGb?: number;
  modelSizeMb?: number;
  tasksCompleted: number;
}

const Stat = ({ label, value }: { label: string; value: string }) => (
  <div>
    <div className="text-[9px] uppercase tracking-[0.2em] text-paper-faint">{label}</div>
    <div className="text-sm font-display">{value}</div>
  </div>
);

const ContributionPanel = ({ loading, loadProgress, cores, deviceMemoryGb, modelSizeMb, tasksCompleted }: Props) => {
  return (
    <div className="mx-4 sm:mx-8 mt-4 sm:mt-5 border border-paper rounded-sm px-4 sm:px-5 py-3 shrink-0">
      <div className="text-[9px] uppercase tracking-[0.25em] text-paper-faint mb-2">Lo que estás aportando a la red</div>

      {loading ? (
        <div className="text-xs text-paper-dim truncate">{loadProgress || 'Cargando modelo...'}</div>
      ) : (
        <div className="grid grid-cols-2 sm:flex sm:flex-wrap gap-x-4 gap-y-3 sm:gap-x-10 sm:gap-y-2">
          <Stat label="Núcleos CPU" value={String(cores)} />
          <Stat label="RAM del dispositivo" value={deviceMemoryGb ? `~${deviceMemoryGb} GB` : 'Safari/iPhone no lo expone'} />
          <Stat label="Modelo cargado en memoria" value={modelSizeMb ? `~${Math.round(modelSizeMb)} MB` : '—'} />
          <Stat label="Tareas resueltas" value={String(tasksCompleted)} />
        </div>
      )}
    </div>
  );
};

export default ContributionPanel;
