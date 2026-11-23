package rpccodec

import "container/list"

type entradaCache struct {
	hash    [tamHash]byte
	payload []byte
}

// cacheGrafos es un LRU de payloads de grafo. El frente de la lista es el más reciente.
// Maestro y trabajador ven la misma secuencia de grafos, así que sus cachés coinciden.
type cacheGrafos struct {
	lista *list.List
	mapa  map[[tamHash]byte]*list.Element
	bytes int
}

func nuevoCache() *cacheGrafos {
	return &cacheGrafos{lista: list.New(), mapa: make(map[[tamHash]byte]*list.Element)}
}

// obtener devuelve el payload y lo marca como el más reciente.
func (c *cacheGrafos) obtener(h [tamHash]byte) ([]byte, bool) {
	el, ok := c.mapa[h]
	if !ok {
		return nil, false
	}
	c.lista.MoveToFront(el)
	return el.Value.(*entradaCache).payload, true
}

// consultar devuelve el payload sin cambiar el orden (se usa para la base de un DELTA).
func (c *cacheGrafos) consultar(h [tamHash]byte) ([]byte, bool) {
	el, ok := c.mapa[h]
	if !ok {
		return nil, false
	}
	return el.Value.(*entradaCache).payload, true
}

// insertar agrega el grafo como el más reciente, o lo mueve al frente si ya existe,
// y saca los más viejos hasta respetar 32 entradas y 64 MiB.
func (c *cacheGrafos) insertar(h [tamHash]byte, payload []byte) {
	if el, ok := c.mapa[h]; ok {
		c.lista.MoveToFront(el)
		return
	}
	el := c.lista.PushFront(&entradaCache{hash: h, payload: payload})
	c.mapa[h] = el
	c.bytes += len(payload)
	for c.lista.Len() > cacheMaxEntradas || c.bytes > cacheMaxBytes {
		atras := c.lista.Back()
		if atras == nil || atras == el {
			break
		}
		e := c.lista.Remove(atras).(*entradaCache)
		delete(c.mapa, e.hash)
		c.bytes -= len(e.payload)
	}
}

// recientes devuelve hasta n entradas de longitud exacta, de la más reciente a la más vieja.
func (c *cacheGrafos) recientes(n int, longitud int) []*entradaCache {
	var out []*entradaCache
	for el := c.lista.Front(); el != nil && len(out) < n; el = el.Next() {
		e := el.Value.(*entradaCache)
		if len(e.payload) == longitud {
			out = append(out, e)
		}
	}
	return out
}
