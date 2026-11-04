package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/tubo"
)

func main() {
	releDef := os.Getenv("FABRIC_RELE")
	if releDef == "" {
		releDef = "buscar"
	}
	claveDef := os.Getenv("FABRIC_CLAVE")

	releFlag := flag.String("rele", releDef, "Dirección del relé (ws://... o 'buscar')")
	claveFlag := flag.String("clave", claveDef, "Clave de acceso al relé")
	nombreFlag := flag.String("nombre", "", "Nombre del trabajador (obligatorio)")
	puertoFlag := flag.Int("puerto", 50052, "Puerto local que ofrece este tubo")
	hostFlag := flag.String("host", "127.0.0.1", "Host local del servicio ofrecido")
	vramFlag := flag.Float64("vram-gb", 0, "Memoria VRAM que presta en GB (opcional)")
	flag.Parse()

	if *nombreFlag == "" {
		log.Fatal("Falta el parámetro obligatorio --nombre")
	}

	var info map[string]any
	if *vramFlag > 0 {
		info = map[string]any{"vram_gb": *vramFlag}
	}

	t := tubo.New(*releFlag, *claveFlag, *nombreFlag, *puertoFlag, *hostFlag, info)

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	if err := t.Vivir(ctx); err != nil && err != context.Canceled {
		log.Fatalf("error fatal en tubo: %v", err)
	}
}
