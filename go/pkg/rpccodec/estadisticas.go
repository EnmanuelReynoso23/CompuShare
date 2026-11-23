package rpccodec

import "fmt"

// Contador acumula pedidos o registros y sus bytes.
type Contador struct {
	N       uint64
	Crudo   uint64 // bytes de ggml-rpc que representan (9 + tamaño por pedido)
	Enviado uint64 // bytes que salen por el WebSocket
}

// Estadisticas de un codificador. Por comando: Crudo es 9 + tamaño de cada pedido y
// Enviado los bytes de payload atribuidos a ese comando (sin la cabecera de sus
// registros RAW, que aparece en Registros[RegistroRAW]). Por registro: Crudo son los
// bytes de ggml-rpc que sustituye y Enviado el tamaño del registro en el cable.
type Estadisticas struct {
	Entrada   uint64 // bytes recibidos del TCP
	Salida    uint64 // bytes enviados como mensajes WS
	Comandos  map[byte]Contador
	Registros map[byte]Contador
}

// Ahorro devuelve la fracción de bytes ahorrada: 1 - Salida/Entrada.
func (e Estadisticas) Ahorro() float64 {
	if e.Entrada == 0 {
		return 0
	}
	return 1 - float64(e.Salida)/float64(e.Entrada)
}

// String resume las cifras en una línea y el detalle por registro.
func (e Estadisticas) String() string {
	s := fmt.Sprintf("rpc1: crudo %d B, enviado %d B (ahorro %.1f %%)", e.Entrada, e.Salida, 100*e.Ahorro())
	for _, t := range []byte{RegistroRAW, RegistroGrafoFull, RegistroGrafoRef, RegistroGrafoDelta, RegistroSetQ8} {
		if c, ok := e.Registros[t]; ok {
			s += fmt.Sprintf("; reg 0x%02x x%d (%d -> %d B)", t, c.N, c.Crudo, c.Enviado)
		}
	}
	return s
}

func nuevasEstadisticas() Estadisticas {
	return Estadisticas{
		Comandos:  make(map[byte]Contador),
		Registros: make(map[byte]Contador),
	}
}

func sumar(m map[byte]Contador, k byte, n, crudo, enviado uint64) {
	c := m[k]
	c.N += n
	c.Crudo += crudo
	c.Enviado += enviado
	m[k] = c
}

// copiar devuelve una copia independiente de las estadísticas.
func (e Estadisticas) copiar() Estadisticas {
	out := Estadisticas{Entrada: e.Entrada, Salida: e.Salida,
		Comandos: make(map[byte]Contador, len(e.Comandos)), Registros: make(map[byte]Contador, len(e.Registros))}
	for k, v := range e.Comandos {
		out.Comandos[k] = v
	}
	for k, v := range e.Registros {
		out.Registros[k] = v
	}
	return out
}
