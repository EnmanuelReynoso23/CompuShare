# Dónde queda la tabla de salida (4-nov-2026, 11:00 a. m. RD)

Laptop (CPU, 4 hilos), Qwen3.5-0.8B-Q8_0 (vocabulario 248.320, ancho 1.024, 24 capas), llama.cpp b12024,
llama-server maestro sin GPU + 1 ggml-rpc-server local, `-ngl 13`, contando bytes con `contar.py`
entre los dos. Bytes por palabra = diferencia entre generar 80 y 16 palabras, dividida entre 64.

    OT='' ./medir.sh 13
    OT='-ot output\.=CPU' ./medir.sh 13
    OT='-ot token_embd\.weight=CPU' ./medir.sh 13

| Opción | Sube (KB/palabra) | Baja (KB/palabra) | tok/s local |
|---|---|---|---|
| sin -ot (también -ngl 99, 24 y 12) | 11,6 | 993,3 | 29,3 |
| `-ot output\.=CPU` (no existe: el modelo comparte la tabla) | 25,9 | 1.017,9 | 25,7 |
| `-ot token_embd\.weight=CPU` | 11,6 | **4,1** | 30,9 |

993,3 KB = 248.320 x 4 bytes: las probabilidades de todo el vocabulario. 4,1 KB = 1.024 x 4: la activación.
Causa: llama.cpp manda la capa de salida al primer equipo de la lista de capas (`src/llama-model.cpp:1690`).
Arreglo: `fabric.reparto.salida_en_el_maestro()`, usado en `fabric/kaggle/nodo_kaggle.py`.
