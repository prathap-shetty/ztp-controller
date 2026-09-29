"""Wire probes for an isolated Docker test network only; never run on a live ZTP LAN."""

import argparse
import hashlib
import socket
import struct
from pathlib import Path


def options(packet):
    result, position = {}, 240
    while position < len(packet):
        code = packet[position]
        position += 1
        if code == 255:
            break
        if code == 0:
            continue
        size = packet[position]
        position += 1
        result[code] = packet[position : position + size]
        position += size
    return result


def dhcp(mode):
    xid = 0x12345678
    packet = bytearray(240)
    struct.pack_into("!BBBBIHH", packet, 0, 1, 1, 6, 0, xid, 0, 0x8000)
    packet[24:28] = socket.inet_aton("192.0.2.3")  # synthetic relay
    packet[28:34] = bytes.fromhex("020000000099")
    packet[236:240] = bytes([99, 130, 83, 99])
    packet += bytes([53, 1, 1, 55, 5, 1, 3, 43, 66, 67, 255])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
        channel.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        channel.settimeout(5)
        channel.bind(("0.0.0.0", 67))
        channel.sendto(packet, ("192.0.2.2", 67))
        response, _ = channel.recvfrom(4096)
        assert struct.unpack_from("!I", response, 4)[0] == xid
        opts = options(response)
        assert opts[53] == b"\x02"
        if mode == "secure-https":
            assert opts[43][:4] == bytes([1, 0, 1, 1]), opts[43].hex()
            assert b"https://192.0.2.2/bootstrap/poap.py" in opts[43]
            assert 66 not in opts and 67 not in opts
        else:
            assert opts[66] == (b"http://192.0.2.2" if mode == "legacy-http" else b"192.0.2.2")
            assert opts[67] == (b"bootstrap/poap.py" if mode == "legacy-http" else b"poap.py")
        requested = bytes(packet[:240]) + bytes([53, 1, 3, 50, 4]) + response[16:20]
        requested += bytes([54, 4]) + opts[54] + bytes([255])
        channel.sendto(requested, ("192.0.2.2", 67))
        ack, _ = channel.recvfrom(4096)
        assert options(ack)[53] == b"\x05"
    print("DHCP relayed DISCOVER/OFFER/REQUEST/ACK passed:", mode)


def tftp(server, expected):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
        channel.settimeout(5)
        channel.sendto(b"\x00\x01poap.py\x00octet\x00blksize\x001024\x00", (server, 69))
        response, peer = channel.recvfrom(4096)
        assert 40000 <= peer[1] <= 40100
        assert response[:2] == b"\x00\x06"  # negotiated block size
        channel.sendto(b"\x00\x04\x00\x00", peer)
        data, block = b"", 1
        while True:
            response, address = channel.recvfrom(4096)
            assert address == peer and struct.unpack("!HH", response[:4]) == (3, block)
            data += response[4:]
            channel.sendto(struct.pack("!HH", 4, block), peer)
            if len(response[4:]) < 1024:
                break
            block += 1
        assert hashlib.sha256(data).digest() == hashlib.sha256(Path(expected).read_bytes()).digest()
    for request in (b"\x00\x02upload.txt\x00octet\x00", b"\x00\x01private.cfg\x00octet\x00"):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
            channel.settimeout(5)
            channel.sendto(request, (server, 69))
            response, _ = channel.recvfrom(4096)
            assert response[:2] == b"\x00\x05"
    print(
        "TFTP passed: bootstrap checksum, negotiated blocks, "
        "transfer-port range, upload/private denial"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("protocol", choices=["dhcp", "tftp"])
    parser.add_argument("--mode", default="legacy-http")
    parser.add_argument("--server", default="192.0.2.2")
    parser.add_argument("--expected")
    args = parser.parse_args()
    if args.protocol == "dhcp":
        dhcp(args.mode)
    else:
        tftp(args.server, args.expected)
