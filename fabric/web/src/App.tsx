import { useEffect, useMemo, useRef, useState } from 'react';
import type { WebWorkerMLCEngine } from '@mlc-ai/web-llm';
import {
  Red,
  leerConfig,
  miIdentidad,
  computeNetworkStats,
  computeAggregateCapacity,
  type NodeRow,
  type MessageRow,
  type TareaEntrante,
} from './red';
import ChatView from './components/ChatView';
import NetworkView, { type Flow } from './components/NetworkView';
import HomeView from './components/HomeView';
import CountBadge from './components/CountBadge';
import ContributionPanel from './components/ContributionPanel';
import NetworkCapacityBar from './components/NetworkCapacityBar';

// Modelo que corre un navegador cuando dona (WebGPU). Los nodos de Kaggle o de
// una PC con GPU corren modelos mucho más grandes con llama.cpp (fabric/nodo).
const MODEL_ID = 'Qwen2.5-1.5B-Instruct-q4f16_1-MLC';

type Tab = 'inicio' | 'chat' | 'red';
type Turno = { role: 'user' | 'assistant'; content: string };

const TURNOS_DE_HISTORIA = 2;
const CARACTERES_POR_RESPUESTA = 400;

const App = () => {
  const config = useMemo(() => leerConfig(), []);
  const [tab, setTab] = useState<Tab>('inicio');
  const [myId, setMyId] = useState('');
  const [conectado, setConectado] = useState(false);
  const [isDonating, setIsDonating] = useState(false);
  const [modelLoading, setModelLoading] = useState(false);
  const [modelReady, setModelReady] = useState(false);
  const [loadProgress, setLoadProgress] = useState('');
  const [awaitingAnswer, setAwaitingAnswer] = useState(false);
  const [prompt, setPrompt] = useState('');
  const [sala, setSala] = useState<MessageRow[]>([]);
  const [enCurso, setEnCurso] = useState<Record<string, { texto: string; nodo: string }>>({});
  const [status, setStatus] = useState('');
  const [nodes, setNodes] = useState<NodeRow[]>([]);
  const [flow, setFlow] = useState<Flow | null>(null);
  const [modelSizeMb, setModelSizeMb] = useState<number | undefined>(undefined);
  const [donateError, setDonateError] = useState('');
  const [resueltas, setResueltas] = useState(0);

  const stats = useMemo(() => computeNetworkStats(nodes), [nodes]);
  const capacity = useMemo(() => computeAggregateCapacity(nodes), [nodes]);
  const myNode = useMemo(() => nodes.find((n) => n.$id === myId), [nodes, myId]);

  const redRef = useRef<Red | null>(null);
  const engineRef = useRef<WebWorkerMLCEngine | null>(null);
  const colaRef = useRef<TareaEntrante[]>([]);
  const procesandoRef = useRef(false);
  const atendidasRef = useRef(new Set<string>());
  const salaRef = useRef<MessageRow[]>([]);
  const deviceMemoryGb = (navigator as { deviceMemory?: number }).deviceMemory;

  // Lo que el chat enseña: la sala del relé + el texto que va llegando de lo propio.
  const chat = useMemo(
    () =>
      sala.map((m) => {
        const vivo = enCurso[m.$id];
        if (m.status !== 'pending' || !vivo) return m;
        return { ...m, status: 'answered' as const, answer: vivo.texto || '...', workerId: vivo.nodo, connectionProof: 'escribiendo...' };
      }),
    [sala, enCurso],
  );

  // ── atender tareas cuando este navegador dona ─────────────────────────────
  const procesarCola = async () => {
    const red = redRef.current;
    if (procesandoRef.current || !red) return;
    procesandoRef.current = true;
    while (colaRef.current.length > 0 && engineRef.current) {
      const tarea = colaRef.current.shift()!;
      red.carga = colaRef.current.length + 1;
      red.latir();
      setStatus('Generando una respuesta para alguien de la red...');
      try {
        const datos = (tarea.datos ?? {}) as { mensajes?: Turno[]; prompt?: string; max_tokens?: number };
        const mensajes = datos.mensajes ?? [{ role: 'user' as const, content: String(datos.prompt ?? '') }];
        const inicio = performance.now();
        const flujo = await engineRef.current.chat.completions.create({
          messages: mensajes,
          stream: true,
          max_tokens: Math.min(datos.max_tokens ?? 600, 1024),
        });
        let texto = '';
        let pendiente = '';
        let ultimo = performance.now();
        for await (const trozo of flujo) {
          const delta = trozo.choices[0]?.delta?.content ?? '';
          texto += delta;
          pendiente += delta;
          if (pendiente && performance.now() - ultimo > 250) {
            red.parcial(tarea.id, pendiente);
            pendiente = '';
            ultimo = performance.now();
          }
        }
        if (pendiente) red.parcial(tarea.id, pendiente);
        const segundos = (performance.now() - inicio) / 1000;
        red.resultado(tarea.id, { texto, modelo: MODEL_ID, segundos: Math.round(segundos * 100) / 100 });
        setResueltas((n) => n + 1);
        setStatus(`Respuesta enviada (${segundos.toFixed(1)} s).`);
      } catch (e) {
        red.fallo(tarea.id, e instanceof Error ? e.message : String(e));
        setStatus('Una tarea falló en este navegador; el nodo sigue disponible.');
      }
    }
    red.carga = 0;
    red.latir();
    procesandoRef.current = false;
  };

  // ── conexión con el relé ──────────────────────────────────────────────────
  useEffect(() => {
    if (!config.clave) {
      setStatus('Falta la clave de la red: abre el enlace de invitación (?clave=...).');
      return;
    }
    const red = new Red(config, miIdentidad(), {
      alConectar: (id) => {
        setMyId(id);
        setConectado(true);
        setStatus(`Conectado al enjambre "${config.enjambre}".`);
      },
      alDesconectar: (motivo) => {
        setConectado(false);
        setStatus(`Sin conexión con la red (${motivo}). Reintentando...`);
      },
      alCambiarNodos: setNodes,
      alCambiarSala: (lista) => {
        salaRef.current = lista;
        setSala(lista);
      },
      alRecibirTarea: (tarea) => {
        if (tarea.tipo !== 'generar' || !engineRef.current) {
          red.fallo(tarea.id, `este navegador no hace ${tarea.tipo}`);
          return;
        }
        colaRef.current.push(tarea);
        void procesarCola();
      },
      alCancelarTarea: (id) => {
        colaRef.current = colaRef.current.filter((t) => t.id !== id);
      },
    });
    redRef.current = red;
    red.conectar();
    return () => red.cerrar();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config]);

  // ── preguntas propias: cuando el relé publica mi mensaje, pido la respuesta ─
  useEffect(() => {
    const red = redRef.current;
    if (!red || !myId) return;
    for (const m of sala) {
      if (m.authorId !== myId || m.status !== 'pending' || atendidasRef.current.has(m.$id)) continue;
      atendidasRef.current.add(m.$id);
      const historia: Turno[] = salaRef.current
        .filter((x) => x.authorId === myId && x.status === 'answered' && x.answer && x.$id !== m.$id)
        .slice(-TURNOS_DE_HISTORIA)
        .flatMap((x) => [
          { role: 'user' as const, content: x.prompt },
          { role: 'assistant' as const, content: (x.answer ?? '').slice(0, CARACTERES_POR_RESPUESTA) },
        ]);
      const inicio = performance.now();
      setFlow({ targetId: '', phase: 'connecting' });
      red
        .pedir<{ texto?: string }>(
          'generar',
          { mensajes: [...historia, { role: 'user', content: m.prompt }], max_tokens: 700, pensar: false },
          (datos, nodo) => {
            setFlow({ targetId: nodo, phase: 'sent' });
            setEnCurso((prev) => ({ ...prev, [m.$id]: { texto: (prev[m.$id]?.texto ?? '') + (datos.texto ?? ''), nodo } }));
          },
        )
        .then((r) => {
          setFlow({ targetId: r.nodo, phase: 'returning' });
          red.cerrarPregunta(m.$id, r.texto ?? '(sin respuesta)', 'respondido', r.nodo, performance.now() - inicio);
        })
        .catch((e: Error) => {
          setFlow((f) => (f ? { ...f, phase: 'error' } : null));
          red.cerrarPregunta(m.$id, e.message, 'error', '', performance.now() - inicio);
        })
        .finally(() => {
          setAwaitingAnswer(false);
          setEnCurso((prev) => {
            const { [m.$id]: _, ...resto } = prev;
            return resto;
          });
          setTimeout(() => setFlow(null), 1200);
        });
    }
  }, [sala, myId]);

  const askQuestion = () => {
    const texto = prompt.trim();
    if (!texto || awaitingAnswer || !redRef.current) return;
    setAwaitingAnswer(true);
    setPrompt('');
    redRef.current.publicarPregunta(texto);
  };

  const donateCompute = async () => {
    if (isDonating || !redRef.current) return;
    setDonateError('');
    const gpu = (navigator as { gpu?: { requestAdapter: () => Promise<{ isFallbackAdapter?: boolean } | null> } }).gpu;
    const adapter = gpu ? await gpu.requestAdapter().catch(() => null) : null;
    if (!adapter || adapter.isFallbackAdapter) {
      setDonateError('Este navegador no tiene WebGPU: para donar hace falta Chrome o Edge recientes con GPU.');
      return;
    }
    setIsDonating(true);
    setModelLoading(true);
    try {
      // WebLLM pesa ~6 MB: solo se baja si este navegador decide donar.
      const { CreateWebWorkerMLCEngine } = await import('@mlc-ai/web-llm');
      const worker = new Worker(new URL('./ai/llm-worker.js', import.meta.url), { type: 'module' });
      engineRef.current = await CreateWebWorkerMLCEngine(worker, MODEL_ID, {
        initProgressCallback: (report) => {
          setLoadProgress(report.text);
          const match = report.text.match(/([\d.]+)\s*MB fetched/);
          if (match) setModelSizeMb((prev) => Math.max(prev ?? 0, parseFloat(match[1])));
        },
      });
      setModelLoading(false);
      setModelReady(true);
      const info: Record<string, string | number | boolean> = { nucleos: navigator.hardwareConcurrency || 1, modelo: MODEL_ID };
      if (deviceMemoryGb) info.memoria_gb = deviceMemoryGb;
      // Se vuelve a entrar al relé, ahora como nodo que sabe `generar`.
      redRef.current.conectar({ capacidades: ['generar'], info });
      setStatus(`Donando: ${MODEL_ID} en la GPU de este navegador.`);
    } catch (e) {
      engineRef.current = null;
      setModelLoading(false);
      setIsDonating(false);
      setDonateError(`No se pudo cargar el modelo: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const tabButton = (id: Tab, label: string) => (
    <button
      onClick={() => setTab(id)}
      className={`min-h-[44px] flex items-center text-[10px] tracking-[0.25em] uppercase py-2 px-4 md:px-1 border-b-2 md:border-b-0 md:border-l-2 transition [writing-mode:horizontal-tb] md:[writing-mode:vertical-lr] ${
        tab === id ? 'border-paper text-paper font-bold' : 'border-transparent text-paper-faint hover:text-paper-dim'
      }`}
    >
      {label}
    </button>
  );

  const host = (() => {
    try {
      return config.rele ? new URL(config.rele).host : 'Kaggle';
    } catch {
      return config.rele;
    }
  })();

  return (
    <div className="h-dvh w-full bg-ink text-paper font-mono flex flex-col md:flex-row overflow-hidden">
      <nav className="w-full md:w-12 shrink-0 border-b md:border-b-0 md:border-r border-line flex flex-row md:flex-col items-center justify-center md:justify-start md:pt-6 gap-2 md:gap-6">
        {tabButton('inicio', 'Inicio')}
        {tabButton('chat', 'Chat')}
        {tabButton('red', 'Red')}
      </nav>

      <div className="flex-1 flex flex-col min-h-0">
        <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between px-4 sm:px-8 py-4 sm:py-5 border-b border-line shrink-0">
          <div className="min-w-0">
            <h1 className="font-display text-base sm:text-lg tracking-[0.15em] truncate">SYNRAG_RED</h1>
            <p className="text-[10px] text-paper-faint mt-1 truncate">
              enjambre {config.enjambre} · relé {host} · {conectado ? myId.substring(0, 10) : 'conectando...'}
            </p>
          </div>

          <div className="flex items-center gap-4 sm:gap-6 shrink-0">
            <CountBadge total={stats.total} idle={stats.idle} busy={stats.busy} />
            <button
              onClick={donateCompute}
              disabled={isDonating || !conectado}
              className="min-h-[44px] px-4 py-2 border border-paper rounded-sm hover:bg-paper hover:text-ink transition disabled:opacity-60 text-xs uppercase tracking-widest whitespace-nowrap flex items-center gap-2"
            >
              <span className={`inline-block w-1.5 h-1.5 rounded-full ${modelReady ? 'bg-paper' : 'border border-paper'}`} aria-hidden />
              {isDonating ? (modelReady ? 'Donando' : 'Cargando...') : 'Donar cómputo'}
            </button>
          </div>
        </header>

        <NetworkCapacityBar capacity={capacity} totalNodes={stats.total} />

        {donateError && (
          <div className="mx-4 sm:mx-8 mt-4 sm:mt-5 border border-paper border-dashed rounded-sm px-4 py-3 shrink-0 text-sm">
            × {donateError}
          </div>
        )}

        {isDonating && (
          <ContributionPanel
            loading={modelLoading}
            loadProgress={loadProgress}
            cores={myNode?.cores ?? navigator.hardwareConcurrency ?? 1}
            deviceMemoryGb={myNode?.deviceMemoryGb ?? deviceMemoryGb}
            modelSizeMb={modelSizeMb}
            tasksCompleted={myNode?.tasksCompleted ?? resueltas}
          />
        )}

        {!modelLoading && status && (
          <div className="px-4 sm:px-8 py-1.5 text-[10px] text-paper-faint border-b border-line shrink-0 truncate">{status}</div>
        )}

        <main className="flex-1 min-h-0 p-4 sm:p-8">
          {tab === 'inicio' ? (
            <HomeView
              stats={stats}
              capacity={capacity}
              isDonating={isDonating}
              modelReady={modelReady}
              onGoToChat={() => setTab('chat')}
              onGoToNetwork={() => setTab('red')}
              onDonate={donateCompute}
            />
          ) : tab === 'chat' ? (
            <ChatView myId={myId} chat={chat} prompt={prompt} setPrompt={setPrompt} onAsk={askQuestion} awaitingAnswer={awaitingAnswer} />
          ) : (
            <NetworkView myId={myId} nodes={nodes} flow={flow} />
          )}
        </main>
      </div>
    </div>
  );
};

export default App;
