package tubo

import (
	"context"
	"log"
	"net"
	"sync/atomic"

	"github.com/coder/websocket"

	"github.com/EnmanuelReynoso23/CompuShare/go/pkg/rpccodec"
)

// Canalizar conecta bidireccionalmente un net.Conn (con TCP_NODELAY) y un websocket.Conn,
// sin códec. Lee y escribe tramas binarias de hasta 256 KB sin copias innecesarias ni base64.
// Cuando cualquier lado se cierra o falla, cierra ambos extremos limpiamente.
func Canalizar(ctx context.Context, ws *websocket.Conn, tcpConn net.Conn, contadorBytes *atomic.Int64) {
	CanalizarCodec(ctx, ws, tcpConn, contadorBytes, nil, nil)
}

// CanalizarCodec es Canalizar con códec rpc1 opcional. Si codifica no es nil, el sentido
// TCP -> WebSocket pasa por el codificador (relé). Si decodifica no es nil, el sentido
// WebSocket -> TCP pasa por el decodificador (trabajador). El sentido no cubierto va crudo.
// El contador cuenta los bytes que cruzan el WebSocket.
func CanalizarCodec(ctx context.Context, ws *websocket.Conn, tcpConn net.Conn, contadorBytes *atomic.Int64,
	codifica *rpccodec.Codificador, decodifica *rpccodec.Decodificador) {
	if tc, ok := tcpConn.(*net.TCPConn); ok {
		_ = tc.SetNoDelay(true)
	}

	wsConn := websocket.NetConn(ctx, ws, websocket.MessageBinary)
	defer wsConn.Close()
	defer tcpConn.Close()

	fin := make(chan struct{}, 2)

	// TCP -> WebSocket binario
	go func() {
		buf := make([]byte, 256*1024)
		for {
			n, err := tcpConn.Read(buf)
			if n > 0 {
				if codifica != nil {
					if msg := codifica.Alimentar(buf[:n]); len(msg) > 0 {
						if contadorBytes != nil {
							contadorBytes.Add(int64(len(msg)))
						}
						if werr := ws.Write(ctx, websocket.MessageBinary, msg); werr != nil {
							break
						}
					}
				} else {
					if contadorBytes != nil {
						contadorBytes.Add(int64(n))
					}
					if _, werr := wsConn.Write(buf[:n]); werr != nil {
						break
					}
				}
			}
			if err != nil {
				break
			}
		}
		fin <- struct{}{}
	}()

	// WebSocket binario -> TCP
	go func() {
		if decodifica != nil {
			// Cada mensaje del códec contiene registros completos: se lee mensaje a mensaje.
			for {
				_, msg, err := ws.Read(ctx)
				if err != nil {
					break
				}
				if contadorBytes != nil {
					contadorBytes.Add(int64(len(msg)))
				}
				out, derr := decodifica.Decodificar(msg)
				if derr != nil {
					log.Printf("códec rpc1: %v; se cierra el canal", derr)
					break
				}
				if len(out) > 0 {
					if _, werr := tcpConn.Write(out); werr != nil {
						break
					}
				}
			}
		} else {
			buf := make([]byte, 256*1024)
			for {
				n, err := wsConn.Read(buf)
				if n > 0 {
					if contadorBytes != nil {
						contadorBytes.Add(int64(n))
					}
					if _, werr := tcpConn.Write(buf[:n]); werr != nil {
						break
					}
				}
				if err != nil {
					break
				}
			}
		}
		fin <- struct{}{}
	}()

	<-fin
}
