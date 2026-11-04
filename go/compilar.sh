#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

GO_BIN="${GO_BIN:-/home/reyno/.local/bin/go}"
if ! command -v "$GO_BIN" >/dev/null 2>&1; then
    GO_BIN="go"
fi

echo "Compilador: $($GO_BIN version)"

PLATAFORMAS=(
    "linux/amd64"
    "linux/arm64"
    "windows/amd64"
    "darwin/arm64"
)

mkdir -p dist

for PLATAFORMA in "${PLATAFORMAS[@]}"; do
    GOOS="${PLATAFORMA%/*}"
    GOARCH="${PLATAFORMA#*/}"
    DIR_SALIDA="dist/${GOOS}_${GOARCH}"
    mkdir -p "$DIR_SALIDA"

    EXT=""
    if [ "$GOOS" = "windows" ]; then
        EXT=".exe"
    fi

    echo "Compilando para $GOOS/$GOARCH..."
    CGO_ENABLED=0 GOOS="$GOOS" GOARCH="$GOARCH" "$GO_BIN" build -ldflags="-s -w" -o "$DIR_SALIDA/compushare-rele$EXT" ./cmd/compushare-rele
    CGO_ENABLED=0 GOOS="$GOOS" GOARCH="$GOARCH" "$GO_BIN" build -ldflags="-s -w" -o "$DIR_SALIDA/compushare-tubo$EXT" ./cmd/compushare-tubo
done

echo "Compilacion cruzada completada exitosamente en go/dist/:"
ls -lh dist/*/*
