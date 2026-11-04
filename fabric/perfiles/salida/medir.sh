#!/bin/bash
# uso: medir.sh NGL
B=~/.local/opt/llama.cpp/build/bin; M=~/.cache/fabric-descargas/modelos/Qwen3.5-0.8B-Q8_0.gguf
$B/ggml-rpc-server -H 127.0.0.1 -p 58952 -t 4 >/dev/null 2>&1 & R=$!
python3 contar.py 58953 58952 & C=$!; sleep 1
$B/llama-server -m $M --rpc 127.0.0.1:58953 -ngl $1 $OT -t 4 --port 58954 -c 2048 -ctxcp 0 --cache-ram 0 >server.log 2>&1 & S=$!
for i in $(seq 120); do curl -s -m 120 localhost:58954/health | grep -q ok && break; sleep 1; done
q() { curl -s -m 120 localhost:58954/completion -d "{\"prompt\":\"La capital de Francia es\",\"n_predict\":$1,\"temperature\":0,\"ignore_eos\":true,\"cache_prompt\":false}" | python3 -c "import json,sys;d=json.load(sys.stdin)['timings'];print(round(d['predicted_per_second'],1))"; }
q 8 >/dev/null; sleep 0.5; a=$(cat cuenta.json); t16=$(q 16); sleep 0.5; b=$(cat cuenta.json); t80=$(q 80); sleep 0.5; c=$(cat cuenta.json)
python3 -c "
import json;a,b,c=json.loads('$a'),json.loads('$b'),json.loads('$c')
for k in ('subida','bajada'): print('ngl $1', k, round(((c[k]-b[k])-(b[k]-a[k]))/64/1000,1), 'KB/palabra')
print('ngl $1 tok/s', '$t80')"
grep -m3 -iE "offloaded|output layer|RPC0.*buffer size|CPU_Mapped model buffer" server.log
cp server.log server-$1.log; kill $S $C $R; wait 2>/dev/null
