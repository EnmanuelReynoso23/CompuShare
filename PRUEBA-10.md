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

## Escenarios

- **A. Una IA dividida en 10** (lo que pidió el usuario): un solo modelo repartido en 10 partes, una
  por computadora, en fila. El maestro escribe el cuento capítulo por capítulo. Muestra cuánto cuesta
  cada salto: 10 computadoras = 10 saltos por cada pasada.
- **B. 10 computadoras, cada una con su modelo** (comparación): el maestro arma el esquema del cuento y
  reparte capítulos; cada computadora escribe los suyos con el resumen de lo anterior.
- **C. A + adivinador** (MTP, como hoy): el mismo A con decodificación especulativa, para ver cuánto
  recupera.

Modelo candidato: Qwen3.5-9B-MTP Q8 (el de hoy; ya probado por RPC) para A y C. Para B, el mismo modelo
entero en cada computadora no cabe 10 veces en 30 GB: se usa Qwen3.5-2B o 4B (verificar en Hugging Face
el día de la prueba).

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

1. ¿Incluyo el escenario B como comparación, o solo A y C?
2. ¿Usamos tiempo de GPU de Kaggle para la prueba real? 30 h/semana, quedan ~27 h; esta prueba usa 1
   cuaderno por ~3 h.
