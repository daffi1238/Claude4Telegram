"""Presencia en tmux: ¿hay alguien tecleando en el pane de una sesión de Claude Code?"""
import asyncio


async def pane_activity(tmux: dict | None) -> int | None:
    """Última pulsación (epoch, s) de un cliente tmux que está viendo ese pane, o None."""
    if not tmux or not tmux.get("pane"):
        return None
    cmd = ["tmux"] + (["-S", tmux["socket"]] if tmux.get("socket") else [])
    cmd += ["list-clients", "-F", "#{pane_id} #{client_activity}"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    except OSError:
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 3)
    except asyncio.TimeoutError:
        proc.kill()
        return None
    times = []
    for line in out.decode(errors="replace").splitlines():
        pane, _, activity = line.partition(" ")
        if pane == tmux["pane"] and activity.isdigit():
            times.append(int(activity))
    return max(times) if times else None


async def typed_since(tmux: dict | None, since: float) -> bool:
    activity = await pane_activity(tmux)
    return activity is not None and activity >= since
