"""A loopback-only fake backend. No real model/GPU is used."""
import argparse
import asyncio
import json
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse, PlainTextResponse, Response

app = FastAPI()
received = 0
received_models = []
gates = {}
active_transfers = {}
transfer_calls = {}

@app.get('/__transfers')
async def transfers():
    return {'active': list(active_transfers), 'calls': transfer_calls}

@app.post('/api/pull')
@app.post('/api/push')
async def transfer(request: Request):
    data = await request.json()
    model = data.get('model') or data.get('name', '')
    if model == 'reject-transfer':
        return JSONResponse({'error': 'model not found'}, status_code=404)
    async def chunks():
        transfer_calls[model] = transfer_calls.get(model, 0) + 1
        active_transfers[model] = True
        try:
            yield b'{"status":"pulling manifest"}\n'
            if model.startswith('held-progress-'):
                yield b'{"status":"pulling fixture","digest":"fixture","completed":983,"total":1000}\n'
            if model.startswith('held-'):
                await gates.setdefault(model, asyncio.Event()).wait()
            yield b'{"status":"success"}\n'
        finally:
            active_transfers.pop(model, None)
    if data.get('stream') is False:
        async for _ in chunks():
            pass
        return {'status': 'success'}
    return StreamingResponse(chunks(), media_type='application/x-ndjson',
                             headers={'X-Test-Transfer': 'preserved'})

@app.post('/__release/{model}')
async def release(model: str):
    gates.setdefault(model, asyncio.Event()).set()
    return {'released': model}

@app.api_route('/',methods=['GET','HEAD'])
async def root(): return PlainTextResponse('Ollama is running')

@app.options('/api/chat')
async def options(): return Response(status_code=204,headers={'Access-Control-Allow-Origin':'http://localhost'})

@app.get('/v1/models/{model}')
async def model_detail(request: Request,model: str):
    return {'query':request.url.query,'auth':request.headers.get('authorization')}

@app.post('/api/blobs/{digest}')
async def upload(request: Request,digest: str):
    return JSONResponse({'bytes':len(await request.body())},status_code=201)

@app.post('/api/create')
@app.post('/api/copy')
@app.delete('/api/delete')
async def manage(): return {'status':'success'}

@app.get('/api/version')
async def version(): return {'version': 'test-only'}

@app.get('/api/tags')
@app.get('/api/ps')
async def models(): return {'models': [{'name': 'simulated:1b'}]}

@app.get('/__received')
async def count(): return {'count': received,'models':received_models}

@app.post('/api/show')
async def show(): return {'details': {'family': 'simulated'}}

async def run(request: Request):
    global received
    received += 1
    data = await request.json()
    model = data.get('model')
    received_models.append(model)
    if model and model.startswith('held-'):
        await gates.setdefault(model, asyncio.Event()).wait()
    path = request.url.path
    if model == 'reject': return JSONResponse({'error': 'model not found'}, status_code=404)
    if model == 'delay-headers': await asyncio.sleep(1.5)
    if path == '/api/embed':
        await asyncio.sleep(.2)
        return {'embeddings': [[0.1, 0.2]]}
    if path == '/api/embeddings': return {'embedding': [0.1]}
    if path == '/v1/embeddings': return {'data': [{'embedding': [0.1]}]}
    streaming = data.get('stream', not path.startswith('/v1/'))
    if not streaming:
        if model == 'slow': await asyncio.sleep(1.4)
        if model == 'invalid': return {'response': 'missing done'}
        if path == '/v1/responses': return {'status':'completed','output':[]}
        if path == '/v1/messages': return {'type':'message','stop_reason':'end_turn','content':[]}
        if path.startswith('/v1/'): return {'choices': [{'text': 'ok', 'finish_reason': 'stop'}]}
        return {'model': model, 'response': 'ok', 'done': True}
    async def chunks():
        if path == '/v1/responses':
            yield b'event: response.completed\ndata: {"type":"response.completed"}\n\n'
            return
        if path == '/v1/messages':
            yield b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
            return
        if path.startswith('/v1/'):
            yield b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            await asyncio.sleep(.2)
            yield b'data: [DONE]\n\n'
            return
        yield b'{"response":"start","done":false}\n'
        await asyncio.sleep(1.4 if model in ('slow','truncated') else .2)
        if model == 'hang': await asyncio.sleep(30)
        if model == 'truncated': return
        if model == 'malformed': yield b'not-json\n'; return
        if model == 'error-stream': yield b'{"error":"simulated runner error"}\n'; return
        # Split terminal marker across TCP chunks to exercise parsing.
        yield b'{"response":"end","do'
        await asyncio.sleep(.05)
        yield b'ne":true}\n'
    return StreamingResponse(chunks(), media_type='text/event-stream' if path.startswith('/v1/') else 'application/x-ndjson')

for path in ('/api/chat','/api/generate','/api/embed','/api/embeddings','/v1/chat/completions','/v1/completions','/v1/embeddings','/v1/responses','/v1/messages'):
    app.add_api_route(path, run, methods=['POST'])

if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--port', type=int, required=True)
    uvicorn.run(app, host='127.0.0.1', port=parser.parse_args().port, access_log=False)
