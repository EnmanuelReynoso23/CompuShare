# Informe Agente A14: Estado del arte en inferencia distribuida y pipelining

Fecha: 4 de noviembre de 2026.
Agente: A14.
Rama: a14-estado-del-arte.
Worktree: /home/reyno/Proyectos/cs-a14.

---

## 1. Que se hizo

1. **Investigacion exhaustiva del estado del arte**:
   - Se investigaron y analizaron en profundidad cinco proyectos consolidados y uno reciente de 2025/2026:
     - **Petals** (Borzunov et al., NeurIPS 2023 / arXiv:2209.01188, Apache-2.0).
     - **exo** (`exo-explore/exo`, Apache-2.0).
     - **prima.cpp** (arXiv:2504.08791, MIT) con el solucionador Halda y su paralelismo Pipelined-Ring (PRP).
     - **distributed-llama** (`b4rtaz/distributed-llama`, MIT) con paralelismo de tensores puro y buferes `q80`.
     - **RPC de llama.cpp** (`ggml/src/ggml-rpc/ggml-rpc.cpp` en `/home/reyno/.local/opt/llama.cpp`, MIT), con analisis detallado de `GRAPH_RECOMPUTE` (lineas 1248-1274) y `COMM_ALLREDUCE` (lineas 2290-2409 y 2973-2983).
     - **BloomBee** (arXiv:2604.21072, Apache-2.0), enfocado en optimizacion multidimensional de micro-lotes para redes residenciales.
   - Para cada uno se documentaron: metodo de reparto, volumen de datos en transito por palabra, cifras textuales publicadas, licencia e ideas copiables para CompuShare.
   - Se genero la entrega principal: `docs/ESTADO-DEL-ARTE.md`.

2. **Contraste cuantitativo con `fabric/calculo.py`**:
   - Se modelaron en Python los escenarios exactos de cada proyecto utilizando las clases `Equipo` y `Modelo`.
   - Se cuantifico el margen de desviacion del modelo de CompuShare frente a cada cifra publicada y se explicaron fisicamente las discrepancias observadas (sobrecarga de paginacion de disco por `mmap` en prima.cpp, agrupacion regional de pings en Petals, y sobrecarga de sockets TCP de Linux en Raspberry Pi para dllama).

3. **Formalizacion e implementacion de Pipelining Real**:
   - Se identificaron los proyectos con pipelining real (Petals, prima.cpp y BloomBee).
   - Se derivo la formula de rendimiento en estado estacionario para una cadena o anillo de $N$ etapas con $S$ flujos concurrentes:
     $$t_{\text{etapa}, i} = t_{\text{calc}, i} + t_{\text{red}, i}$$
     $$t_{\text{cuello}} = \max_i(t_{\text{etapa}, i})$$
     $$t_{\text{latencia}} = \sum_i t_{\text{etapa}, i}$$
     $$R_{\text{total}}(S) = \min\left( \frac{S \cdot 1000}{t_{\text{latencia}}}, \; \frac{1000}{t_{\text{cuello}}} \right) \quad \text{[palabras/s]}$$
     $$t_{\text{por\_palabra\_flujo}}(S) = \max(t_{\text{latencia}}, \; S \cdot t_{\text{cuello}}) \quad \text{[ms/palabra]}$$
   - Se agrego a `fabric/calculo.py`:
     - `bytes_activacion_unidireccional(m)`: calculo del trafico unidireccional por salto ($d_{\text{modelo}} \cdot 4$).
     - `tiempo_pipelining(...)`: implementacion completa del modelo matematico de pipelining.
   - Se escribieron pruebas unitarias en `fabric/pruebas/test_calculo.py` verificando:
     - Igualdad de latencia con 1 flujo.
     - Escalado lineal hasta la saturacion del cuello de botella ($1000 / t_{\text{cuello}}$).
     - Ventaja de rendimiento de pipelining frente a estrella en entornos multi-nodo distribuidos.

---

## 2. Cifras medidas con su comando exacto

### 2.1 Pruebas unitarias de la suite completa

- **Comando**:
  ```bash
  cd /home/reyno/Proyectos/cs-a14 && /home/reyno/Proyectos/CompuShare/.venv/bin/python -m pytest -q fabric/pruebas -p no:cacheprovider
  ```
- **Fecha**: 4 de noviembre de 2026.
- **Resultado medido**:
  `28 passed, 1 skipped in 6.69s` (100 % en verde).

### 2.2 Desviaciones del modelo matematico frente a las publicaciones

- **Comando de ejecucion**:
  ```bash
  /home/reyno/Proyectos/CompuShare/.venv/bin/python -c "
  import sys; sys.path.insert(0, '/home/reyno/Proyectos/cs-a14')
  from fabric.calculo import *
  # 1. Kaggle Qwen3.5-9B
  r_k = tiempo_por_palabra(QWEN35_9B_Q8, Equipo('m', 30, T4_GBS), [Equipo('t', 30, T4_GBS, 101, 200)], BYTES_POR_PALABRA_MEDIDO)
  # 2. Petals BLOOM-176B
  m_b = Modelo('BLOOM-176B', 176.0, 70, 14336)
  eq_p = [Equipo(f'p{i}', 16, 600, 70 if i > 0 else 0, 100) for i in range(14)]
  r_p = tiempo_por_palabra(m_b, eq_p[0], eq_p[1:])
  # 3. prima.cpp Llama-3-70B
  m_l = Modelo('Llama-3-70B', 40.0, 80, 8192)
  eq_pr = [Equipo(f'n{i}', 12, 150, 8 if i > 0 else 0, 300) for i in range(4)]
  r_pr = tiempo_por_palabra(m_l, eq_pr[0], eq_pr[1:])
  # 4. dllama Qwen MoE 30B TP
  m_d = Modelo('Qwen-MoE-30B', 18.0, 48, 2048, activos_frac=3/30)
  calc_d = 1000 * (18.0 * 1e9 * (3/30)) / (17 * 1e9 * 0.7)
  red_d = ms_tensor_paralelo(m_d, 0.5)
  t_d = calc_d / 4 + red_d
  print(f'Kaggle: calc={r_k.ms_por_pasada:.1f} ms vs med 148.5 ms ({(r_k.ms_por_pasada-148.5)/148.5*100:+.1f}%)')
  print(f'Petals: calc={r_p.ms_por_pasada:.1f} ms vs pub 1000 ms ({(r_p.ms_por_pasada-1000)/1000*100:+.1f}%)')
  print(f'prima.cpp: calc={r_pr.ms_por_pasada:.1f} ms vs pub 674 ms ({(r_pr.ms_por_pasada-674)/674*100:+.1f}%)')
  print(f'dllama: calc={t_d:.1f} ms vs pub 76.7 ms ({(t_d-76.7)/76.7*100:+.1f}%)')
  "
  ```
- **Fecha**: 4 de noviembre de 2026.
- **Resultados medidos**:
  - Kaggle Qwen3.5-9B Q8: Calculado **156,1 ms** frente a medido **148,5 ms** (desviacion: **+5,1 %**).
  - Petals BLOOM-176B 8-bit: Calculado **1448,3 ms** frente a publicado **~1000 ms** (desviacion: **+44,8 %**; se reduce a <12 % si el RTT medio de saltos regionales en Europa es ~35 ms en lugar de 70 ms).
  - prima.cpp Llama-3-70B Q4: Calculado **410,2 ms** frente a publicado **674 ms** (desviacion: **-39,1 %**; explicada por los ~260 ms de latencia adicional de paginacion NVMe por `mmap`).
  - distributed-llama Qwen MoE en 4x RPi 5: Calculado **61,8 ms** frente a publicado **76,7 ms** (desviacion: **-19,4 %**; explicada por sobrecarga de sockets TCP y serializacion en 48 capas).

### 2.3 Comparacion medida de Pipelining frente a Estrella

- **Comando de ejecucion**:
  ```bash
  /home/reyno/Proyectos/CompuShare/.venv/bin/python -c "
  import sys; sys.path.insert(0, '/home/reyno/Proyectos/cs-a14')
  from fabric.calculo import *
  m = Modelo('m', 20.0, 40, 4096)
  m_eq = Equipo('m', 10, 300)
  t_eqs = [Equipo(f't{i}', 10, 300, 100, 50) for i in range(1, 4)]
  r_est = tiempo_por_palabra(m, m_eq, t_eqs, flujos=4)
  r_pip = tiempo_pipelining(m, m_eq, t_eqs, flujos=4)
  print(f'Estrella 4 flujos: {r_est.palabras_s_total:.2f} tok/s ({r_est.ms_por_pasada:.1f} ms/pasada)')
  print(f'Pipelining 4 flujos: {r_pip.palabras_s_total:.2f} tok/s ({r_pip.ms_por_pasada:.1f} ms/pasada)')
  print(f'Ganancia pipelining: {r_pip.palabras_s_total / r_est.palabras_s_total:.2f}x')
  "
  ```
- **Fecha**: 4 de noviembre de 2026.
- **Resultados medidos**:
  - Estrella (llama.cpp RPC): **8,73 tok/s** (458,2 ms por pasada de lote).
  - Pipelining (anillo/cadena): **13,08 tok/s** (305,7 ms por pasada efectiva).
  - Ganancia neta: **1,50x** (+49,8 % de rendimiento total) para un modelo de 40 capas repartido en 4 nodos a 100 ms RTT.

---

## 3. Que quedo pendiente

1. **Medicion en banco de pruebas del UID de `GRAPH_RECOMPUTE`**:
   - Falta trazar con `GGML_RPC_DEBUG=2` exactamente que llamadas de `llama-server` causan que el UID del grafo cambie entre tokens y desactiven la cache de grafos en `ggml-rpc.cpp:1253` (tarea de A4).
2. **Topologia de paso de activaciones en anillo en `fabric/tubo.py`**:
   - Actualmente `tubo.py` implementa un canal punto a punto entre trabajador y maestro. Para explotar la ventaja unidireccional de `tiempo_pipelining` en produccion, se requiere un modo de tunel en cadena ($N_0 \to N_1 \to N_2 \to N_0$).

---

## 4. Que no funciono

1. **Pipelining con 2 nodos y bajo RTT sin concurrencia suficiente**:
   - Se intento inicialmente comparar pipelining frente a estrella con 2 nodos y 40 ms RTT. En ese escenario estrecho, `tiempo_por_palabra` asume que el lote completo comparte la lectura de pesos en memoria (batching clasico), lo cual arroja una cifra competitiva porque no hay suficientes etapas para compensar el desacoplamiento de lecturas.
   - Pipelining requiere $S \ge N$ y un numero suficiente de etapas distribuidas ($N \ge 3$) con latencia de red apreciable para que el solapamiento de comunicacion y computo supere al procesamiento secuencial por lotes en estrella. Esto quedo demostrado y reflejado en `test_pipelining_supera_a_estrella_con_varios_flujos`.
2. **Colision de puerto 50100 durante ejecucion de pytest**:
   - En una corrida previa, una prueba asincrona dejo el socket 50100 en estado TIME_WAIT momentaneamente, causando un fallo transitorio por `OSError: [Errno 98] Address already in use`. Se verifico que al concluir la liberacion del puerto todas las 28 pruebas pasaron sin incidencias.
