# Informe A11: Viabilidad y Mediciones de Modelos MoE en CompuShare

Fecha: 4 de noviembre de 2026  
Autor: Agente A11 (rama `a11-moe`, worktree `/home/reyno/Proyectos/cs-a11`)  
Entorno: Laptop Linux x86_64 (12 hilos, 15 GB RAM, CPU Intel Core i5-13420H, sin GPU dedicada útil)  
Binarios: `llama.cpp` con soporte RPC en `/home/reyno/.local/opt/llama.cpp/build/bin/` (build `ddf48f8`)  
Modelo evaluado: OLMoE-1B-7B-0924-Instruct cuantizado en `Q4_K_M` (3,92 GiB, 6,92B parámetros totales, 1,3B parámetros activos) en `/home/reyno/.cache/fabric-descargas/modelos/OLMoE-1B-7B-0924-Instruct-Q4_K_M.gguf`  

---

## 1. Fundamentos y Mecánica de Enrutamiento en un MoE

### 1.1 Desmitificación del concepto "100 mini-IAs, una por tema, con 3 o 4 saltos"

La hipótesis de que un modelo Mixture-of-Experts (MoE) puede dividirse entre usuarios como si fueran "100 mini-IAs temáticas donde una consulta salta entre 3 o 4 nodos según el tema" es conceptual y técnicamente incorrecta por las siguientes razones:

1. **Los expertos no representan dominios temáticos:** En la literatura científica de modelos MoE ([Shazeer et al., 2017](https://arxiv.org/abs/1701.06538); [Fedus et al., 2021](https://arxiv.org/abs/2101.03961); [Clark et al., 2022](https://arxiv.org/abs/2202.08906); [Muennighoff et al., 2024](https://arxiv.org/abs/2409.02060)), los análisis de activación demuestran que los expertos no se especializan en materias ("biología", "historia", "derecho"). Se especializan en propiedades morfológicas y sintácticas de bajo nivel: sufijos, puntuación, tokens de cierre de bloque, frecuencias estadísticas de aparición o roles posicionales dentro del contexto.
2. **Los expertos residen en CADA capa:** No existe una conmutación estática previa a nivel de modelo. En una arquitectura MoE, la red feed-forward (FFN) estándar se sustituye por un grupo de $E$ expertos en **cada una de las $L$ capas** del Transformer.
3. **El enrutamiento se reevalúa en cada token y en cada capa:** La decisión de qué expertos intervienen se toma token a token mediante una función de compuerta (*router*) que proyecta el vector de estado oculto intermedio de esa capa específica. Dado que el estado en la capa $l+1$ es el resultado de la atención y los expertos de la capa $l$, es imposible determinar a priori qué expertos se activarán en las capas sucesivas.

### 1.2 Flujo matemático del enrutamiento Top-k en `llama.cpp`

Para cada token en cada capa $l \in [0, L-1]$:

1. **Proyección de compuerta (Router Logits):**  
   El tensor de entrada normalizado $x \in \mathbb{R}^{d_\text{embd}}$ se multiplica por la matriz de pesos del enrutador $W_g \in \mathbb{R}^{E \times d_\text{embd}}$ (`ffn_gate_inp`):
   $$H(x) = W_g x \in \mathbb{R}^E$$
   *Código en `llama.cpp`:* [`src/llama-graph.cpp:2165`](file:///home/reyno/.local/opt/llama.cpp/src/llama-graph.cpp#L2165)
   ```cpp
   logits = build_lora_mm(gate_inp, cur); // [n_expert, n_tokens]
   ```

2. **Probabilidades de selección (Softmax / Sigmoid):**  
   Se normalizan los logits a una distribución de probabilidades:
   $$P(x) = \text{Softmax}(H(x))$$
   *Código en `llama.cpp`:* [`src/llama-graph.cpp:2183`](file:///home/reyno/.local/opt/llama.cpp/src/llama-graph.cpp#L2183)
   ```cpp
   probs = ggml_soft_max(ctx0, logits); // [n_expert, n_tokens]
   ```

3. **Selección de los $k$ mejores expertos (Top-k Argsort):**  
   El enrutador extrae los $k$ índices con mayor puntuación (`n_expert_used`):
   $$K = \text{TopK}(P(x), k)$$
   *Código en `llama.cpp`:* [`src/llama-graph.cpp:2249`](file:///home/reyno/.local/opt/llama.cpp/src/llama-graph.cpp#L2249)
   ```cpp
   selected_experts = ggml_argsort_top_k(ctx0, selection_probs, n_expert_used);
   ```

4. **Normalización de pesos de los expertos activos:**  
   Los pesos correspondientes a los expertos seleccionados se re-escalan para sumar 1:
   $$w_i = \frac{P(x)_i}{\sum_{j \in K} P(x)_j}, \quad \forall i \in K$$
   *Código en `llama.cpp`:* [`src/llama-graph.cpp:2289`](file:///home/reyno/.local/opt/llama.cpp/src/llama-graph.cpp#L2289)
   ```cpp
   weights = ggml_div(ctx0, weights, weights_sum);
   ```

5. **Evaluación de expertos con `ggml_mul_mat_id`:**  
   Solo los bloques de pesos de los $k$ expertos activos son multiplicados mediante la operación indexada SwiGLU:
   $$y_i = W_{\text{down}}^{(i)} \left( \text{SiLU}(W_{\text{gate}}^{(i)} x) \odot W_{\text{up}}^{(i)} x \right)$$
   *Código en `llama.cpp`:* [`src/llama-graph.cpp:2319-2464`](file:///home/reyno/.local/opt/llama.cpp/src/llama-graph.cpp#L2319-L2464) invocando `build_lora_mm_id` sobre `gate_exps`, `up_exps` y `down_exps`.

6. **Combinación lineal y conexión residual:**  
   $$y = \sum_{i \in K} w_i y_i, \quad x_{\text{out}} = x + y$$
   *Código en `llama.cpp`:* [`src/llama-graph.cpp:2490-2525`](file:///home/reyno/.local/opt/llama.cpp/src/llama-graph.cpp#L2490-L2525).

En [`src/models/olmoe.cpp:136-147`](file:///home/reyno/.local/opt/llama.cpp/src/models/olmoe.cpp#L136-L147), esta rutina (`build_moe_ffn`) se ejecuta secuencialmente en las 16 capas del modelo.

### 1.3 Fracción de parámetros utilizados por token

Sea $P_{\text{compartido}}$ el volumen de parámetros en embeddings, atención multi-cabeza y normas, y $P_{\text{expertos}}$ el total en los $E$ expertos por capa:
$$\text{Fracción activa} = \frac{P_{\text{compartido}} + \frac{k}{E} P_{\text{expertos}}}{P_{\text{compartido}} + P_{\text{expertos}}}$$

Cifras en modelos MoE reales:
- **OLMoE-1B-7B (Muennighoff et al., 2024):** 64 expertos por capa, top-8 activos. Fracción en capas MoE: $8 / 64 = 12,50\%$. Considerando capas compartidas: **18,8% de pesos activos por token** (1,3B de 6,92B).
- **Mixtral 8x7B (Jiang et al., 2024):** 8 expertos por capa, top-2 activos. Fracción en capas MoE: $2 / 8 = 25,00\%$. Total activo: **27,6% de pesos activos por token** (12,9B de 46,7B).
- **DeepSeek-V3 (DeepSeek-AI, 2024):** 256 expertos enrutados (top-8 activos) + 1 experto compartido. Fracción en capas MoE: $8 / 256 = 3,12\%$. Total activo: **5,5% de pesos activos por token** (37B de 671B).

### 1.4 Por qué repartir expertos por Internet multiplica los saltos

Si se intentara ubicar expertos en nodos remotos independientes conectados por Internet (e.g. 64 nodos, uno por experto):
1. Al evaluar cada capa $l$, el router selecciona $k$ expertos. Como cada capa depende causalmente de la anterior, no es posible evaluar la capa $l+1$ sin haber sumado las salidas de los expertos de la capa $l$.
2. Para cada token individual generado, las activaciones deben cruzar la red hacia los nodos de los expertos y regresar. Esto impone **como mínimo $L$ viajes de ida y vuelta (RTT) por token**.
3. Con un RTT real de $237\text{ ms}$ (túnel medido en `fabric/PRUEBA-10.md`):
   - En OLMoE ($L = 16$ capas): $16 \times 237\text{ ms} = 3.792\text{ ms} = 3,79\text{ s}$ solo en latencia de red por token $\implies \mathbf{0,26\text{ tok/s}}$.
   - En Mixtral ($L = 32$ capas): $32 \times 237\text{ ms} = 7.584\text{ ms} = 7,58\text{ s}$ por token $\implies \mathbf{0,13\text{ tok/s}}$.
   - En DeepSeek ($L = 61$ capas): $61 \times 237\text{ ms} = 14.457\text{ ms} = 14,46\text{ s}$ por token $\implies \mathbf{0,069\text{ tok/s}}$.
   Generar 100 tokens tomaría entre 6 y 24 minutos, haciendo inviable cualquier interacción en tiempo real.

---

## 2. Mediciones Empíricas Locales con OLMoE-1B-7B

Todas las mediciones se ejecutaron sobre el modelo `OLMoE-1B-7B-0924-Instruct-Q4_K_M.gguf` (3,92 GiB) el 4 de noviembre de 2026 en la laptop de desarrollo. Los datos de transferencia de red se obtuvieron con un proxy TCP asíncrono intermedio que contabilizó bytes bidireccionales reales.

### 2.1 Medición 1: Modelo completo en una sola máquina (CPU local)

- **Comando exacto:**
  ```bash
  /home/reyno/.local/opt/llama.cpp/build/bin/llama-bench \
    -m /home/reyno/.cache/fabric-descargas/modelos/OLMoE-1B-7B-0924-Instruct-Q4_K_M.gguf \
    -p 16 -n 32 -t 6 -r 3
  ```
- **Archivo de resultado:** [`bench_local.log`](file:///home/reyno/Proyectos/cs-a11/bench_local.log)
- **Cifras medidas (3 repeticiones):**
  - Prompt processing (`pp16`): **78,33 ± 5,76 tok/s**
  - Generación de texto (`tg32`): **20,25 ± 2,06 tok/s**

---

### 2.2 Medición 2: Reparto por capas en 2 procesos `ggml-rpc-server` locales (`--rpc` con `-ts 1/1`)

Se iniciaron 2 servidores RPC independientes escuchando en los puertos `50123` y `50124` (3 hilos cada uno). El cliente `llama-bench` se conectó a través de proxies locales en `50133` y `50134` repartiendo las 16 capas en partes iguales (capas 0 a 7 en Nodo 1, capas 8 a 15 en Nodo 2).

- **Comando exacto de servidores:**
  ```bash
  /home/reyno/.local/opt/llama.cpp/build/bin/ggml-rpc-server -H 127.0.0.1 -p 50123 -t 3 &
  /home/reyno/.local/opt/llama.cpp/build/bin/ggml-rpc-server -H 127.0.0.1 -p 50124 -t 3 &
  ```
- **Comando exacto de benchmark:**
  ```bash
  /home/reyno/.local/opt/llama.cpp/build/bin/llama-bench \
    -m /home/reyno/.cache/fabric-descargas/modelos/OLMoE-1B-7B-0924-Instruct-Q4_K_M.gguf \
    -p 16 -n 32 -t 3 -r 3 \
    --rpc 127.0.0.1:50133,127.0.0.1:50134 -ngl 99 -ts 1/1
  ```
- **Archivo de resultado:** [`bench_rpc_capas.log`](file:///home/reyno/Proyectos/cs-a11/bench_rpc_capas.log)
- **Cifras medidas (3 repeticiones):**
  - Prompt processing (`pp16`): **75,46 ± 12,59 tok/s**
  - Generación de texto (`tg32`): **37,98 ± 2,90 tok/s**
  - *Incremento de velocidad en CPU:* 37,98 tok/s frente a 20,25 tok/s (1,87x) debido a que 2 procesos independientes aprovechan mejor el ancho de banda de memoria L3 y eliminan la contención de hilos en CPU.
- **Tráfico medido durante decodificación:**
  - Delta al pasar de $N = 16$ a $N = 32$ tokens de generación: 3.973.568 bytes en 16 tokens.
  - **Tráfico por token:** **248.348 bytes/token** (~242,5 KiB/token), correspondientes a la activación intermedia en la frontera entre capas y cabeceras RPC de sincronización.

---

### 2.3 Medición 3: Expertos en un proceso y el resto en otro (`-ot / --override-tensor`)

En `llama.cpp`, el parámetro `-ot` asigna tensores mediante expresiones regulares a tipos de buffer de dispositivo registrados (`RPC0[host:puerto]`). Se configuró para ubicar todos los tensores de expertos (`.*ffn_.*exps.*`) en el proceso RPC 2 (`50134`), mientras que la atención, embeddings y normalizaciones residieron en el proceso RPC 1 (`50133`).

- **Comando exacto:**
  ```bash
  /home/reyno/.local/opt/llama.cpp/build/bin/llama-bench \
    -m /home/reyno/.cache/fabric-descargas/modelos/OLMoE-1B-7B-0924-Instruct-Q4_K_M.gguf \
    -p 16 -n 32 -t 3 -r 3 \
    --rpc 127.0.0.1:50133,127.0.0.1:50134 -ngl 99 \
    -ot ".*ffn_.*exps.*=RPC0[127.0.0.1:50134]"
  ```
- **Archivo de resultado:** [`bench_rpc_expertos.log`](file:///home/reyno/Proyectos/cs-a11/bench_rpc_expertos.log)
- **Cifras medidas (3 repeticiones):**
  - Prompt processing (`pp16`): **69,82 ± 7,97 tok/s**
  - Generación de texto (`tg32`): **28,39 ± 3,23 tok/s**
  - En prueba interactiva con `llama-cli` y prompt largo: cayó a **15,7 tok/s**.
  - Caída de rendimiento frente al reparto por capas: **-25,2 %** en loopback local sin latencia simulada, debido a la sobrecarga de 16 transferencias por token en el grafo de ejecución.
- **Tráfico medido durante decodificación:**
  - Delta al pasar de $N = 16$ a $N = 32$ tokens de generación: 10.993.360 bytes en 16 tokens.
  - **Tráfico por token:** **687.085 bytes/token** (~671,0 KiB/token).
  - *Comparativa de tráfico:* El reparto de expertos cruza **2,77 veces más bytes** por token que el reparto por capas (687 KB vs 248 KB), y genera 16 divisiones de grafo por token en lugar de 1 sola.

---

## 3. Análisis de Esquemas con Latencia de Red Real (RTT = 237 ms)

Utilizando la latencia de ida y vuelta de $237\text{ ms}$ medida en el túnel de Cloudflare (`fabric/PRUEBA-10.md`), un ancho de banda típico de $50\text{ Mb/s}$ ($6,25\text{ MB/s}$) y los tiempos de cómputo medidos:

### 3.1 Esquema A: Repartir expertos entre computadoras por Internet
- En OLMoE ($L = 16$ capas), cada token exige $16$ saltos de red consecutivos:
  $$T_{\text{latencia}} = 16 \times 237\text{ ms} = 3.792\text{ ms} = 3,792\text{ s}$$
  $$T_{\text{ancho\_banda}} = \frac{687\text{ KB}}{6.250\text{ KB/s}} \approx 110\text{ ms} = 0,110\text{ s}$$
  $$T_{\text{cómputo}} \approx 26\text{ ms} = 0,026\text{ s}$$
  $$T_{\text{total por token}} = 3.792 + 110 + 26 = 3.928\text{ ms} \approx 3,93\text{ s/token}$$
  $$\text{Velocidad máxima} = \frac{1}{3,93\text{ s}} \approx \mathbf{0,25\text{ tok/s}}$$
- En un modelo de 32 capas (ej. Mixtral 8x7B):
  $$T_{\text{total por token}} \approx (32 \times 0,237) + 0,220 + 0,050 \approx 7,85\text{ s/token} \implies \mathbf{0,13\text{ tok/s}}$$
- **Conclusión:** **TOTALMENTE INVIABLE.** El usuario esperaría 6,5 minutos para recibir 100 tokens.

---

### 3.2 Esquema B: Repartir por capas completas (Pipeline Parallelism, `--rpc` con `-ts 1/1`)
- El Nodo 1 procesa las capas 0..7 con todos sus expertos en local; el Nodo 2 procesa las capas 8..15 con todos sus expertos en local.
- Solo hay **1 salto de red de ida y vuelta por token** (en la frontera entre capas):
  $$T_{\text{latencia}} = 1 \times 237\text{ ms} = 0,237\text{ s}$$
  $$T_{\text{ancho\_banda}} = \frac{248\text{ KB}}{6.250\text{ KB/s}} \approx 40\text{ ms} = 0,040\text{ s}$$
  $$T_{\text{cómputo}} \approx 26\text{ ms} = 0,026\text{ s}$$
  $$T_{\text{total por token}} = 237 + 40 + 26 = 303\text{ ms} \approx 0,303\text{ s/token}$$
  $$\text{Velocidad con 1 flujo} = \frac{1}{0,303\text{ s}} \approx \mathbf{3,30\text{ tok/s}}$$
- **Ganancia frente a repartir expertos:** El reparto por capas es **13,2 veces más rápido** en OLMoE y **26 veces más rápido** en Mixtral.

---

### 3.3 Esquema C: Reparto por capas con flujos paralelos (`--parallel N`)
- Al procesar $N = 4$ u $8$ solicitudes simultáneas, mientras el Nodo 2 computa el token de la consulta A, el Nodo 1 calcula el token de la consulta B.
- La latencia por respuesta sigue siendo de ~300 ms, pero el rendimiento agregado se multiplica por $N$:
  $$\text{Throughput total} \approx \min\left( N \times 3,3\text{ tok/s}, \frac{1000\text{ ms}}{T_{\text{cómputo}}} \right) \approx \mathbf{15 - 25\text{ tok/s agregados}}$$
- **Conclusión:** **ESQUEMA RECOMENDADO PARA COMPUSHARE.**

---

### 3.4 Esquema D: Nodo individual con poca GPU y mucha RAM (`--cpu-moe` / `-cmoe`)
- En equipos de usuarios con 4-8 GB de VRAM y 16-32 GB de RAM del sistema:
  `llama.cpp` permite ubicar la atención en GPU y los expertos en RAM mediante `-cmoe` o `-ncmoe`.
- El tráfico no sale a Internet (viaja por el bus PCIe local a 16-32 GB/s, latencia de sub-microsegundos).
- Se cargan dinámicamente solo los $k$ expertos activos ($12,5\%$ a $25\%$ del FFN).
- **Velocidad esperada:** **15 - 35 tok/s**, sin dependencia de red.

---

## 4. Tabla Comparativa de Esquemas

| Esquema | Saltos RTT por token | Tráfico por token | Vel. con RTT 237 ms | ¿Conviene en CompuShare? |
|---|---|---|---|---|
| **A. Expertos repartidos por Internet** | $L$ (16 a 64) | 687 KB / tok | **0,25 tok/s** | **NO CONVIENE.** Multiplica la latencia por el número de capas. Inútil para uso interactivo. |
| **B. Capas completas por RPC (1 flujo)** | 1 | 248 KB / tok | **3,30 tok/s** | **SÍ CONVIENE.** Divide memoria sin multiplicar la latencia. |
| **C. Capas completas con flujos paralelos (`--parallel N`)** | 1 (amortizado) | 248 KB / tok | **15 - 25 tok/s (agregado)** | **SÍ CONVIENE.** Máximo aprovechamiento de la red compartida. |
| **D. Experto en RAM local (`--cpu-moe`)** | 0 (PCIe) | 0 en red | **15 - 35 tok/s** | **SÍ CONVIENE.** Ideal para computadoras individuales de usuarios. |

---

## 5. Resumen de Hallazgos y Qué Quedó Pendiente

1. **Lo que SÍ se puede hacer con MoE en CompuShare:**
   - Repartir modelos MoE grandes (ej. Qwen3.6-35B-A3B o DeepSeek) dividiéndolos **por capas consecutivas** entre nodos con `--rpc` (`-ts`). Cada nodo almacena todos los expertos correspondientes a su segmento de capas.
   - Utilizar `--cpu-moe` en computadoras individuales para mantener la atención en la tarjeta gráfica del usuario y los expertos en su memoria RAM sin cruzar la red.
   - Atender múltiples peticiones en paralelo con `--parallel` para ocultar la latencia de 237 ms del túnel.
2. **Lo que NO se debe hacer:**
   - Intentar fragmentar expertos individuales entre computadoras distintas por la red. Introduce $L$ viajes de ida y vuelta obligatorios por token, colapsando la velocidad a fracciones de token por segundo.
3. **Pendientes:**
   - Evaluar el comportamiento de `--moe-cache-mib` en nodos trabajadores con GPU para medir si una pequeña caché VRAM acelera el modo `--cpu-moe`.
