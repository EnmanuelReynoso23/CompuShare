# SynRAG Red: web

Página para usar la red desde el navegador: chat compartido del enjambre, mapa de nodos y botón
**Donar cómputo**, que carga `Qwen2.5-1.5B-Instruct` con WebLLM (WebGPU) y convierte la pestaña en un
nodo `generar` más, al lado de los nodos de Kaggle o de una PC con GPU.

Antes era "p2p-neural-fabric" sobre Appwrite (tablas `nodes`/`signals`/`messages`) y WebRTC. Todo eso
se quitó:

- Sin Appwrite: el relé (`fabric/rele`) guarda en memoria quién está y la sala del chat.
- Sin WebRTC/STUN/TURN: pregunta y respuesta van por el relé (wss://), que reparte al nodo con menos carga.
- Sin el "ring pipeline" de capas: era simulado (tensores de `Math.sin`, palabras de una lista fija).
  Cada nodo corre su modelo completo; la red reparte tareas, no capas.

## Configuración

| Origen | Relé | Clave | Enjambre |
| --- | --- | --- | --- |
| Enlace | `?rele=wss://...` | `?clave=...` | `?enjambre=...` |
| Build | `VITE_FABRIC_RELE` | `VITE_FABRIC_CLAVE` | `publico` |

Lo que llega por el enlace se guarda en `localStorage` (`fabric:config`), así el enlace de invitación
solo se abre una vez. El relé ve el texto de las preguntas: no escribas datos privados.

```bash
cp .env.example .env   # pon VITE_FABRIC_CLAVE solo si el build es privado
npm install
npm run dev            # o npm run build -> dist/ (estático)
```

## Archivos

- `src/red.ts`: cliente del relé (mismo protocolo que `fabric/rele/rele.py`), reconexión y latidos.
- `src/App.tsx`: chat, donación con WebLLM y cola de tareas del navegador como nodo.
- `src/ai/llm-worker.js`: worker de WebLLM.
- `src/components/`: vistas (inicio, chat, red) y paneles.
