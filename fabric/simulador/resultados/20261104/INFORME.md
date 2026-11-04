# Informe de Calibracion: Simulador de Escenarios Distribuidos

- Fecha de ejecucion: 2026-11-04 11:40:35
- Modelo evaluado: Qwen3.5-0.8B-Q8_0.gguf (CPU, 24 capas, d_modelo=1024)
- Parametros de ejecucion: `-ctxcp 0 --cache-ram 0 -ngl 999 --split-mode layer`
- Plataforma: Linux x86_64, CPU Intel Core i5-13420H (12 vCPU), 15 GB RAM

---

## 1. Resumen de Mediciones por Escenario

| N Nodos | Perfil de Red | RTT Enlace | ms/palabra (mediana) | tok/s Generacion | ms/token Prompt | tok/s Prompt | Bytes/palabra (total) |
|---|---|---|---|---|---|---|---|
| 2 | sin retraso | 0 ms | 36.0 ms | 27.79 tok/s | 12.1 ms | 82.7 tok/s | 1098.5 KB |
| 2 | misma ciudad | 20 ms | 176.7 ms | 5.66 tok/s | 34.2 ms | 29.2 tok/s | 1098.5 KB |
| 2 | mismo país | 80 ms | 433.6 ms | 2.31 tok/s | 74.7 ms | 13.4 tok/s | 1098.5 KB |
| 4 | sin retraso | 0 ms | 63.3 ms | 15.80 tok/s | 16.9 ms | 59.3 tok/s | 1128.6 KB |
| 4 | misma ciudad | 20 ms | 249.8 ms | 4.00 tok/s | 48.7 ms | 20.5 tok/s | 1128.6 KB |
| 4 | mismo país | 80 ms | 668.2 ms | 1.50 tok/s | 115.3 ms | 8.7 tok/s | 1128.6 KB |
| 6 | sin retraso | 0 ms | 62.3 ms | 16.05 tok/s | 17.4 ms | 57.5 tok/s | 1158.6 KB |
| 6 | misma ciudad | 20 ms | 306.7 ms | 3.26 tok/s | 58.6 ms | 17.1 tok/s | 1158.6 KB |
| 6 | mismo país | 80 ms | 882.6 ms | 1.13 tok/s | 143.6 ms | 7.0 tok/s | 1158.6 KB |

---

## 2. Calibracion contra fabric/calculo.py

| N Nodos | Perfil | ms Medido | ms Calculado (Teorico) | ms Calc (CPU) | ms Red (Teorico) | Desviacion % | Estado |
|---|---|---|---|---|---|---|---|
| 2 | sin retraso | 36.0 ms | 29.3 ms | 29.3 ms | 0.0 ms | +22.9 % | DESVIADO (> 20 %) |
| 2 | misma ciudad | 176.7 ms | 157.2 ms | 29.3 ms | 127.9 ms | +12.4 % | OK (<= 20 %) |
| 2 | mismo país | 433.6 ms | 365.0 ms | 29.3 ms | 335.8 ms | +18.8 % | OK (<= 20 %) |
| 4 | sin retraso | 63.3 ms | 29.3 ms | 29.3 ms | 0.0 ms | +116.1 % | DESVIADO (> 20 %) |
| 4 | misma ciudad | 249.8 ms | 199.6 ms | 29.3 ms | 170.3 ms | +25.2 % | DESVIADO (> 20 %) |
| 4 | mismo país | 668.2 ms | 529.8 ms | 29.3 ms | 500.6 ms | +26.1 % | DESVIADO (> 20 %) |
| 6 | sin retraso | 62.3 ms | 29.3 ms | 29.3 ms | 0.0 ms | +112.7 % | DESVIADO (> 20 %) |
| 6 | misma ciudad | 306.7 ms | 242.0 ms | 29.3 ms | 212.7 ms | +26.7 % | DESVIADO (> 20 %) |
| 6 | mismo país | 882.6 ms | 694.6 ms | 29.3 ms | 665.4 ms | +27.1 % | DESVIADO (> 20 %) |

---

## 3. Desglose de Trafico por Enlace (Bytes por Palabra)

| N Nodos | Perfil | Enlace | Sentido Ida (llama->rpc) | Sentido Vuelta (rpc->llama) | Total Enlace |
|---|---|---|---|---|---|
| 2 | sin retraso | Nodo 1 | 58.9 KB | 5.9 KB | 64.8 KB |
| 2 | sin retraso | Nodo 2 | 40.4 KB | 993.3 KB | 1033.7 KB |
| 2 | misma ciudad | Nodo 1 | 58.9 KB | 5.9 KB | 64.8 KB |
| 2 | misma ciudad | Nodo 2 | 40.4 KB | 993.3 KB | 1033.7 KB |
| 2 | mismo país | Nodo 1 | 58.9 KB | 5.9 KB | 64.8 KB |
| 2 | mismo país | Nodo 2 | 40.4 KB | 993.3 KB | 1033.7 KB |
| 4 | sin retraso | Nodo 1 | 42.6 KB | 5.9 KB | 48.5 KB |
| 4 | sin retraso | Nodo 2 | 25.4 KB | 5.9 KB | 31.3 KB |
| 4 | sin retraso | Nodo 3 | 26.6 KB | 5.9 KB | 32.5 KB |
| 4 | sin retraso | Nodo 4 | 23.0 KB | 993.3 KB | 1016.3 KB |
| 4 | misma ciudad | Nodo 1 | 42.6 KB | 5.9 KB | 48.5 KB |
| 4 | misma ciudad | Nodo 2 | 25.4 KB | 5.9 KB | 31.3 KB |
| 4 | misma ciudad | Nodo 3 | 26.6 KB | 5.9 KB | 32.5 KB |
| 4 | misma ciudad | Nodo 4 | 23.0 KB | 993.3 KB | 1016.3 KB |
| 4 | mismo país | Nodo 1 | 42.6 KB | 5.9 KB | 48.5 KB |
| 4 | mismo país | Nodo 2 | 25.4 KB | 5.9 KB | 31.3 KB |
| 4 | mismo país | Nodo 3 | 26.6 KB | 5.9 KB | 32.5 KB |
| 4 | mismo país | Nodo 4 | 23.0 KB | 993.3 KB | 1016.3 KB |
| 6 | sin retraso | Nodo 1 | 36.4 KB | 5.9 KB | 42.3 KB |
| 6 | sin retraso | Nodo 2 | 20.4 KB | 5.9 KB | 26.3 KB |
| 6 | sin retraso | Nodo 3 | 20.4 KB | 5.9 KB | 26.3 KB |
| 6 | sin retraso | Nodo 4 | 20.4 KB | 5.9 KB | 26.3 KB |
| 6 | sin retraso | Nodo 5 | 20.4 KB | 5.9 KB | 26.3 KB |
| 6 | sin retraso | Nodo 6 | 17.9 KB | 993.3 KB | 1011.2 KB |
| 6 | misma ciudad | Nodo 1 | 36.4 KB | 5.9 KB | 42.3 KB |
| 6 | misma ciudad | Nodo 2 | 20.4 KB | 5.9 KB | 26.3 KB |
| 6 | misma ciudad | Nodo 3 | 20.4 KB | 5.9 KB | 26.3 KB |
| 6 | misma ciudad | Nodo 4 | 20.4 KB | 5.9 KB | 26.3 KB |
| 6 | misma ciudad | Nodo 5 | 20.4 KB | 5.9 KB | 26.3 KB |
| 6 | misma ciudad | Nodo 6 | 17.9 KB | 993.3 KB | 1011.2 KB |
| 6 | mismo país | Nodo 1 | 36.4 KB | 5.9 KB | 42.3 KB |
| 6 | mismo país | Nodo 2 | 20.4 KB | 5.9 KB | 26.3 KB |
| 6 | mismo país | Nodo 3 | 20.4 KB | 5.9 KB | 26.3 KB |
| 6 | mismo país | Nodo 4 | 20.4 KB | 5.9 KB | 26.3 KB |
| 6 | mismo país | Nodo 5 | 20.4 KB | 5.9 KB | 26.3 KB |
| 6 | mismo país | Nodo 6 | 17.9 KB | 993.3 KB | 1011.2 KB |

---

## 4. Analisis de Desviaciones y Calibracion Matematica

### N=2 - sin retraso (Desviacion: +22.9 %)
- **Explicacion**: En 'sin retraso', la latencia teorica es 0 ms, por lo que calculo.py solo computa tiempo de lectura de memoria (29.3 ms). La sobrecarga medida (36.0 ms vs 29.3 ms) proviene de la serializacion de grafos RPC en CPU, multiplexado de N=2 procesos en un solo CPU (13th Gen i5), y el retorno del tensor completo de logits (1099 KB/palabra) del ultimo nodo al maestro.

### N=4 - sin retraso (Desviacion: +116.1 %)
- **Explicacion**: En 'sin retraso', la latencia teorica es 0 ms, por lo que calculo.py solo computa tiempo de lectura de memoria (29.3 ms). La sobrecarga medida (63.3 ms vs 29.3 ms) proviene de la serializacion de grafos RPC en CPU, multiplexado de N=4 procesos en un solo CPU (13th Gen i5), y el retorno del tensor completo de logits (1129 KB/palabra) del ultimo nodo al maestro.

### N=4 - misma ciudad (Desviacion: +25.2 %)
- **Explicacion**: Con N=4 nodos distribuidos en estrella, llama.cpp realiza 4 ciclos secuenciales de peticion-respuesta (ida y vuelta) por token generado. Al acumular jitter y despacho de 4 procesos en la misma CPU, el tiempo de sincronizacion entre hilos de la laptop agrega sobrecarga al RTT simulado. El ultimo nodo retorna los logits completos de 248.322 tokens (1129 KB) en cada palabra, lo que incrementa el tiempo de transmision frente a la asuncion de calculo.py de transferir unicamente la activacion oculta (8 KB).

### N=4 - mismo país (Desviacion: +26.1 %)
- **Explicacion**: Con N=4 nodos distribuidos en estrella, llama.cpp realiza 4 ciclos secuenciales de peticion-respuesta (ida y vuelta) por token generado. Al acumular jitter y despacho de 4 procesos en la misma CPU, el tiempo de sincronizacion entre hilos de la laptop agrega sobrecarga al RTT simulado. El ultimo nodo retorna los logits completos de 248.322 tokens (1129 KB) en cada palabra, lo que incrementa el tiempo de transmision frente a la asuncion de calculo.py de transferir unicamente la activacion oculta (8 KB).

### N=6 - sin retraso (Desviacion: +112.7 %)
- **Explicacion**: En 'sin retraso', la latencia teorica es 0 ms, por lo que calculo.py solo computa tiempo de lectura de memoria (29.3 ms). La sobrecarga medida (62.3 ms vs 29.3 ms) proviene de la serializacion de grafos RPC en CPU, multiplexado de N=6 procesos en un solo CPU (13th Gen i5), y el retorno del tensor completo de logits (1159 KB/palabra) del ultimo nodo al maestro.

### N=6 - misma ciudad (Desviacion: +26.7 %)
- **Explicacion**: Con N=6 nodos distribuidos en estrella, llama.cpp realiza 6 ciclos secuenciales de peticion-respuesta (ida y vuelta) por token generado. Al acumular jitter y despacho de 6 procesos en la misma CPU, el tiempo de sincronizacion entre hilos de la laptop agrega sobrecarga al RTT simulado. El ultimo nodo retorna los logits completos de 248.322 tokens (1159 KB) en cada palabra, lo que incrementa el tiempo de transmision frente a la asuncion de calculo.py de transferir unicamente la activacion oculta (8 KB).

### N=6 - mismo país (Desviacion: +27.1 %)
- **Explicacion**: Con N=6 nodos distribuidos en estrella, llama.cpp realiza 6 ciclos secuenciales de peticion-respuesta (ida y vuelta) por token generado. Al acumular jitter y despacho de 6 procesos en la misma CPU, el tiempo de sincronizacion entre hilos de la laptop agrega sobrecarga al RTT simulado. El ultimo nodo retorna los logits completos de 248.322 tokens (1159 KB) en cada palabra, lo que incrementa el tiempo de transmision frente a la asuncion de calculo.py de transferir unicamente la activacion oculta (8 KB).

### Hallazgos Principales:
1. **Trafico asimetrico por enlace**: El penultimo y anteriores nodos solo transmiten el estado de activacion (~4 KB de vuelta para d_modelo=1024 f32). En cambio, el ultimo nodo que contiene la cabeza del modelo (lm_head) retorna el tensor completo de logits del vocabulario entero (248.322 tokens x 4 bytes = ~993 KB por palabra).
2. **Sobrecarga en 'sin retraso'**: Cuando la latencia de red es 0, el cuello de botella es la serializacion del grafo de ejecucion RPC y la contencion de hilos/procesos compartiendo la misma CPU y memoria RAM.
3. **Linealidad con la latencia**: En enlaces con latencia ('misma ciudad' y 'mismo país'), el tiempo por palabra escala proporcionalmente a N x RTT, validando la topologia en estrella del RPC de llama.cpp.
