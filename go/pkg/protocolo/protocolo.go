package protocolo

import "regexp"

var (
	EnjambreValido = regexp.MustCompile(`^[a-z0-9-]{1,40}$`)
	IDValido       = regexp.MustCompile(`^[A-Za-z0-9_-]{4,48}$`)
)

const (
	MaxMensajeBytes = 8 * 1024 * 1024
	TuboTrozo       = 256 * 1024
	MensajesPorSala = 50
	TextoMax        = 8000
)

var TiposDeTarea = map[string]struct{}{
	"eco":       {},
	"generar":   {},
	"embeber":   {},
	"reordenar": {},
}

// MensajeBase para inspeccionar el tipo 't'.
type MensajeBase struct {
	T string `json:"t"`
}

// Hola enviado por nodos o clientes al conectarse.
type Hola struct {
	T           string         `json:"t"`
	Clave       string         `json:"clave"`
	Enjambre    string         `json:"enjambre"`
	Rol         string         `json:"rol"`
	ID          string         `json:"id,omitempty"`
	Nombre      string         `json:"nombre,omitempty"`
	Capacidades []string       `json:"capacidades,omitempty"`
	Info        map[string]any `json:"info,omitempty"`
}

// NodoPublico representa el estado visible de un nodo.
type NodoPublico struct {
	ID          string         `json:"id"`
	Nombre      string         `json:"nombre"`
	Capacidades []string       `json:"capacidades"`
	Info        map[string]any `json:"info"`
	Carga       int            `json:"carga"`
	Completadas int            `json:"completadas"`
}

// MensajeChat representa un mensaje en la sala de chat.
type MensajeChat struct {
	ID        string `json:"id"`
	Creado    string `json:"creado"`
	Texto     string `json:"texto"`
	De        string `json:"de"`
	Estado    string `json:"estado"`
	Respuesta string `json:"respuesta,omitempty"`
	Por       string `json:"por,omitempty"`
	MS        *int64 `json:"ms,omitempty"`
}

// Bienvenido respuesta del relé tras hola válido.
type Bienvenido struct {
	T        string        `json:"t"`
	ID       string        `json:"id"`
	Nodos    []NodoPublico `json:"nodos"`
	Mensajes []MensajeChat `json:"mensajes"`
}

// Rechazo respuesta de error antes de cerrar conexión.
type Rechazo struct {
	T      string `json:"t"`
	Motivo string `json:"motivo"`
}

// NodosMsg difunde la lista actualizada de nodos.
type NodosMsg struct {
	T     string        `json:"t"`
	Nodos []NodoPublico `json:"nodos"`
}

// Latido enviado periódicamente.
type Latido struct {
	T     string `json:"t"`
	Carga *int   `json:"carga,omitempty"`
}

// TareaCliente petición de tarea enviada por un cliente al relé.
type TareaCliente struct {
	T     string `json:"t"`
	ID    string `json:"id"`
	Tipo  string `json:"tipo"`
	Datos any    `json:"datos,omitempty"`
	Para  string `json:"para,omitempty"`
}

// TareaNodo enviada por el relé al nodo asignado.
type TareaNodo struct {
	T     string `json:"t"`
	ID    string `json:"id"`
	Tipo  string `json:"tipo"`
	Datos any    `json:"datos,omitempty"`
	De    string `json:"de"`
}

// DevolucionNodo enviada por el nodo con avance, resultado o error.
type DevolucionNodo struct {
	T       string `json:"t"`
	ID      string `json:"id"`
	Datos   any    `json:"datos,omitempty"`
	Mensaje string `json:"mensaje,omitempty"`
}

// DevolucionCliente enviada por el relé al cliente original.
type DevolucionCliente struct {
	T       string `json:"t"`
	ID      string `json:"id"`
	Nodo    string `json:"nodo"`
	Datos   any    `json:"datos,omitempty"`
	Mensaje string `json:"mensaje,omitempty"`
}

// Cancelar enviada para cancelar una tarea.
type Cancelar struct {
	T  string `json:"t"`
	ID string `json:"id"`
}

// MensajeChatEnvio enviado por cliente para escribir en la sala.
type MensajeChatEnvio struct {
	T     string `json:"t"`
	Texto string `json:"texto"`
}

// MensajeChatDifusion enviado por relé a la sala.
type MensajeChatDifusion struct {
	T       string      `json:"t"`
	Mensaje MensajeChat `json:"mensaje"`
}

// ResponderChat enviado por cliente para cerrar/responder un mensaje.
type ResponderChat struct {
	T         string `json:"t"`
	MensajeID string `json:"mensaje_id"`
	Respuesta string `json:"respuesta"`
	Estado    string `json:"estado,omitempty"`
	Por       string `json:"por,omitempty"`
	MS        *int64 `json:"ms,omitempty"`
}

// Ofrezco enviado por el tubo trabajador para ofrecer un puerto.
type Ofrezco struct {
	T      string         `json:"t"`
	Clave  string         `json:"clave"`
	Nombre string         `json:"nombre"`
	Info   map[string]any `json:"info,omitempty"`
}

// Listo enviado por el relé cuando el tubo está preparado.
type Listo struct {
	T      string `json:"t"`
	Puerto int    `json:"puerto"`
}

// Abre enviado por el relé para que el trabajador conecte un canal de datos.
type Abre struct {
	T  string `json:"t"`
	ID string `json:"id"`
}

// Datos enviado por el trabajador al abrir el WebSocket de datos para un ID.
type Datos struct {
	T     string `json:"t"`
	Clave string `json:"clave"`
	ID    string `json:"id"`
}
