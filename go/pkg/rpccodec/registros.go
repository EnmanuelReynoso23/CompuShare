// Package rpccodec implementa el códec rpc1 del tubo: reduce los bytes que cruzan el
// tubo maestro -> trabajador reconociendo el protocolo de ggml-rpc (llama.cpp b11434).
//
// La especificación de formato de cable está en docs/CODEC-RPC.md. Este paquete y su
// equivalente en Python (fabric/rpc_codec.py) deben producir y leer exactamente los
// mismos bytes.
package rpccodec

import (
	"bytes"
	"compress/zlib"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
)

// Tipos de registro en el sentido maestro -> trabajador.
const (
	RegistroRAW        byte = 0x00
	RegistroGrafoFull  byte = 0x01
	RegistroGrafoRef   byte = 0x02
	RegistroGrafoDelta byte = 0x03
	RegistroSetQ8      byte = 0x04
)

// Comandos de ggml-rpc que el códec interpreta.
const (
	CmdSetTensor    byte = 6
	CmdGraphCompute byte = 10
	// CmdLimite: un comando >= 64 hace que el codificador pase el resto de la conexión en crudo.
	CmdLimite byte = 64
)

// Tamaños del protocolo y límites de la caché y de los registros.
const (
	tamCabecera      = 9                    // cmd u8 + tamaño u64
	tamRPCTensor     = 296                  // sizeof(rpc_tensor), struct empaquetado (#pragma pack(1)) en b11434
	tamCabSetTensor  = tamRPCTensor + 1 + 8 // rpc_tensor | cache_flag u8 | offset u64 (= 305, b11434)
	tamHash          = 16                   // primeros 16 bytes de SHA-256
	tamGrafoMax      = 64 << 20             // grafos y SET_TENSOR candidatos a codificar: hasta 64 MiB
	trozoRaw         = 256 << 10            // tamaño máximo de un registro RAW
	cacheMaxEntradas = 32
	cacheMaxBytes    = 64 << 20
	deltaCandidatos  = 4
	q8MinFloats      = 1024
	q8Bloque         = 32
	tamBloqueQ8      = 2 + q8Bloque // d float16 + 32 int8
	tipoF32          = 0

	// MaxMensajeCodec es el límite de lectura de un mensaje WS del códec. Un registro
	// completo (grafo o SET_TENSOR) puede pesar hasta tamGrafoMax; se añade holgura.
	MaxMensajeCodec = 80 << 20
)

// Errores del decodificador.
var (
	ErrHashDesconocido     = errors.New("rpccodec: hash de grafo desconocido en la caché")
	ErrRegistroDesconocido = errors.New("rpccodec: tipo de registro desconocido")
	ErrMensajeCorrupto     = errors.New("rpccodec: mensaje corrupto o truncado")
)

// hashGrafo devuelve los primeros 16 bytes de SHA-256 del payload.
func hashGrafo(payload []byte) [tamHash]byte {
	s := sha256.Sum256(payload)
	var h [tamHash]byte
	copy(h[:], s[:tamHash])
	return h
}

// comprimir devuelve el formato zlib del payload con nivel rápido.
func comprimir(p []byte) []byte {
	var b bytes.Buffer
	w, _ := zlib.NewWriterLevel(&b, zlib.BestSpeed)
	_, _ = w.Write(p)
	_ = w.Close()
	return b.Bytes()
}

// descomprimir inflá z y exige exactamente n bytes de salida.
func descomprimir(z []byte, n uint64) ([]byte, error) {
	r, err := zlib.NewReader(bytes.NewReader(z))
	if err != nil {
		return nil, fmt.Errorf("%w: zlib: %v", ErrMensajeCorrupto, err)
	}
	out := make([]byte, n)
	if _, err := io.ReadFull(r, out); err != nil {
		return nil, fmt.Errorf("%w: zlib: %v", ErrMensajeCorrupto, err)
	}
	// La lectura final verifica la suma de comprobación y que no sobren bytes.
	var extra [1]byte
	k, err := r.Read(extra[:])
	if k > 0 || (err != nil && err != io.EOF) {
		return nil, fmt.Errorf("%w: zlib sobra o es inválido", ErrMensajeCorrupto)
	}
	return out, nil
}

// lector recorre un mensaje WS con comprobación de límites; el primer error queda guardado.
type lector struct {
	b   []byte
	i   int
	err error
}

func (l *lector) fin() bool { return l.i >= len(l.b) || l.err != nil }

func (l *lector) bytes(n uint64) []byte {
	if l.err != nil {
		return nil
	}
	if uint64(len(l.b)-l.i) < n {
		l.err = ErrMensajeCorrupto
		return nil
	}
	v := l.b[l.i : l.i+int(n)]
	l.i += int(n)
	return v
}

func (l *lector) u8() byte {
	b := l.bytes(1)
	if b == nil {
		return 0
	}
	return b[0]
}

func (l *lector) u32() uint64 {
	b := l.bytes(4)
	if b == nil {
		return 0
	}
	return uint64(binary.LittleEndian.Uint32(b))
}

func (l *lector) u64() uint64 {
	b := l.bytes(8)
	if b == nil {
		return 0
	}
	return binary.LittleEndian.Uint64(b)
}
