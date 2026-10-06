"""Kapture — a Lightshot-style screenshot tool for Linux."""

APP_NAME = "Kapture"
VERSION  = "4.1.0"
AUTHOR   = "Yeakin Iqra"


def spawn(coro):
    """Fire-and-forget a coroutine on the Qt/asyncio loop, logging any exception
    instead of letting it vanish with the task."""
    import asyncio
    import logging

    def _done(task):
        if not task.cancelled() and task.exception():
            logging.getLogger("kapture").error("background task failed",
                                               exc_info=task.exception())
    task = asyncio.ensure_future(coro)
    task.add_done_callback(_done)
    return task
