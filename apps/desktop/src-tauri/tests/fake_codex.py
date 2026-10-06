"""协议测试子进程：拆包、交错通知、超时与断开。"""
import json
import sys
import time

def send(value, split=False):
    line=json.dumps(value)+'\n'
    if split:
        sys.stdout.write(line[:5]);sys.stdout.flush();time.sleep(.02)
        line=line[5:]
    sys.stdout.write(line);sys.stdout.flush()

for line in sys.stdin:
    request=json.loads(line)
    method=request.get('method')
    if method=='initialize':send({'id':request['id'],'result':{}},True)
    elif method=='ping':
        send({'method':'test/notification','params':{'ok':True}})
        send({'id':request['id'],'result':{'pong':True}},True)
    elif method=='fail':send({'id':request['id'],'error':{'code':-1,'message':'expected failure'}})
    elif method=='die':sys.exit(0)
