"""Probe: a bare platform hub held at a speed on the shattered-planet route; logs each second and records deaths.

    python -m validator.probes.platform_route DISTANCE SPEED SECONDS   # e.g. 0.05 100 12
"""
import json, sys
from validator.circuit import _wait_ticks
from validator.server import Server

DIST, SPEED, SECONDS = float(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3])

def lua(s, code):
    return s.command("/sc " + " ".join(code.split()))

with Server() as s:
    lua(s, """
        local f = game.forces.player
        f.unlock_space_platforms()
        local p = f.create_space_platform{name = "probe", planet = "nauvis", starter_pack = "space-platform-starter-pack"}
        p.apply_starter_pack()
        storage.probe = p
        storage.deaths = {}
        script.on_event(defines.events.on_entity_died, function(e)
          storage.deaths[#storage.deaths + 1] = {tick = e.tick, name = e.entity.name, cause = e.cause and e.cause.name,
            damage = e.damage_type and e.damage_type.name}
        end)
        p.space_connection = "solar-system-edge-shattered-planet"
        p.distance = %s
        script.on_nth_tick(1, function() local q = storage.probe if q and q.valid then q.speed = %s end end)
    """ % (DIST, SPEED))
    for t in range(SECONDS):
        _wait_ticks(s, 60, timeout=30)
        out = lua(s, """
            local p = storage.probe
            if not (p and p.valid) then rcon.print(helpers.table_to_json{tick = game.tick, platform = "gone", deaths = storage.deaths}) return end
            local names = {}
            for _, a in pairs(p.surface.find_entities_filtered{type = "asteroid"}) do names[a.name] = (names[a.name] or 0) + 1 end
            rcon.print(helpers.table_to_json{tick = game.tick, distance = p.distance, asteroids = names,
              hub = p.hub and p.hub.valid and p.hub.health or "gone", deaths = #storage.deaths})
        """)
        print(out.strip()[:400])
        if '"gone"' in out and "platform" in out:
            break
