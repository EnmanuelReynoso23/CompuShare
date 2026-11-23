# Plan v2: menos bytes por token, red de casa y nodo completo (23-nov-2026)

Sigue a `PLAN.md` (4-nov). Lo medido hasta hoy está en el README; aquí solo lo que falta y por qué.

## Diagnóstico revisado (leyendo `ggml-rpc.cpp` de llama.cpp b11434)

| Afirmación | Qué dice el código / la medición |
| --- | --- |
| "Los 13,9 KB por token son un vector de ~3.584 floats" | No. Se midió con Qwen3.5-0.8B (ancho 1.024): la activación son 4,1 KB (`fabric/perfiles/salida/RESULTADO.md`). El resto es sobre todo el pedido `GRAPH_COMPUTE`/`GRAPH_RECOMPUTE`, entradas (tokens, posiciones, máscara) y respuestas. |
| "Los 633 KB/token con especulativa son activaciones `[γ, d]`" | Poco probable que sea lo principal: con d = 4.096 y γ ~ 4-8 son 64-128 KB por sentido. llama.cpp solo reusa el **último** grafo (`last_graph_uid`); con borrador + verificación + MTP los grafos se alternan y cada uno se reenvía entero (296 B por tensor, cientos de KB). Hipótesis principal: el grafo. Se mide con `fabric/perfiles/desglose_rpc.py`. |
| "Cuantizar a Q8_0 en el túnel baja 633 KB a 165 KB" | Q8_0 solo baja lo que son activaciones float32 (-73 % de esa parte) y cambia la salida. Lo que pesa es el grafo, y ese se puede bajar **sin pérdida** (caché de grafos + delta XOR + zlib a los dos lados del tubo). Las dos cosas van en el códec `rpc1` (`docs/CODEC-RPC.md`); Q8_0 queda opcional y apagado. |
| "ICE/QUIC divide el RTT a la mitad" | Solo cuando hay un tercer salto (relé público o túnel de Cloudflare como en Kaggle). Con `compushare servir` el relé corre **en el maestro**: maestro -> relé es local. |
| "QUIC elimina el bloqueo de cabecera" | Para un solo flujo ordenado (como `ggml-rpc`) un paquete perdido igual frena ese flujo en QUIC. QUIC ayuda con varias conexiones a la vez, el arranque y el cambio de red, no con esto. |
| "4 trozos a la vez rinde menos porque el lote es síncrono" | Plausible pero sin medir. En b11434 el cliente RPC ya manda `SET_TENSOR`/`GRAPH_COMPUTE` asíncronos (`send_async`); la espera es por `GET_TENSOR` al final. Se vuelve a medir con el desglose antes de tocar nada. |
| "Si un trabajador tiene el modelo entero, no partir" | Correcto. Con el modelo entero en un nodo solo viajan texto y tokens: unos cientos de bytes por token. Es el modo **nodo completo**. |

## Paquetes de trabajo

| Id | Qué | Archivos | Se da por hecho cuando |
| --- | --- | --- | --- |
| P1 | Códec `rpc1` en Python + negociación relé/tubo + `desglose_rpc.py` | `fabric/rpc_codec.py`, `fabric/rele/rele.py`, `fabric/tubo.py`, `fabric/perfiles/desglose_rpc.py` | ida y vuelta byte por byte en pruebas (cortes arbitrarios, grafos sintéticos), extremo a extremo por relé + tubo reales con un servidor RPC falso |
| P2 | Códec `rpc1` en Go + misma negociación en `go/pkg/rele` y `go/pkg/tubo` | `go/pkg/rpccodec`, `go/pkg/rele`, `go/pkg/tubo` | pruebas Go en verde y vectores compartidos con Python |
| P3 | Ventana especulativa según la red (γ*) y ancho de banda medido | `fabric/calculo.py`, `fabric/reparto.py`, `fabric/rele/rele.py` (sonda), `fabric/tubo.py`, `src/compushare/nodo/maestro.py` | `servir` mide subida por tubo, calcula γ* y pasa `--spec-draft-n-max` / `--spec-ngram-mod-n-max` |
| P4 | Modo nodo completo + motor Go en la CLI | `src/compushare/cli.py`, `src/compushare/nodo/*` | `unirse --modelo X --completo` sirve el modelo entero por un tubo y `servir` lo prefiere; `unirse --motor go` usa `compushare-tubo` |
| P5 | Juntar, pruebas cruzadas Python <-> Go, documentación | todo | `pytest` y `go test` en verde; README y este plan al día |

## Lo que solo se puede cerrar con llama.cpp y modelos reales

Este contenedor no tiene llama.cpp compilado ni acceso a Hugging Face, así que las cifras de tok/s y de
bytes por comando **no se miden aquí**. Para la próxima corrida (local o Kaggle):

1. `python -m fabric.perfiles.desglose_rpc --escucha 50200 --destino 50100` entre `llama-server` y el tubo,
   con y sin `--spec-type ngram-mod,draft-mtp`: confirma qué comando se lleva los 633 KB.
2. La misma transcripción con `--sin-codec` y con el códec: bytes por token y tok/s.
3. Con `--activaciones q8`: parecido letra por letra contra la salida sin Q8.
4. `compushare calcular --subida-mbps 15` contra lo medido con un enlace limitado por el simulador.
