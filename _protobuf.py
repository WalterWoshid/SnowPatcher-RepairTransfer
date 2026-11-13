"""Minimal SSLB protobuf reader. Unknown fields are preserved by the patcher."""

def varint(data, pos):
    value = 0
    for shift in range(0, 70, 7):
        c = data[pos]; pos += 1
        value |= (c & 127) << shift
        if c < 128: return value, pos
    raise ValueError('varint')

def decode(data):
    result = []; pos = 0
    while pos < len(data):
        start = pos
        key, pos = varint(data,pos)
        field, wire = key >> 3, key & 7
        if field == 0: raise ValueError('zero field')
        if wire == 0: value, pos = varint(data,pos)
        elif wire == 1: value = data[pos:pos+8]; pos += 8
        elif wire == 2:
            size,pos = varint(data,pos); value = data[pos:pos+size]; pos += size
        elif wire == 5: value = data[pos:pos+4]; pos += 4
        else: raise ValueError('wire')
        if pos > len(data): raise ValueError('overflow')
        result.append((field,wire,value,start,pos))
    return result
