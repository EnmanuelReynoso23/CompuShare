package rpccodec_test

import (
	"bytes"
	"math/rand"
	"testing"
)

// Con Q8 activo la salida no depende de cómo se corte el TCP: el codificador
// decide por pedido completo, no por trozo.
func TestQ8IndependienteDeLosCortes(t *testing.T) {
	rnd := rand.New(rand.NewSource(6))
	flujo := flujoSintetico(rnd, 256, 12, 50, true)

	msgsA, estA := codificar(flujo, true, trozoFijo(len(flujo)))
	msgsB, estB := codificar(flujo, true, trozos(rnd, 5000))

	outA, errA := decodificar(msgsA)
	outB, errB := decodificar(msgsB)
	if errA != nil || errB != nil {
		t.Fatalf("errores decodificando: %v / %v", errA, errB)
	}
	if !bytes.Equal(outA, outB) {
		t.Fatal("la salida Q8 cambia según los cortes")
	}
	if estA.Registros[0x04].N != estB.Registros[0x04].N || estA.Registros[0x04].N == 0 {
		t.Fatalf("registros Q8 distintos o nulos: %d vs %d", estA.Registros[0x04].N, estB.Registros[0x04].N)
	}
}
