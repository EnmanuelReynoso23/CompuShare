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

## Pendiente

- Lectura del prompt lenta: leer `llama-server.log` del maestro (`lanzar.py --bajar`) para ver el reparto por
  equipo y dónde se va el tiempo. La regex de `reparto` no encontró la línea de buffers en el log.
- Comparar con el mismo modelo en un solo cuaderno (costo del reparto).
- El relé en Python cuesta: en local el tubo baja de 7,1 a 4,1 tok/s frente al RPC directo.
