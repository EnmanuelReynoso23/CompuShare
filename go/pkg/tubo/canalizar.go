package tubo

import (
	"context"
	"net"
	"sync/atomic"

	"github.com/coder/websocket"
)

// Canalizar conecta bidireccionalmente un net.Conn (con TCP_NODELAY) y un websocket.Conn.
// Lee y escribe tramas binarias de hasta 256 KB sin copias innecesarias ni base64.
// Cuando cualquier lado se cierra o falla, cierra ambos extremos limpiamente.
func Canalizar(ctx context.Context, ws *websocket.Conn, tcpConn net.Conn, contadorBytes *atomic.Int64) {
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
				if contadorBytes != nil {
					contadorBytes.Add(int64(n))
				}
				_, werr := wsConn.Write(buf[:n])
				if werr != nil {
					break
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
		buf := make([]byte, 256*1024)
		for {
			n, err := wsConn.Read(buf)
			if n > 0 {
				if contadorBytes != nil {
					contadorBytes.Add(int64(n))
				}
				_, werr := tcpConn.Write(buf[:n])
				if werr != nil {
					break
				}
			}
			if err != nil {
				break
			}
		}
		fin <- struct{}{}
	}()

	<-fin
}
