import {
  Cpu,
  Zap,
  Shield,
  Network,
  MessageSquare,
  ArrowRight,
  Activity,
} from 'lucide-react';
import type { AggregateCapacity, NetworkStats } from '../red';

interface Props {
  stats: NetworkStats;
  capacity: AggregateCapacity;
  isDonating: boolean;
  modelReady: boolean;
  onGoToChat: () => void;
  onGoToNetwork: () => void;
  onDonate: () => void;
}

const HomeView = ({
  stats,
  capacity,
  isDonating,
  modelReady,
  onGoToChat,
  onGoToNetwork,
  onDonate,
}: Props) => {
  return (
    <div className="w-full h-full overflow-y-auto pr-1 sm:pr-3 space-y-12 pb-12 font-mono text-paper">
      {/* Hero Section */}
      <section className="border border-line rounded-sm p-6 sm:p-10 grid-surface relative overflow-hidden">
        <div className="max-w-3xl space-y-4">
          <div className="inline-flex items-center gap-2 px-2.5 py-1 border border-line-strong bg-ink text-[11px] uppercase tracking-widest text-paper-dim">
            <span className="w-1.5 h-1.5 rounded-full bg-paper animate-pulse" />
            Red P2P de Inteligencia Artificial Colaborativa
          </div>

          <h1 className="font-display text-2xl sm:text-4xl tracking-wider leading-tight text-paper">
            CÓMPUTO DE IA COMPARTIDO ENTRE NAVEGADORES Y GPUs
          </h1>

          <p className="text-sm sm:text-base text-paper-dim leading-relaxed font-sans max-w-2xl">
            Una red descentralizada donde cualquier persona puede donar la potencia de su computadora o celular, y cualquier usuario puede hacer preguntas a modelos de lenguaje reales que corren en la GPU de los participantes: navegadores con WebGPU y nodos con llama.cpp (por ejemplo, cuadernos de Kaggle con 2 x T4).
          </p>

          <div className="pt-3 flex flex-wrap items-center gap-3">
            <button
              onClick={onGoToChat}
              className="min-h-[46px] px-6 py-2.5 bg-paper text-ink font-bold text-xs uppercase tracking-widest hover:opacity-90 transition flex items-center gap-2 rounded-sm shadow-sm"
            >
              <span>Entrar al Chat Compartido</span>
              <ArrowRight className="w-4 h-4" />
            </button>

            <button
              onClick={onDonate}
              disabled={isDonating}
              className="min-h-[46px] px-6 py-2.5 border border-paper text-paper text-xs uppercase tracking-widest hover:bg-paper hover:text-ink transition disabled:opacity-40 rounded-sm flex items-center gap-2"
            >
              <Cpu className="w-4 h-4" />
              <span>{isDonating ? (modelReady ? 'Donando a la Red' : 'Cargando Modelo...') : 'Donar mi Computadora'}</span>
            </button>

            <button
              onClick={onGoToNetwork}
              className="min-h-[46px] px-5 py-2.5 border border-line hover:border-line-strong text-paper-dim hover:text-paper text-xs uppercase tracking-widest transition rounded-sm flex items-center gap-2"
            >
              <Activity className="w-4 h-4" />
              <span>Ver Topología en Vivo</span>
            </button>
          </div>
        </div>

        {/* Live Network Pill stats */}
        <div className="mt-8 pt-6 border-t border-line grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
          <div>
            <div className="text-[10px] uppercase text-paper-faint tracking-widest">Nodos Conectados</div>
            <div className="text-lg font-bold mt-0.5">{stats.total} activos</div>
          </div>
          <div>
            <div className="text-[10px] uppercase text-paper-faint tracking-widest">Núcleos Totales</div>
            <div className="text-lg font-bold mt-0.5">{capacity.totalCores || 0} cores</div>
          </div>
          <div>
            <div className="text-[10px] uppercase text-paper-faint tracking-widest">Memoria Reportada</div>
            <div className="text-lg font-bold mt-0.5">{capacity.totalMemoryGb || 0} GB RAM</div>
          </div>
          <div>
            <div className="text-[10px] uppercase text-paper-faint tracking-widest">Tareas Resueltas</div>
            <div className="text-lg font-bold mt-0.5">{capacity.totalTasksCompleted || 0} preguntas</div>
          </div>
        </div>
      </section>

      {/* Function-by-Function Explanation */}
      <section className="space-y-6">
        <div className="border-b border-line pb-3">
          <h2 className="font-display text-lg tracking-wider text-paper uppercase">
            ¿Qué hace cada función de la plataforma?
          </h2>
          <p className="text-xs text-paper-faint mt-1 font-sans">
            Guía explicativa de cada componente técnico y cómo colaboran entre sí.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {/* Card 1: Donar Cómputo */}
          <div className="border border-line rounded-sm p-5 bg-ink flex flex-col justify-between hover:border-line-strong transition space-y-4">
            <div className="space-y-3">
              <div className="w-9 h-9 rounded-sm border border-line flex items-center justify-center text-paper">
                <Cpu className="w-5 h-5" />
              </div>
              <h3 className="font-display text-sm font-bold tracking-wide">1. Donar Cómputo (WebGPU)</h3>
              <p className="text-xs text-paper-dim font-sans leading-relaxed">
                Descarga y monta el modelo de lenguaje generativo completo en tu navegador usando <strong>WebGPU</strong> y un Web Worker dedicado. Tu máquina pasa a ser un nodo voluntario que puede procesar preguntas de otros usuarios sin que instales nada.
              </p>
            </div>
            <div className="text-[10px] text-paper-faint border-t border-line pt-2">
              Tecnología: WebLLM + WebGPU nativo
            </div>
          </div>

          {/* Card 2: Enrutamiento por Menor Carga */}
          <div className="border border-line rounded-sm p-5 bg-ink flex flex-col justify-between hover:border-line-strong transition space-y-4">
            <div className="space-y-3">
              <div className="w-9 h-9 rounded-sm border border-line flex items-center justify-center text-paper">
                <Zap className="w-5 h-5" />
              </div>
              <h3 className="font-display text-sm font-bold tracking-wide">2. Enrutamiento Inteligente</h3>
              <p className="text-xs text-paper-dim font-sans leading-relaxed">
                Cuando alguien hace una pregunta, la red consulta la tabla de nodos activos y <strong>selecciona automáticamente el dispositivo con menor número de tareas activas</strong> (y mayor número de núcleos), evitando saturar un solo equipo.
              </p>
            </div>
            <div className="text-[10px] text-paper-faint border-t border-line pt-2">
              Tecnología: Algoritmo de balanceo dinámico
            </div>
          </div>

          {/* Card 3: Relé */}
          <div className="border border-line rounded-sm p-5 bg-ink flex flex-col justify-between hover:border-line-strong transition space-y-4">
            <div className="space-y-3">
              <div className="w-9 h-9 rounded-sm border border-line flex items-center justify-center text-paper">
                <Network className="w-5 h-5" />
              </div>
              <h3 className="font-display text-sm font-bold tracking-wide">3. Relé ligero en memoria</h3>
              <p className="text-xs text-paper-dim font-sans leading-relaxed">
                Un relé WebSocket pequeño reparte cada pregunta al nodo con menos carga y devuelve la respuesta por partes. <strong>No guarda nada en disco</strong>: ni base de datos ni cuentas; al reiniciarse, la sala empieza vacía.
              </p>
            </div>
            <div className="text-[10px] text-paper-faint border-t border-line pt-2">
              Tecnología: WebSocket + JSON (fabric/rele)
            </div>
          </div>

          {/* Card 4: Chat Compartido */}
          <div className="border border-line rounded-sm p-5 bg-ink flex flex-col justify-between hover:border-line-strong transition space-y-4">
            <div className="space-y-3">
              <div className="w-9 h-9 rounded-sm border border-line flex items-center justify-center text-paper">
                <MessageSquare className="w-5 h-5" />
              </div>
              <h3 className="font-display text-sm font-bold tracking-wide">4. Sala de Chat Colectiva</h3>
              <p className="text-xs text-paper-dim font-sans leading-relaxed">
                Una sala transparente donde todos los usuarios conectados ven en tiempo real las preguntas realizadas, qué nodo las resolvió, cuántos milisegundos tomó y el texto mientras se va generando.
              </p>
            </div>
            <div className="text-[10px] text-paper-faint border-t border-line pt-2">
              Tecnología: sala del enjambre en el relé
            </div>
          </div>

          {/* Card 5: Simulador de Topología */}
          <div className="border border-line rounded-sm p-5 bg-ink flex flex-col justify-between hover:border-line-strong transition space-y-4">
            <div className="space-y-3">
              <div className="w-9 h-9 rounded-sm border border-line flex items-center justify-center text-paper">
                <Activity className="w-5 h-5" />
              </div>
              <h3 className="font-display text-sm font-bold tracking-wide">5. Simulador de Red SVG</h3>
              <p className="text-xs text-paper-dim font-sans leading-relaxed">
                En la pestaña "Red", puedes ver visualmente el diagrama de todos los nodos activos en el enjambre, las líneas de conexión y un pulso animado en vivo que muestra exactamente cómo el prompt viaja al nodo asignado y regresa la respuesta.
              </p>
            </div>
            <div className="text-[10px] text-paper-faint border-t border-line pt-2">
              Tecnología: Gráficos vectoriales interactivos
            </div>
          </div>

          {/* Card 6: Privacidad y Cifrado */}
          <div className="border border-line rounded-sm p-5 bg-ink flex flex-col justify-between hover:border-line-strong transition space-y-4">
            <div className="space-y-3">
              <div className="w-9 h-9 rounded-sm border border-line flex items-center justify-center text-paper">
                <Shield className="w-5 h-5" />
              </div>
              <h3 className="font-display text-sm font-bold tracking-wide">6. Anónimo y con clave</h3>
              <p className="text-xs text-paper-dim font-sans leading-relaxed">
                Cero cuentas, correos o números de teléfono. Cada navegador genera un ID anónimo. Para entrar al enjambre hace falta la clave de la red y todo viaja cifrado por <strong>TLS (wss://)</strong>. El relé sí ve el texto de las preguntas: no escribas datos privados.
              </p>
            </div>
            <div className="text-[10px] text-paper-faint border-t border-line pt-2">
              Tecnología: TLS + clave compartida
            </div>
          </div>
        </div>
      </section>

      {/* How to Connect Devices Step by Step */}
      <section className="border border-line rounded-sm p-6 sm:p-8 bg-ink-soft space-y-6">
        <div>
          <h2 className="font-display text-lg tracking-wider text-paper uppercase">
            ¿Cómo conectar tu Teléfono o una segunda Computadora?
          </h2>
          <p className="text-xs text-paper-faint mt-1 font-sans">
            Sigue estos 3 sencillos pasos para probar la red con dos dispositivos:
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 text-xs">
          <div className="space-y-2.5">
            <div className="flex items-center gap-2 font-bold text-paper">
              <span className="w-5 h-5 rounded-full border border-paper flex items-center justify-center text-[10px]">1</span>
              <span>Abre el enlace en tu Teléfono</span>
            </div>
            <p className="text-paper-dim font-sans leading-relaxed">
              Abre la misma dirección en el navegador de tu celular (Chrome o Safari en iOS 18+) conectado a la misma red Wi-Fi o internet.
            </p>
          </div>

          <div className="space-y-2.5">
            <div className="flex items-center gap-2 font-bold text-paper">
              <span className="w-5 h-5 rounded-full border border-paper flex items-center justify-center text-[10px]">2</span>
              <span>Haz clic en "Donar Cómputo"</span>
            </div>
            <p className="text-paper-dim font-sans leading-relaxed">
              En uno de los dos dispositivos (por ejemplo el teléfono), pulsa <strong>"Donar cómputo"</strong>. Se descargará el modelo y se anunciará como nodo activo en la red.
            </p>
          </div>

          <div className="space-y-2.5">
            <div className="flex items-center gap-2 font-bold text-paper">
              <span className="w-5 h-5 rounded-full border border-paper flex items-center justify-center text-[10px]">3</span>
              <span>Pregunta desde tu PC</span>
            </div>
            <p className="text-paper-dim font-sans leading-relaxed">
              En tu computadora ve al <strong>Chat</strong> y escribe tu pregunta. La red enrutará la petición al celular, el celular resolverá la IA en su GPU y devolverá la respuesta por P2P.
            </p>
          </div>
        </div>

        <div className="pt-2 flex justify-start">
          <button
            onClick={onGoToChat}
            className="px-5 py-2.5 bg-paper text-ink font-bold text-xs uppercase tracking-widest hover:opacity-90 transition rounded-sm flex items-center gap-2"
          >
            <span>Empezar en el Chat</span>
            <ArrowRight className="w-4 h-4" />
          </button>
        </div>
      </section>
    </div>
  );
};

export default HomeView;
