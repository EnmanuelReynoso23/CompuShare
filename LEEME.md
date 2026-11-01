# fabric: la red de nodos de SynRAG

Une los dos proyectos: **SynRAG** busca el código en local (BM25 + FlashRank, sin red) y la **red**
reparte el trabajo pesado (redactar, embeber, reordenar) entre nodos con GPU: cuadernos de Kaggle,
una PC propia o un navegador con WebGPU. Sustituye a `p2p-neural-fabric` (Appwrite + WebRTC + un
"pipeline de capas" que era simulado), cuyas tablas se borraron de la base de producción de AsistoYA el 31-oct-2026.

```
  agente (Claude, Antigravity...)            navegador (fabric/web, Vercel)
        |  MCP: preguntar_red                       |  chat / donar
        v                                           v
  engine/red.py --- wss:// ---> relé (fabric/rele)  <--- wss:// --- nodos
  (busca en local)              dentro del cuaderno            Kaggle 2xT4: llama.cpp + Qwen
                                Kaggle `calidad`, túnel        PC: llama.cpp / Ollama
                                de Cloudflare; dirección       navegador: WebLLM
                                publicada en ntfy.sh
```

## Piezas

| Ruta | Qué es |
| --- | --- |
| `rele/rele.py` | Relé WebSocket (Python, `websockets`). Memoria pura: nodos, tareas en curso y la sala del chat (últimos 50 mensajes). Clave compartida, límite de ritmo por conexión, `GET /salud`. |
| `rele/Dockerfile`, `docker-compose.yml` | Por si algún día corre en un servidor propio (hoy no se usa). |
| `descubrir.py` | Publica y busca la dirección del relé (ntfy.sh). |
| `nodo/nodo.py` | Nodo: entra al relé con sus capacidades, late cada 10 s, atiende tareas con un semáforo por capacidad y se reconecta solo. |
| `nodo/motores.py` | `eco`, `generar` (cualquier servidor OpenAI con streaming: llama.cpp, vLLM, Ollama), `embeber` y `reordenar` (sentence-transformers). |
| `cliente.py` | Cliente Python (`ClienteRed`, `pedir_sync`). |
| `kaggle/` | Cuaderno-nodo y `lanzar.py` para subirlo a Kaggle con GPU. |
| `pruebas/` | `pytest` de punta a punta y `delegar.py`, que manda trabajo real a los nodos y escribe el informe. |
| `web/` | Página de la red (chat, mapa, donar con WebGPU). |
| `../engine/red.py` | Herramienta MCP `preguntar_red` de SynRAG. |

## Protocolo (un JSON por mensaje, campo `t`)

| De | `t` | Campos |
| --- | --- | --- |
| todos | `hola` | `clave`, `enjambre` (`[a-z0-9-]{1,40}`), `rol` (`nodo`/`cliente`), `id?`, `nombre?`, `capacidades?`, `info?` |
| relé | `bienvenido` | `id`, `nodos`, `mensajes` |
| relé | `rechazo` | `motivo` (y cierra) |
| relé | `nodos` | lista `{id, nombre, capacidades, info, carga, completadas}` cada vez que cambia |
| todos | `latido` | `carga?` (sin latido en 60 s, el relé corta) |
| cliente | `tarea` | `id`, `tipo`, `datos`, `para?` (id de un nodo concreto) |
| relé -> nodo | `tarea` | `id`, `tipo`, `datos`, `de` |
| nodo | `parcial` / `resultado` / `error` | `id`, `datos` o `mensaje` (el relé los reenvía a quien pidió, con `nodo`) |
| cualquiera | `cancelar` | `id` |
| cliente | `mensaje` / `responder` | sala del chat; solo el autor cierra su mensaje |

Errores que devuelve el relé: `sin_nodos`, `tarea_invalida`, `nodo_desconectado`, `vencida` (15 min).
Un `id` pedido se respeta si nadie más lo tiene abierto (así el navegador conserva su id al pasar a donar).

## Dónde corre (31-oct-2026)

- **Nada en Oracle** (decisión del usuario). El relé que estuvo ahí unas horas se quitó: contenedor,
  `/opt/fabric` y el bloque de Caddy.
- **Relé**: dentro del cuaderno Kaggle `calidad` (`rele: "aqui"` en `lanzar.py`). Sale por un túnel
  rápido de Cloudflare (`cloudflared` versión fija) y publica `wss://*.trycloudflare.com` en ntfy.sh,
  tema `synrag-red-<sha256 de la clave>` (`descubrir.py`). Los demás nodos, la web, `delegar.py` y
  `engine/red.py` buscan ahí la dirección más nueva que responda `/salud`.
- **Web**: Vercel (`fabric/web`, estático).
- Local: `~/.config/synrag/fabric.env` (600) con `FABRIC_CLAVE` y `FABRIC_RELE=buscar`.
- La red existe mientras corre el cuaderno `calidad` (máx. 12 h; 30 h de GPU por semana en Kaggle).

## Kaggle

```bash
python fabric/kaggle/lanzar.py              # sube y arranca los dos nodos (GPU T4 x2, privados)
python fabric/kaggle/lanzar.py --estado
DURACION_MIN=55 python fabric/kaggle/lanzar.py rapido
python fabric/kaggle/lanzar.py --bajar /tmp/salida   # resumen.json y llama-server.log
. ~/.config/synrag/fabric.env && .venv-fabric/bin/python -m fabric.pruebas.delegar
```

| Nodo | Modelo (GGUF de unsloth, UD-Q4_K_M) | Extra |
| --- | --- | --- |
| `calidad` | Qwen3.8-27B denso (16,5 GB) | embeddings `Qwen3-Embedding-0.6B` y reordenado `bge-reranker-v2-m3` |
| `rapido` | Qwen3.6-35B-A3B MoE (22,1 GB, 3B activos) | |

El cuaderno baja llama.cpp ya compilado con CUDA 12.8 (release fijada en `lanzar.py`); si
`--list-devices` no ve CUDA, compila para la GPU que encuentre. Qwen3.8-Flash-Next (~100 GB) no cabe
en 2 x T4. La clave del relé va dentro del código del cuaderno privado: rotarla después de probar
(`fabric.env` local; se cambió el 31-oct).

## SynRAG por la red

Con `FABRIC_CLAVE` y `websockets` instalado, el servidor MCP registra
`preguntar_red(pregunta, project, limit, modelo)`: busca en local, manda los mejores trozos a un nodo
`generar` y devuelve la respuesta citando `ruta:líneas`, la lista de fuentes y qué modelo respondió.
Sin nodos devuelve las fuentes locales. Sin configuración no se registra (igual que Argon).

## Pruebas

```bash
uv venv .venv-fabric -p 3.12 && uv pip install -p .venv-fabric websockets httpx pytest
.venv-fabric/bin/python -m pytest -q fabric/pruebas
```

Relé real en un puerto libre y nodos reales: eco, sin nodos, clave mala, reparto entre dos nodos,
streaming, nodo caído, sala del chat, límite de ritmo, reconexión con el mismo id, motor `generar`
contra un SSE de mentira y `preguntar_red` con un nodo falso.

## Resultados

Ver `kaggle/resultados/<fecha>/INFORME.md`.
