package rpccodec

import (
	"encoding/binary"
	"math"
)

type estadoCodec int

const (
	estCabecera estadoCodec = iota // leyendo cmd + tamaño (9 bytes)
	estRaw                         // copiando payload en crudo, sin almacenarlo
	estBuffer                      // almacenando el payload para decidir su codificación
	estCrudo                       // tras un comando >= 64: todo el resto en crudo
)

// Codificador convierte el flujo TCP de pedidos ggml-rpc (maestro -> trabajador) en
// mensajes WebSocket con registros rpc1. No es concurrente: una goroutine por conexión.
type Codificador struct {
	q8    bool
	cache *cacheGrafos
	est   Estadisticas

	estado     estadoCodec
	cab        [tamCabecera]byte
	nCab       int
	cmd        byte
	total      uint64 // tamaño declarado del payload del pedido en curso
	restante   uint64 // estRaw: bytes de payload que faltan por llegar
	buf        []byte // estBuffer: payload acumulado
	tipoOK     bool   // SET_TENSOR: el tipo ya se revisó
	grafoVisto bool   // ya pasó un GRAPH_COMPUTE en la conexión

	rawAcum []byte // bytes en crudo pendientes de volcar como registros RAW
	salida  []byte // registros del mensaje en construcción
}

// NuevoCodificador crea un codificador para una conexión. q8 activa la cuantización
// Q8_0 (con pérdida) de SET_TENSOR de activaciones F32.
func NuevoCodificador(q8 bool) *Codificador {
	return &Codificador{q8: q8, cache: nuevoCache(), est: nuevasEstadisticas()}
}

// Alimentar recibe el siguiente trozo del TCP y devuelve un mensaje WebSocket con
// registros completos, o nil si no hay nada que enviar todavía. Cada mensaje contiene
// solo registros completos; un pedido grande sin codificar sale en registros RAW.
func (c *Codificador) Alimentar(p []byte) []byte {
	c.est.Entrada += uint64(len(p))
	for len(p) > 0 {
		switch c.estado {
		case estCabecera:
			n := copy(c.cab[c.nCab:], p)
			c.nCab += n
			p = p[n:]
			if c.nCab == tamCabecera {
				c.inicioPedido()
			}
		case estRaw:
			n := min(uint64(len(p)), c.restante)
			c.addRaw(p[:n], c.cmd)
			c.restante -= n
			p = p[n:]
			if c.restante == 0 {
				c.reiniciarCabecera()
			}
		case estBuffer:
			n := min(uint64(len(p)), c.total-uint64(len(c.buf)))
			c.buf = append(c.buf, p[:n]...)
			p = p[n:]
			if c.cmd == CmdSetTensor && !c.tipoOK && len(c.buf) >= 12 {
				c.tipoOK = true
				if binary.LittleEndian.Uint32(c.buf[8:12]) != tipoF32 || !c.q8 {
					c.pasarBufferACrudo()
					break
				}
			}
			if uint64(len(c.buf)) == c.total {
				c.finBuffer()
				c.reiniciarCabecera()
			}
		case estCrudo:
			c.addRaw(p, 0xFF)
			p = nil
		}
	}
	c.volcarRaw()
	if len(c.salida) == 0 {
		return nil
	}
	out := c.salida
	c.salida = nil
	c.est.Salida += uint64(len(out))
	return out
}

// Estadisticas devuelve una copia de las cifras acumuladas.
func (c *Codificador) Estadisticas() Estadisticas {
	return c.est.copiar()
}

func (c *Codificador) reiniciarCabecera() {
	c.estado = estCabecera
	c.nCab = 0
	c.buf = nil
}

// inicioPedido se llama con la cabecera completa y decide cómo tratar el pedido.
func (c *Codificador) inicioPedido() {
	cmd := c.cab[0]
	tam := binary.LittleEndian.Uint64(c.cab[1:tamCabecera])
	if cmd >= CmdLimite {
		c.estado = estCrudo
		c.addRaw(c.cab[:], 0xFF)
		return
	}
	sumar(c.est.Comandos, cmd, 1, tamCabecera+tam, 0)
	c.cmd = cmd
	c.total = tam
	if cmd == CmdGraphCompute {
		c.grafoVisto = true
	}

	switch {
	case tam == 0:
		c.addRaw(c.cab[:], cmd)
		c.reiniciarCabecera()
	case cmd == CmdGraphCompute && tam <= tamGrafoMax:
		c.iniciarBuffer()
	case cmd == CmdSetTensor && c.q8 && c.grafoVisto && tam >= tamCabSetTensor+4*q8MinFloats &&
		tam <= tamGrafoMax && (tam-tamCabSetTensor)%4 == 0:
		c.iniciarBuffer()
	default:
		c.addRaw(c.cab[:], cmd)
		c.estado = estRaw
		c.restante = tam
	}
}

func (c *Codificador) iniciarBuffer() {
	c.estado = estBuffer
	c.buf = make([]byte, 0, c.total)
	c.tipoOK = false
}

// pasarBufferACrudo vuelca cabecera y lo ya almacenado en crudo y sigue en modo RAW.
func (c *Codificador) pasarBufferACrudo() {
	c.addRaw(c.cab[:], c.cmd)
	c.addRaw(c.buf, c.cmd)
	c.restante = c.total - uint64(len(c.buf))
	c.buf = nil
	c.estado = estRaw
}

// finBuffer codifica un payload completo; si no compensa, lo manda en crudo.
func (c *Codificador) finBuffer() {
	var rec []byte
	var ok bool
	if c.cmd == CmdGraphCompute {
		rec, ok = c.codificarGrafo(c.buf)
	} else {
		rec, ok = c.codificarQ8(c.buf)
	}
	if !ok {
		c.addRaw(c.cab[:], c.cmd)
		c.addRaw(c.buf, c.cmd)
		c.buf = nil
		return
	}
	c.emitir(rec, c.cmd, uint64(tamCabecera+len(c.buf)))
	c.buf = nil
}

// codificarGrafo elige REF, DELTA o FULL para un grafo. Devuelve false si el crudo gana.
func (c *Codificador) codificarGrafo(payload []byte) ([]byte, bool) {
	h := hashGrafo(payload)
	if _, ok := c.cache.obtener(h); ok {
		rec := make([]byte, 0, 1+tamHash)
		rec = append(rec, RegistroGrafoRef)
		rec = append(rec, h[:]...)
		return rec, true
	}

	n := len(payload)
	tamMejor := 5 + tamCabecera + n // registro RAW equivalente
	var mejor []byte

	z := comprimir(payload)
	if rec := registroFull(n, z); len(rec) < tamMejor {
		mejor, tamMejor = rec, len(rec)
	}
	for _, base := range c.cache.recientes(deltaCandidatos, n) {
		x := make([]byte, n)
		for i := range x {
			x[i] = payload[i] ^ base.payload[i]
		}
		if rec := registroDelta(base.hash, n, comprimir(x)); len(rec) < tamMejor {
			mejor, tamMejor = rec, len(rec)
		}
	}
	if mejor == nil {
		return nil, false
	}
	c.cache.insertar(h, payload)
	return mejor, true
}

func registroFull(n int, z []byte) []byte {
	rec := make([]byte, 0, 1+4+4+len(z))
	rec = append(rec, RegistroGrafoFull)
	rec = binary.LittleEndian.AppendUint32(rec, uint32(n))
	rec = binary.LittleEndian.AppendUint32(rec, uint32(len(z)))
	return append(rec, z...)
}

func registroDelta(base [tamHash]byte, n int, z []byte) []byte {
	rec := make([]byte, 0, 1+tamHash+4+4+len(z))
	rec = append(rec, RegistroGrafoDelta)
	rec = append(rec, base[:]...)
	rec = binary.LittleEndian.AppendUint32(rec, uint32(n))
	rec = binary.LittleEndian.AppendUint32(rec, uint32(len(z)))
	return append(rec, z...)
}

// codificarQ8 cuantiza un SET_TENSOR F32 de activaciones a bloques Q8_0 de 32 valores.
// Devuelve false si no se puede (tipo, valores no finitos, escala fuera de float16).
func (c *Codificador) codificarQ8(payload []byte) ([]byte, bool) {
	if binary.LittleEndian.Uint32(payload[8:12]) != tipoF32 {
		return nil, false
	}
	if (len(payload)-tamCabSetTensor)%4 != 0 {
		return nil, false
	}
	n := (len(payload) - tamCabSetTensor) / 4
	vals := payload[tamCabSetTensor:]
	nb := (n + q8Bloque - 1) / q8Bloque

	rec := make([]byte, 0, 1+4+tamCabSetTensor+8+nb*tamBloqueQ8)
	rec = append(rec, RegistroSetQ8)
	rec = binary.LittleEndian.AppendUint32(rec, tamCabSetTensor)
	rec = append(rec, payload[:tamCabSetTensor]...)
	rec = binary.LittleEndian.AppendUint64(rec, uint64(n))

	for b := 0; b < nb; b++ {
		ini := b * q8Bloque
		fin := min(ini+q8Bloque, n)
		var amax float32
		for i := ini; i < fin; i++ {
			bits := binary.LittleEndian.Uint32(vals[4*i:])
			if (bits>>23)&0xff == 0xff { // NaN o infinito: esa activación va cruda
				return nil, false
			}
			if a := math.Float32frombits(bits &^ 0x80000000); a > amax {
				amax = a
			}
		}
		d := amax / 127
		dh := F32ToF16(d)
		if dh&0x7c00 == 0x7c00 { // la escala no cabe en float16
			return nil, false
		}
		rec = binary.LittleEndian.AppendUint16(rec, dh)
		for i := 0; i < q8Bloque; i++ {
			var q int8
			if j := ini + i; j < fin && d != 0 {
				x := math.Float32frombits(binary.LittleEndian.Uint32(vals[4*j:])) / d
				r := math.Round(float64(x)) // redondeo de medio hacia fuera de cero
				if r > 127 {
					r = 127
				} else if r < -127 {
					r = -127
				}
				q = int8(r)
			}
			rec = append(rec, byte(q))
		}
	}
	return rec, true
}

// addRaw añade bytes en crudo al mensaje; se volcarán como registros RAW.
func (c *Codificador) addRaw(p []byte, cmd byte) {
	if len(p) == 0 {
		return
	}
	c.rawAcum = append(c.rawAcum, p...)
	sumar(c.est.Comandos, cmd, 0, 0, uint64(len(p)))
}

// volcarRaw convierte los bytes en crudo pendientes en registros RAW de hasta 256 KiB.
func (c *Codificador) volcarRaw() {
	for off := 0; off < len(c.rawAcum); off += trozoRaw {
		fin := min(off+trozoRaw, len(c.rawAcum))
		trozo := c.rawAcum[off:fin]
		c.salida = append(c.salida, RegistroRAW)
		c.salida = binary.LittleEndian.AppendUint32(c.salida, uint32(len(trozo)))
		c.salida = append(c.salida, trozo...)
		sumar(c.est.Registros, RegistroRAW, 1, uint64(len(trozo)), uint64(5+len(trozo)))
	}
	c.rawAcum = nil
}

// emitir añade un registro codificado, respetando el orden con el crudo pendiente.
func (c *Codificador) emitir(rec []byte, cmd byte, crudo uint64) {
	c.volcarRaw()
	c.salida = append(c.salida, rec...)
	sumar(c.est.Registros, rec[0], 1, crudo, uint64(len(rec)))
	sumar(c.est.Comandos, cmd, 0, 0, uint64(len(rec)))
}
