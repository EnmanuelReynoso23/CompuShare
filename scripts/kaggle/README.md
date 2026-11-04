# Despliegue de Nodos Trabajadores en Kaggle / Google Colab

Este directorio contiene las herramientas para desplegar instancias efímeras de GPUs en Kaggle (2x NVIDIA Tesla T4) o Google Colab y conectarlas al **Relé Maestro de CompuShare**.

---

## 🚀 Cómo ejecutar en Kaggle (Notebook o Script)

1. **Crear un nuevo Notebook en Kaggle:**
   - Selecciona acelerador: **GPU T4 x 2** o **GPU P100**.
   - Habilita la opción **Internet: On** en la barra lateral derecha.

2. **Ejecutar el script en una celda de código:**

```python
!git clone https://github.com/EnmanuelReynoso23/CompuShare.git
%cd CompuShare

# Reemplaza con la dirección pública de tu Relé Maestro (ej. Cloudflare Tunnel, ngrok o IP directa)
!python scripts/kaggle/worker_kaggle.py "wss://tu-rele.tudominio.com/ws/worker"
```

El script se encargará automáticamente de:
- Descargar el binario optimizado con soporte CUDA de `llama-server`.
- Descargar el modelo cuantizado en formato GGUF (por defecto Qwen 2.5 Coder 7B/14B Q4_K_M).
- Iniciar el servidor local de inferencia en `http://127.0.0.1:8080`.
- Abrir un túnel WebSocket saliente hacia el Relé Maestro de CompuShare.

---

## 🔒 Conectando trabajadores remotos a tu Relé Local

Si ejecutas el Relé Maestro en tu computadora local:
```bash
compushare servir --puerto 8000
```

Puedes exponerlo hacia internet para que los workers de Kaggle/Colab se conecten usando cualquiera de estos métodos:
- **Cloudflare Tunnel (Recomendado, gratuito y estable):**
  ```bash
  cloudflared tunnel --url http://localhost:8000
  ```
- **ngrok:**
  ```bash
  ngrok http 8000
  ```
Luego utilizas la URL generada (`wss://xxxx.trycloudflare.com/ws/worker` o `wss://xxxx.ngrok-free.app/ws/worker`) como parámetro en Kaggle.
