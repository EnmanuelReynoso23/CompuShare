# La matemática de CompuShare (4-nov-2026)

Cómo correr un modelo abierto entre amigos, cuánto tarda cada palabra y qué conviene arreglar primero.
El código está en `fabric/calculo.py`, los escenarios en `fabric/escenarios.py` y las pruebas en
`fabric/pruebas/test_calculo.py`. Las tablas de abajo salen de `python -m fabric.escenarios`.

Lo que es SUPUESTO (equipos de los amigos, subida de 10 Mb/s, capas y KV de modelos que no hemos bajado)
está marcado en el código. Lo demás sale de mediciones con su archivo.

## 1. Las fórmulas

**Memoria.** El modelo cabe si

    M_necesaria = (pesos + kv_por_token x contexto x flujos) x 1,10  <=  suma de la memoria de los equipos usados

Las capas se reparten proporcional a la memoria de cada equipo (igual que llama.cpp).

**Cálculo por palabra.** En la generación cada equipo lee una vez los pesos de sus capas; manda el ancho de
banda de memoria, no los TFLOPS:

    t_calc = suma_i( capas_i x (pesos x fracción_activa / capas) / (banda_i x 0,7) )

En un MoE solo se leen los expertos elegidos: fracción_activa = 3/35 en un 35B-A3B. Por eso un MoE grande
calcula casi como uno chico, aunque pide la memoria del grande.

**Red por palabra.** llama.cpp RPC es una estrella: el maestro manda la activación a cada trabajador y espera
la salida antes de seguir. Cada trabajador usado suma una ida y vuelta y lo que cruza su enlace:

    t_red = suma_trabajadores( rtt_i + bytes_por_palabra_i x 8 / subida_i )
    bytes_por_palabra = fijo + 2 x d_modelo x 4 x flujos        (activación f32 de ida y de vuelta)

`fijo` es lo que se reenvía en cada palabra aparte de la activación (el grafo de cálculo, la máscara de
atención). llama.cpp b12024 ya trae `GRAPH_RECOMPUTE` (`ggml/src/ggml-rpc/ggml-rpc.cpp:1248`): si el grafo
tiene el mismo `uid` que uno anterior no lo vuelve a mandar. En la corrida de Kaggle medimos 315 KB por
palabra, diez veces la activación (32 KB): o el grafo no se estaba reutilizando o viaja otra cosa. Esa es la
incógnita más cara del proyecto (sección 3).

**Tiempo por pasada y velocidad.**

    T = t_calc + t_red
    palabras/s por pregunta = E / T          palabras/s total = flujos x E / T

**Decodificación especulativa.** Se adivinan k palabras; cada una acierta con probabilidad alfa. Palabras
por pasada:

    E(alfa, k) = 1 + alfa + alfa^2 + ... + alfa^k = (1 - alfa^(k+1)) / (1 - alfa)

y la pasada cuesta T x (1 + gamma x k), donde gamma es lo que encarece cada palabra adivinada.

**Amigos que apagan la PC.** Si cada uno está encendido con probabilidad p, una cadena de n funciona con p^n.
Con un suplente por tramo: (1 - (1 - p)^2)^n.

**Tramposos.** Si se recalcula en otro equipo una fracción f de las pasadas y un nodo corrompe una fracción c,
se lo detecta tras n pasadas con probabilidad 1 - (1 - f c)^n.

## 2. Calibración con lo medido


Calculado 156 ms por palabra (cálculo 43 + red 114); medido 147-150 ms.

MTP: con alfa 0,79 y k 4 (3,3 palabras por pasada, medido en local), 10,5 tok/s medidos -> cada palabra adivinada encarece la pasada un 28 %.

El modelo cae dentro del 6 % de la corrida real (`20261031-dividido`) usando la ida y vuelta mediana de
101 ms medida al relé el 1-nov. El HELLO de 237 ms fue una sola medición y no cuadra con 147 ms por
palabra; hay que repetirlo. gamma = 0,28 es alto: cada palabra adivinada parece costar casi un tercio de
pasada, lo que sugiere que la cabeza MTP o la verificación hacen viajes extra por la red.

## 3. Entre amigos

### Qwen3.8-27B Q4 entre amigos (ida y vuelta 40 ms, subida 10 Mb/s)

| Equipos:capas | ms cálculo | ms red | ms por pasada | palabras/s por pregunta | palabras/s total | Caso |
|---|---|---|---|---|---|---|
| tu PC:32, amigo 2:32 | 65 | 292 | 357 | 2.8 | 2.8 | grafo reenviado (315 KB, medido), 1 flujos |
| tu PC:32, amigo 2:32 | 65 | 390 | 456 | 2.2 | 8.8 | grafo reenviado (315 KB, medido), 4 flujos |
| tu PC:24, amigo 1:16, amigo 2:24 | 71 | 1043 | 1114 | 0.9 | 7.2 | grafo reenviado (315 KB, medido), 8 flujos |
| tu PC:32, amigo 2:32 | 65 | 73 | 138 | 7.2 | 7.2 | solo activación (grafo reutilizado), 1 flujos |
| tu PC:32, amigo 2:32 | 65 | 171 | 237 | 4.2 | 16.9 | solo activación (grafo reutilizado), 4 flujos |
| tu PC:24, amigo 1:16, amigo 2:24 | 71 | 604 | 675 | 1.5 | 11.9 | solo activación (grafo reutilizado), 8 flujos |
| tu PC:32, amigo 2:32 | 121 | 134 | 255 | 11.4 | 11.4 | solo activación, 1 flujo, MTP como hoy (k=3) |
| tu PC:32, amigo 2:32 | 79 | 87 | 166 | 19.9 | 19.9 | solo activación, 1 flujo, MTP sin viajes extra (k=4) |

### Qwen3.6-35B-A3B Q4 entre amigos (ida y vuelta 40 ms, subida 10 Mb/s)

| Equipos:capas | ms cálculo | ms red | ms por pasada | palabras/s por pregunta | palabras/s total | Caso |
|---|---|---|---|---|---|---|
| tu PC:18, amigo 1:12, amigo 2:18 | 8 | 584 | 592 | 1.7 | 1.7 | grafo reenviado (315 KB, medido), 1 flujos |
| tu PC:18, amigo 1:12, amigo 2:18 | 8 | 663 | 670 | 1.5 | 6.0 | grafo reenviado (315 KB, medido), 4 flujos |
| tu PC:18, amigo 1:12, amigo 2:18 | 8 | 768 | 775 | 1.3 | 10.3 | grafo reenviado (315 KB, medido), 8 flujos |
| tu PC:18, amigo 1:12, amigo 2:18 | 8 | 106 | 114 | 8.8 | 8.8 | solo activación (grafo reutilizado), 1 flujos |
| tu PC:18, amigo 1:12, amigo 2:18 | 8 | 185 | 193 | 5.2 | 20.8 | solo activación (grafo reutilizado), 4 flujos |
| tu PC:18, amigo 1:12, amigo 2:18 | 8 | 290 | 297 | 3.4 | 26.9 | solo activación (grafo reutilizado), 8 flujos |
| tu PC:18, amigo 1:12, amigo 2:18 | 14 | 196 | 210 | 13.9 | 13.9 | solo activación, 1 flujo, MTP como hoy (k=3) |
| tu PC:18, amigo 1:12, amigo 2:18 | 9 | 127 | 137 | 24.1 | 24.1 | solo activación, 1 flujo, MTP sin viajes extra (k=4) |

### Formas de repartir que NO sirven por internet (solo red, por palabra)

| Forma | ms por palabra a 40 ms | a 5 ms | a 0,5 ms (misma casa) |
|---|---|---|---|
| Capas partidas entre 2 (tensor paralelo) (Qwen3.8-27B Q4) | 2560 | 320 | 32 |
| Expertos de MoE en equipos distintos (Qwen3.6-35B-A3B Q4) | 1920 | 240 | 24 |

### Amigos que apagan la PC (cada uno encendido el 90 % de las veces)

| Amigos en la cadena | Funciona | Con un suplente por tramo |
|---|---|---|
| 1 | 90 % | 99.0 % |
| 2 | 81 % | 98.0 % |
| 3 | 73 % | 97.0 % |
| 4 | 66 % | 96.1 % |

### Detectar a un tramposo

Revisando al azar el 2 % de las pasadas, un nodo que corrompe el 10 % de sus respuestas se detecta con 99 % de seguridad en 2301 pasadas (99.0 %).

## 4. Qué dice la cuenta (ordenado por ganancia)

1. **Bajar los bytes por palabra es lo primero.** Primer hallazgo (4-nov, `fabric/perfiles/salida/RESULTADO.md`):
   si el maestro no tiene GPU, llama.cpp deja la tabla de salida en el trabajador y en cada palabra vuelven
   las probabilidades de todo el vocabulario (993 KB en Qwen3.5). Con `fabric.reparto.salida_en_el_maestro()`
   bajan solo 4,1 KB (la activación): 64 veces menos en total, unos 800 ms menos por palabra con 10 Mb/s.
   Ya está en el nodo de Kaggle. Lo que sigue: con 315 KB por palabra y 10 Mb/s de subida, el 27B va a
   2,8 palabras/s; solo con la activación, a 7,2 (2,6 veces). Con varias preguntas a la vez la diferencia
   crece. Hay que medir qué viaja en cada palabra por comando RPC (tarea de A4) y lograr que el grafo se
   reutilice. Luego: activación en f16 en vez de f32 (la mitad) y comprimir lo que se repite.
2. **La especulativa sin viajes extra casi triplica.** Hoy (gamma 0,28) el mejor k es 3 y da 1,6 veces. Si
   adivinar y verificar no suman viajes (gamma 0,05), k = 4 da 2,8 veces: 27B a ~20 palabras/s. Hay que
   ver dónde corre la cabeza MTP (en el maestro, sin red) y medir alfa por la red de verdad (A7).
3. **Varias preguntas a la vez reparten la ida y vuelta.** Con 4 flujos el total sube 2,3 veces (27B) y 2,4
   (MoE). Pasado cierto punto la subida de 10 Mb/s se llena con activaciones y la caché KV obliga a sumar
   otro amigo (otro salto): para cada red hay un número de flujos óptimo, y la calculadora lo da.
4. **Usar los menos amigos posibles.** Cada amigo suma una ida y vuelta entera. `elegir_optimo` prueba todas
   las combinaciones y a veces prefiere un amigo con más memoria y más lejos a dos cercanos; `reparto.py`
   hoy toma los más cercanos primero y no ve eso.
5. **Un MoE es el mejor modelo para amigos.** El 35B-A3B calcula en 8 ms por palabra porque lee solo 3 B de
   parámetros; casi todo el tiempo es red. Se reparte por capas (cada amigo con todos los expertos de sus
   capas), nunca por expertos.
6. **Lo que no sirve por internet:** partir cada capa entre dos equipos (tensor paralelo, que llama.cpp ya
   permite con `COMM_ALLREDUCE`) o poner expertos en equipos distintos: son una ida y vuelta por capa y por
   palabra, 2 s por palabra a 40 ms. Solo valen dentro de una misma casa (red local de menos de 1 ms).
7. **Suplentes.** Con 3 amigos encendidos al 90 % la cadena funciona el 73 % de las veces; con un suplente por
   tramo, el 97 %. Hace falta que un suplente cargue las mismas capas y entre si uno se cae.
8. **Revisar al azar.** Recalcular el 2 % de las pasadas en otro equipo detecta a quien corrompe el 10 % de
   sus respuestas en unas 2.300 pasadas (menos de 5 minutos a 10 palabras/s). Con amigos de confianza
   alcanza; para una red pública hay que subir f.

## 5. Lo que falta medir para dejar de suponer

- Bytes por palabra por comando RPC, con grafo reutilizado y sin él (A4).
- Ida y vuelta y subida reales entre las casas de los amigos (A9).
- alfa y gamma de la especulativa por la red (A7).
- Banda efectiva de cada equipo de amigo: `llama-bench` en cada uno.
- Capas, ancho y KV por token reales de Qwen3.8-27B y del MoE elegido (leer del GGUF).
