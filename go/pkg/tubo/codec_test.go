package tubo_test

import (
	"bytes"
	"context"
	"encoding/binary"
	"fmt"
	"io"
	"math/rand"
	"net"
	"testing"
	"time"

	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/rele"
	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/tubo"
)

// grafoRPC devuelve un GRAPH_COMPUTE sintético: 150 rpc_tensor de 296 bytes casi todos a
// cero. forma (0..2) cambia el lote, así que hay tres grafos distintos que se alternan.
func grafoRPC(forma int) []byte {
	const nodos = 150
	p := binary.LittleEndian.AppendUint32(nil, 0)
	p = binary.LittleEndian.AppendUint32(p, nodos)
	for i := 0; i < nodos; i++ {
		p = binary.LittleEndian.AppendUint64(p, uint64(0x1000+i))
	}
	p = binary.LittleEndian.AppendUint32(p, nodos)
	for i := 0; i < nodos; i++ {
		t := make([]byte, 296)
		binary.LittleEndian.PutUint64(t[0:], uint64(0x1000+i))
		binary.LittleEndian.PutUint32(t[24:], 4096)
		binary.LittleEndian.PutUint32(t[28:], uint32(1+forma*3))
		binary.LittleEndian.PutUint64(t[224:], uint64(0x7f0000000000+forma*0x100000+i*0x1000))
		p = append(p, t...)
	}
	return p
}

// flujoRPC arma un flujo TCP tipo ggml-rpc: pesos aleatorios (crudos) y después pasos
// con GRAPH_COMPUTE alternados, GRAPH_RECOMPUTE y activaciones aleatorias.
func flujoRPC(rnd *rand.Rand, pasos int) []byte {
	var b []byte
	pedido := func(cmd byte, payload []byte) {
		b = append(b, cmd)
		b = binary.LittleEndian.AppendUint64(b, uint64(len(payload)))
		b = append(b, payload...)
	}
	pesos := make([]byte, 1<<20)
	rnd.Read(pesos)
	pedido(6, pesos)
	for p := 0; p < pasos; p++ {
		pedido(10, grafoRPC(p%3))
		pedido(16, make([]byte, 12))
		act := make([]byte, 4*4096)
		rnd.Read(act)
		pedido(6, act)
	}
	return b
}

// extremoAExtremo conecta relé y tubo reales. Un servidor TCP falso (en el lugar de
// ggml-rpc-server) guarda lo que recibe. Devuelve lo recibido y los bytes que cruzaron
// el WebSocket del tubo.
func extremoAExtremo(t *testing.T, flujo []byte, codecRele bool, tuboSinCodec bool) ([]byte, int64) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	clave := "clave-codec-e2e"

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("error abriendo servidor falso: %v", err)
	}
	defer ln.Close()
	guardado := make(chan []byte, 1)
	go func() {
		conn, err := ln.Accept()
		if err != nil {
			guardado <- nil
			return
		}
		defer conn.Close()
		buf := make([]byte, len(flujo))
		n, _ := io.ReadFull(conn, buf)
		guardado <- buf[:n]
	}()

	r := rele.Nuevo(clave, 0)
	r.CodecActivo = codecRele
	listoRele := make(chan struct{})
	go func() {
		_ = r.Iniciar(ctx, listoRele)
	}()
	select {
	case <-listoRele:
	case <-time.After(5 * time.Second):
		t.Fatal("timeout arrancando el relé")
	}

	puertoFalso := ln.Addr().(*net.TCPAddr).Port
	tb := tubo.New(fmt.Sprintf("ws://127.0.0.1:%d", r.Puerto), clave, "trabajador-codec", puertoFalso, "127.0.0.1",
		map[string]any{"vram_gb": 4.0})
	tb.SinCodecs = tuboSinCodec
	go func() {
		_ = tb.Vivir(ctx)
	}()
	select {
	case <-tb.CanalListo():
	case <-time.After(5 * time.Second):
		t.Fatal("timeout esperando que el tubo esté listo")
	}

	conn, err := net.Dial("tcp", fmt.Sprintf("127.0.0.1:%d", tb.PuertoRemoto))
	if err != nil {
		t.Fatalf("error conectando al canal del tubo: %v", err)
	}
	defer conn.Close()

	// Cortes aleatorios, como los que haría llama-server.
	rnd := rand.New(rand.NewSource(99))
	for off := 0; off < len(flujo); {
		n := min(1+rnd.Intn(200000), len(flujo)-off)
		if _, err := conn.Write(flujo[off : off+n]); err != nil {
			t.Fatalf("error escribiendo el flujo: %v", err)
		}
		off += n
	}

	select {
	case recibido := <-guardado:
		return recibido, tb.Bytes.Load()
	case <-time.After(30 * time.Second):
		t.Fatal("timeout esperando el flujo en el servidor falso")
		return nil, 0
	}
}

func TestCodecExtremoAExtremoConCodec(t *testing.T) {
	flujo := flujoRPC(rand.New(rand.NewSource(1)), 30)
	recibido, enCable := extremoAExtremo(t, flujo, true, false)
	if !bytes.Equal(recibido, flujo) {
		t.Fatalf("con códec: lo recibido (%d B) no coincide con lo enviado (%d B)", len(recibido), len(flujo))
	}
	if enCable*100 >= int64(len(flujo))*75 {
		t.Fatalf("con códec no hay ahorro: %d de %d bytes por el WebSocket", enCable, len(flujo))
	}
	t.Logf("con códec: %d B crudos -> %d B por el WebSocket (%.1f %%)", len(flujo), enCable,
		100*float64(enCable)/float64(len(flujo)))
}

func TestCodecExtremoAExtremoRelaySinCodec(t *testing.T) {
	flujo := flujoRPC(rand.New(rand.NewSource(2)), 10)
	recibido, enCable := extremoAExtremo(t, flujo, false, false)
	if !bytes.Equal(recibido, flujo) {
		t.Fatal("sin códec en el relé: lo recibido no coincide")
	}
	if enCable != int64(len(flujo)) {
		t.Fatalf("sin códec en el relé debe ir crudo: %d != %d", enCable, len(flujo))
	}
}

func TestCodecExtremoAExtremoTuboViejo(t *testing.T) {
	flujo := flujoRPC(rand.New(rand.NewSource(3)), 10)
	recibido, enCable := extremoAExtremo(t, flujo, true, true)
	if !bytes.Equal(recibido, flujo) {
		t.Fatal("tubo que no anuncia rpc1: lo recibido no coincide")
	}
	if enCable != int64(len(flujo)) {
		t.Fatalf("tubo que no anuncia rpc1 debe ir crudo: %d != %d", enCable, len(flujo))
	}
}
