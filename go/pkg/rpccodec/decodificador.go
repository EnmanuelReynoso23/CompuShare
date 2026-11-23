package rpccodec

import (
	"encoding/binary"
	"fmt"
	"math"
)

// Decodificador convierte cada mensaje WebSocket rpc1 en los bytes exactos que
// ggml-rpc espera por TCP. Mantiene la caché de grafos igual que el codificador.
type Decodificador struct {
	cache *cacheGrafos
}

// NuevoDecodificador crea un decodificador para una conexión.
func NuevoDecodificador() *Decodificador {
	return &Decodificador{cache: nuevoCache()}
}

// Decodificar procesa un mensaje completo y devuelve los bytes para el TCP. Un registro
// nunca se parte entre mensajes, así que cada mensaje se decodifica por sí solo. Ante un
// hash desconocido, un tipo desconocido o un mensaje truncado devuelve error.
func (d *Decodificador) Decodificar(msg []byte) ([]byte, error) {
	l := &lector{b: msg}
	out := make([]byte, 0, len(msg))
	for !l.fin() {
		tipo := l.u8()
		if l.err != nil {
			break
		}
		switch tipo {
		case RegistroRAW:
			n := l.u32()
			out = append(out, l.bytes(n)...)

		case RegistroGrafoFull:
			n := l.u32()
			zl := l.u32()
			z := l.bytes(zl)
			if l.err != nil {
				break
			}
			if n > tamGrafoMax {
				return nil, ErrMensajeCorrupto
			}
			payload, err := descomprimir(z, n)
			if err != nil {
				return nil, err
			}
			out = agregarGrafo(out, payload)
			d.cache.insertar(hashGrafo(payload), payload)

		case RegistroGrafoRef:
			var h [tamHash]byte
			copy(h[:], l.bytes(tamHash))
			if l.err != nil {
				break
			}
			payload, ok := d.cache.obtener(h)
			if !ok {
				return nil, ErrHashDesconocido
			}
			out = agregarGrafo(out, payload)

		case RegistroGrafoDelta:
			var base [tamHash]byte
			copy(base[:], l.bytes(tamHash))
			n := l.u32()
			zl := l.u32()
			z := l.bytes(zl)
			if l.err != nil {
				break
			}
			baseP, ok := d.cache.consultar(base)
			if !ok {
				return nil, ErrHashDesconocido
			}
			if uint64(len(baseP)) != n {
				return nil, fmt.Errorf("%w: base de longitud %d, se esperaba %d", ErrMensajeCorrupto, len(baseP), n)
			}
			if n > tamGrafoMax {
				return nil, ErrMensajeCorrupto
			}
			payload, err := descomprimir(z, n)
			if err != nil {
				return nil, err
			}
			for i := range payload {
				payload[i] ^= baseP[i]
			}
			out = agregarGrafo(out, payload)
			d.cache.insertar(hashGrafo(payload), payload)

		case RegistroSetQ8:
			hl := l.u32()
			hdr := l.bytes(hl)
			n := l.u64()
			if l.err != nil {
				break
			}
			// Cada 32 valores ocupan 34 bytes, así que n no puede superar el largo del mensaje.
			if n > uint64(len(msg)) {
				return nil, ErrMensajeCorrupto
			}
			nb := (n + q8Bloque - 1) / q8Bloque
			bloques := l.bytes(nb * tamBloqueQ8)
			if l.err != nil {
				break
			}
			out = append(out, CmdSetTensor)
			out = binary.LittleEndian.AppendUint64(out, hl+4*n)
			out = append(out, hdr...)
			for i := uint64(0); i < n; i++ {
				blk := bloques[(i/q8Bloque)*tamBloqueQ8:]
				x := F16ToF32(binary.LittleEndian.Uint16(blk)) * float32(int8(blk[2+i%q8Bloque]))
				out = binary.LittleEndian.AppendUint32(out, math.Float32bits(x))
			}

		default:
			return nil, fmt.Errorf("%w: 0x%02x", ErrRegistroDesconocido, tipo)
		}
		if l.err != nil {
			return nil, l.err
		}
	}
	if l.err != nil {
		return nil, l.err
	}
	return out, nil
}

// agregarGrafo escribe un grafo como pedido GRAPH_COMPUTE completo: cmd + tamaño + payload.
func agregarGrafo(out []byte, payload []byte) []byte {
	out = append(out, CmdGraphCompute)
	out = binary.LittleEndian.AppendUint64(out, uint64(len(payload)))
	return append(out, payload...)
}
