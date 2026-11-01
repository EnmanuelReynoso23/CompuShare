# Prueba: 10 computadoras escriben un cuento de 100 hojas (para el 1-nov-2026)

Pedido del usuario (31-oct, noche): simular 10 computadoras divididas, con IA, escribiendo un cuento de
100 hojas, "como si fuera internet", y medir a qué velocidad escriben.

## Qué se mide

- Cuento de 100 hojas = ~27.000 palabras (~270 por hoja) = ~40.000 tokens de salida.
- Por escenario: tok/s, hojas por hora, tiempo total, bytes por la red y si el cuento queda coherente.
- Todo se escribe en `kaggle/resultados/<fecha>-prueba10/INFORME.md`, con el cuento completo al lado.

## Las 10 "computadoras"

Cada computadora es un proceso propio (`ggml-rpc-server` + `fabric.tubo`) con su propia red falsa
delante: un intermediario que impone latencia, variación (jitter), ancho de banda y pérdida, hecho con
`prueba-local/proxy.py` mejorado (hoy ya simula latencia y cuenta comandos RPC).

Perfiles de red (ida y vuelta):

| Perfil | Latencia | Variación | Ancho de banda |
| --- | --- | --- | --- |
| misma ciudad | 10 ms | 2 ms | 100 Mb/s |
| mismo país | 40 ms | 10 ms | 50 Mb/s |
| otro continente | 120 ms | 20 ms | 20 Mb/s |
| Cloudflare medido (31-oct) | 237 ms | 40 ms | medir |

Dónde corre:

1. **Ensayo en esta PC con Docker** (Docker 29.8 instalado): 10 contenedores reales, red con
   `tc netem` (latencia y pérdida de verdad en el kernel), modelo diminuto (Qwen3.5-0.8B) en CPU.
   Sirve para ver que el montaje funciona; las velocidades no cuentan (4 GB de VRAM, 15 GB de RAM).
2. **Prueba real en Kaggle** (2 x T4, 30 GB): Kaggle no tiene Docker, así que las 10 computadoras son
   10 procesos repartidos entre las 2 GPU, con la red falsa en el intermediario. El cálculo es GPU
   real; la red es simulada con los perfiles de arriba.

## Escenarios (ajustado al pedido del usuario, 31-oct noche)

Idea del usuario: EL MISMO modelo en las 10 computadoras a la vez, sumando su CPU, GPU y RAM, para que
corra en equipos que solos no podrían. Nada de islas.

- Cada computadora simulada es "débil": límite de ~2-3 GB para su parte (`--tensor-split` en partes
  iguales) y algunas solo con CPU (`ggml-rpc-server -d CPU -t 2`).
- Modelo que no cabe en ninguna sola: Qwen3.8-27B UD-Q4_K_M (16,5 GB) repartido en 10 (~1,7 GB cada una
  más caché KV). Respaldo: Qwen3.5-9B-MTP Q8 (9,8 GB, probado el 31-oct).

- **A. Un capítulo a la vez**: las 10 en fila; cada palabra pasa por todas. Muestra el costo de cada salto.
- **B. Varios capítulos a la vez con el mismo modelo dividido** (`--parallel N` en llama-server): mientras
  la computadora 10 termina una palabra del capítulo 1, la 1 ya calcula el capítulo 2; así trabajan
  todas al mismo tiempo. Medir velocidad total con N = 1, 4, 8.
- **C. A y B con el adivinador** (MTP), si el modelo elegido lo trae.

Estimación previa (con las cifras del 31-oct: ~45 ms de cálculo por palabra + 1 ida y vuelta por salto),
escenario A, 40.000 tokens: misma ciudad ~1 h 40 min, mismo país ~5 h, otro continente ~14 h. B debería
multiplicar la velocidad total por casi N mientras la red aguante.

## Qué hay que construir

1. `proxy.py` -> `fabric/simulador/red.py`: latencia + variación + ancho de banda + pérdida, por enlace.
2. `fabric/simulador/escenario.py`: levanta N computadoras (procesos o contenedores), arma la cadena
   `--rpc` con N puertos y mide.
3. `fabric/simulador/cuento.py`: esquema -> 25 capítulos de ~4 hojas, con resumen corrido para que el
   cuento no se contradiga; guarda el texto y las cifras.
4. `docker-compose` para el ensayo local (imagen con llama.cpp b12024 + Python).
5. Modo en `lanzar.py` para correrlo en un cuaderno Kaggle.

## Por resolver antes (lo que quedó de hoy)

- La primera petición después de cargar sigue lenta (calentamiento).
- Ver si con 10 servidores RPC el orden de capas y la memoria se reparten bien (`--tensor-split`).
- Quedan ~8 consultas `GET_ALLOC_SIZE` por cada largo de prompt nuevo, que con 10 saltos pesan más.

## Decisiones del usuario

1. (Resuelto: el mismo modelo dividido en las 10; B = varios capítulos a la vez.)
2. ¿Usamos tiempo de GPU de Kaggle para la prueba real? 30 h/semana, quedan ~27 h; esta prueba usa 1
   cuaderno por ~3 h.
