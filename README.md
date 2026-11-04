# CompuShare: La Computadora Compartida

**Red Distribuida de Cómputo e Inferencia de Inteligencia Artificial**

> *"No necesitamos centros de datos gigantescos controlados por unos pocos. El poder ya está aquí, distribuido en millones de máquinas conectadas que, juntas, forman una sola supercomputadora."*

---

## 💡 Manifiesto y Visión

Hoy en día, el acceso a la inteligencia artificial de vanguardia está hipercentralizado. Dependemos de un puñado de corporaciones que controlan la infraestructura y cobran por cada token generado. 

**CompuShare** nace del análisis sobre el futuro inevitable del cómputo: **la computadora compartida**. 

De la misma forma que BitTorrent descentralizó el almacenamiento y Folding@home demostró que millones de computadoras personales podían superar a las mayores supercomputadoras científicas del mundo, **CompuShare** une recursos de cómputo diversos (laptops personales, GPUs ociosas, instancias de Kaggle, Google Colab y servidores comunitarios) en una **malla global de inferencia**.

En CompuShare:
- Quien tiene cómputo ocioso lo aporta a la red como **Nodo Trabajador**.
- Quien necesita ejecutar tareas o razonamiento de IA consume de la red mediante una interfaz estándar compatible con OpenAI.
- Los nodos efímeros (que nacen y mueren en plataformas como Kaggle o Colab) se orquestan sin interrumpir el servicio.

---

## 🏛️ Arquitectura General

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
4. **Sinergia con SynRAG:** Los agentes que consumen CompuShare utilizan **SynRAG** como motor AST local para reducir hasta en un 60%+ la cantidad de tokens enviados por la red.

---

## 🚀 Hoja de Ruta (Roadmap)

- [x] **Fase 0 (Prototipo validado):** Pruebas de concepto iniciales en Kaggle con nodos maestro, trabajador, relé WebSocket y modelos Qwen vía `llama.cpp`.
- [ ] **Fase 1 (Desacoplamiento):** Separación total del código de red de SynRAG hacia este repositorio; estandarización de scripts de inicialización de workers.
- [ ] **Fase 2 (Protocolo de Descubrimiento):** Registro dinámico de nodos mediante tokens/identificadores sin necesidad de reconfigurar URLs a mano.
- [ ] **Fase 3 (Tolerancia a Nodos Efímeros):** Reintento transparente de inferencia si una sesión de Kaggle/Colab expira a mitad de una respuesta.
- [ ] **Fase 4 (CLI Universal):** `compushare join` para donar cómputo en 1 línea y `compushare serve` para levantar el endpoint local.

---

## 📂 Estructura del Proyecto

```text
compushare/
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

## 📄 Licencia

Este proyecto está bajo la licencia **MIT** - consulta el archivo [LICENSE](LICENSE) para más detalles.
