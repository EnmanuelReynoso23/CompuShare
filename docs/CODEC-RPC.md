# Códec `rpc1` del tubo (especificación)

Objetivo: bajar los bytes que cruzan el tubo maestro -> trabajador **sin tocar llama.cpp**, leyendo el
protocolo de `ggml-rpc` dentro del relé (lado maestro) y reconstruyendo el flujo exacto en el tubo
(lado trabajador) antes de entregarlo a `ggml-rpc-server` en `127.0.0.1`.

Referencia: `ggml/src/ggml-rpc/ggml-rpc.cpp` de llama.cpp b11434 (protocolo 7.0.0); los números de
comando 0-17 son los mismos en b12024.

## Por qué (diagnóstico, 23-nov-2026)

- Cada pedido de `ggml-rpc` es `| cmd (1 byte) | tamaño (u64 LE) | datos |`. Las respuestas son
  `| tamaño (u64 LE) | datos |` y solo existen para algunos comandos.
- `GRAPH_COMPUTE` (10) manda el grafo entero serializado:
  `| device u32 | n_nodes u32 | nodes u64 x n_nodes | n_tensors u32 | rpc_tensor x n_tensors |`, con
  `sizeof(rpc_tensor) = 296` bytes. Un trozo de un modelo de 9B en un trabajador son cientos de nodos:
  cientos de KB por grafo.
- llama.cpp solo evita reenviarlo con `GRAPH_RECOMPUTE` (16) si el grafo es **el mismo que el último**
  (`last_graph_uid`, un solo grafo recordado por dispositivo). Con decodificación especulativa se alternan
  grafos de distinto tamaño de lote (borrador, verificación de γ+1 tokens, MTP), así que casi cada paso
  reenvía el grafo completo. Hipótesis principal para los ~633 KB por token medidos en Kaggle: la mayor
  parte es grafo, no activaciones (la activación de Qwen3.5-9B son 4.096 x 4 B = 16 KB por token).
- Los grafos de dos pasos con distinto lote tienen la misma forma y solo cambian `ne`, `nb`, `data` y
  algunos `op_params`: un XOR contra el anterior es casi todo ceros y se comprime a muy poco.

La hipótesis se confirma o descarta con `fabric/perfiles/desglose_rpc.py` (bytes por comando y dirección).

## Negociación (compatible con nodos viejos)

1. El trabajador anuncia lo que entiende en su mensaje de control:
   `{"t": "ofrezco", ..., "codecs": ["rpc1"]}`. Un trabajador viejo no manda `codecs`.
2. Al abrir cada conexión, el relé decide. Si el trabajador anunció `rpc1` y el relé lo tiene activo
   (por defecto sí; se apaga con `FABRIC_CODEC=0` o `compushare servir --sin-codec`), manda
   `{"t": "abre", "id": ..., "codec": "rpc1"}`. Si no, manda `abre` sin `codec` y todo viaja crudo, como hoy.
3. El trabajador usa en esa conexión exactamente el códec que diga `abre` (ausente = crudo).

## Formato en el sentido maestro -> trabajador

Cada mensaje binario del WebSocket es una concatenación de **registros**. Un registro nunca se parte entre
dos mensajes. Enteros en little endian.

| Tipo | Nombre | Cuerpo | Qué hace el decodificador |
| --- | --- | --- | --- |
| `0x00` | RAW | `len u32` + `len` bytes | los escribe tal cual al TCP |
| `0x01` | GRAPH_FULL | `len u32` (crudo) + `zlen u32` + `zlib(payload)` | descomprime; escribe `0x0A` + `u64 len` + payload; lo guarda en la caché |
| `0x02` | GRAPH_REF | `hash` (16 bytes) | busca el payload en la caché; lo escribe como arriba; lo marca como más reciente |
| `0x03` | GRAPH_DELTA | `hash_base` (16) + `len u32` + `zlen u32` + `zlib(payload XOR base)` | `base` = caché[hash_base] (mismo `len`); payload = XOR; lo escribe; lo guarda |
| `0x04` | SET_TENSOR_Q8 | `hlen u32` + `hlen` bytes de cabecera + `n u64` (floats) + bloques Q8_0 | reconstruye los `n` float32; escribe `0x06` + `u64 (hlen + 4n)` + cabecera + floats |

Detalles:

- `hash` = primeros 16 bytes de SHA-256 del payload del grafo (sin `cmd` ni tamaño).
- `zlib` = formato zlib (RFC 1950) estándar; nivel libre (el decodificador no depende de él).
- **Caché de grafos**, idéntica a los dos lados: diccionario ordenado `hash -> payload`. Cada grafo
  escrito al TCP (por FULL, REF o DELTA) se inserta o se mueve al final (más reciente). Si hay más de
  **32** entradas o suman más de **64 MiB**, se sacan las más viejas hasta cumplir. Como los dos lados ven
  la misma secuencia de grafos, las cachés coinciden sin mensajes extra.
- Usar una entrada como base de un DELTA **no** la reordena; solo REF, FULL y DELTA (el grafo nuevo) la
  marcan como más reciente. Un grafo que sale en RAW no entra en la caché.
- Un mensaje WS del códec puede pesar hasta 80 MiB (un grafo completo); el tubo acepta ese tamaño.
- Si el decodificador recibe un REF o DELTA con un hash que no tiene, o un tipo desconocido, es un error:
  cierra la conexión (llama.cpp reintenta / el maestro relanza).
- **Q8_0** (opcional, con pérdida, apagado por defecto: `compushare servir --activaciones q8`):
  - Solo para `SET_TENSOR` (6) con: ya pasó al menos un `GRAPH_COMPUTE` en la conexión (así los pesos de
    la carga nunca se tocan), `rpc_tensor.type == 0` (F32, `u32` en el byte 8 del payload), datos
    `= tamaño - 305` múltiplo de 4, al menos 1.024 floats, y **todos los valores finitos** (la máscara de
    atención lleva `-inf`: va cruda).
  - Cabecera = los primeros 305 bytes del payload (`rpc_tensor` 296 + `cache_flag` u8 + `offset` u64, como
    en `serialize_set_tensor` de b11434). `hlen = 305`. El decodificador no interpreta la cabecera: usa `hlen`.
  - Bloques de 32 floats: `d = amax/127` (en float32), escala guardada como float16 (IEEE 754 half, LE),
    `q[i] = round_half_away_from_zero(x[i] / d)` acotado a [-127, 127] (si `d == 0`, todo 0). El último bloque
    se rellena con ceros. Cada bloque son 2 + 32 = 34 bytes.
  - Reconstrucción: `x[i] = float32(float32(d_f16) * q[i])`, que da el mismo resultado en Python y en Go.
    Los codificadores no tienen que ser idénticos bit a bit; el decodificador sí.
  - 4 bytes por valor pasan a 1,0625: un 73 % menos. Cambia la salida del modelo un poco: hay que medirlo
    con `compushare transcribir` antes de usarlo en serio.
- Todo lo demás (otros comandos, `SET_TENSOR` grandes de la carga de pesos, comandos desconocidos) va en
  registros RAW **sin almacenar el payload entero**: se puede ir mandando a medida que llega. Si aparece un
  `cmd >= 64` el codificador deja de interpretar y pasa a RAW el resto de la conexión.
- El codificador solo usa GRAPH_FULL/DELTA/Q8 si el resultado es más chico que mandarlo crudo; si no, RAW.
- Grafos de más de 64 MiB: RAW.

## Sentido trabajador -> maestro

En `rpc1` va **crudo**, igual que hoy (las respuestas no se enmarcan). Comprimir las respuestas de
`GET_TENSOR` (la activación que vuelve de un trabajador para ir al siguiente) queda para `rpc2`.

## Implementaciones

- Python: `fabric/rpc_codec.py`, usado por `fabric/rele/rele.py` (codifica) y `fabric/tubo.py` (decodifica).
- Go: `go/pkg/rpccodec`, usado por `go/pkg/rele` y `go/pkg/tubo`.
- Pruebas: ida y vuelta byte por byte con flujos sintéticos (grafos con registros de 296 bytes), cortes
  arbitrarios del flujo TCP, y vectores compartidos Python <-> Go en `fabric/pruebas/vectores_codec/`.
