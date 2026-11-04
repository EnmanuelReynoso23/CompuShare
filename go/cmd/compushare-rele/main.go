package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
	"strconv"
	"syscall"

	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/rele"
)

func envInt(nombre string, defecto int) int {
	v := os.Getenv(nombre)
	if v == "" {
		return defecto
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return defecto
	}
	return n
}

func envInt64(nombre string, defecto int64) int64 {
	v := os.Getenv(nombre)
	if v == "" {
		return defecto
	}
	n, err := strconv.ParseInt(v, 10, 64)
	if err != nil {
		return defecto
	}
	return n
}

func envFloat(nombre string, defecto float64) float64 {
	v := os.Getenv(nombre)
	if v == "" {
		return defecto
	}
	f, err := strconv.ParseFloat(v, 64)
	if err != nil {
		return defecto
	}
	return f
}

func main() {
	claveDef := os.Getenv("FABRIC_CLAVE")
	puertoDef := envInt("PUERTO", 8095)
	maxConDef := envInt("MAX_CONEXIONES", 300)
	maxBytesDef := envInt64("MAX_BYTES", 4*1024*1024)
	rafagaDef := envFloat("RAFAGA", 120.0)
	ritmoDef := envFloat("RITMO", 40.0)
	tareaVenceDef := envFloat("TAREA_VENCE_S", 900.0)
	latidoVenceDef := envFloat("LATIDO_VENCE_S", 60.0)
	tuboBaseDef := envInt("TUBO_PUERTO_BASE", 50100)

	clave := flag.String("clave", claveDef, "Clave compartida del relé (o FABRIC_CLAVE)")
	puerto := flag.Int("puerto", puertoDef, "Puerto donde escucha el relé (o PUERTO)")
	maxCon := flag.Int("max-conexiones", maxConDef, "Límite máximo de conexiones concurrentes")
	maxBytes := flag.Int64("max-bytes", maxBytesDef, "Tamaño máximo en bytes por mensaje")
	rafaga := flag.Float64("rafaga", rafagaDef, "Ráfaga máxima de mensajes en la cubeta de fichas")
	ritmo := flag.Float64("ritmo", ritmoDef, "Ritmo sostenido de mensajes por segundo")
	tareaVence := flag.Float64("tarea-vence-s", tareaVenceDef, "Segundos tras los cuales vence una tarea")
	latidoVence := flag.Float64("latido-vence-s", latidoVenceDef, "Segundos sin latido para desconectar un par")
	tuboBase := flag.Int("tubo-puerto-base", tuboBaseDef, "Puerto base para tubos locales")
	flag.Parse()

	if *clave == "" {
		log.Fatal("Falta FABRIC_CLAVE: el relé no arranca abierto.")
	}

	r := rele.Nuevo(*clave, *puerto)
	r.MaxConexiones = *maxCon
	r.MaxBytes = *maxBytes
	r.Rafaga = *rafaga
	r.Ritmo = *ritmo
	r.TareaVenceS = *tareaVence
	r.LatidoVenceS = *latidoVence
	r.TuboPuertoBase = *tuboBase

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	listo := make(chan struct{})
	if err := r.Iniciar(ctx, listo); err != nil {
		log.Fatalf("error fatal en relé: %v", err)
	}
}
