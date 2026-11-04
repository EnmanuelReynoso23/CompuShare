#!/bin/bash
# Script de inicio rápido para Worker en Kaggle o Colab
set -e

RELE_URL="${1:-ws://tu-ip-o-dominio:8000/ws/worker}"
MODELO="${2:-qwen2.5-coder-7b}"

echo "=========================================================="
echo "Iniciando Worker de CompuShare hacia: $RELE_URL"
echo "Modelo: $MODELO"
echo "=========================================================="

python3 scripts/kaggle/worker_kaggle.py "$RELE_URL"
