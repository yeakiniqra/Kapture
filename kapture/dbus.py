"""Minimal session-bus helpers on jeepney (pure Python).

PySide6's QtDBus can't demarshal a{sv} (QDBusArgument.beginMap binds the *write*
overload), and the XDG portal answers in a{sv} — so all D-Bus goes through
jeepney, which hands back plain Python dicts.
"""

from jeepney import DBusAddress, new_method_call, message_bus
from jeepney.io.blocking import open_dbus_connection


def addr(service: str, path: str, iface: str) -> DBusAddress:
    return DBusAddress(path, bus_name=service, interface=iface)


def call(address: DBusAddress, method: str, signature: str = None, args: tuple = (),
         timeout: float = 5) -> tuple:
    """Blocking method call → reply body. Raises on D-Bus errors / timeout."""
    with open_dbus_connection(bus="SESSION") as conn:
        return conn.send_and_get_reply(
            new_method_call(address, method, signature, args), timeout=timeout).body


def has_owner(name: str) -> bool:
    """Is `name` currently owned on the session bus (i.e. is that service up)?"""
    try:
        return bool(call(message_bus, "NameHasOwner", "s", (name,), timeout=2)[0])
    except Exception:
        return False
