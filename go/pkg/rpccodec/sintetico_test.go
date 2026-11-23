package rpccodec_test

import (
	"encoding/binary"
	"fmt"
	"math"
	"math/rand"
)

// pedido es un pedido ggml-rpc: comando y payload (sin cabecera).
type pedido struct {
	cmd     byte
	payload []byte
}

func u32(v uint32) []byte { return binary.LittleEndian.AppendUint32(nil, v) }
func u64(v uint64) []byte { return binary.LittleEndian.AppendUint64(nil, v) }

// serializar produce el flujo TCP: cmd u8 | tamaño u64 LE | payload.
func serializar(ps []pedido) []byte {
	var b []byte
	for _, p := range ps {
		b = append(b, p.cmd)
		b = append(b, u64(uint64(len(p.payload)))...)
		b = append(b, p.payload...)
	}
	return b
}

// parsearPedidos separa un flujo TCP en pedidos; falla si el flujo no cuadra.
func parsearPedidos(b []byte) ([]pedido, error) {
	var out []pedido
	for i := 0; i < len(b); {
		if len(b)-i < 9 {
			return nil, fmt.Errorf("cabecera truncada en %d", i)
		}
		cmd := b[i]
		n := binary.LittleEndian.Uint64(b[i+1:])
		i += 9
		if uint64(len(b)-i) < n {
			return nil, fmt.Errorf("payload truncado en %d", i)
		}
		out = append(out, pedido{cmd: cmd, payload: b[i : i+int(n)]})
		i += int(n)
	}
	return out, nil
}

// rpcTensor construye un rpc_tensor de 296 bytes con los campos relevantes situados.
func rpcTensor(id uint64, tipo uint32, ne, nb [4]uint32, data uint64, nombre string) []byte {
	b := make([]byte, 296)
	le := binary.LittleEndian
	le.PutUint64(b[0:], id)
	le.PutUint32(b[8:], tipo)
	le.PutUint64(b[16:], 0x7000)
	for i := 0; i < 4; i++ {
		le.PutUint32(b[24+4*i:], ne[i])
		le.PutUint32(b[40+4*i:], nb[i])
	}
	le.PutUint32(b[56:], 5) // op
	le.PutUint64(b[224:], data)
	copy(b[232:], nombre)
	return b
}

// grafo construye un GRAPH_COMPUTE: device | n_nodes | nodes | n_tensors | rpc_tensor x n.
// forma (0..2) cambia el tamaño de lote, así que ne/nb/data difieren entre formas.
// marca > 0 cambia solo el puntero de datos del primer nodo: un grafo casi igual.
func grafo(forma, nodos, marca int) []byte {
	lote := []uint32{1, 4, 8}[forma%3]
	var b []byte
	b = append(b, u32(0)...)
	b = append(b, u32(uint32(nodos))...)
	for i := 0; i < nodos; i++ {
		b = append(b, u64(uint64(0x1000+i))...)
	}
	b = append(b, u32(uint32(nodos))...)
	for i := 0; i < nodos; i++ {
		ne := [4]uint32{4096, lote, 1, 1}
		nb := [4]uint32{4, 4 * 4096, 4 * 4096 * lote, 4 * 4096 * lote}
		data := uint64(0x7f0000000000) + uint64(forma)*0x100000 + uint64(i)*0x1000
		if i == 0 {
			data += uint64(marca)
		}
		b = append(b, rpcTensor(uint64(0x1000+i), 0, ne, nb, data, fmt.Sprintf("nodo_%d", i))...)
	}
	return b
}

// setTensor construye SET_TENSOR como b11434: rpc_tensor | cache_flag u8 | offset u64 | datos.
func setTensor(tipo uint32, ne [4]uint32, datos []byte) []byte {
	var b []byte
	b = append(b, rpcTensor(7, tipo, ne, [4]uint32{4, 16384, 0, 0}, 0x5000, "act")...)
	b = append(b, 0) // cache_flag
	b = append(b, u64(0)...)
	return append(b, datos...)
}

// floatsBytes serializa floats f32 little endian.
func floatsBytes(v []float32) []byte {
	b := make([]byte, 4*len(v))
	for i, x := range v {
		binary.LittleEndian.PutUint32(b[4*i:], math.Float32bits(x))
	}
	return b
}

// pesos devuelve un SET_TENSOR F32 grande (carga de pesos).
func pesos(rnd *rand.Rand, kb int) pedido {
	n := kb * 1024 / 4
	v := make([]float32, n)
	for i := range v {
		v[i] = float32(rnd.NormFloat64() * 0.02)
	}
	return pedido{cmd: 6, payload: setTensor(0, [4]uint32{uint32(n), 1, 1, 1}, floatsBytes(v))}
}

// activacion devuelve un SET_TENSOR F32 de activaciones de 4096 valores por token.
func activacion(rnd *rand.Rand, tokens int) pedido {
	n := 4096 * tokens
	v := make([]float32, n)
	for i := range v {
		v[i] = float32(rnd.NormFloat64())
	}
	return pedido{cmd: 6, payload: setTensor(0, [4]uint32{4096, uint32(tokens), 1, 1}, floatsBytes(v))}
}

// mascara devuelve un SET_TENSOR F32 con -inf (máscara de atención): va cruda.
func mascara(tokens int) pedido {
	n := 4096 * tokens
	v := make([]float32, n)
	for i := range v {
		v[i] = 0
		if i%7 == 3 {
			v[i] = float32(math.Inf(-1))
		}
	}
	return pedido{cmd: 6, payload: setTensor(0, [4]uint32{4096, uint32(tokens), 1, 1}, floatsBytes(v))}
}

// flujoSintetico arma un flujo de inferencia: carga de pesos y luego pasos con
// GRAPH_COMPUTE (3 formas alternadas), GRAPH_RECOMPUTE, activaciones, máscara y GET_TENSOR.
func flujoSintetico(rnd *rand.Rand, pesosKB, pasos, nodos int, conMascara bool) []byte {
	ps := []pedido{
		{cmd: 14, payload: nil}, // HELLO
		{cmd: 0, payload: append(u64(4096), u32(0)...)},
		pesos(rnd, pesosKB),
	}
	for p := 0; p < pasos; p++ {
		forma := p % 3
		ps = append(ps,
			pedido{cmd: 10, payload: grafo(forma, nodos, 0)},
			pedido{cmd: 16, payload: append(u32(0), u64(uint64(p))...)},
			activacion(rnd, []int{1, 2, 1}[forma]),
			pedido{cmd: 8, payload: append(rpcTensor(7, 0, [4]uint32{1, 1, 1, 1}, [4]uint32{}, 0, "t"), append(u64(0), u64(16)...)...)},
		)
		if conMascara && p%4 == 0 {
			ps = append(ps, mascara(1))
		}
	}
	return serializar(ps)
}

// trozos devuelve una función que da tamaños de corte aleatorios en [1, max].
func trozos(rnd *rand.Rand, max int) func() int {
	return func() int { return 1 + rnd.Intn(max) }
}

// trozoFijo devuelve un corte de tamaño constante.
func trozoFijo(n int) func() int { return func() int { return n } }
