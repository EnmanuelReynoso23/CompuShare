# Informe del Agente A2: Simulador de Escenarios Distribuidos y Calibracion

- Fecha: 2026-11-04
- Agente: A2
- Rama: a2-escenario
- Worktree: /home/reyno/Proyectos/cs-a2
- Archivo de resultados: fabric/simulador/resultados/20261104/resultado.json
- Informe detallado de ejecucion: fabric/simulador/resultados/20261104/INFORME.md

---

## 1. Resumen del Trabajo Realizado

Se implemento y ejecuto el simulador de escenarios distribuidos de CompuShare (`fabric/simulador/escenario.py`), que orquesta:
1. N instancias locales de `ggml-rpc-server` (N = 2, 4, 6), asignando puertos exclusivos dentro del rango autorizado (51000-51999) y limitando hilos por proceso (maximo 2 hilos para N=2, 1 hilo para N>=4) para respetar el limite de 15 GB de RAM y 6 hilos por proceso de REGLAS.md.
2. N proxies TCP basados en `Enlace` de `fabric/simulador/red.py`, intercalados entre el maestro y cada servidor RPC para imponer los perfiles de red: "sin retraso" (directo local), "misma ciudad" (10 ms latencia, 2 ms jitter, 100 Mb/s) y "mismo pais" (40 ms latencia, 10 ms jitter, 50 Mb/s).
3. Un proceso maestro `llama-server` que reparte las capas del modelo `Qwen3.5-0.8B-Q8_0.gguf` entre los N trabajadores mediante `--rpc`, con `-ngl 999 --split-mode layer -ctxcp 0 --cache-ram 0 -c 2048`.
4. Medicion por escenario (N x perfil) de:
   - Tiempo de generacion por palabra en ms (mediana de 3 repeticiones tras calentamiento) y tasa tok/s.
   - Tiempo de lectura del prompt (ms/token) y tasa de prefill.
   - Trafico por enlace en bytes por palabra contando en el proxy (desglosado en ida maestro->rpc y vuelta rpc->maestro).
   - Comparacion analitica contra `fabric/calculo.py` (`tiempo_por_palabra`), utilizando el ancho de banda de memoria medido previamente con `llama-bench`.
5. Se agregaron pruebas automatizadas en `fabric/pruebas/test_escenario.py` que verifican la asignacion de puertos en rango, la configuracion de escenarios y ejecucion corta de integracion.

---

## 2. Entorno y Cargas del Sistema Durante las Mediciones

Todas las mediciones se ejecutaron bajo exclusion mutua con `flock /home/reyno/Proyectos/cs-agentes/medir.lock`.

- Comando de ejecucion:
  `cd /home/reyno/Proyectos/cs-a2 && flock /home/reyno/Proyectos/cs-agentes/medir.lock /home/reyno/Proyectos/CompuShare/.venv/bin/python fabric/simulador/escenario.py --todos --n-predict 16 --repeticiones 3`
- Fecha y hora de corrida: 2026-11-04 11:25:55 a 11:40:35 AST.
- Plataforma: Linux x86_64 (CachyOS), CPU Intel Core i5-13420H (12 vCPUs), 15 GiB RAM (5.7 GiB usados al inicio, 9.6 GiB disponibles).
- Carga base del sistema (uptime): load average 1.32, 1.62, 1.69.
- Procesos concurrentes en ejecucion: entorno grafico Hyprland, navegador Google Chrome (tabs de trabajo), instancias CLI de claude y agy, y daemon de intercambio kswapd0. No hubo otros agentes midiendo simultaneamente gracias a `medir.lock`.
- Ancho de banda de memoria base medido con `llama-bench`:
  `llama-bench -m Qwen3.5-0.8B-Q8_0.gguf -p 0 -n 32 -t 4 -r 2 -o json` -> **27.36 GB/s**.

---

## 3. Cifras Medidas por Escenario

Valores consolidados (mediana de 3 corridas con n_predict=16 tras corrida de calentamiento):

| N | Perfil | RTT Enlace | ms/palabra (medido) | tok/s Generacion | ms/token Prompt | tok/s Prompt | Bytes/palabra (total) |
|---|---|---|---|---|---|---|---|
| 2 | sin retraso | 0 ms | 36.0 ms | 27.79 tok/s | 12.1 ms | 82.7 tok/s | 1098.5 KB |
| 2 | misma ciudad | 20 ms | 176.7 ms | 5.66 tok/s | 34.2 ms | 29.2 tok/s | 1098.5 KB |
| 2 | mismo pais | 80 ms | 433.6 ms | 2.31 tok/s | 74.7 ms | 13.4 tok/s | 1098.5 KB |
| 4 | sin retraso | 0 ms | 63.3 ms | 15.80 tok/s | 16.9 ms | 59.3 tok/s | 1128.6 KB |
| 4 | misma ciudad | 20 ms | 249.8 ms | 4.00 tok/s | 48.7 ms | 20.5 tok/s | 1128.6 KB |
| 4 | mismo pais | 80 ms | 668.2 ms | 1.50 tok/s | 115.3 ms | 8.7 tok/s | 1128.6 KB |
| 6 | sin retraso | 0 ms | 62.3 ms | 16.05 tok/s | 17.4 ms | 57.5 tok/s | 1158.6 KB |
| 6 | misma ciudad | 20 ms | 306.7 ms | 3.26 tok/s | 58.6 ms | 17.1 tok/s | 1158.6 KB |
| 6 | mismo pais | 80 ms | 882.6 ms | 1.13 tok/s | 143.6 ms | 7.0 tok/s | 1158.6 KB |

---

## 4. Desglose de Trafico por Enlace (Bytes por Palabra)

El conteo en los proxies revela una fuerte asimetria estructural del protocolo RPC de llama.cpp:

1. **Nodos intermedios (Nodo 1 en N=2; Nodos 1 a 3 en N=4; Nodos 1 a 5 en N=6):**
   - Sentido ida (maestro -> rpc): 20.4 KB a 58.9 KB por palabra (contiene el grafo RPC, mascara y activacion de entrada).
   - Sentido vuelta (rpc -> maestro): exactamente **5.9 KB** por palabra (el tensor de activacion oculta f32 para `d_modelo=1024` mas sobrecarga de encabezado RPC).
   - Total por enlace intermedio: ~26 KB a 65 KB por palabra.

2. **Ultimo nodo (Nodo 2 en N=2; Nodo 4 en N=4; Nodo 6 en N=6):**
   - Sentido ida: 17.9 KB a 40.4 KB por palabra.
   - Sentido vuelta: exactamente **993.3 KB** por palabra.
   - Motivo: Aloja la cabeza del modelo (`lm_head`). En cada paso autoregresivo de decodificacion, devuelve el tensor completo de logits para todo el vocabulario (248.322 tokens x 4 bytes f32 = 993.288 bytes).
   - Total ultimo enlace: ~1.01 MB a 1.03 MB por palabra.

---

## 5. Calibracion con fabric/calculo.py y Analisis de Desviaciones

Comparacion contra `tiempo_por_palabra` con modelo `Qwen3.5-0.8B` (pesos 0.80088 GB, 24 capas, d_modelo 1024) y ancho de banda medido (27.36 GB/s, eficiencia 1.0):

| N | Perfil | ms Medido | ms Calculado | ms Calc (CPU) | ms Red (Teorico) | Desviacion % | Estado |
|---|---|---|---|---|---|---|---|
| 2 | sin retraso | 36.0 ms | 29.3 ms | 29.3 ms | 0.0 ms | +22.9 % | DESVIADO (> 20 %) |
| 2 | misma ciudad | 176.7 ms | 157.2 ms | 29.3 ms | 127.9 ms | +12.4 % | OK (<= 20 %) |
| 2 | mismo pais | 433.6 ms | 365.0 ms | 29.3 ms | 335.8 ms | +18.8 % | OK (<= 20 %) |
| 4 | sin retraso | 63.3 ms | 29.3 ms | 29.3 ms | 0.0 ms | +116.1 % | DESVIADO (> 20 %) |
| 4 | misma ciudad | 249.8 ms | 199.6 ms | 29.3 ms | 170.3 ms | +25.2 % | DESVIADO (> 20 %) |
| 4 | mismo pais | 668.2 ms | 529.8 ms | 29.3 ms | 500.6 ms | +26.1 % | DESVIADO (> 20 %) |
| 6 | sin retraso | 62.3 ms | 29.3 ms | 29.3 ms | 0.0 ms | +112.7 % | DESVIADO (> 20 %) |
| 6 | misma ciudad | 306.7 ms | 242.0 ms | 29.3 ms | 212.7 ms | +26.7 % | DESVIADO (> 20 %) |
| 6 | mismo pais | 882.6 ms | 694.6 ms | 29.3 ms | 665.4 ms | +27.1 % | DESVIADO (> 20 %) |

### Explicacion de las desviaciones:

1. **Escenarios 'sin retraso' (+22.9 % en N=2, +116.1 % en N=4, +112.7 % en N=6):**
   - La formula teorica asume que el calculo puro escala linealmente con el ancho de banda de memoria (`t_calc = pesos / banda`), arrojando 29.3 ms constantes.
   - En la ejecucion local sobre un solo CPU, dividir el modelo en N procesos RPC concurrentes no reduce el tiempo de calculo: introduce contencion de cache L3 y multiplexado de hilos en la misma memoria RAM.
   - Ademas, cada paso requiere serializar grafos de ejecucion, despachar sockets TCP locales y transmitir 1.1 MB por palabra en memoria, lo que agrega un costo fijo de ~33 ms adicionales cuando N >= 4.

2. **Escenarios con red ('misma ciudad' y 'mismo pais'):**
   - Para N=2, la concordancia con la formula es notable: +12.4 % en misma ciudad y +18.8 % en mismo pais, ambos dentro del umbral de calibracion del 20 %.
   - Para N=4 y N=6, la desviacion observada se estabiliza de manera uniforme entre +25.2 % y +27.1 %.
   - Origen del exceso de ~26 %:
     a) llama.cpp RPC ejecuta de forma secuencial en estrella: el maestro despacha a cada nodo en orden de capas. Al encadenar 4 o 6 saltos secuenciales con variacion Gaussiana (jitter de 2 ms y 10 ms), la distribucion del RTT total acumulado se desplaza hacia la cola superior de la varianza.
     b) La sobrecarga de despacho y serializacion de multiples llamadas RPC por token (comprobaciones de alloc y grafos) suma entre 10 ms y 15 ms por nodo, no contemplados en el modelo de transmision pura de `calculo.py`.

---

## 6. Que No Funciono y Como se Resolvio

1. **Timeout durante la carga inicial del modelo en perfiles con limite de ancho de banda:**
   - En el primer intento con N=2 "mismo pais", `llama-server` tardo mas de 90 segundos en arrancar y disparo `RuntimeError`.
   - Causa: Al iniciar sin cache de tensores local en el servidor RPC, `llama-server` transfiere las capas del modelo a traves del proxy TCP. Con 775 MB de modelo divididos entre los nodos a 50 Mb/s (6.25 MB/s reales), la transferencia toma ~124 segundos.
   - Solucion: Se incremento el `timeout_listo` por defecto de `Escenario.iniciar()` a 240 segundos. Con este ajuste, todos los 9 escenarios cargaron y midieron sin fallas.

2. **Supuesto de ancho de banda ilimitado en 'sin retraso':**
   - En la implementacion preliminar se utilizo un fallback de 1000 Mb/s para perfiles con `mbps=None`. Esto introducia artificialmente 9.4 ms de retardo de red teorico para transferir el tensor de logits de 1.1 MB sobre loopback.
   - Se corrigio para usar `subida_mbps = 1e6` (el estandar de `calculo.py` para enlaces no limitados), reflejando con exactitud que en `red.py` la ruta `_canal_directo` no impone retardo.

---

## 7. Pendientes

- Explorar el uso de `--override-tensor` o procesamiento local de `lm_head` en el maestro: evitar que el ultimo nodo transmita 993 KB de logits por token reduciria el tiempo de transmision en enlaces WAN de 80 ms hasta en un 80 %.
- Evaluar `ggml-rpc-server -c` con cache pregenerada mediante `fabric.rpc_cache` para acelerar el arranque en conexiones lentas.
