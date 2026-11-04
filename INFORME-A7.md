# Informe A7: Decodificación Especulativa MTP en Red Simulada

Fecha: 2026-11-04
Agente: A7 (CompuShare)
Worktree: `/home/reyno/Proyectos/cs-a7` (rama `a7-especulativa`)
Archivo de datos: `fabric/resultados_a7.json`
Script de medición: `fabric/medir_a7.py`

---

## 1. Resumen Ejecutivo

Se evaluó empíricamente la decodificación especulativa MTP (Multi-Token Prediction) en un entorno distribuido heterogéneo utilizando `llama-server` (maestro local) y `ggml-rpc-server` (trabajador) conectados mediante el simulador de red `fabric/simulador/red.py` con el perfil "mismo país" (latencia de 40 ms ida, 10 ms jitter, 50 Mb/s de ancho de banda).

### Conclusiones Principales:
1. **$k$ óptimo para la red de amigos**: **$k = 4$**, alcanzando **410,49 ms/palabra** (2,44 tok/s), lo que representa una aceleración de **1,18x** frente a la generación sin especulativa ($k=0$, 482,83 ms/palabra).
2. **Comportamiento de $\gamma$**: Para $k=4$, el parámetro de sobrecosto por token especulativo es $\gamma = 0,8127$. Este valor es sensiblemente superior al valor de referencia en Kaggle ($\gamma = 0,28$) debido a que cada paso del draft en el trabajador acumula viajes de red y sincronizaciones RPC sobre el enlace de 40 ms.
3. **Ubicación de la cabeza MTP**: En llama.cpp b12024, la cabeza MTP reside en `src/models/qwen35.cpp:495-663` como la capa `il = 24` (`blk.24.*`). Al repartir con `-ngl 13`, dicha capa queda asignada al trabajador `RPC0`.
4. **Intento de reubicar MTP al maestro con `-ot`**: No reduce $\gamma$, sino que lo dispara a $\gamma = 2,2911$ en $k=4$ y duplica el tiempo por palabra (de 410,5 ms a 946,7 ms). La causa técnica es que `-ot` únicamente reubica los tensores de pesos en memoria CPU, pero la estructura `dev_layer[24]` y el búfer de atención (caché KV) permanecen en `RPC0`, provocando transferencias cruzadas bidireccionales en la red en cada paso del draft.

---

## 2. Configuración Experimental

Todas las pruebas se ejecutaron en CPU local bajo el bloqueo exclusivo de medición:
`flock /home/reyno/Proyectos/cs-agentes/medir.lock`

- **Fecha y hora de inicio**: 2026-11-04T16:08:46.841401+00:00
- **Carga inicial del sistema**: Load average `[0.72, 0.84, 0.96]` (procesos activos de fondo: entornos de desarrollo e interfaces del sistema).
- **Modelo**: `Qwen3.5-0.8B-MTP-Q8_0.gguf` (ubicado en `/home/reyno/.cache/fabric-descargas/modelos/`), compuesto por 24 capas base + 1 capa MTP (25 capas en total).
- **Hilos de cómputo**: `-t 4` en `llama-server` y `-t 4` en `ggml-rpc-server` (máximo permitido: 6 hilos).
- **Puertos de red asignados (rango A7 55000–55999)**:
  - Servidor RPC: `127.0.0.1:55100`
  - Proxy simulador de red: `127.0.0.1:55200`
  - Servidor HTTP llama.cpp: `127.0.0.1:55300`
- **Perfil de red**: "mismo país" (latencia 40 ms ida, 10 ms jitter, 50 Mb/s).
- **Control de MTP en llama.cpp**:
  - Para $k=0$: `--spec-type none`.
  - Para $k \in \{1, 2, 3, 4, 6\}$: `--spec-type draft-mtp --spec-draft-n-max <k>`.
- **Aislamiento de la fase de generación**:
  Se ejecutó una consulta de 16 tokens ($A$) y una consulta de 48 tokens ($B$) sobre el prompt `"La capital de Francia es"`, con `ignore_eos: true` y `cache_prompt: false`. La métrica de generación pura se obtiene de la diferencia $B - A$ (32 tokens netos), eliminando cualquier interferencia de la evaluación del prompt.
- **Salida en el maestro**: Se aplicó en todas las ejecuciones `fabric.reparto.salida_en_el_maestro(0)` (`-ot "(token_embd|output)\.weight=CPU"`).

---

## 3. Resultados: Cabeza MTP en el Trabajador (Por Defecto)

Comando base del servidor:
```bash
/home/reyno/.local/opt/llama.cpp/build/bin/llama-server \
  -m /home/reyno/.cache/fabric-descargas/modelos/Qwen3.5-0.8B-MTP-Q8_0.gguf \
  --rpc 127.0.0.1:55200 \
  -ngl 13 \
  -ot "(token_embd|output)\.weight=CPU" \
  -t 4 \
  --port 55300 \
  -c 2048 -ctxcp 0 --cache-ram 0 \
  --spec-type draft-mtp --spec-draft-n-max <k>
```

### Tabla de Mediciones (Suite 1: MTP en RPC0)

| $k$ | ms / palabra | Palabras / s | Tasa acierto ($\alpha$) | Palabras / pasada | Mensajes / pasada | Bytes / pasada | $\gamma$ (`calibrar_mtp`) |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **0** | 482,83 | 2,07 | 0,0000 | 1,000 | 46,9 | 54.558 B | 0,0000 |
| **1** | 532,59 | 1,88 | 1,0000 | 2,000 | 185,9 | 239.485 B | 1,2061 |
| **2** | 470,89 | 2,12 | 1,0000 | 3,200 | 245,5 | 313.688 B | 0,9629 |
| **3** | 441,67 | 2,26 | 1,0000 | 4,000 | 345,6 | 454.647 B | 0,8863 |
| **4** | **410,49** | **2,44** | **1,0000** | **5,333** | **445,2** | **587.168 B** | **0,8127** |
| **6** | 538,11 | 1,86 | 0,8387 | 5,333 | 609,0 | 815.505 B | 0,6488 |

### Análisis de la Suite 1:
- Con $k=1$, el tiempo por palabra sube a 532,59 ms porque generar 1 token adicional duplica los pasos de red sin amortizar el RTT.
- A partir de $k=2$, la alta tasa de aceptación compensa el sobrecosto de red, alcanzando el óptimo en **$k=4$** con **410,49 ms/palabra** (2,44 tok/s).
- En $k=6$, la tasa de acierto cae a $\alpha = 0,8387$ (26 de 31 tokens aceptados), lo que introduce penalizaciones por rechazo y verificación, degradando la velocidad a 538,11 ms/palabra (peor que sin especulativa).
- **Comparación con $\gamma = 0,28$**:
  En Kaggle (entorno GPU con comunicación local de alta velocidad), $\gamma$ fue de 0,28. En nuestra red con 40 ms de RTT, $\gamma$ oscila entre **0,81 y 0,96** en la zona óptima. Esto demuestra que la sobrecarga de red domina el costo de cada token del draft.

---

## 4. Análisis del Código Fuente de llama.cpp

Se inspeccionó el repositorio de llama.cpp en `/home/reyno/.local/opt/llama.cpp`:

1. **Cálculo de la cabeza MTP**:
   - **Archivo**: `/home/reyno/.local/opt/llama.cpp/src/models/qwen35.cpp`
   - **Líneas**: 495–663 (`struct llama_model_qwen35::graph_mtp`).
   - **Línea 505**:
     ```cpp
     const int il = hparams.n_layer();
     ```
     En Qwen3.5-0.8B, `hparams.n_layer()` vale 24 (capas 0 a 23). Por lo tanto, la capa MTP recibe el índice `il = 24`. Sus tensores en el archivo GGUF son `blk.24.*` (15 tensores que suman 20,74 MB).

2. **Asignación de dispositivo para la capa MTP**:
   - **Archivo**: `/home/reyno/.local/opt/llama.cpp/src/llama-model.cpp`
   - **Líneas**: 1690–1715 (`llama_model::get_layer_buft_list`).
   - El modelo cuenta con $24 + 1 = 25$ capas. Con la opción `-ngl 13`, el cálculo del índice inicial de capas asignadas a GPU/RPC es:
     ```cpp
     i_gpu_start = 25 + 1 - 13 = 13;
     ```
   - Capas `0..11`: asignadas a `CPU` (maestro).
   - Capas `12..24`: asignadas a `RPC0` (trabajador).
   - En consecuencia, la capa 24 (cabeza MTP) se aloja por defecto en el servidor RPC del trabajador.

---

## 5. Resultados: Prueba de MTP en el Maestro (`-ot`)

Para evaluar si mover la cabeza MTP a la CPU del maestro reducía los viajes de red y bajaba $\gamma$, se ejecutó el servidor con la regla de reemplazo de tensores:
`-ot "(token_embd|output)\.weight|blk\.24\..*=CPU"`

### Tabla de Mediciones (Suite 2: MTP forzada a CPU vía `-ot`)

| $k$ | ms / palabra | Palabras / s | Tasa acierto ($\alpha$) | Palabras / pasada | Mensajes / pasada | Bytes / pasada | $\gamma$ (`calibrar_mtp`) |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **0** | 465,71 | 2,15 | 0,0000 | 1,000 | 48,2 | 54.558 B | 0,0000 |
| **1** | 1.173,99 | 0,85 | 1,0000 | 2,000 | 363,2 | 464.329 B | 4,0417 |
| **2** | 1.076,90 | 0,93 | 1,0000 | 3,200 | 560,8 | 726.365 B | 2,9686 |
| **3** | 1.041,27 | 0,96 | 1,0000 | 4,000 | 757,6 | 993.951 B | 2,6478 |
| **4** | 946,72 | 1,06 | 1,0000 | 5,333 | 1.006,3 | 1.311.009 B | 2,2911 |
| **6** | 1.290,10 | 0,78 | 0,8387 | 5,333 | 1.345,7 | 1.758.233 B | 1,8601 |

### Causa Técnica de la Degradación:
Mover la cabeza MTP con `-ot` no disminuyó $\gamma$; al contrario, lo triplicó (de 0,81 a 2,29 en $k=4$), y el rendimiento se redujo a la mitad (de 410,5 ms a 946,7 ms).

1. **Mapeo parcial de `-ot`**: `-ot` (`llama-model-loader.cpp:1248-1270`) solo redirige los búferes de memoria donde se cargan los tensores de pesos del archivo GGUF.
2. **Persistencia de `dev_layer`**: El arreglo `pimpl->dev_layer` asigna la capa `24` a `RPC0` en función de `-ngl 13`.
3. **Caché KV fragmentada**: La memoria asignada a la atención de la capa 24 (`llama_memory_attn` en `llama-model.cpp:3294-3296`) se crea sobre el dispositivo de la capa (`RPC0`).
4. **Tráfico cruzado**: En tiempo de ejecución, el planificador `ggml_backend_sched` detecta que las activaciones y pesos residen en la CPU del maestro, pero la memoria de atención está en el trabajador. Esto fuerza múltiples intercambios de datos por la red para cada token del draft.
5. **Impacto en tráfico**: En $k=4$, los paquetes por pasada aumentan de 445 a 1.006 (+126 %), y los bytes por pasada aumentan de 587 KB a 1,31 MB (+123 %).

---

## 6. Modificaciones en el Código del Proyecto

1. **`fabric/reparto.py`**:
   Se agregó la función `mtp_en_el_maestro(gpus_locales: int, patron_capa: str = r"blk\.24\..*") -> list[str]` con documentación explícita que advierte sobre el comportamiento medido en llama.cpp b12024.
2. **`fabric/pruebas/test_reparto.py`**:
   Se implementó la prueba unitaria `test_mtp_en_el_maestro()` validando la generación correcta de argumentos `-ot`. Suite completa: 42 pruebas pasadas, 1 omitida.
3. **`fabric/medir_a7.py`**:
   Script de medición automatizado con manejo de procesos, simulación asíncrona de red, queries diferenciales para aislamiento de tokens y recolección de métricas.
4. **`fabric/resultados_a7.json`**:
   Almacén de datos con todas las telemetrías y métricas crudas de las suites de medición.

---

## 7. Qué se Hizo, Qué Funcionó y Qué Quedó Pendiente

### Qué se hizo:
- Análisis del código fuente de llama.cpp para localizar la implementación y asignación de dispositivos de la cabeza MTP.
- Construcción y validación del arnés de pruebas para decodificación especulativa sobre `fabric/simulador/red.py`.
- Medición sistemática para $k \in \{0, 1, 2, 3, 4, 6\}$ bajo dos arquitecturas de colocación de tensores.
- Despeje riguroso del parámetro $\gamma$ mediante `fabric.calculo.calibrar_mtp`.

### Qué funcionó:
- La especulativa MTP con la cabeza en el trabajador acelera la generación en un **18 %** (de 482,8 ms a 410,5 ms) en $k=4$.
- El aislamiento de tráfico mediante consultas diferenciales $B - A$ eliminó el ruido de la fase de prompt.

### Qué no funcionó y por qué:
- **Reducir $\gamma$ con `-ot`**: El intento de colocar la cabeza MTP en el maestro mediante `-ot` fracasó por limitaciones arquitectónicas de llama.cpp b12024, que desacopla la ubicación de los pesos de la ubicación de la caché KV y de `dev_layer`.

### Qué quedó pendiente:
- Para que la cabeza MTP pueda ejecutarse en el maestro sin viajes de red y lograr $\gamma \approx 0,05$, se requiere modificar el motor C++ de llama.cpp para que la capa MTP ($il = 24$) y su contexto de atención se instancien en un contexto CPU independiente, evitando que `-ngl` arrastre la capa al trabajador RPC.
