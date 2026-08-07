#!/usr/bin/env python3
"""
Reader/writer for Stellar Blade's custom .locres **version 4** format.

Stock Unreal tops out at version 3 (Optimized_CityHash64_UTF16), so
UnrealLocres and friends cannot read these files. See docs/05-locres-v4-format.md.

Round-trip safe: loading and re-saving an untouched file reproduces it
byte-for-byte, including every hash and ExternID.
"""
import struct

MAGIC = bytes([0x0E, 0x14, 0x74, 0x75, 0x67, 0x4A, 0x03, 0xFC,
               0x4A, 0x15, 0x90, 0x9D, 0xC3, 0x37, 0x7F, 0x1B])


class _Reader:
    def __init__(self, b):
        self.b, self.p = b, 0

    def u8(self):
        v = self.b[self.p]; self.p += 1; return v

    def i32(self):
        v = struct.unpack_from('<i', self.b, self.p)[0]; self.p += 4; return v

    def u32(self):
        v = struct.unpack_from('<I', self.b, self.p)[0]; self.p += 4; return v

    def i64(self):
        v = struct.unpack_from('<q', self.b, self.p)[0]; self.p += 8; return v

    def fstr(self):
        n = self.i32()
        if n == 0:
            return ''
        if n < 0:
            raw = self.b[self.p:self.p + (-n) * 2]
            self.p += (-n) * 2
            return raw.decode('utf-16-le').rstrip('\0')
        raw = self.b[self.p:self.p + n]
        self.p += n
        return raw.decode('utf-8').rstrip('\0')


def _wfstr(t):
    """UE FString: ASCII -> UTF-8 with positive length, else UTF-16LE with negative."""
    if t == '':
        return struct.pack('<i', 0)
    try:
        t.encode('ascii')
        d = t.encode('utf-8') + b'\0'
        return struct.pack('<i', len(d)) + d
    except UnicodeEncodeError:
        d = (t + '\0').encode('utf-16-le')
        return struct.pack('<i', -(len(d) // 2)) + d


class LocRes:
    """A parsed .locres v4 file.

    Entries are (key_hash, key, source_hash, string_index, extern_id); the
    text itself lives in a shared pool that entries reference by index.
    """

    def __init__(self, path):
        b = open(path, 'rb').read()
        r = _Reader(b)
        if b[:16] != MAGIC:
            raise ValueError(f'{path}: not a locres file')
        r.p = 16
        self.version = r.u8()
        if self.version != 4:
            raise ValueError(
                f'{path}: expected locres v4 (Stellar Blade), got v{self.version}')
        strings_off = r.i64()
        self.entry_count = r.u32()

        save = r.p
        r.p = strings_off
        self.strings, self.refcounts = [], []
        for _ in range(r.u32()):
            self.strings.append(r.fstr())
            self.refcounts.append(r.i32())
        r.p = save

        self.namespaces = []
        for _ in range(r.u32()):
            ns_hash = r.u32()
            ns = r.fstr()
            entries = []
            for _ in range(r.u32()):
                kh = r.u32()
                k = r.fstr()
                sh = r.u32()
                idx = r.i32()
                ext = r.u32()
                entries.append([kh, k, sh, idx, ext])
            self.namespaces.append([ns_hash, ns, entries])

    def get(self, ns, key):
        for _, name, entries in self.namespaces:
            if name != ns:
                continue
            for e in entries:
                if e[1] == key:
                    return self.strings[e[3]]
        return None

    def set(self, ns, key, value):
        """Repoint one entry at `value`. Returns True if the key existed.

        Appends to the string pool rather than mutating it. Entries share
        pool indices -- identical source lines are deduplicated -- so editing
        a pooled string in place would silently change unrelated subtitles.
        """
        for _, name, entries in self.namespaces:
            if name != ns:
                continue
            for e in entries:
                if e[1] != key:
                    continue
                old = e[3]
                self.strings.append(value)
                self.refcounts.append(1)
                if self.refcounts[old] > 0:
                    self.refcounts[old] -= 1
                e[3] = len(self.strings) - 1
                return True
        return False

    def items(self, ns=None):
        """Yield (namespace, key, text, extern_id)."""
        for _, name, entries in self.namespaces:
            if ns and name != ns:
                continue
            for e in entries:
                yield name, e[1], self.strings[e[3]], e[4]

    def save(self, path):
        body = bytearray()
        body += struct.pack('<I', len(self.namespaces))
        for ns_hash, ns, entries in self.namespaces:
            body += struct.pack('<I', ns_hash) + _wfstr(ns)
            body += struct.pack('<I', len(entries))
            for kh, k, sh, idx, ext in entries:
                body += struct.pack('<I', kh) + _wfstr(k)
                body += struct.pack('<IiI', sh, idx, ext)

        pool = bytearray()
        pool += struct.pack('<I', len(self.strings))
        for s, rc in zip(self.strings, self.refcounts):
            pool += _wfstr(s) + struct.pack('<i', rc)

        header_len = 16 + 1 + 8 + 4
        out = bytearray()
        out += MAGIC
        out += bytes([self.version])
        out += struct.pack('<q', header_len + len(body))
        out += struct.pack('<I', self.entry_count)
        out += body
        out += pool
        open(path, 'wb').write(bytes(out))


if __name__ == '__main__':
    import sys
    lr = LocRes(sys.argv[1])
    print(f'version {lr.version}  entries {lr.entry_count}  '
          f'pooled strings {len(lr.strings)}')
    for _, ns, entries in sorted(lr.namespaces, key=lambda x: -len(x[2])):
        print(f'  {len(entries):6d}  {ns}')
