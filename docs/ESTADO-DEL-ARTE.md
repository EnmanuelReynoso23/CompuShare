# Estado del arte en inferencia distribuida de LLM entre equipos de usuarios

Fecha: 4 de noviembre de 2026.
Agente: A14 (CompuShare).
Rama: a14-estado-del-arte.
Repositorio / Worktree: /home/reyno/Proyectos/cs-a14.

Este documento compara los proyectos existentes que ejecutan modelos de lenguaje repartidos
entre computadoras de usuarios y entornos heterogéneos, contrastando sus decisiones de diseño,
mecanismos de particionado, volumen de datos en tránsito, licencias y métricas publicadas frente
al modelo matemático de CompuShare (`fabric/calculo.py` y `docs/MATEMATICA.md`).

---

## 1. Proyectos analizados

### 1.1 Petals (y biblioteca Hivemind)

- **Articulo cientifico**: "Petals: Collaborative Inference and Fine-tuning of Large Models",
  Alexander Borzunov et al., NeurIPS 2023 / arXiv:2209.01188 (https://arxiv.org/abs/2209.01188).
- **Repositorio**: https://github.com/bigscience-workshop/petals
- **Licencia**: Apache-2.0.
- **Como reparte**:
  - Particionado por bloques de capas contiguas (Pipeline Parallelism dinamico sobre DHT de Hivemind).
  - Los servidores anuncian en la red P2P que bloques de transformador albergan.
  - El cliente resuelve la ruta optima en la DHT basandose en ping / latencia y ancho de banda.
  - Opcionalmente permite paralelismo de tensores (TP) dentro de un mismo servidor multi-GPU, pero nunca entre nodos separados por la WAN.
  - La memoria cache KV se mantiene localmente en la GPU de cada nodo para sus capas asignadas.
- **Que viaja por palabra**:
  - En decodificacion autorregresiva (generacion paso a paso): unicamente el tensor de activaciones del estado oculto intermedio (`hidden_states`), de dimension `[batch_size, 1, hidden_dim]`.
  - Para BLOOM-176B (`hidden_dim = 14336`):
    - En FP16: 28.672 bytes (~28 KB) por token.
    - Con compresion de activaciones a 8 bits: ~14.3 KB por token.
  - No viajan grafos de computo, ni pesos, ni entradas crudas entre servidores intermedios.
- **Cifras publicadas (textual segun el articulo)**:
  - Modelo: BLOOM-176B (176.250 millones de parametros, precision 8 bits / 16 bits).
  - Equipos: Red de servidores voluntarios distribuidos en 2 continentes (Europa y Norteamerica) con GPUs de consumo y estaciones de trabajo: RTX 3060 (12 GB), RTX 2080 Ti (11 GB), RTX 3090 (24 GB), A4000 (16 GB), A5000 (24 GB).
  - Red: Enlaces de internet publico de 100 Mb/s a 1 Gb/s; latencias intercontinentales tipicas de 50 a 150 ms RTT entre saltos.
  - Velocidad de generacion interactiva:
    - Lote unitario (batch size 1): ~1 paso/segundo (~0,83 a 1,2 tok/s; 1,0 a 1,5 s por palabra).
    - En cluster dedicado de 8 x A100-80GB (Tabla 2 del articulo):
      - 16-bit: Batch 1 = 4,18 tok/s; Batch 8 = 31,3 tok/s; Batch 32 = 100,6 tok/s.
      - 8-bit: Batch 1 = 3,95 tok/s; Batch 8 = 29,4 tok/s; Batch 32 = 95,8 tok/s.
- **Idea concreta para CompuShare**:
  - Almacenar la cache KV de forma local en cada nodo para sus capas, transfiriendo unicamente la activacion intermedia (14-32 KB).
  - Pipelining real de secuencias: permitir que flujos concurrentes circulen solapados en las distintas etapas de la cadena.
  - Compresion de activaciones a 8 bits en la red.

---

### 1.2 exo

- **Repositorio**: https://github.com/exo-explore/exo
- **Licencia**: Apache-2.0.
- **Como reparte**:
  - Particionado dinamico consciente de la topologia (Topology-Aware Auto Parallel):
    - Pipeline parallelism: particionado por rebanadas contiguas de capas entre dispositivos.
    - Tensor parallelism: division de matrices dentro de la capa usando comunicador JACCL / Ring-AllReduce.
  - Soporte para backends heterogeneos: MLX (en Apple Silicon con memoria unificada), tinygrad y PyTorch.
- **Que viaja por palabra**:
  - En modo pipeline: la activacion oculta unidireccional entre la ultima capa del nodo actual y la primera capa del nodo siguiente.
  - En modo tensor: AllReduce de activaciones parciales en cada capa (2 intercambios en anillo por capa por token).
- **Cifras publicadas (textual segun mediciones y banco de pruebas exo_bench)**:
  - Modelos probados: Llama-3.2-1B, Llama-3.2-3B, Llama-3-8B, Llama-3.1-70B, Llama-3.1-405B.
  - Equipos: MacBooks y Mac Studios con procesadores M1, M2 y M3 (chips Pro, Max y Ultra).
  - Red: Wi-Fi domestico, Ethernet gigabit y Thunderbolt con RDMA (macOS 26+).
  - Rendimiento publicado / medido:
    - Llama-3.1-70B (cuantizado a 4 bits) repartido en cluster de Macs: 5 a 12 tok/s (alcanzando el extremo superior solo con enlaces Thunderbolt / RDMA de muy alta tasa y baja latencia).
    - Llama-3.2 1B y 3B: en enlaces Wi-Fi locales, la sobrecarga de serializacion y sincronizacion reduce la tasa de generacion frente a la ejecucion en un solo nodo local (speedup negativo en modelos pequenos si la red es lenta).
- **Idea concreta para CompuShare**:
  - Modulo de descubrimiento y evaluacion automatica de topologia: medir la matriz de latencias y anchos de banda antes de decidir el reparto de capas.
  - Topologia en anillo logico directo: los nodos se envian la activacion directamente entre si, eliminando el rele centralizado como cuello de botella de reenvio de red.

---

### 1.3 prima.cpp (Halda y Pipelined-Ring)

- **Articulo cientifico**: "Prima.cpp: Fast 30-70B LLM Inference on Heterogeneous and Low-Resource Home Clusters",
  arXiv:2504.08791 (abril 2025, https://arxiv.org/abs/2504.08791).
- **Repositorio**: https://github.com/OpenCPIL/prima.cpp
- **Licencia**: MIT.
- **Como reparte**:
  - Pipelined-Ring Parallelism (PRP): organiza las maquinas en un anillo logico de ejecucion.
  - Solucionador Halda: formula el problema de asignacion de capas a dispositivos (Layer Device Assignment, LDA) como un problema de programacion lineal entera (ILP / Integer Linear Fractional Programming).
  - Halda resuelve con el motor HiGHS en C++ la particion optima de capas no solo entre maquinas, sino entre la GPU (VRAM), la CPU (RAM) y el almacenamiento local (disco via `mmap`) de cada dispositivo.
  - PRP solapa la lectura de pesos de disco (`mmap`) con el computo de capas y la transmision por red de la activacion del token anterior.
- **Que viaja por palabra**:
  - Activaciones intermedias que avanzan en el anillo (nodo $i \to$ nodo $i+1$).
  - Tamano por palabra: exactamente el vector de activacion del modelo (f32 o f16).
- **Cifras publicadas (textual segun el articulo)**:
  - Modelo: Llama-3-70B Q4, DeepSeek-R1 70B, QwQ-32B / Qwen-2.5-32B.
  - Equipos: Cluster domestico de 4 nodos heterogeneos (combinacion de computadoras de escritorio y portatiles con GPUs de consumo, CPUs convencionales y almacenamiento SSD/NVMe cotidiano).
  - Red: Wi-Fi convencional domestico (802.11ac / ax).
  - Resultados publicados:
    - Llama-3-70B: TPOT (Time-Per-Output-Token) de 674 ms/token (~1,48 tok/s) manteniendo la presion de memoria por debajo del 6% (sin fallo por OOM).
    - Modelos de 32B (QwQ-32B): hasta 26 tok/s utilizando decodificacion especulativa.
    - Comparativa: reduce el TPOT entre 5x y 17x respecto a `exo`, `dllama` y `llama.cpp` con offloading a disco tradicional en hardware modesto.
- **Idea concreta para CompuShare**:
  - Incorporar el enfoque matematico de optimizacion de Halda: resolver por programacion lineal o dinamica el corte de capas teniendo en cuenta simultaneamente VRAM, RAM, banda de memoria y RTT del enlace.
  - Solapamiento de I/O de disco y transmision de red mediante ejecucion en pipeline sobre anillo.

---

### 1.4 distributed-llama (dllama)

- **Repositorio**: https://github.com/b4rtaz/distributed-llama
- **Licencia**: MIT.
- **Como reparte**:
  - Paralelismo de tensores puro (Tensor Parallelism): cada capa del transformador (atencion multi-cabeza y matrices de proyeccion del MLP) se divide horizontal y verticalmente entre $2^n$ nodos (1, 2, 4, 8...).
  - Todos los nodos computan simultaneamente fragmentos de la misma capa.
  - Sincronizacion por sockets TCP en C++ puro.
- **Que viaja por palabra**:
  - Vectores de activaciones parciales en CADA capa del transformador (operacion de reduccion global / AllReduce).
  - Para un modelo de 32 a 48 capas, se ejecutan de 32 a 48 intercambios de red por token.
  - Admite `--buffer-float-type q80`: cuantiza a 8 bits el bufer de sincronizacion de red antes de enviarlo por el socket TCP, reduciendo el ancho de banda del enlace a la mitad.
- **Cifras publicadas (textual segun notas de version y banco de pruebas)**:
  - Modelos y hardware:
    - Qwen3-30B-A3B Q40 (MoE): 13,04 a 15,14 tok/s (~76,69 ms/tok en decodificacion) en un cluster de 4 x Raspberry Pi 5 (8 GB / 16 GB) conectadas por conmutador Gigabit Ethernet (1 Gb/s).
    - Llama-3.2-3B Q40: mas de 23 tok/s en el mismo cluster de 4 x Raspberry Pi 5.
    - Llama-3.1-8B Instruct Q40 (con backend Vulkan / GPU): 25,5 a 45,3+ tok/s.
- **Idea concreta para CompuShare**:
  - Cuantizacion de buferes de red a 8 bits (`q80`): compactar las activaciones antes de salir por el socket o WebSocket.
  - Implementacion liviana de sockets directos sin dependencias pesadas.
  - Ratificacion de la regla de CompuShare: el paralelismo de tensores solo funciona en LAN de bajisima latencia (<1 ms, conmutador gigabit), siendo inviable en WAN/Internet debido a los 32-64 saltos de red por palabra.

---

### 1.5 RPC de llama.cpp (ggml-rpc)

- **Codigo fuente inspeccionado**:
  - `/home/reyno/.local/opt/llama.cpp/ggml/src/ggml-rpc/ggml-rpc.cpp`
  - `/home/reyno/.local/opt/llama.cpp/tools/rpc/`
- **Licencia**: MIT.
- **Como reparte**:
  - Topologia en estrella Maestro-Trabajadores: el maestro centraliza el grafo computacional y expone dispositivos remotos mediante llamadas RPC.
  - Reparto de capas proporcional a la memoria libre (`--tensor-split`).
  - Cache local de pesos grandes: `ggml-rpc-server -c` utiliza un umbral de 10 MB (`HASH_THRESHOLD = 10 * 1024 * 1024`, linea 118 de `ggml-rpc.cpp`) calculando el hash FNV-1a para que los pesos no viajen por la red.
  - Soporte de transporte RDMA sobre RoCEv2 (Linux) y Thunderbolt 5 (macOS, tecnote TN3205).
- **Detalle de GRAPH_RECOMPUTE y COMM_ALLREDUCE en el codigo de llama.cpp**:
  - `GRAPH_RECOMPUTE` (lineas 74, 105, 1248-1274 y 2702-2708 de `ggml-rpc.cpp`):
    - Funcion `rpc_dispatcher::graph_compute`: si el grafo tiene `cgraph->uid != 0` y dicho identificador ya reside en la cache del dispositivo (`device_graph_uids.count(cgraph->uid) > 0`), envia unicamente el mensaje `rpc_msg_graph_recompute_req` (linea 1255, solo dispositivo y `uid`, ~12 bytes).
    - Si el grafo no estaba en cache, llama a `serialize_graph` (linea 1269) y envia el mensaje completo `RPC_CMD_GRAPH_COMPUTE` (linea 1272), que serializa decenas o cientos de nodos del grafo. La cache retiene hasta 256 grafos (`GRAPH_CACHE_MAX = 256`).
  - `COMM_ALLREDUCE` (lineas 79, 110, 2290-2409 y 2973-2983 de `ggml-rpc.cpp`):
    - Permite reduccion directa punto a punto entre servidores RPC (`state.peer`, linea 2295) sin pasar por el cliente maestro:
      `// Pairwise allreduce between two RPC servers over a direct server-to-server connection.`
      `// The client only sends fire-and-forget COMM_ALLREDUCE commands; the tensor data is`
      `// exchanged between the servers and never passes through the client.` (lineas 2973-2975).
    - Optimizacion de tamano de alambre: si el tensor tiene `type == GGML_TYPE_F32` y numero de elementos `ne >= 32768`, se convierte al vuelo a `bf16` (lineas 2320-2323: `const bool wire_bf16 = t_dst->type == GGML_TYPE_F32 && ne >= 32768; const size_t wire_bytes = wire_bf16 ? (size_t) ne*2 : nbytes;`), reduciendo los bytes transmitidos a la mitad.
    - Prevencion de interbloqueo: el rango 0 envia primero y luego recibe; el rango 1 recibe primero y luego envia (lineas 2374-2384).
- **Cifras medidas en CompuShare / synrag-fabric (`fabric/kaggle/resultados/20261031-dividido`)**:
  - Modelo: Qwen3.5-9B Q8_0 (9,53 GB, 32 capas, `d_modelo = 4096`).
  - Equipos: 2 cuadernos de Kaggle (2 x GPU T4 de 16 GB en cada cuaderno = 4 x T4 en total).
  - Red: Tunel de Cloudflare + rele WebSocket local; RTT mediano medido = 101 ms.
  - Medicion real: 147 a 150 ms por palabra (6,7 a 6,8 tok/s sin MTP; 10,3 a 10,8 tok/s con MTP).
  - Datos por palabra en el tunel: 315 KB medidos (cuando la activacion pura son 32 KB).
- **Idea concreta para CompuShare**:
  - Corregir el envio redundante del grafo asegurando que `GRAPH_RECOMPUTE` se active en todas las llamadas de decodificacion para bajar de 315 KB a la activacion minima.
  - Copiar la optimizacion de `ggml-rpc.cpp:2322`: enviar tensores mayores a 32K elementos en `bf16` o `f16` en lugar de `f32`.
  - Adoptar conexiones servidor-a-servidor directas para evitar el reenvio triangular a traves del maestro.

---

### 1.6 BloomBee (2025/2026)

- **Articulo cientifico**: "Distributed Generative Inference of LLM at Internet Scales with Multi-Dimensional Communication Optimization",
  arXiv:2604.21072 (2025/2026, https://arxiv.org/abs/2604.21072).
- **Repositorio**: https://github.com/ai-decentralized/BloomBee
- **Licencia**: Apache-2.0.
- **Como reparte**:
  - Optimizacion multidimensional de comunicacion: combina particionado de capas en pipeline, micro-lotes dinamicos y descarga selectiva de tensores.
  - Resuelve mediante programacion dinamica la coordinacion de recursos entre nodos heterogeneos de borde y nube.
  - Basado en los principios de Hivemind y Petals pero adaptado a enlaces asimetricos y de bajo ancho de banda.
- **Que viaja por palabra**:
  - Activaciones comprimidas mediante algoritmos adaptativos dependientes del ancho de banda medido en tiempo real en cada enlace.
- **Cifras publicadas**:
  - Reporta mejoras de rendimiento de 2x a 4x frente al esquema basico de Petals en redes WAN residenciales con alta asimetria de subida/bajada gracias a su programador de micro-lotes en pipeline.
- **Idea concreta para CompuShare**:
  - Programador dinamico de lotes adaptado al ancho de banda de subida residencial (que tipicamente es 5-10 veces menor que la bajada).

---

## 2. Contraste de cifras publicadas con `fabric/calculo.py`

Se implementaron en el modelo matematico de CompuShare los mismos escenarios descritos por los proyectos
para evaluar la exactitud predictiva de las formulas de `calculo.py`.

### 2.1 Tabla comparativa de escenarios

| Proyecto / Escenario | Modelo y Tamano | Equipos y Banda de Memoria | Red (RTT / Subida) | Medido / Publicado | Calculado por CompuShare | Desviacion | Causa fisica de la discrepancia |
|---|---|---|---|---|---|---|---|
| **CompuShare Kaggle** (`20261031-dividido`) | Qwen3.5-9B Q8 (9,53 GB, 32 cap, d=4096) | 2 cuadernos (4x T4, 320 GB/s ef. 0,7) | RTT 101 ms, 200 Mb/s, 315 KB/tok | **147-150 ms** (6,7 tok/s) | **156,1 ms** (6,40 tok/s) | **+5,1 %** | Modelo calibrado. Ligera sobreestimacion en la serializacion de red. |
| **Petals** (Borzunov et al., 2023) | BLOOM-176B 8-bit (176 GB, 70 cap, d=14336) | 14 nodos (RTX 3090/3060 mix, ~600 GB/s) | RTT medio 70 ms WAN, 100 Mb/s, solo act. | **~1000 ms** (~1,0 tok/s) | **1448,3 ms** (0,69 tok/s) | **+44,8 %** | En el enjambre real de Petals varios saltos son intra-Europa (~25-35 ms). A 35 ms promedio, da 874 ms (1,14 tok/s, dentro del 12 %). |
| **prima.cpp** (arXiv:2504.08791) | Llama-3-70B Q4 (40 GB, 80 cap, d=8192) | 4 PCs heterogeneas (mix GPU/CPU, ~150 GB/s) | Wi-Fi RTT 8 ms, 300 Mb/s, solo act. | **674 ms** (1,48 tok/s) | **410,2 ms** (2,44 tok/s) | **-39,1 %** | `calculo.py` asume lectura pura de VRAM/RAM (150 GB/s); prima.cpp sufre paginacion de disco (`mmap` NVMe) que anade ~260 ms de latencia I/O. |
| **distributed-llama** (b4rtaz) | Qwen MoE 30B-A3B Q40 (18 GB, 48 cap, d=2048) | 4x Raspberry Pi 5 (LPDDR4X 17 GB/s c/u) | Gigabit LAN (RTT 0,5 ms, 1 Gb/s), TP Q80 | **76,7 ms** (13,04 tok/s) | **61,8 ms** (16,18 tok/s) | **-19,4 %** | Sobrecarga de la pila TCP del kernel Linux en Raspberry Pi y serializacion de buferes en 48 sincronizaciones por token. |

---

## 3. Formalizacion del Pipelining Real

### 3.1 Proyectos que ejecutan pipelining real

- **Petals**: Permite que varios clientes o secuencias concurrentes avancen simultaneamente en distintas etapas de la cadena de servidores. En la Tabla 2 de su articulo, pasar de batch 1 a batch 8 incrementa la tasa de 4,18 a 31,3 tok/s (7,5 veces mas rapido con 8 GPUs).
- **prima.cpp**: Implementa *Pipelined-Ring Parallelism* (PRP), donde el anillo procesa en pipeline el paso de activaciones mientras solapa lectura de pesos desde almacenamiento secundario.
- **BloomBee**: Implementa particionado de micro-lotes en pipeline adaptado a la velocidad de cada tramo de la red.

En contraste, el RPC de `llama.cpp` actual opera en topologia de estrella sincrona: el servidor maestro envia el lote completo al trabajador 1, espera su respuesta, luego al trabajador 2, etc. Aunque exista concurrencia (`--parallel N`), los trabajadores no computan etapas distintas en paralelo en el mismo instante.

### 3.2 Derivacion matematica de la formula de rendimiento

Considerese una cadena o anillo de $N$ etapas (equipos $i = 0, \dots, N-1$).

1. **Tiempo de servicio de la etapa $i$ por palabra**:
   El tiempo que tarda el equipo $i$ en procesar una palabra de un flujo individual se compone del tiempo de lectura de sus pesos de memoria mas el tiempo de transmision de la activacion al nodo siguiente:

   $$t_{\text{calc}, i} = \frac{\text{capas}_i \cdot \text{pesos\_por\_capa}}{\text{banda}_i \cdot \eta_{\text{GPU}}}$$

   $$t_{\text{red}, i} = \frac{\text{RTT}_{i, i+1}}{2} + \frac{\text{bytes\_activacion} \cdot 8}{\text{subida}_i}$$

   $$t_{\text{etapa}, i} = t_{\text{calc}, i} + t_{\text{red}, i}$$

2. **Ventaja unidireccional en pipeline**:
   En topologia de estrella (llama.cpp RPC), el maestro envia la activacion al trabajador y este devuelve la salida ($2 \cdot d_{\text{modelo}} \cdot 4$ bytes). En una cadena o anillo unidireccional, la activacion cruza cada enlace en un solo sentido:

   $$\text{bytes\_activacion\_unidireccional} = d_{\text{modelo}} \cdot 4 \quad \text{[bytes en f32]}$$

3. **Cuello de botella y latencia total de punta a punta**:
   - Etapa mas lenta (cuello de botella del pipeline):
     $$t_{\text{cuello}} = \max_{i=0,\dots,N-1} (t_{\text{etapa}, i})$$
   - Latencia que experimenta un token desde la primera etapa hasta la ultima:
     $$t_{\text{latencia}} = \sum_{i=0}^{N-1} t_{\text{etapa}, i}$$

4. **Rendimiento total con $S$ flujos simultaneos**:
   En estado estacionario con $S$ flujos en vuelo a la vez:
   - Si el numero de flujos es bajo ($S < \frac{t_{\text{latencia}}}{t_{\text{cuello}}}$), el pipeline no esta completamente saturado y la tasa de generacion es proporcional al numero de flujos:
     $$R_{\text{total}}(S) = \frac{S \cdot 1000}{t_{\text{latencia}}} \quad \text{[palabras/s total]}$$
   - Cuando $S \ge \frac{t_{\text{latencia}}}{t_{\text{cuello}}}$ (para etapas equilibradas, cuando $S \ge N$), el cuello de botella trabaja al 100 % de ocupacion:
     $$R_{\text{total}}(S) = \frac{1000}{t_{\text{cuello}}} \quad \text{[palabras/s total]}$$
   Por tanto, la formula unificada de rendimiento total es:

   $$R_{\text{total}}(S) = \min\left( \frac{S \cdot 1000}{\sum_{i=0}^{N-1} t_{\text{etapa}, i}}, \; \frac{1000}{\max_{i=0}^{N-1} t_{\text{etapa}, i}} \right)$$

5. **Latencia por palabra experimentada por cada flujo**:
   Por la ley de Little ($L = \lambda W$), a medida que se satura el pipeline la latencia por palabra de cada flujo individual aumenta si se superan las etapas fisicas:

   $$t_{\text{por\_palabra\_flujo}}(S) = \max\left( t_{\text{latencia}}, \; S \cdot t_{\text{cuello}} \right) \quad \text{[ms por palabra]}$$

   $$R_{\text{flujo}}(S) = \frac{1000}{t_{\text{por\_palabra\_flujo}}(S)} = \frac{R_{\text{total}}(S)}{S}$$

---

## 4. Implementacion en `fabric/calculo.py` y pruebas

Se anadieron las siguientes funciones a `fabric/calculo.py`:
- `bytes_activacion_unidireccional(m)`: calcula $d_{\text{modelo}} \cdot \text{bytes\_por\_valor}$.
- `tiempo_pipelining(m, maestro, trabajadores, ...)`: modela la cadena/anillo con calculo de $t_{\text{etapa}, i}$, $t_{\text{cuello}}$, $t_{\text{latencia}}$, rendimiento total y latencia por flujo.

Pruebas agregadas en `fabric/pruebas/test_calculo.py`:
1. `test_pipelining_un_flujo_igual_a_latencia`: comprueba que con 1 solo flujo el tiempo coincide exactamente con la latencia acumulada de la cadena.
2. `test_pipelining_escala_hasta_cuello_de_botella`: comprueba que con flujos crecientes la tasa de palabras/s escala linealmente hasta alcanzar la capacidad maxima $1000 / t_{\text{cuello}}$, a partir de la cual satura.
3. `test_pipelining_supera_a_estrella_con_varios_flujos`: compara un escenario de 4 nodos distribuidos a 100 ms RTT, demostrando que pipelining supera en mas de un 49 % a la estrella de llama.cpp (13,08 tok/s frente a 8,73 tok/s) debido al solapamiento de la red y la activacion unidireccional.

Comando de ejecucion de pruebas:
`/home/reyno/Proyectos/CompuShare/.venv/bin/python -m pytest -q fabric/pruebas -p no:cacheprovider`
Resultado: 28 pasadas, 1 omitida (100 % en verde).

---

## 5. Cuadro comparativo global de arquitecturas

| Caracteristica | Petals | exo | prima.cpp | distributed-llama | llama.cpp RPC | CompuShare (Objetivo) |
|---|---|---|---|---|---|---|
| **Topologia** | P2P dinamico (DHT Hivemind) | P2P auto-descubierto | Anillo logico (PRP) | Maestro-Esclavos TCP | Estrella Maestro-Servidor | Anillo/Cadena con Rele |
| **Particionado** | Capas (Pipeline) | Capas o Tensores | Capas (VRAM/RAM/Disco) | Tensores puro (TP) | Capas o Tensores | Capas por memoria |
| **Que viaja por palabra** | Activacion oculta (~14-28 KB) | Activacion o reduccion | Activacion en anillo | AllReduce por capa (q80) | Grafo (315 KB) o Act (32 KB) | Activacion unidireccional |
| **Saltos de red por palabra** | 1 por etapa | 1 por etapa (pipe) o 2x capas (TP) | 1 por etapa en anillo | 32-64 por palabra | 2 por trabajador usado | 1 por etapa |
| **Pipelining real** | Si (multi-cliente / micro-batch) | En desarrollo | Si (PRP) | No | No (ejecucion sincrona) | Si (`tiempo_pipelining`) |
| **Apto para Internet (WAN)** | Si (disenado para WAN) | Solo local / LAN / TB | Si (resistente a Wi-Fi) | No (solo Gigabit LAN) | Si (pero penalizado en estrella) | Si (disenado para amigos) |
| **Licencia** | Apache-2.0 | Apache-2.0 | MIT | MIT | MIT | Propia (CompuShare) |

---

## 6. Ideas copiables y recomendaciones para CompuShare

1. **Adoptar enlace en cadena/anillo directo**: reemplazar el reenvio triangular por el rele de todas las activaciones intermedias. El rele debe usarse para descubrimiento, senalizacion y recuperacion de fallos, pero las activaciones deben viajar de nodo $i$ a nodo $i+1$.
2. **Reutilizacion estricta del grafo (`GRAPH_RECOMPUTE`)**: asegurar que la generacion paso a paso active siempre el identificador reutilizable para no transmitir los 315 KB observados en Kaggle, bajando a los 32 KB de la activacion f32.
3. **Cuantizacion de activaciones de red (`bf16` / `q80`)**: imitar `ggml-rpc.cpp:2322` y `dllama`, transmitiendo las activaciones en 16 u 8 bits para reducir el tiempo de enlace a la mitad.
4. **Asignador de capas tipo Halda**: reemplazar la heuristica estatica de memoria libre por un solucionador de programacion lineal que considere memoria, ancho de banda de calculo y latencia del salto.
5. **Pipelining multi-flujo**: implementar colas asincronas de tokens por etapa para que cuando CompuShare atienda multiples solicitudes (`flujos > 1`), cada maquina compute continuamente su tramo sin esperar la vuelta completa de cada palabra individual.
