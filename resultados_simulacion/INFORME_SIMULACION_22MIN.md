# Informe de Simulación: Transcripción de la Biblia en 22 Minutos
**Fecha:** 2026-11-04 17:53:49  
**Configuración:** 4 Laptops residenciales vía Wi-Fi con latencia simulada de 50 ms Ping (RTT).  
**Fuente:** Santa Biblia (Reina-Valera 1960) íntegra: 66 libros, 1.189 capítulos, 31.104 versículos.

---

## 1. Resumen Ejecutivo

| Parámetro | Valor Medido |
| :--- | :--- |
| **Tiempo Total de Ejecución** | **22.11 minutos (1326.8 segundos)** |
| **Tokens Totales Procesados** | **1,108,792 tokens** |
| **Palabras Totales** | **9,943 palabras** |
| **Volumen de Texto Generado** | **3.77 MB** |
| **Rendimiento Global del Enjambre** | **835.7 tokens/segundo** |
| **Latencia de Red Simulada** | **50 ms RTT (±5 ms Jitter Wi-Fi)** |

---

## 2. Desglose Operativo por Laptop

| Nodo | Equipo Simulado | IP | Capítulos Asignados | Tokens | Throughput Real | Tiempo Total |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **laptop-01-alfa** | Lenovo ThinkPad X1 (Intel Core Ultra + NPU) | `192.168.1.101` | 298 | 307,933 | 232.1 tok/s | **22.11 min** |
| **laptop-02-beta** | Dell XPS 15 (NVIDIA RTX 4060 Laptop GPU) | `192.168.1.102` | 298 | 292,150 | 238.9 tok/s | **20.38 min** |
| **laptop-03-gamma** | Apple MacBook Pro (Apple Silicon M3 Max) | `192.168.1.103` | 298 | 288,029 | 234.8 tok/s | **20.44 min** |
| **laptop-04-delta** | Asus ROG Zephyrus (AMD Ryzen 9 + RTX 4070) | `192.168.1.104` | 295 | 220,680 | 235.9 tok/s | **15.59 min** |


---

## 3. Archivos Apache Generados

1. **`httpd-compushare.conf`:** Archivo de configuración para Apache HTTP Server 2.4 con VirtualHost, ProxyPass WebSocket y cabeceras.
2. **`access.log`:** Registro de **1,189 solicitudes HTTP 200** en formato *Apache Combined Log*.
3. **`error.log`:** Registro de ciclo de vida del servidor y eventos de los workers.
4. **`index.html`:** Dashboard estático servible por Apache con visualización de resultados.

---
*Generado automáticamente por el motor de simulación de CompuShare.*
