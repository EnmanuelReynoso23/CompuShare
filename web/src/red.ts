// Conexión con el relé de la red (mismo protocolo que fabric/rele/rele.py).
// Sustituye a Appwrite (tablas nodes/signals/messages) y a la señalización WebRTC.
// El relé corre dentro de un cuaderno de Kaggle; si no hay ?rele=, se busca (buscarRele):
// el relé guarda en memoria quién está, reparte las tareas al nodo con menos
// carga y mantiene la sala de chat del enjambre.

export interface NodeRow {
  $id: string;
  nombre: string;
  capacidades: string[];
  modelo?: string;
  gpu?: string;
  vramGb?: number;
  cores: number;
  deviceMemoryGb?: number;
  activeTasks: number;
  tasksCompleted: number;
}

export type MessageStatus = 'pending' | 'answered' | 'error';

export interface MessageRow {
  $id: string;
  $createdAt: string;
  prompt: string;
  answer?: string;
  authorId: string;
  workerId?: string;
  timeMs?: number;
  status: MessageStatus;
  connectionProof?: string;
}

export interface NetworkStats {
  total: number;
  idle: number;
  busy: number;
}

export interface AggregateCapacity {
  totalCores: number;
  totalMemoryGb: number;
  nodesReportingMemory: number;
  totalTasksCompleted: number;
}

export function computeNetworkStats(nodes: NodeRow[]): NetworkStats {
  return {
    total: nodes.length,
    idle: nodes.filter((n) => n.activeTasks === 0).length,
    busy: nodes.filter((n) => n.activeTasks > 0).length,
  };
}

export function computeAggregateCapacity(nodes: NodeRow[]): AggregateCapacity {
  return {
    totalCores: nodes.reduce((sum, n) => sum + (n.cores || 0), 0),
    totalMemoryGb: nodes.reduce((sum, n) => sum + (n.deviceMemoryGb || n.vramGb || 0), 0),
    nodesReportingMemory: nodes.filter((n) => n.deviceMemoryGb || n.vramGb).length,
    totalTasksCompleted: nodes.reduce((sum, n) => sum + (n.tasksCompleted || 0), 0),
  };
}

export interface ConfigRed {
  rele: string;
  clave: string;
  enjambre: string;
}

const CLAVE_CONFIG = 'fabric:config';
const CLAVE_ID = 'fabric:id';

/** Relé, clave y enjambre: de la URL (?rele=&clave=&enjambre=), si no de lo guardado, si no del build. */
export function leerConfig(): ConfigRed {
  let guardada: Partial<ConfigRed> = {};
  try {
    guardada = JSON.parse(localStorage.getItem(CLAVE_CONFIG) || '{}');
  } catch {
    guardada = {};
  }
  const url = new URLSearchParams(window.location.search);
  const config: ConfigRed = {
    // Vacío = buscar el relé que publicó el cuaderno de Kaggle que lo aloja (ver buscarRele).
    rele: url.get('rele') || import.meta.env.VITE_FABRIC_RELE || '',
    clave: url.get('clave') || guardada.clave || import.meta.env.VITE_FABRIC_CLAVE || '',
    enjambre: (url.get('enjambre') || guardada.enjambre || 'publico').toLowerCase().replace(/[^a-z0-9-]/g, '').slice(0, 40) || 'publico',
  };
  try {
    localStorage.setItem(CLAVE_CONFIG, JSON.stringify(config));
  } catch {
    /* sin almacenamiento: se usa solo en esta pestaña */
  }
  return config;
}

const NTFY = 'https://ntfy.sh';

/** Mismo tema que fabric/descubrir.py: sale de la clave, así que solo quien la tiene sabe dónde mirar. */
async function temaDeLaClave(clave: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`synrag-red:${clave}`));
  const hex = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
  return `synrag-red-${hex.slice(0, 24)}`;
}

/** El relé vive en un cuaderno de Kaggle con un túnel que cambia en cada arranque: la dirección más nueva que siga viva. */
export async function buscarRele(clave: string): Promise<string | null> {
  const r = await fetch(`${NTFY}/${await temaDeLaClave(clave)}/json?poll=1&since=12h`);
  const urls = (await r.text())
    .split('\n')
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l) as { event?: string; message?: string })
    .filter((m) => m.event === 'message' && m.message?.startsWith('wss://'))
    .map((m) => m.message as string);
  for (const url of urls.reverse()) {
    try {
      const salud = await fetch(`https://${url.slice('wss://'.length)}/salud`, { signal: AbortSignal.timeout(8000) });
      if (salud.ok) return url;
    } catch {
      /* cuaderno ya apagado: la siguiente */
    }
  }
  return null;
}

/** Identidad anónima de este navegador (ya no hay sesión de Appwrite). */
export function miIdentidad(): string {
  try {
    const guardado = localStorage.getItem(CLAVE_ID);
    if (guardado) return guardado;
  } catch {
    /* sigue abajo */
  }
  const bytes = crypto.getRandomValues(new Uint8Array(8));
  const id = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  try {
    localStorage.setItem(CLAVE_ID, id);
  } catch {
    /* efímera */
  }
  return id;
}

interface NodoDelRele {
  id: string;
  nombre: string;
  capacidades: string[];
  info: Record<string, string | number | boolean>;
  carga: number;
  completadas: number;
}

interface MensajeDelRele {
  id: string;
  creado: string;
  texto: string;
  de: string;
  estado: 'pendiente' | 'respondido' | 'error';
  respuesta?: string;
  por?: string;
  ms?: number;
}

function aNodeRow(n: NodoDelRele): NodeRow {
  const num = (v: unknown) => (typeof v === 'number' ? v : undefined);
  return {
    $id: n.id,
    nombre: n.nombre,
    capacidades: n.capacidades,
    modelo: typeof n.info.modelo === 'string' ? n.info.modelo : undefined,
    gpu: typeof n.info.gpu === 'string' ? n.info.gpu : undefined,
    vramGb: num(n.info.vram_gb),
    cores: num(n.info.nucleos) ?? 0,
    deviceMemoryGb: num(n.info.memoria_gb),
    activeTasks: n.carga,
    tasksCompleted: n.completadas,
  };
}

function aMessageRow(m: MensajeDelRele): MessageRow {
  return {
    $id: m.id,
    $createdAt: m.creado,
    prompt: m.texto,
    answer: m.respuesta,
    authorId: m.de,
    workerId: m.por,
    timeMs: m.ms,
    status: m.estado === 'pendiente' ? 'pending' : m.estado === 'error' ? 'error' : 'answered',
    connectionProof: m.estado === 'respondido' ? 'resuelto por la red vía el relé' : undefined,
  };
}

export interface TareaEntrante {
  id: string;
  tipo: string;
  datos: unknown;
  de: string;
}

export interface Eventos {
  alConectar?: (id: string) => void;
  alDesconectar?: (motivo: string) => void;
  alCambiarNodos?: (nodos: NodeRow[]) => void;
  alCambiarSala?: (mensajes: MessageRow[]) => void;
  /** Solo si esta conexión entra como nodo. */
  alRecibirTarea?: (tarea: TareaEntrante) => void;
  alCancelarTarea?: (id: string) => void;
}

interface Pendiente {
  resolver: (datos: unknown) => void;
  rechazar: (e: Error) => void;
  alParcial?: (datos: { texto?: string }, nodo: string) => void;
}

export interface OpcionesNodo {
  capacidades: string[];
  info: Record<string, string | number | boolean>;
}

/**
 * Una conexión con el relé. Como cliente pide tareas; como nodo además las
 * atiende. Se reconecta sola con espera creciente.
 */
export class Red {
  private ws: WebSocket | null = null;
  private sala = new Map<string, MessageRow>();
  private pendientes = new Map<string, Pendiente>();
  private contador = 0;
  private espera = 1000;
  private cerrada = false;
  private latido: ReturnType<typeof setInterval> | null = null;
  private config: ConfigRed;
  private id: string;
  private eventos: Eventos;
  private comoNodo: OpcionesNodo | null = null;
  carga = 0;

  constructor(config: ConfigRed, id: string, eventos: Eventos) {
    this.config = config;
    this.id = id;
    this.eventos = eventos;
  }

  conectar(comoNodo: OpcionesNodo | null = null) {
    this.comoNodo = comoNodo;
    this.cerrada = false;
    const vieja = this.ws;
    if (vieja && vieja.readyState <= WebSocket.OPEN) {
      // Se espera a que el relé suelte la conexión vieja para conservar el mismo id.
      this.ws = null;
      vieja.onclose = () => {
        this.rechazarPendientes();
        void this.abrir();
      };
      vieja.close();
      return;
    }
    void this.abrir();
  }

  private rechazarPendientes() {
    if (this.latido) clearInterval(this.latido);
    for (const p of this.pendientes.values()) p.rechazar(new Error('conexión perdida con el relé'));
    this.pendientes.clear();
  }

  private async abrir() {
    const comoNodo = this.comoNodo;
    let url = this.config.rele;
    if (!url) {
      url = (await buscarRele(this.config.clave).catch(() => null)) ?? '';
      if (!url) {
        this.eventos.alDesconectar?.('la red está apagada: no hay ningún cuaderno de Kaggle con el relé');
        if (!this.cerrada) setTimeout(() => void this.abrir(), 30000);
        return;
      }
    }
    if (this.cerrada) return;
    const ws = new WebSocket(url);
    this.ws = ws;
    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          t: 'hola',
          clave: this.config.clave,
          enjambre: this.config.enjambre,
          rol: comoNodo ? 'nodo' : 'cliente',
          id: this.id,
          nombre: comoNodo ? `navegador-${this.id.slice(0, 4)}` : 'navegador',
          capacidades: comoNodo?.capacidades ?? [],
          info: comoNodo?.info ?? {},
        }),
      );
    };
    ws.onmessage = (e) => this.recibir(JSON.parse(e.data));
    ws.onclose = (e) => {
      if (this.ws !== ws) return;
      this.rechazarPendientes();
      this.eventos.alDesconectar?.(e.reason || 'sin conexión');
      if (this.cerrada || e.reason === 'clave incorrecta') return;
      setTimeout(() => void this.abrir(), this.espera);
      this.espera = Math.min(this.espera * 2, 30000);
    };
  }

  cerrar() {
    this.cerrada = true;
    if (this.latido) clearInterval(this.latido);
    this.ws?.close();
  }

  private enviar(m: Record<string, unknown>) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(m));
  }

  private publicarSala() {
    const lista = [...this.sala.values()].sort((a, b) => a.$createdAt.localeCompare(b.$createdAt));
    this.eventos.alCambiarSala?.(lista);
  }

  private recibir(m: Record<string, any>) {
    switch (m.t) {
      case 'bienvenido':
        this.id = m.id;
        this.espera = 1000;
        this.sala.clear();
        for (const x of m.mensajes as MensajeDelRele[]) this.sala.set(x.id, aMessageRow(x));
        this.publicarSala();
        this.eventos.alCambiarNodos?.((m.nodos as NodoDelRele[]).map(aNodeRow));
        this.eventos.alConectar?.(m.id);
        if (this.latido) clearInterval(this.latido);
        this.latido = setInterval(() => this.enviar({ t: 'latido', carga: this.carga }), 10000);
        break;
      case 'rechazo':
        this.eventos.alDesconectar?.(m.motivo);
        break;
      case 'nodos':
        this.eventos.alCambiarNodos?.((m.nodos as NodoDelRele[]).map(aNodeRow));
        break;
      case 'mensaje':
        this.sala.set(m.mensaje.id, aMessageRow(m.mensaje));
        this.publicarSala();
        break;
      case 'tarea':
        this.eventos.alRecibirTarea?.(m as TareaEntrante);
        break;
      case 'cancelar':
        this.eventos.alCancelarTarea?.(m.id);
        break;
      case 'parcial': {
        this.pendientes.get(m.id)?.alParcial?.(m.datos ?? {}, m.nodo);
        break;
      }
      case 'resultado':
      case 'error': {
        const p = this.pendientes.get(m.id);
        if (!p) break;
        this.pendientes.delete(m.id);
        if (m.t === 'resultado') p.resolver({ ...(m.datos ?? {}), nodo: m.nodo });
        else p.rechazar(new Error(m.mensaje === 'sin_nodos' ? 'No hay ningún nodo libre en la red ahora mismo.' : m.mensaje));
        break;
      }
    }
  }

  /** Pide una tarea al nodo con menos carga que la sepa hacer. */
  pedir<T = Record<string, unknown>>(
    tipo: string,
    datos: unknown,
    alParcial?: Pendiente['alParcial'],
    timeoutMs = 300000,
  ): Promise<T & { nodo: string }> {
    const id = `w${++this.contador}`;
    return new Promise((resolver, rechazar) => {
      const vence = setTimeout(() => {
        this.pendientes.delete(id);
        this.enviar({ t: 'cancelar', id });
        rechazar(new Error('La red no respondió a tiempo.'));
      }, timeoutMs);
      this.pendientes.set(id, {
        resolver: (d) => {
          clearTimeout(vence);
          resolver(d as T & { nodo: string });
        },
        rechazar: (e) => {
          clearTimeout(vence);
          rechazar(e);
        },
        alParcial,
      });
      this.enviar({ t: 'tarea', id, tipo, datos });
    });
  }

  publicarPregunta(texto: string) {
    this.enviar({ t: 'mensaje', texto });
  }

  cerrarPregunta(mensajeId: string, respuesta: string, estado: 'respondido' | 'error', por: string, ms: number) {
    this.enviar({ t: 'responder', mensaje_id: mensajeId, respuesta, estado, por, ms });
  }

  // Lado nodo
  parcial(id: string, texto: string) {
    this.enviar({ t: 'parcial', id, datos: { texto } });
  }

  resultado(id: string, datos: Record<string, unknown>) {
    this.enviar({ t: 'resultado', id, datos });
  }

  fallo(id: string, mensaje: string) {
    this.enviar({ t: 'error', id, mensaje });
  }

  latir() {
    this.enviar({ t: 'latido', carga: this.carga });
  }
}
