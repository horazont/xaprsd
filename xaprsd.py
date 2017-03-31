#!/usr/bin/python3
import asyncio
import functools
import re
import socket
import signal

import xml.sax.saxutils

import aioxmpp.xso
import aioxmpp.xml


if not hasattr(asyncio, "ensure_future"):
    asyncio.ensure_future = getattr(asyncio, "async")


class GeoLoc(aioxmpp.xso.XSO):
    TAG = "http://jabber.org/protocol/geoloc", "geoloc"

    lat = aioxmpp.xso.ChildText(
        ("http://jabber.org/protocol/geoloc", "lat"),
        type_=aioxmpp.xso.Float(),
    )

    lon = aioxmpp.xso.ChildText(
        ("http://jabber.org/protocol/geoloc", "lon"),
        type_=aioxmpp.xso.Float(),
    )


class APRS1(aioxmpp.xso.XSO):
    TAG = "urn:xaprs:legacy", "aprs1"

    body = aioxmpp.xso.Text()


aioxmpp.Message.xep0080_geoloc = aioxmpp.xso.Child([GeoLoc])
aioxmpp.Message.xaprs_aprs1 = aioxmpp.xso.Child([APRS1])

# increase performance: callsigns are always valid JIDs, we don’t need
# validation here
aioxmpp.Message.from_.xq_descriptor.type_ = aioxmpp.xso.String()
aioxmpp.Message.to.xq_descriptor.type_ = aioxmpp.xso.String()


def _fast_characters(self, s):
    self._finish_pending_start_element()
    self._write(xml.sax.saxutils.escape(s).encode("utf-8"))


# increase performance by removing the validity check for character data
aioxmpp.xml.XMPPXMLGenerator.characters = _fast_characters


CLIENTS = {}
TO_REAP = []
MESSAGE_ID_CTR = 0


def tocall2version(tocall):
    if tocall == "APAND1":
        return 1
    elif tocall.startswith("APDR"):
        match = re.match("APDR(\d\d)", tocall)
        if match:
            return match.group(1)
    return 0


def decode_to_degrees(degrees, minutes, seconds):
    return degrees + minutes / 60 + seconds / 3600


def parse_data_somehow(data):
    if not data:
        return None

    if data[0] in "@/":
        try:
            idx = data.index("z")
        except ValueError:
            pass
        else:
            data = data[idx+1:]

    try:
        if data[0] in "=!":
            # position info
            lat_data = data[1:9]
            lon_data = data[10:19]
            lat_sign = -1 if lat_data[-1] == "S" else 1
            lon_sign = -1 if lat_data[-1] == "W" else 1
            lat_data = decode_to_degrees(
                int(lat_data[0:2]),
                int(lat_data[2:4]),
                int(lat_data[5:7])
            ) * lat_sign
            lon_data = decode_to_degrees(
                int(lon_data[0:3]),
                int(lon_data[3:5]),
                int(lon_data[6:8])
            ) * lon_sign

            return lat_data, lon_data
    except IndexError:
        pass


def parse_aprs1(postline):
    (call, rest) = postline.split('>', 1)
    callonly = call.split('-')[0]
    (tocall, rest2) = rest.split(',', 1)
    ver = tocall2version(tocall)
    (path, post) = rest2.split(':', 1)
    # this is imperfect, but I don’t have the time for a full APRS parser
    try:
        raw, post = post.split(' ', 1)
    except ValueError:
        data = None
    else:
        try:
            data = parse_data_somehow(raw)
        except (ValueError, TypeError):
            data = None
    post = post.strip()
    return call, callonly, tocall, path, data, post, ver


@asyncio.coroutine
def _handle_client(callsign, stream_reader, stream_writer):
    # this doesn’t work with Debian stable
    # sock = stream_writer.transport.get_extra_info("socket")
    # try:
    #     # we don’t want to receive anything
    #     sock.shutdown(socket.SHUT_RD)
    # except OSError:
    #     pass
    # del stream_reader

    # queue at most three messages
    queue = asyncio.Queue(maxsize=3)
    me = asyncio.Task.current_task()
    xml_writer = iter(aioxmpp.xml.write_xmlstream(
        stream_writer, "APRS",
        from_=callsign,
        nsmap={None: "jabber:client"},
    ))
    next(xml_writer)
    stream_writer.write(
        b"\n"
        b"<!-- Welcome to xaprsd. Have fun with your stream! \n"
        b"     This software is licensed under AGPLv3. \n"
        b"     Get the source code at https://github.com/horazont/xaprsd -->"
        b"\n")
    CLIENTS[me] = queue
    try:
        while True:
            item = yield from queue.get()
            xml_writer.send(item)
            # dirty hack
            yield from asyncio.sleep(0)
            yield from stream_writer.drain()
    finally:
        TO_REAP.append(me)
        try:
            xml_writer.close()
        except:
            pass
        stream_writer.close()
        try:
            del CLIENTS[me]
        except KeyError:
            pass


def parse_and_forward(binary_line, text_line):
    global MESSAGE_ID_CTR

    from_, _, to, _, geocoords, body, _ = parse_aprs1(text_line)

    msg = aioxmpp.Message(
        type_=aioxmpp.MessageType.NORMAL,
        from_=from_,
        to=to,
    )
    msg.id_ = "aprs-f{:03d}".format(MESSAGE_ID_CTR)
    if body:
        msg.body[None] = body
    if geocoords is not None:
        lat, lon = geocoords
        msg.xep0080_geoloc = GeoLoc()
        msg.xep0080_geoloc.lat = lat
        msg.xep0080_geoloc.lon = lon

    msg.xaprs_aprs1 = APRS1()

    # try to stay close to the original binary data
    try:
        legacy_line = binary_line.decode("latin1")
    except UnicodeDecodeError:
        legacy_line = text_line
    msg.xaprs_aprs1.body = legacy_line.rstrip()

    MESSAGE_ID_CTR += 1

    for queue in CLIENTS.values():
        try:
            queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass


@asyncio.coroutine
def _aprs_client(server, port, callsign):
    login_msg = b"".join([
        b"user ", callsign.encode("ascii"), b" pass -1 vers XAPRSProxy 04.01\n"
    ])

    while True:
        print("connecting to APRS server {}".format(server))
        try:
            reader, writer = yield from asyncio.open_connection(
                server,
                port,
            )
        except OSError as exc:
            print("failed to connect to APRS server: {}".format(exc))
            yield from asyncio.sleep(1)
            continue

        writer.write(login_msg)

        try:
            yield from writer.drain()
        except OSError as exc:
            print("failed to write to APRS server: {}".format(exc))
            yield from asyncio.sleep(1)
            continue

        print("connected to APRS server")
        while True:
            line = yield from reader.readline()
            if not line:
                break
            text_line = line.decode("utf-8", errors='replace')
            if text_line.startswith("#"):
                continue
            try:
                parse_and_forward(line, text_line)
            except Exception as exc:
                print(
                    "failed to parse and forward APRS message ({}):"
                    " {!r}".format(
                        exc,
                        line
                    )
                )

        print("disconnected from APRS server")
        try:
            writer.close()
        except OSError:
            pass

        yield from asyncio.sleep(5)


@asyncio.coroutine
def reaper():
    global TO_REAP
    while True:
        to_reap = list(TO_REAP)
        TO_REAP = []
        for task in to_reap:
            try:
                yield from task
            except asyncio.CancelledError:
                pass
            except Exception:
                import traceback
                traceback.print_exc()
        yield from asyncio.sleep(1)


@asyncio.coroutine
def main(loop, aprs_server, aprs_port, callsign, listen_port):
    stop_signal = asyncio.Event()
    loop.add_signal_handler(signal.SIGINT, stop_signal.set)

    server = yield from asyncio.start_server(
        functools.partial(_handle_client, callsign),
        port=listen_port,
        loop=loop,
    )

    reader_task = asyncio.ensure_future(_aprs_client(
        aprs_server,
        aprs_port,
        callsign,
    ))

    reaper_task = asyncio.ensure_future(reaper())

    stop_fut = asyncio.ensure_future(stop_signal.wait())

    try:
        done, pending = yield from asyncio.wait(
            [reader_task, stop_fut],
            return_when=asyncio.FIRST_COMPLETED
        )
        if reader_task in done:
            if not stop_fut.done():
                stop_fut.cancel()
            yield from reader_task
    finally:
        tasks_to_cancel = list(CLIENTS)
        tasks_to_cancel.insert(0, reader_task)
        tasks_to_cancel.insert(1, reaper_task)
        for task in tasks_to_cancel:
            task.cancel()
        server.close()
        yield from server.wait_closed()
        for task in tasks_to_cancel:
            try:
                yield from task
            except:
                pass


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "aprs_server",
        help="APRS full feed server"
    )
    parser.add_argument(
        "callsign",
        help="Callsign to use to log into the APRS server"
    )
    parser.add_argument(
        "--aprs-port",
        type=int,
        default=10152,
        help="TCP port to connect to (default: 10152)"
    )
    parser.add_argument(
        "--listen-port", "-p",
        type=int,
        default=20481,
        help="TCP port to bind to (default: 20481, a.k.a. 0xa000 ^ 0xf001)"
    )

    args = parser.parse_args()

    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main(
            loop,
            args.aprs_server,
            args.aprs_port,
            args.callsign,
            args.listen_port,
        ))
    finally:
        loop.close()
