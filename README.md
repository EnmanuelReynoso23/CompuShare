# CompuShare: La Computadora Compartida

**Red Distribuida de Cómputo e Inferencia de Inteligencia Artificial**

> *"No necesitamos centros de datos gigantescos controlados por unos pocos. El poder ya está aquí, distribuido en millones de máquinas conectadas que, juntas, forman una sola supercomputadora."*

---

## Manifiesto y Visión

Hoy en día, el acceso a la inteligencia artificial de vanguardia está hipercentralizado. Dependemos de un puñado de corporaciones que controlan la infraestructura y cobran por cada token generado. 

**CompuShare** nace del análisis sobre el futuro inevitable del cómputo: **la computadora compartida**. 

De la misma forma que BitTorrent descentralizó el almacenamiento y Folding@home demostró que millones de computadoras personales podían superar a las mayores supercomputadoras científicas del mundo, **CompuShare** une recursos de cómputo diversos (laptops personales, GPUs ociosas, instancias de Kaggle, Google Colab y servidores comunitarios) en una **malla global de inferencia**.

En CompuShare:
- Quien tiene cómputo ocioso lo aporta a la red como **Nodo Trabajador**.
- Quien necesita ejecutar tareas o razonamiento de IA consume de la red mediante una interfaz estándar compatible con OpenAI.
- Los nodos efímeros (que nacen y mueren en plataformas como Kaggle o Colab) se orquestan sin interrumpir el servicio.

---

## Arquitectura General

```mermaid
flowchart TD
    subgraph Clientes["Clientes & Agentes"]
        A["Claude Code / Antigravity / Cursor"]
        B["CLI / SDK de CompuShare"]
    end

    subgraph Relé["Orquestación & Enrutamiento"]
        R["Relé Maestro (WebSocket / HTTP Router)"]
        Q["Cola de Tareas & Balanceo"]
    end

    subgraph Enjambre["Malla de Nodos Trabajadores (CompuShare Swarm)"]
        W1["Kaggle T4 x 2 (Qwen 3.6 / llama.cpp)"]
        W2["Colab GPU / Servidor Local"]
        W3["PC Gamer / GPU Ociosa"]
    end

    A -->|OpenAI-Compatible API /v1| R
    B -->|OpenAI-Compatible API /v1| R
    R <--> Q
    Q <-->|Túnel Seguro / Cloudflare / WebSockets| W1
    Q <-->|Túnel Seguro / Cloudflare / WebSockets| W2
    Q <-->|Túnel Seguro / Cloudflare / WebSockets| W3
```

### Componentes Clave:
1. **Relé Maestro (`compushare-relay`):** Punto de encuentro ligero que distribuye los prompts entrantes hacia los nodos disponibles según su latencia, modelo cargado y carga actual.
2. **Nodos Trabajadores (`compushare-worker`):** Ejecutan modelos de lenguaje abiertos (como Qwen 3.6/3.8, DeepSeek, Llama) usando motores de alto rendimiento como `llama.cpp` o `vLLM`.
3. **Túneles Seguros:** Conexión de nodos detrás de NAT/firewalls mediante túneles Cloudflare, Tailscale o WebSockets directos.
4. **Sinergia con SynRAG:** Los agentes que consumen CompuShare utilizan **SynRAG** como motor AST local para mandar por la red solo los fragmentos de código que hacen falta. Medido en SynRAG: -63,9 % de tokens con un agente real en 10 tareas de varios pasos (CLAIMS fila 41 de SynRAG); no está medido sobre CompuShare.

---

## Estado y plan

Lo que funciona hoy está en `fabric/` (relé, tubo, nodos, reparto de capas, cuadernos de Kaggle).
Medido el 1-nov-2026: un modelo dividido en 2 cuadernos de Kaggle (4 GPU) genera 10,3-10,8 tok/s
(`fabric/kaggle/resultados/20261101-2040`). Fases, cifras y tareas: [PLAN.md](PLAN.md).

---

## Estructura del Proyecto

```text
compushare/
├── fabric/               # Red que ya funciona: relé, tubo, nodos, Kaggle (traído de synrag-fabric)
├── src/
│   └── compushare/
│       ├── __init__.py
│       ├── cli.py          # Interfaz de línea de comandos
│       ├── nodo/           # Lógica del nodo (maestro y trabajador)
│       │   ├── maestro.py
│       │   └── trabajador.py
│       └── rele/           # Enrutamiento de tareas y WebSocket
├── scripts/
│   └── kaggle/             # Cuadernos y guiones para workers en Kaggle
├── tests/
├── pyproject.toml
├── LICENSE
└── README.md
```

---

## Licencia

Este proyecto está bajo la licencia **MIT** - consulta el archivo [LICENSE](LICENSE) para más detalles.
