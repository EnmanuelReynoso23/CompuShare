# Una IA en dos computadoras: Qwen3.5-9B Q8_0 entre dos cuadernos Kaggle (31-oct-2026, 9:20-9:40 p. m. RD)

`DURACION_MIN=30 python fabric/kaggle/lanzar.py --dividido`

| Pieza | Dónde |
| --- | --- |
| Maestro | Cuaderno `synrag-red-nodo-maestro` (2 x T4): relé + túnel de Cloudflare + `llama-server --rpc 127.0.0.1:50100` |
| Trabajador | Cuaderno `synrag-red-nodo-trabajador` (2 x T4): `ggml-rpc-server -d CUDA0,CUDA1 -H 127.0.0.1 -c` + `fabric.tubo` |
| Enlace | Tubo TCP dentro del WebSocket del relé, por `wss://*.trycloudflare.com`. Nada RPC expuesto. |

## Cifras (medidas, tal cual)

| Qué | Valor |
| --- | --- |
| Bytes por el túnel al cargar el modelo | 42,4 MB (el modelo pesa 9,53 GB: los pesos grandes salieron de la caché armada desde el GGUF del trabajador) |
| Bytes por token | 315 KB |
| Generación (diagnóstico en el maestro) | 6,7 tok/s |
| Generación (pregunta por la red, 2 veces) | 6,9 y 6,8 tok/s |
| Lectura del prompt | 0,2 tok/s en frío; pregunta corta: 50,5 s en frío, 28,7 s en caliente (unos 15 s son generación) |
| Respuesta | Correcta (lista mutable vs tupla inmutable, en español) |

## Por qué la lectura del prompt era lenta (medido después, 10:00-10:30 p. m.)

`llama-server.log` del maestro: generación estable a 147-150 ms por token, pero el prompt costaba fijo
por pregunta (12 tokens 56,7 s, 34 tokens 30,8 s, 4 tokens 7,8 s) y había 4 s muertos antes de empezar.

En local, con un intermediario que decodifica el protocolo RPC (`prueba-local/proxy.py`):

| Por pregunta | Con puntos de control (por defecto) | `-ctxcp 0 --cache-ram 0` |
| --- | --- | --- |
| `GET_TENSOR` (cada uno espera una ida y vuelta) | 37 a 85 | 1 |
| Lectura del prompt con 60 ms de latencia simulada | 3,3 tok/s | 15,6 tok/s |

Causa: en modelos híbridos (Qwen3.5, con capas recurrentes) llama-server guarda "puntos de control"
del estado recurrente copiándolo capa por capa desde el trabajador. Arreglado en el maestro
(commit 86ac18b). Quedan unas 8 consultas `GET_ALLOC_SIZE` por cada largo de prompt nuevo.

## Mejoras preparadas

- Decodificación especulativa con la cabeza MTP del modelo (`--spec-type draft-mtp`, GGUF de
  `unsloth/Qwen3.5-9B-MTP-GGUF`). En local: 79 % de aciertos, 3,25 tokens por pasada.
- Reparto entre N trabajadores (`fabric/reparto.py`): mide la latencia de cada uno con el saludo
  HELLO del protocolo RPC y usa los menos posibles, de menor latencia.

## Segunda corrida con los arreglos (10:40-10:50 p. m.)

Con MTP (`--spec-type draft-mtp`) y sin puntos de control (`-ctxcp 0 --cache-ram 0`), mismo modelo y
mismos 2 cuadernos:

| Qué | Primera corrida | Segunda corrida |
| --- | --- | --- |
| Misma pregunta por la red, en caliente | 28,7 s | 10,7 s |
| Misma pregunta, primera vez | 50,5 s | 11,4 s |
| Generación | 6,8-6,9 tok/s | 10,3-10,8 tok/s |
| Ida y vuelta por el tubo (HELLO RPC, nueva medición) | - | 237 ms |
| Por el túnel al cargar | 42,4 MB | 42,3 MB |

El diagnóstico interno del maestro (primera petición, en frío) dio 3,6 tok/s y 0,2 tok/s de prompt:
la primera petición después de cargar sigue lenta (calentamiento); las siguientes, no.

## Pendiente

- El log de llama-server (verbosidad 3) no trae el reparto por equipo: falta otra forma de leerlo.
- Comparar con el mismo modelo en un solo cuaderno (costo del reparto).
- La primera petición tras cargar sigue lenta; el diagnóstico no registró los aciertos de MTP.
- 237 ms de ida y vuelta por Cloudflare: probar conexión directa entre cuadernos (más rápida).
- El relé en Python cuesta: en local el tubo baja de 7,1 a 4,1 tok/s frente al RPC directo.
