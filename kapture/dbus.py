"""Minimal session-bus helpers on jeepney (pure Python).

PySide6's QtDBus can't demarshal a{sv} (QDBusArgument.beginMap binds the *write*
overload), and the XDG portal answers in a{sv} — so all D-Bus goes through
jeepney, which hands back plain Python dicts.

Linux only: jeepney is imported lazily so the package imports cleanly on
Windows, where none of this is ever called.
"""


def addr(service: str, path: str, iface: str):
    """(path, bus_name, interface) — turned into a jeepney DBusAddress on use."""
    return (path, service, iface)


def _address(a):
    from jeepney import DBusAddress
    return DBusAddress(a[0], bus_name=a[1], interface=a[2])


def call(address, method: str, signature: str = None, args: tuple = (),
         timeout: float = 5) -> tuple:
    """Blocking method call → reply body. Raises on D-Bus errors / timeout."""
    from jeepney import new_method_call
    from jeepney.io.blocking import open_dbus_connection
    with open_dbus_connection(bus="SESSION") as conn:
        return conn.send_and_get_reply(
            new_method_call(_address(address), method, signature, args), timeout=timeout).body


def has_owner(name: str) -> bool:
    """Is `name` currently owned on the session bus (i.e. is that service up)?"""
    try:
        from jeepney import message_bus, new_method_call
        from jeepney.io.blocking import open_dbus_connection
        with open_dbus_connection(bus="SESSION") as conn:
            return bool(conn.send_and_get_reply(
                new_method_call(message_bus, "NameHasOwner", "s", (name,)), timeout=2).body[0])
    except Exception:
        return False
