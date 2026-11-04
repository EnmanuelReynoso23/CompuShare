import asyncio, sys, json
ESC, DST = int(sys.argv[1]), int(sys.argv[2])
c = {"subida": 0, "bajada": 0}
async def pipe(r, w, k):
    try:
        while (d := await r.read(1 << 20)):
            c[k] += len(d); w.write(d); await w.drain()
    finally:
        w.close()
async def h(r, w):
    r2, w2 = await asyncio.open_connection("127.0.0.1", DST)
    await asyncio.gather(pipe(r, w2, "subida"), pipe(r2, w, "bajada"))
async def volcar():
    while True:
        await asyncio.sleep(0.2); open("cuenta.json", "w").write(json.dumps(c))
async def main():
    s = await asyncio.start_server(h, "127.0.0.1", ESC); asyncio.create_task(volcar())
    async with s: await s.serve_forever()
asyncio.run(main())
