package rpccodec_test

import (
	"bytes"
	"encoding/binary"
	"errors"
	"math"
	"math/rand"
	"os"
	"path/filepath"
	"testing"

	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/rpccodec"
)

// codificar pasa el flujo por un codificador en cortes dados por tam y devuelve los mensajes.
func codificar(flujo []byte, q8 bool, tam func() int) ([][]byte, rpccodec.Estadisticas) {
	c := rpccodec.NuevoCodificador(q8)
	var msgs [][]byte
	for off := 0; off < len(flujo); {
		n := tam()
		if n > len(flujo)-off {
			n = len(flujo) - off
		}
		if m := c.Alimentar(flujo[off : off+n]); m != nil {
			msgs = append(msgs, m)
		}
		off += n
	}
	return msgs, c.Estadisticas()
}

// decodificar pasa los mensajes por un decodificador nuevo y concatena la salida.
func decodificar(msgs [][]byte) ([]byte, error) {
	d := rpccodec.NuevoDecodificador()
	var out []byte
	for _, m := range msgs {
		o, err := d.Decodificar(m)
		if err != nil {
			return out, err
		}
		out = append(out, o...)
	}
	return out, nil
}

func enviado(msgs [][]byte) int {
	n := 0
	for _, m := range msgs {
		n += len(m)
	}
	return n
}

func TestF16Exacto(t *testing.T) {
	for h := 0; h <= 0xffff; h++ {
		f := rpccodec.F16ToF32(uint16(h))
		if f != f { // NaN
			continue
		}
		if r := rpccodec.F32ToF16(f); r != uint16(h) {
			t.Fatalf("ida y vuelta f16 0x%04x -> %v -> 0x%04x", h, f, r)
		}
	}
	casos := []struct {
		f float32
		h uint16
	}{
		{1, 0x3c00}, {-2, 0xc000}, {65504, 0x7bff}, {65520, 0x7c00}, {1e9, 0x7c00},
		{float32(math.Ldexp(1, -24)), 0x0001}, {float32(math.Ldexp(1, -25)), 0x0000},
		{float32(0.1), 0x2e66},
		{float32(1 + math.Ldexp(1, -11)), 0x3c00},   // empate: al par (1.0)
		{float32(1 + 3*math.Ldexp(1, -11)), 0x3c02}, // empate: al par (1+2^-9)
		{0, 0x0000}, {float32(math.Copysign(0, -1)), 0x8000},
	}
	for _, c := range casos {
		if r := rpccodec.F32ToF16(c.f); r != c.h {
			t.Errorf("F32ToF16(%v) = 0x%04x, se esperaba 0x%04x", c.f, r, c.h)
		}
	}
}

func TestIdaYVueltaCortesArbitrarios(t *testing.T) {
	rnd := rand.New(rand.NewSource(1))
	flujo := flujoSintetico(rnd, 2048, 30, 120, true)

	cortes := map[string]func() int{
		"un byte":       trozoFijo(1),
		"tres bytes":    trozoFijo(3),
		"aleatorios":    trozos(rnd, 70000),
		"256 KiB":       trozoFijo(256 << 10),
		"todo de golpe": trozoFijo(len(flujo)),
	}
	for nombre, tam := range cortes {
		f := flujo
		if nombre == "un byte" {
			f = f[:200000] // un byte por llamada es lento: se prueba un prefijo
		}
		msgs, _ := codificar(f, false, tam)
		out, err := decodificar(msgs)
		if err != nil {
			t.Fatalf("%s: error decodificando: %v", nombre, err)
		}
		if !bytes.Equal(out, f) {
			t.Fatalf("%s: la salida no coincide con el flujo (%d vs %d bytes)", nombre, len(out), len(f))
		}
	}
}

func TestMensajesRawAcotados(t *testing.T) {
	rnd := rand.New(rand.NewSource(2))
	flujo := flujoSintetico(rnd, 3072, 2, 40, false)
	msgs, _ := codificar(flujo, false, trozoFijo(1<<20))
	for i, m := range msgs {
		// Un trozo de 1 MiB sale como RAW de 256 KiB mas cabeceras; ningun mensaje debe
		// crecer mucho mas que lo que entro en esa llamada.
		if len(m) > (1<<20)+64<<10 {
			t.Fatalf("mensaje %d demasiado grande: %d bytes", i, len(m))
		}
	}
	if out, err := decodificar(msgs); err != nil || !bytes.Equal(out, flujo) {
		t.Fatalf("ida y vuelta falló: %v", err)
	}
}

func TestAhorroGrafosRepetidos(t *testing.T) {
	var ps []pedido
	for p := 0; p < 150; p++ {
		ps = append(ps, pedido{cmd: 10, payload: grafo(p%3, 200, 0)})
		ps = append(ps, pedido{cmd: 16, payload: append(u32(0), u64(uint64(p))...)})
	}
	flujo := serializar(ps)
	msgs, est := codificar(flujo, false, trozoFijo(64<<10))
	out, err := decodificar(msgs)
	if err != nil || !bytes.Equal(out, flujo) {
		t.Fatalf("ida y vuelta falló: %v", err)
	}

	var crudo, env uint64
	for _, tipo := range []byte{rpccodec.RegistroGrafoFull, rpccodec.RegistroGrafoRef, rpccodec.RegistroGrafoDelta} {
		c := est.Registros[tipo]
		crudo += c.Crudo
		env += c.Enviado
	}
	ahorro := 1 - float64(env)/float64(crudo)
	t.Logf("grafos repetidos: crudo %d B, enviado %d B, ahorro %.2f %%", crudo, env, 100*ahorro)
	if ahorro < 0.90 {
		t.Fatalf("ahorro en grafos repetidos %.2f %% (< 90 %%)", 100*ahorro)
	}
	if est.Registros[rpccodec.RegistroGrafoRef].N < 140 {
		t.Fatalf("pocos REF: %d", est.Registros[rpccodec.RegistroGrafoRef].N)
	}
	t.Logf("%s", est)
}

func TestDeltaCuandoAyuda(t *testing.T) {
	ps := []pedido{
		{cmd: 10, payload: grafo(0, 200, 0)},
		{cmd: 10, payload: grafo(0, 200, 8)}, // casi igual: un puntero distinto
	}
	flujo := serializar(ps)
	msgs, est := codificar(flujo, false, trozoFijo(1<<20))
	if est.Registros[rpccodec.RegistroGrafoDelta].N != 1 {
		t.Fatalf("se esperaba un registro DELTA, estadísticas: %s", est)
	}
	out, err := decodificar(msgs)
	if err != nil || !bytes.Equal(out, flujo) {
		t.Fatalf("ida y vuelta con DELTA falló: %v", err)
	}
}

func TestQ8ErrorAcotadoYMascaraCruda(t *testing.T) {
	rnd := rand.New(rand.NewSource(4))
	ps := []pedido{
		pesos(rnd, 64), // antes de cualquier GRAPH_COMPUTE: va cruda
		{cmd: 10, payload: grafo(0, 40, 0)},
		activacion(rnd, 1),
		mascara(1),
		activacion(rnd, 8),
	}
	flujo := serializar(ps)
	msgs, est := codificar(flujo, true, trozoFijo(1<<20))
	if est.Registros[rpccodec.RegistroSetQ8].N != 2 {
		t.Fatalf("se esperaban 2 registros Q8, estadísticas: %s", est)
	}
	out, err := decodificar(msgs)
	if err != nil {
		t.Fatalf("error decodificando: %v", err)
	}
	orig, _ := parsearPedidos(flujo)
	dec, err := parsearPedidos(out)
	if err != nil || len(dec) != len(orig) {
		t.Fatalf("pedidos decodificados: %d, esperados %d (%v)", len(dec), len(orig), err)
	}
	for i := range orig {
		o, d := orig[i], dec[i]
		if o.cmd != d.cmd || len(o.payload) != len(d.payload) {
			t.Fatalf("pedido %d: cmd o tamaño distintos", i)
		}
		if bytes.Equal(o.payload, d.payload) {
			continue
		}
		// Solo las activaciones Q8 pueden diferir: cabecera igual y error por bloque acotado.
		if o.cmd != 6 || !bytes.Equal(o.payload[:305], d.payload[:305]) {
			t.Fatalf("pedido %d: diferencia fuera de los datos Q8", i)
		}
		xs := floatsDe(o.payload[305:])
		ys := floatsDe(d.payload[305:])
		for b := 0; b < len(xs); b += 32 {
			fin := min(b+32, len(xs))
			var amax float64
			for _, x := range xs[b:fin] {
				amax = math.Max(amax, math.Abs(float64(x)))
			}
			for j := b; j < fin; j++ {
				if e := math.Abs(float64(xs[j] - ys[j])); e > amax/127+1e-9 {
					t.Fatalf("pedido %d, valor %d: error %g > amax/127 %g", i, j, e, amax/127)
				}
			}
		}
	}
	// Una activación de 4096 valores ocupa 305 + 4 + 8 + 34*128 bytes en lugar de 305+16384.
	c := est.Registros[rpccodec.RegistroSetQ8]
	if float64(c.Enviado)/float64(c.Crudo) > 0.28 {
		t.Fatalf("Q8 no reduce lo bastante: %d -> %d", c.Crudo, c.Enviado)
	}
}

func floatsDe(b []byte) []float32 {
	v := make([]float32, len(b)/4)
	for i := range v {
		v[i] = math.Float32frombits(binary.LittleEndian.Uint32(b[4*i:]))
	}
	return v
}

func TestHashDesconocidoEsError(t *testing.T) {
	msg := []byte{rpccodec.RegistroGrafoRef}
	msg = append(msg, make([]byte, 16)...)
	_, err := rpccodec.NuevoDecodificador().Decodificar(msg)
	if !errors.Is(err, rpccodec.ErrHashDesconocido) {
		t.Fatalf("se esperaba ErrHashDesconocido, llegó %v", err)
	}
}

func TestErroresDeMensaje(t *testing.T) {
	d := rpccodec.NuevoDecodificador()
	if _, err := d.Decodificar([]byte{0x7f}); !errors.Is(err, rpccodec.ErrRegistroDesconocido) {
		t.Errorf("tipo desconocido: %v", err)
	}
	raw := []byte{rpccodec.RegistroRAW, 10, 0, 0, 0, 1, 2}
	if _, err := d.Decodificar(raw); !errors.Is(err, rpccodec.ErrMensajeCorrupto) {
		t.Errorf("RAW truncado: %v", err)
	}
	full := []byte{rpccodec.RegistroGrafoFull, 4, 0, 0, 0, 3, 0, 0, 0, 1, 2, 3}
	if _, err := d.Decodificar(full); err == nil {
		t.Errorf("zlib inválido debería fallar")
	}
}

func TestComandoMayorQue64VaCrudo(t *testing.T) {
	g := grafo(0, 60, 0)
	flujo := serializar([]pedido{
		{cmd: 10, payload: g},
		{cmd: 70, payload: []byte("xyz")},
		{cmd: 10, payload: g},
	})
	msgs, est := codificar(flujo, false, trozoFijo(1<<20))
	if est.Registros[rpccodec.RegistroGrafoRef].N != 0 || est.Registros[rpccodec.RegistroGrafoFull].N != 1 {
		t.Fatalf("tras cmd>=64 no debe haber grafos codificados: %s", est)
	}
	out, err := decodificar(msgs)
	if err != nil || !bytes.Equal(out, flujo) {
		t.Fatalf("ida y vuelta falló: %v", err)
	}
}

func TestIdaYVueltaSinQ8(t *testing.T) {
	rnd := rand.New(rand.NewSource(5))
	flujo := flujoSintetico(rnd, 512, 6, 50, true)
	msgs, _ := codificar(flujo, false, trozos(rnd, 9000))
	out, err := decodificar(msgs)
	if err != nil || !bytes.Equal(out, flujo) {
		t.Fatalf("ida y vuelta sin Q8 falló: %v", err)
	}
}

// Vectores compartidos con Python: fabric/pruebas/vectores_codec/.
func TestVectoresPython(t *testing.T) {
	dir := filepath.Join("..", "..", "..", "fabric", "pruebas", "vectores_codec")
	flujoPath := filepath.Join(dir, "flujo_crudo.bin")
	msgsPath := filepath.Join(dir, "mensajes.bin")
	flujo, err1 := os.ReadFile(flujoPath)
	msgsBin, err2 := os.ReadFile(msgsPath)
	if err1 != nil || err2 != nil {
		t.Skipf("vectores de Python no disponibles: %v %v", err1, err2)
	}
	verificarVectores(t, flujo, msgsBin)
}

// Vectores generados por Go: testdata/ (los lee también el lado Python).
func TestVectoresGoIdaYVuelta(t *testing.T) {
	flujo, err1 := os.ReadFile(filepath.Join("testdata", "flujo_crudo_go.bin"))
	msgsBin, err2 := os.ReadFile(filepath.Join("testdata", "mensajes_go.bin"))
	if err1 != nil || err2 != nil {
		t.Skipf("vectores Go no generados (ver TestGenerarVectoresGo): %v %v", err1, err2)
	}
	verificarVectores(t, flujo, msgsBin)
}

func verificarVectores(t *testing.T, flujo, msgsBin []byte) {
	t.Helper()
	var msgs [][]byte
	for i := 0; i < len(msgsBin); {
		if len(msgsBin)-i < 4 {
			t.Fatalf("cabecera de mensaje truncada en %d", i)
		}
		n := int(binary.LittleEndian.Uint32(msgsBin[i:]))
		i += 4
		if n > len(msgsBin)-i {
			t.Fatalf("mensaje truncado en %d", i)
		}
		msgs = append(msgs, msgsBin[i:i+n])
		i += n
	}
	out, err := decodificar(msgs)
	if err != nil {
		t.Fatalf("error decodificando vectores: %v", err)
	}
	if !bytes.Equal(out, flujo) {
		t.Fatalf("decodificar los mensajes no da el flujo crudo (%d vs %d bytes)", len(out), len(flujo))
	}
}

// TestGenerarVectoresGo escribe testdata/ para que Python lo pruebe. Solo corre con
// GENERAR_VECTORES_GO=1; el resultado va al repositorio.
func TestGenerarVectoresGo(t *testing.T) {
	if os.Getenv("GENERAR_VECTORES_GO") != "1" {
		t.Skip("solo con GENERAR_VECTORES_GO=1")
	}
	rnd := rand.New(rand.NewSource(42))
	flujo := flujoSintetico(rnd, 24, 6, 60, true)
	msgs, _ := codificar(flujo, false, trozos(rnd, 40000))

	var bin []byte
	for _, m := range msgs {
		bin = binary.LittleEndian.AppendUint32(bin, uint32(len(m)))
		bin = append(bin, m...)
	}
	dir := "testdata"
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "flujo_crudo_go.bin"), flujo, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "mensajes_go.bin"), bin, 0o644); err != nil {
		t.Fatal(err)
	}
	t.Logf("flujo %d B, mensajes %d B (%d mensajes)", len(flujo), len(bin), len(msgs))
}

// El Q8 que cuantiza Python debe reconstruirse en Go exactamente igual que en Python.
func TestVectoresPythonQ8(t *testing.T) {
	dir := filepath.Join("..", "..", "..", "fabric", "pruebas", "vectores_codec")
	msg, err1 := os.ReadFile(filepath.Join(dir, "q8.bin"))
	esperado, err2 := os.ReadFile(filepath.Join(dir, "q8_esperado.bin"))
	if err1 != nil || err2 != nil {
		t.Skipf("vectores Q8 de Python no disponibles: %v %v", err1, err2)
	}
	out, err := rpccodec.NuevoDecodificador().Decodificar(msg)
	if err != nil {
		t.Fatalf("error decodificando q8.bin: %v", err)
	}
	if !bytes.Equal(out, esperado) {
		t.Fatalf("Go reconstruye el Q8 distinto que Python (%d vs %d bytes)", len(out), len(esperado))
	}
}
