#!/usr/bin/env python3
"""Minimaler Guacamole-Client: spricht guacd an und protokolliert, was zurueckkommt."""
import socket, sys, time

def enc(*parts):
    return (",".join("%d.%s" % (len(p.encode()), p) for p in parts) + ";").encode()

class Reader:
    def __init__(self, s): self.s=s; self.buf=""
    def inst(self, timeout=15):
        self.s.settimeout(timeout)
        while ";" not in self.buf:
            d=self.s.recv(65536)
            if not d: return None
            self.buf+=d.decode("utf-8","replace")
        raw,self.buf=self.buf.split(";",1)
        raw=raw.strip()
        if not raw:
            return ["(leer)"]
        if raw.find(".") < 0 or not raw.split(".",1)[0].isdigit():
            print("ROH:", repr(raw[:200]))
            return ["(roh)"]
        parts=[]; i=0
        while i < len(raw):
            j=raw.find(".", i)
            if j < 0:
                break
            n=int(raw[i:j])
            parts.append(raw[j+1:j+1+n]); i=j+1+n+1
        return parts

params=dict(p.split("=",1) for p in sys.argv[1:])
s=socket.create_connection(("127.0.0.1",4822),10)
r=Reader(s)
s.sendall(enc("select","rdp"))
args=r.inst()
names=args[1:]
s.sendall(enc("size","1280","800","96")); s.sendall(enc("audio","audio/L16")); s.sendall(enc("video")); s.sendall(enc("image","image/png","image/jpeg"))
values=[params.get(n,"") for n in names]
if names and names[0] == "VERSION_1_1_0": values[0]="VERSION_1_1_0"
s.sendall(enc("connect",*values))
print("gesendete Parameter:", {n:("***" if n=="password" else v) for n,v in zip(names,values) if v})
drawing=0; seen={}; streams={}; t0=time.time()
try:
    while time.time()-t0 < 20:
        i=r.inst(25)
        if i is None: print("Verbindung von guacd geschlossen"); break
        op=i[0]
        if op=="blob" and len(i)>2:
            import base64
            streams.setdefault(i[1], b"")
            try: streams[i[1]] += base64.b64decode(i[2])
            except Exception: pass
        if op=="end" and len(i)>1 and i[1] in streams:
            open("/tmp/guac_img_%s.png"%i[1],"wb").write(streams.pop(i[1]))
        if op=="png" and len(i)>5:
            import base64
            try:
                data=base64.b64decode(i[5])
                open("/tmp/guac_frame_%d.png"%drawing,"wb").write(data)
            except Exception: pass
        if op=="sync":
            s.sendall(enc("sync", i[1] if len(i)>1 else "0"))  # enc() setzt die Laengen selbst
        if op in ("png","cfill","copy","rect","img","blob","end","sync","cursor","size"):
            drawing+=1
            seen[op]=seen.get(op,0)+1
        elif op=="error": print("FEHLER von guacd:", i[1:]); break
        elif op=="disconnect": print("disconnect"); break
        else: print("<-", op, i[1:8])
except socket.timeout:
    print("timeout")
print("Zeichenanweisungen gesamt:", drawing, seen)
