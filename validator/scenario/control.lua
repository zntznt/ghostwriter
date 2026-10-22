-- Ghostwriter validator harness. Loaded as a scenario by validator/server.py;
-- Python calls it over RCON:
--   /sc remote.call("ghostwriter", "validate", "<blueprint string>")
-- and gets back JSON via rcon.print:
--   {import = rc, export = "<string>", builds = {[path] = build}}
-- rc is LuaItemStack.import_stack's result (0 ok, -1 ok with errors, 1 fail).
-- Each build covers one blueprint (path "" or "1/0" inside books):
--   {entities = n, unplaced = {...}, unplaceable = {...}, unrevived = {...},
--    rebuilt = "<string>"}
-- unplaceable: names that can't be placed even alone on the prepared test
-- site; unplaced: the rest, i.e. the blueprint itself collides; unrevived:
-- placed as ghosts but not buildable (e.g. elevated rails without supports).
-- The site is prepared per blueprint: a surface meeting its entities'
-- surface_conditions (or a real planet surface when only that works), a base
-- tile, the tiles each entity's tile_buildability_rules need, and a resource
-- under each mining drill.

local SURFACE = "ghostwriter"
local MARGIN = 8
local BASE_TILES = {"refined-concrete", "space-platform-foundation"}

local function new_surface(name)
  return game.create_surface(name, {default_enable_all_autoplace_controls = false, no_enemies_mode = true})
end

-- The default test surface (planet-like default properties).
local function surface()
  return game.surfaces[SURFACE] or new_surface(SURFACE)
end

local function clear(s, area)
  for _, e in pairs(s.find_entities_filtered{area = area}) do
    if e.valid and e.type ~= "character" then e.destroy() end
  end
end

local function prepare(s, area, tile)
  local lt, rb = area[1], area[2]
  local centre = {(lt[1] + rb[1]) / 2, (lt[2] + rb[2]) / 2}
  s.request_to_generate_chunks(centre, math.ceil(math.max(rb[1] - lt[1], rb[2] - lt[2]) / 64) + 1)
  s.force_generate_chunk_requests()
  clear(s, area)
  local tiles = {}
  for x = math.floor(lt[1]), math.ceil(rb[1]) do
    for y = math.floor(lt[2]), math.ceil(rb[2]) do
      tiles[#tiles + 1] = {name = tile or "refined-concrete", position = {x, y}}
    end
  end
  s.set_tiles(tiles)
end

local function bounds(entities, tiles)
  local x1, y1, x2, y2 = 0, 0, 0, 0
  for _, list in pairs({entities, tiles}) do
    for _, e in pairs(list) do
      x1, y1 = math.min(x1, e.position.x), math.min(y1, e.position.y)
      x2, y2 = math.max(x2, e.position.x), math.max(y2, e.position.y)
    end
  end
  local r = math.max(-x1, -y1, x2, y2) + MARGIN
  return {{-r, -r}, {r, r}}
end

local function key(name, x, y)
  return name .. "@" .. (math.floor(x * 8 + 0.5) / 8) .. "," .. (math.floor(y * 8 + 0.5) / 8)
end

-- A surface whose properties satisfy every entity's surface_conditions:
-- each property at its default, clamped into the allowed range. Surfaces are
-- created on demand and named after the properties that differ.
local function surface_for(entities)
  local range = {}
  for _, e in pairs(entities) do
    local proto = prototypes.entity[e.name]
    for _, c in pairs((proto and proto.surface_conditions) or {}) do
      local r = range[c.property] or {-math.huge, math.huge}
      range[c.property] = {math.max(r[1], c.min or -math.huge), math.min(r[2], c.max or math.huge)}
    end
  end
  local changed = {}
  for name, sp in pairs(prototypes.surface_property) do
    local r = range[name]
    if r then
      local v = math.min(math.max(sp.default_value, r[1]), r[2])
      if v ~= sp.default_value then changed[#changed + 1] = {name, v} end
    end
  end
  if #changed == 0 then return surface() end
  table.sort(changed, function(a, b) return a[1] < b[1] end)
  local parts = {}
  for _, c in pairs(changed) do parts[#parts + 1] = c[1] .. "_" .. c[2] end
  local name = SURFACE .. "-" .. table.concat(parts, "-")
  local s = game.surfaces[name]
  if not s then
    s = new_surface(name)
    for _, c in pairs(changed) do s.set_property(c[1], c[2]) end
  end
  return s
end

local function tile_layers(name)
  local mask = prototypes.tile[name] and prototypes.tile[name].collision_mask
  return (mask and mask.layers) or {}
end

-- First tile (common ones first) with every required layer and no colliding one.
local function tile_for(required, colliding)
  local function fits(name)
    local layers = tile_layers(name)
    for layer in pairs(required or {}) do if not layers[layer] then return false end end
    for layer in pairs(colliding or {}) do if layers[layer] then return false end end
    return true
  end
  for _, name in pairs({"refined-concrete", "space-platform-foundation", "water", "deepwater", "empty-space"}) do
    if prototypes.tile[name] and fits(name) then return name end
  end
  local names = {}
  for name in pairs(prototypes.tile) do names[#names + 1] = name end
  table.sort(names)
  for _, name in pairs(names) do if fits(name) then return name end end
end

-- Rotate a north-facing offset clockwise to `direction` (16-way; cardinals only).
local function rotate(x, y, direction)
  local quarter = math.floor(((direction or 0) % 16) / 4)
  for _ = 1, quarter do x, y = -y, x end
  return x, y
end

-- Tiles an entity's tile_buildability_rules demand (water under an offshore
-- pump, empty space behind a thruster), set at its blueprint position.
local function apply_tile_rules(s, e, base)
  local proto = prototypes.entity[e.name]
  local tiles = {}
  for _, rule in pairs((proto and proto.tile_buildability_rules) or {}) do
    local tile = tile_for(rule.required_tiles and rule.required_tiles.layers,
                          rule.colliding_tiles and rule.colliding_tiles.layers)
    if tile and tile ~= base then
      local a, b = rule.area.left_top, rule.area.right_bottom
      local ax, ay = rotate(a.x, a.y, e.direction)
      local bx, by = rotate(b.x, b.y, e.direction)
      local x1, x2 = math.min(ax, bx) + e.position.x, math.max(ax, bx) + e.position.x
      local y1, y2 = math.min(ay, by) + e.position.y, math.max(ay, by) + e.position.y
      -- Every tile the area overlaps (its edges can fall on tile centres).
      local eps = 1 / 256
      for x = math.floor(x1 + eps), math.ceil(x2 - eps) - 1 do
        for y = math.floor(y1 + eps), math.ceil(y2 - eps) - 1 do
          tiles[#tiles + 1] = {name = tile, position = {x, y}}
        end
      end
    end
  end
  if #tiles > 0 then s.set_tiles(tiles) end
end

-- A resource under a mining drill or pumpjack, of a category it can mine.
local function apply_resources(s, e)
  local proto = prototypes.entity[e.name]
  if not (proto and proto.type == "mining-drill") then return end
  local categories = proto.resource_categories or {}
  local names = {}
  for name, p in pairs(prototypes.entity) do
    if p.type == "resource" and categories[p.resource_category] then names[#names + 1] = name end
  end
  table.sort(names)
  local resource = names[1]
  if not resource then return end
  local box = proto.collision_box
  local spots = {{e.position.x, e.position.y}}
  for x = math.floor(e.position.x + box.left_top.x), math.ceil(e.position.x + box.right_bottom.x) - 1 do
    for y = math.floor(e.position.y + box.left_top.y), math.ceil(e.position.y + box.right_bottom.y) - 1 do
      spots[#spots + 1] = {x + 0.5, y + 0.5}
    end
  end
  for _, at in pairs(spots) do
    pcall(s.create_entity, {name = resource, position = at, amount = 100000})
  end
end

-- Entity names whose placement doesn't depend on tiles or resources the
-- harness prepares per entity (those can't be judged standing alone).
local function plain_names(entities)
  local names, n = {}, 0
  for _, e in pairs(entities) do
    local proto = prototypes.entity[e.name]
    local special = proto and (proto.type == "mining-drill" or next(proto.tile_buildability_rules or {}))
    if proto and not special and not names[e.name] then names[e.name] = true n = n + 1 end
  end
  return names, n
end

-- How many of `names` stand alone on surface s with base tile `tile`.
local function fits(s, names, area, tile)
  local probe = {area[2][1] + 2 * MARGIN, 0}
  prepare(s, {{probe[1] - MARGIN, -MARGIN}, {probe[1] + MARGIN, MARGIN}}, tile)
  local n = 0
  for name in pairs(names) do
    if s.can_place_entity{name = name, position = probe, force = "player",
                          build_check_type = defines.build_check_type.manual} then n = n + 1 end
  end
  return n
end

-- Where to build: the surface matching the entities' surface_conditions,
-- else a real planet surface (some restrictions, like Fulgora's lightning rods,
-- only show on the planet itself), with the base tile that fits most entities.
local function site_for(entities, area)
  local names, total = plain_names(entities)
  local best = {score = -1}
  local function try(s)
    for _, tile in pairs(BASE_TILES) do
      if prototypes.tile[tile] then
        local n = fits(s, names, area, tile)
        if n > best.score then best = {surface = s, tile = tile, score = n} end
        if n == total then return true end
      end
    end
  end
  if try(surface_for(entities)) then return best.surface, best.tile end
  local planets = {}
  for name in pairs(game.planets) do planets[#planets + 1] = name end
  table.sort(planets)
  for _, name in pairs(planets) do
    local planet = game.planets[name]
    local s = planet.surface or planet.create_surface()
    s.peaceful_mode = true
    if try(s) then break end
  end
  return best.surface, best.tile
end

-- Build a blueprint where its entities stand in the blueprint (absolute
-- snapping on a 2-tile grid keeps every position, rails included), on a surface
-- and tiles that meet its entities' needs, then revive it. Several passes, so
-- entities that need others built first (rolling stock on rails) get them.
-- Returns the report part shared by validation and circuit tests, the surface,
-- the area, and the built entities keyed by blueprint entity_number.
local function place(stack)
  local entities = stack.get_blueprint_entities() or {}
  local area = bounds(entities, stack.get_blueprint_tiles() or {})
  local s, tile = site_for(entities, area)
  prepare(s, area, tile)
  for _, e in pairs(entities) do
    apply_tile_rules(s, e, tile)
    apply_resources(s, e)
  end
  stack.blueprint_snap_to_grid = {2, 2}
  stack.blueprint_absolute_snapping = true
  stack.blueprint_position_relative_to_grid = {0, 0}

  local number_at, wanted = {}, {}
  for _, e in pairs(entities) do
    number_at[key(e.name, e.position.x, e.position.y)] = e.entity_number
    wanted[e.name] = (wanted[e.name] or 0) + 1
  end
  local out = {entities = #entities, surface = s.name, tile = tile, unplaced = {}, unplaceable = {}, unrevived = {}}
  local built, count = {}, {}
  for _ = 1, 4 do
    local ghosts = stack.build_blueprint{
      surface = s, force = "player", position = {0, 0},
      build_mode = defines.build_mode.forced, skip_fog_of_war = false,
    }
    local progress = false
    -- Tile ghosts first so entities that need the tiles can revive.
    for _, g in pairs(ghosts) do
      if g.valid and g.name == "tile-ghost" then g.revive() end
    end
    for _, g in pairs(ghosts) do
      if g.valid and g.name == "entity-ghost" then
        local number = number_at[key(g.ghost_name, g.position.x, g.position.y)]
        local name = g.ghost_name
        local _, entity = g.revive()
        if entity then
          progress = true
          count[name] = (count[name] or 0) + 1
          if number then built[number] = entity end
        end
      end
    end
    if not progress then break end
    -- Re-place what didn't build: a ghost laid before its rails existed
    -- (rolling stock) only builds as a fresh ghost on the real rails.
    for _, g in pairs(s.find_entities_filtered{area = area, name = "entity-ghost"}) do g.destroy() end
  end

  local ghosts_left = {}
  for _, g in pairs(s.find_entities_filtered{area = area, name = "entity-ghost"}) do
    out.unrevived[#out.unrevived + 1] = g.ghost_name
    ghosts_left[g.ghost_name] = (ghosts_left[g.ghost_name] or 0) + 1
  end
  local probe = {area[2][1] + 2 * MARGIN, 0}
  for name, n in pairs(wanted) do
    -- Placed as a ghost but not built is reported as unrevived, not here.
    local missing = n - (count[name] or 0) - (ghosts_left[name] or 0)
    if missing > 0 then
      prepare(s, {{probe[1] - MARGIN, -MARGIN}, {probe[1] + MARGIN, MARGIN}}, tile)
      local alone = s.can_place_entity{name = name, position = probe, force = "player",
                                       build_check_type = defines.build_check_type.manual}
      -- Placeable alone but not here: the blueprint itself collides. Otherwise
      -- the test surface can't hold it (needs something not prepared).
      local list = alone and out.unplaced or out.unplaceable
      list[#list + 1] = name .. " x" .. missing
    end
  end
  return out, s, area, built
end

local function build(stack)
  local out, s, area = place(stack)
  local inv = game.create_inventory(1)
  local copy = inv[1]
  copy.set_stack("blueprint")
  copy.create_blueprint{
    surface = s, force = "player", area = area,
    include_entities = true, include_modules = true, include_station_names = true,
    include_trains = true, include_fuel = true,
  }
  if copy.is_blueprint_setup() then out.rebuilt = copy.export_stack() end
  inv.destroy()
  clear(s, area)
  return out
end

local function build_all(stack, path, builds)
  if stack.is_blueprint and stack.is_blueprint_setup() then
    builds[path] = build(stack)
  elseif stack.is_blueprint_book then
    local inv = stack.get_inventory(defines.inventory.item_main)
    for i = 1, #inv do
      if inv[i].valid_for_read then
        -- Children are addressed by their 0-based book index, as in the JSON.
        build_all(inv[i], (path == "" and "" or path .. "/") .. (i - 1), builds)
      end
    end
  end
end

-- Circuit tests: build one blueprint and keep it, powered by a global
-- electric network; inject signals with script-wired constant combinators
-- (no reach limit); read networks and logistic requests after some ticks.

local function powered(s)
  s.create_global_electric_network()
  storage.power = storage.power or {}
  local source = storage.power[s.name]
  if not (source and source.valid) then
    local at = {-1000, -1000}
    s.request_to_generate_chunks(at, 1)
    s.force_generate_chunk_requests()
    storage.power[s.name] = s.create_entity{name = "electric-energy-interface", position = at, force = "player"}
  end
end

local function signal_id(name)
  for _, kind in pairs({"item", "fluid", "virtual_signal"}) do
    if prototypes[kind][name] then
      return {type = (kind == "virtual_signal") and "virtual" or kind, name = name, quality = "normal"}
    end
  end
end

local function signals_of(network)
  local out = {}
  for _, sig in pairs((network and network.signals) or {}) do
    local name = sig.signal.name
    if sig.signal.quality and sig.signal.quality ~= "normal" then name = name .. "@" .. sig.signal.quality end
    out[name] = sig.count
  end
  return out
end

local function circuit_teardown()
  local c = storage.circuit
  if not c then return end
  for _, inj in pairs(c.injectors) do
    if inj.valid then inj.destroy() end
  end
  clear(c.surface, c.area)
  storage.circuit = nil
end

-- Flow tests: build one blueprint and keep it, powered only through its own
-- poles (a script-wired pole joins them to an unlimited source); every tick
-- top up fed belt lanes from their back end and empty drain belts, counting
-- what left them.

local LANES = {left = defines.transport_line.left_line, right = defines.transport_line.right_line}

-- A pool links blueprints run together (a whole production book): what drains
-- off belts and the fluid machines make is credited to it, and pooled feeds
-- only hand out what it holds.
local function flow_tick()
  local f = storage.flow
  if not (f and f.started) then return end
  local pool = f.pool
  for _, feed in pairs(f.feeds) do
    if feed.belt.valid then
      local line = feed.belt.get_transport_line(feed.lane)
      if line.can_insert_at_back() and (not feed.pooled or (pool[feed.item] or 0) >= 1) then
        if line.insert_at_back({name = feed.item}) and feed.pooled then pool[feed.item] = pool[feed.item] - 1 end
      end
    end
  end
  local caps = f.caps
  for _, belt in pairs(f.drains) do
    if belt.valid then
      for _, lane in pairs(LANES) do
        local line = belt.get_transport_line(lane)
        local contents = line.get_contents()
        -- Backpressure: a lane whose item the pool already holds enough of stays
        -- put, so the belt backs up and the row making it slows, as in a factory.
        local full = false
        for _, item in pairs(contents) do
          if caps[item.name] and (pool[item.name] or 0) >= caps[item.name] then full = true end
        end
        if not full then
          for _, item in pairs(contents) do
            f.counts[item.name] = (f.counts[item.name] or 0) + item.count
            pool[item.name] = (pool[item.name] or 0) + item.count
          end
          line.clear()
        end
      end
    end
  end
  for _, sink in pairs(f.sinks) do
    -- Same for fluids: a sink stops draining while the pool is full, so the line fills.
    local cap = caps[sink.fluid]
    if cap and sink.entity.valid then
      local full = (pool[sink.fluid] or 0) >= cap
      if full ~= sink.off then
        sink.off = full
        sink.entity.set_infinity_pipe_filter(not full and {name = sink.fluid, percentage = 0, mode = "at-most"} or nil)
      end
    end
  end
  if next(f.pooled_fluids) then
    local stats = game.forces.player.get_fluid_production_statistics(f.surface)
    for name, last in pairs(f.pooled_fluids) do
      local now = stats.get_input_count(name)
      pool[name] = (pool[name] or 0) + now - last
      f.pooled_fluids[name] = now
    end
    for _, feed in pairs(f.fluid_feeds) do
      local have = pool[feed.fluid] or 0
      if have > 0 and feed.entity.valid then
        pool[feed.fluid] = have - feed.entity.insert_fluid{name = feed.fluid, amount = math.min(have, 1000)}
      end
    end
  end
end
-- Flight tests: a platform blueprint built around a fresh space platform's hub
-- (the blueprint's origin on the hub's centre), then flown from one space
-- location toward another. Records the furthest it got along the connection,
-- every death and what caused it, and the hub's cargo, until the hub dies.

-- Joules in the stand-in power source, which makes none: what it loses is what the platform used.
local BUDGET = 1e15

-- Ammo in the turrets and fluid in the thrusters and in any tank (a reserve): supplied,
-- but not used yet.
local function held_by(thrusters, turrets, tanks)
  local held = {}
  for _, list in pairs{thrusters, tanks} do
    for _, t in pairs(list) do
      if t.valid then
        for name, amount in pairs(t.get_fluid_contents()) do held[name] = (held[name] or 0) + amount end
      end
    end
  end
  for _, t in pairs(turrets) do
    local inv = t.valid and t.get_inventory(defines.inventory.turret_ammo)
    if inv then
      for _, c in pairs(inv.get_contents()) do held[c.name] = (held[c.name] or 0) + c.count end
    end
  end
  return held
end

-- What of the hub-fed ammo (Flight.feed) is on the platform's belts: it left the hub but
-- hasn't been used.
local function on_belts(surface, names)
  local held = {}
  for _, b in pairs(surface.find_entities_filtered{type = {"transport-belt", "underground-belt"}}) do
    for i = 1, b.get_max_transport_line_index() do
      for _, c in pairs(b.get_transport_line(i).get_contents()) do
        if names[c.name] then held[c.name] = (held[c.name] or 0) + c.count end
      end
    end
  end
  return held
end

local function flight_tick()
  local fl = storage.flight
  if not fl then return end
  local p = fl.platform
  if not (p and p.valid and p.hub and p.hub.valid) then
    fl.ended = fl.ended or {tick = game.tick, reason = "platform gone"}
    return
  end
  if fl.filling then
    -- Readied in orbit before leaving (fill): the hub's feed items held at their stock while
    -- its feed fills the belts and turrets, as a player would top it up. Nothing else counts
    -- until it leaves.
    if game.tick % 60 == 0 then
      local hub = p.hub.get_inventory(defines.inventory.hub_main)
      fl.drawn = 0
      for name, n in pairs(fl.filling) do
        local short = n - hub.get_item_count(name)
        if short > 0 then
          local got = hub.insert{name = name, count = short}
          fl.filled[name], fl.drawn = (fl.filled[name] or 0) + got, fl.drawn + got
        end
      end
    end
    return
  end
  if p.space_connection and p.space_connection.name == fl.connection and p.distance then
    fl.furthest = math.max(fl.furthest, fl.reversed and 1 - p.distance or p.distance)
  end
  fl.speed_max = math.max(fl.speed_max, p.speed or 0)
  -- Where it is, as a share of the route from the flight's origin, and how far it has flown.
  local pos = fl.pos
  if p.space_connection and p.space_connection.name == fl.connection and p.distance then
    pos = fl.reversed and 1 - p.distance or p.distance
  elseif p.space_location then
    pos = p.space_location.name == fl.origin and 0 or p.space_location.name == fl.destination and 1 or pos
  end
  fl.odometer, fl.pos = fl.odometer + math.abs(pos - fl.pos) * fl.length, pos
  -- turn: there the schedule goes back to the origin, which turns a platform round at once
  -- and keeps its speed (NOTES.md); back: when it arrives there again.
  if fl.turn and not fl.turned and pos >= fl.turn then
    p.schedule = {current = 1, records = {{station = fl.origin}}}
    fl.turned = game.tick
    -- What it had then: the two legs apart.
    fl.at_turn = {km = fl.odometer, cargo = p.hub.get_inventory(defines.inventory.hub_main).get_contents()}
  end
  if fl.turned and not fl.back and p.space_location and p.space_location.name == fl.origin then fl.back = game.tick end
  if game.tick % 60 == 0 then
    -- The hub rebuilds what dies as new entities, which need stocking too.
    fl.thrusters = p.surface.find_entities_filtered{type = "thruster"}
    fl.turrets = p.surface.find_entities_filtered{type = "ammo-turret"}
  end
  -- What the script gives is counted (fl.supplied), as a budget for making it on board.
  local supplied = fl.supplied
  if fl.fuel then
    for _, t in pairs(fl.thrusters) do
      if t.valid then
        for _, fluid in pairs{"thruster-fuel", "thruster-oxidizer"} do
          supplied[fluid] = (supplied[fluid] or 0) + t.insert_fluid{name = fluid, amount = 1000}
        end
      end
    end
  end
  if fl.ammo and game.tick % 60 == 0 then
    for _, t in pairs(fl.turrets) do
      local ammo = t.valid and fl.ammo[t.name]
      if ammo then
        local inv = t.get_inventory(defines.inventory.turret_ammo)
        local have = inv and inv.get_item_count(ammo) or 0
        if inv and have < 10 then supplied[ammo] = (supplied[ammo] or 0) + t.insert{name = ammo, count = 10 - have} end
      end
    end
  end
  if game.tick % 60 == 0 then
    -- take: what the script empties from the hub each second and counts, so a full hub
    -- doesn't cap what is measured.
    local hub = p.hub.get_inventory(defines.inventory.hub_main)
    for _, name in pairs(fl.take or {}) do
      local n = hub.get_item_count(name)
      if n > 0 then fl.taken[name] = (fl.taken[name] or 0) + hub.remove{name = name, count = n} end
    end
    -- feed: what left the hub of the ammo its inserters feed the turrets (what fill gave back included).
    for name, n in pairs(fl.feed_start or {}) do fl.fed[name] = n - hub.get_item_count(name) + (fl.filled[name] or 0) end
    -- A turret with no ammo, each second.
    for _, t in pairs(fl.turrets) do
      local inv = t.valid and t.get_inventory(defines.inventory.turret_ammo)
      if inv and inv.is_empty() then fl.dry = fl.dry + 1 end
    end
    -- Kept each second: the cargo and the source go with the hub.
    fl.cargo = hub.get_contents()
    fl.hub_health = p.hub.health
    -- ran_out: km flown since when the hub has held none of an item it left with. A cargo bay
    -- that dies spills its slots' items, which come back when the hub rebuilds it (NOTES.md):
    -- an item that comes back hasn't run out.
    for name in pairs(fl.watch) do
      if hub.get_item_count(name) == 0 then fl.ran_out[name] = fl.ran_out[name] or fl.odometer
      else fl.ran_out[name] = nil end
    end
    -- low: seconds a thruster held under low_fuel of either fluid (thrust follows the line's fill).
    for _, t in pairs(fl.low_fuel and fl.thrusters or {}) do
      local c = t.valid and t.get_fluid_contents() or {}
      if t.valid and ((c["thruster-fuel"] or 0) < fl.low_fuel or (c["thruster-oxidizer"] or 0) < fl.low_fuel) then
        fl.low = fl.low + 1
        break
      end
    end
    if fl.source and fl.source.valid then fl.power_j = BUDGET - fl.source.energy end
    -- Power made on board: the surface's own network statistics (a fresh surface starts at 0).
    local power = p.surface.global_electric_network_statistics
    if power then
      local base = fl.power_base or {}
      fl.generated_j = (power.output_counts["fusion-generator"] or 0) - (base.generated or 0)
      fl.plant_j = (power.input_counts["fusion-reactor"] or 0) + (power.input_counts["cryogenic-plant"] or 0)
        - (base.plant or 0)
    end
    -- Short of power: a fusion reactor that takes power first (as railguns do) and still
    -- gets less than it asks. A railgun reads low_power whenever it refills after a shot,
    -- even on unlimited power, so it can't tell (NOTES.md). Not in the first second: a
    -- readied reactor still starting reads low_power for its first few ticks.
    for _, r in pairs(game.tick - fl.start >= 60 and p.surface.find_entities_filtered{type = "fusion-reactor"} or {}) do
      if r.status == defines.entity_status.low_power or r.status == defines.entity_status.no_power then
        fl.brownout = fl.brownout + 1
        break
      end
    end
    fl.held = held_by(fl.thrusters, fl.turrets, p.surface.find_entities_filtered{type = "storage-tank"})
    if fl.feed_start then
      for name, n in pairs(on_belts(p.surface, fl.feed_start)) do fl.held[name] = (fl.held[name] or 0) + n end
    end
    -- What the platform's own machines made (script inserts don't count).
    local stats = p.force.get_fluid_production_statistics(p.surface)
    fl.made = {}
    for _, f in pairs{"thruster-fuel", "thruster-oxidizer"} do
      local n = stats.get_input_count(f) - (fl.made_base[f] or 0)
      if n > 0 then fl.made[f] = n end
    end
  end
end

-- What the hub builds from its stock in flight: what died, rebuilt (construction-robotics).
local function count_rebuilt(name, n)
  local fl = storage.flight
  if fl then fl.rebuilt[name] = (fl.rebuilt[name] or 0) + n end
end

script.on_event(defines.events.on_space_platform_built_entity, function(event)
  if event.entity.valid then count_rebuilt(event.entity.name, 1) end
end)

script.on_event(defines.events.on_space_platform_built_tile, function(event)
  count_rebuilt(event.tile.name, #event.tiles)
end)

script.on_event(defines.events.on_tick, function(event)
  flow_tick(event)
  flight_tick()
end)

script.on_event(defines.events.on_entity_died, function(event)
  local fl = storage.flight
  if not (fl and event.entity.valid and event.entity.surface.index == fl.surface) then return end
  local cause = (event.cause and event.cause.valid and event.cause.name)
    or (event.damage_type and event.damage_type.name) or "unknown"
  local key = event.entity.name .. " <- " .. cause
  fl.deaths[key] = (fl.deaths[key] or 0) + 1
  if event.entity.name == "cargo-bay" then
    -- By |x|, tiles from the hub's centre: a dead bay cuts off the ones beyond it (NOTES.md).
    local x = tostring(math.floor(math.abs(event.entity.position.x)))
    fl.bays_lost[x] = (fl.bays_lost[x] or 0) + 1
  end
  if event.entity.name == "space-platform-hub" then
    fl.ended = {tick = event.tick, reason = "hub destroyed by " .. cause}
  end
end)

-- Research for a flight: {series = levels done}, e.g. {["railgun-damage"] = 4}.
-- A series is the technologies named "<series>-<n>"; each gets as many of its
-- levels as fall within the count, so the game applies its own effects. On an
-- infinite technology, LuaTechnology.level is the next level to research. A
-- technology with no number, e.g. {["construction-robotics"] = 1}, has 1 level.
-- Returns what to restore afterwards, or nil and an error.
local function set_research(force, research)
  local was = {}
  for series, done in pairs(research or {}) do
    local found, most = false, 0
    for name, tech in pairs(force.technologies) do
      local n = name:match("^(.+)%-%d+$")
      if n == series then
        found = true
        local first, last = tech.prototype.level, tech.prototype.max_level
        most = math.max(most, last)
        was[#was + 1] = {name = name, researched = tech.researched, level = tech.level}
        if done >= last then
          tech.researched = true
        elseif done >= first then
          tech.level = done + 1
        end
      end
    end
    local single = not found and force.technologies[series]
    if single then
      found, most = true, 1
      was[#was + 1] = {name = series, researched = single.researched, level = single.level}
      if done >= 1 then single.researched = true end
    end
    if not found then return nil, "no technology series " .. series, was end
    if done > most then return nil, series .. " has " .. most .. (most == 1 and " level" or " levels"), was end
  end
  return was
end

local function restore_research(force, was)
  for i = #(was or {}), 1, -1 do
    local tech = force.technologies[was[i].name]
    tech.researched = was[i].researched
    if not was[i].researched then tech.level = was[i].level end
  end
end

local function flight_teardown()
  local fl = storage.flight
  if not fl then return end
  game.speed = 1
  game.tick_paused = false
  if fl.platform and fl.platform.valid then fl.platform.destroy() end
  restore_research(game.forces.player, fl.research)
  storage.flight = nil
end

-- The flight leaves: onto the route at from_start (or at the origin), scheduled to the
-- destination, moving at start_speed if given. Its clock and counts start here; what it
-- made and generated while readied (fill) is left out.
local function leave(fl)
  local p = fl.platform
  if fl.from_start > 0 then
    p.space_connection = fl.connection
    p.distance = fl.reversed and 1 - fl.from_start or fl.from_start
  else
    p.space_location = fl.origin
  end
  p.schedule = {current = 1, records = {{station = fl.destination}}}
  p.paused = false
  if fl.start_speed then p.speed = fl.start_speed end
  fl.filling, fl.start, fl.pos, fl.odometer, fl.furthest = nil, game.tick, fl.from_start, 0, fl.from_start
  fl.deaths, fl.rebuilt, fl.dry, fl.brownout, fl.speed_max, fl.low = {}, {}, 0, 0, 0, 0
  for _, c in pairs(p.hub.get_inventory(defines.inventory.hub_main).get_contents()) do fl.watch[c.name] = true end
  local power = p.surface.global_electric_network_statistics
  fl.power_base = power and {generated = power.output_counts["fusion-generator"] or 0,
                             plant = (power.input_counts["fusion-reactor"] or 0)
                               + (power.input_counts["cryogenic-plant"] or 0)} or {}
  local stats = p.force.get_fluid_production_statistics(p.surface)
  for _, f in pairs{"thruster-fuel", "thruster-oxidizer"} do fl.made_base[f] = stats.get_input_count(f) end
end

local function connection_between(from, to)
  for name, c in pairs(prototypes.space_connection) do
    if (c.from.name == from and c.to.name == to) or (c.from.name == to and c.to.name == from) then return name, c end
  end
end

-- Foundation under every tile an entity of the blueprint covers.
local function footprints(entities, dx, dy)
  local tiles, seen = {}, {}
  for _, e in pairs(entities) do
    local box = prototypes.entity[e.name].collision_box
    local w, h = box.right_bottom.x - box.left_top.x, box.right_bottom.y - box.left_top.y
    if (e.direction or 0) % 8 == 4 then w, h = h, w end
    for x = math.floor(e.position.x - w / 2 + 0.01), math.ceil(e.position.x + w / 2 - 0.01) - 1 do
      for y = math.floor(e.position.y - h / 2 + 0.01), math.ceil(e.position.y + h / 2 - 0.01) - 1 do
        local k = x .. "," .. y
        if not seen[k] then
          seen[k] = true
          tiles[#tiles + 1] = {name = "space-platform-foundation", position = {x + dx, y + dy}}
        end
      end
    end
  end
  return tiles
end

-- at: where to put the source, else the free spot nearest the area's corner.
local function source_for(s, area, poles, at)
  local spot = at or s.find_non_colliding_position("medium-electric-pole", {area[2][1] + 2, area[1][2] + 2}, 64, 1)
  local helper = s.create_entity{name = "medium-electric-pole", position = spot, force = "player"}
  local eei = s.create_entity{
    name = "electric-energy-interface", force = "player",
    position = at and {at[1] + 2.5, at[2] + 0.5} or s.find_non_colliding_position("electric-energy-interface", spot, 3, 0.5),
  }
  eei.power_production = 1e9
  eei.electric_buffer_size = 1e9
  -- One pole from each separate network (blueprints run side by side have one
  -- each), chained to the source by script wires.
  local copper = defines.wire_connector_id.pole_copper
  local last = helper
  for _, pole in pairs(poles) do
    last.get_wire_connector(copper, true).connect_to(pole.get_wire_connector(copper, true), false,
                                                     defines.wire_origin.script)
    last = pole
  end
  return {helper, eei}
end

local function flow_teardown()
  local f = storage.flow
  if not f then return end
  game.speed = 1
  local recipes = game.forces.player.recipes
  for _, name in pairs(f.locked) do recipes[name].enabled = false end
  for _, e in pairs(f.helpers) do
    if e.valid then e.destroy() end
  end
  clear(f.surface, f.area)
  storage.flow = nil
end

local function reply(t)
  rcon.print(helpers.table_to_json(t))
end

remote.add_interface("ghostwriter", {
  circuit_setup = function(text)
    circuit_teardown()
    flow_teardown()
    flight_teardown()
    local inv = game.create_inventory(1)
    local stack = inv[1]
    local rc = stack.import_stack(text)
    if not (stack.valid_for_read and stack.is_blueprint) then
      inv.destroy()
      return reply{error = "not a blueprint (import_stack returned " .. rc .. ")"}
    end
    local out, s, area, built = place(stack)
    powered(s)
    inv.destroy()
    storage.circuit = {surface = s, area = area, built = built, injectors = {}}
    local numbers = {}
    for n in pairs(built) do numbers[#numbers + 1] = n end
    out.built = numbers
    reply(out)
  end,

  circuit_inject = function(json)
    local args = helpers.json_to_table(json)
    local c = storage.circuit
    local target = c and c.built[args.entity]
    if not (target and target.valid) then return reply{error = "entity " .. args.entity .. " was not built"} end
    local connector_id = defines.wire_connector_id[args.connector]
    local target_connector = connector_id and target.get_wire_connector(connector_id, true)
    if not target_connector then return reply{error = target.name .. " has no connector " .. args.connector} end

    local slot = args.entity .. ":" .. args.connector
    local inj = c.injectors[slot]
    if not (inj and inj.valid) then
      local s = c.surface
      local spot = s.find_non_colliding_position("constant-combinator", {c.area[2][1] + 2, c.area[1][2] + 2}, 64, 1)
      inj = s.create_entity{name = "constant-combinator", position = spot, force = "player"}
      local color = args.connector:find("green") and defines.wire_connector_id.circuit_green
                    or defines.wire_connector_id.circuit_red
      inj.get_wire_connector(color, true).connect_to(target_connector, false, defines.wire_origin.script)
      c.injectors[slot] = inj
    end
    local section = inj.get_control_behavior().get_section(1)
    for i = section.filters_count, 1, -1 do section.clear_slot(i) end
    local i = 0
    for name, count in pairs(args.signals or {}) do
      local id = signal_id(name)
      if not id then return reply{error = "unknown signal " .. name} end
      i = i + 1
      section.set_slot(i, {value = id, min = count})
    end
    reply{ok = true}
  end,

  circuit_read = function(json)
    local args = helpers.json_to_table(json)
    local c = storage.circuit
    local entity = c and c.built[args.entity]
    if not (entity and entity.valid) then return reply{error = "entity " .. args.entity .. " was not built"} end
    local out = {}
    if args.connector then
      local id = defines.wire_connector_id[args.connector]
      if not id then return reply{error = "no connector " .. args.connector} end
      out.signals = signals_of(entity.get_circuit_network(id))
    end
    local point = entity.type == "logistic-container"
      and entity.get_logistic_point(defines.logistic_member_index.logistic_container)
    if point then
      out.requests = {}
      for _, f in pairs(point.filters or {}) do
        if f.name and f.count ~= 0 then out.requests[f.name] = f.count end
      end
    end
    for k, v in pairs(defines.entity_status) do
      if v == entity.status then out.status = k end
    end
    reply(out)
  end,

  circuit_teardown = function()
    circuit_teardown()
    reply{ok = true}
  end,

  flow_setup = function(json)
    circuit_teardown()
    flow_teardown()
    flight_teardown()
    local args = helpers.json_to_table(json)
    local inv = game.create_inventory(1)
    local stack = inv[1]
    local rc = stack.import_stack(args.blueprint)
    if not (stack.valid_for_read and stack.is_blueprint) then
      inv.destroy()
      return reply{error = "not a blueprint (import_stack returned " .. rc .. ")"}
    end
    local out, s, area, built = place(stack)
    inv.destroy()
    -- Circuit tests leave the surface on a global network, which would power
    -- every entity whatever the poles do.
    if s.has_global_electric_network then s.destroy_global_electric_network() end
    local f = {surface = s, area = area, built = built, helpers = {}, feeds = {}, drains = {}, counts = {},
               locked = {}, pool = {}, fluid_feeds = {}, pooled_fluids = {}, sinks = {}, caps = {}}
    local networks, poles = {}, {}
    for _, e in pairs(built) do
      if e.valid and e.type == "electric-pole" and not networks[e.electric_network_id] then
        networks[e.electric_network_id] = true
        poles[#poles + 1] = e
      end
    end
    if poles[1] then f.helpers = source_for(s, area, poles) end
    storage.flow = f
    game.speed = args.speed or 1
    -- Furnaces only take what an unlocked recipe smelts (iron plates need
    -- Steel processing); flow tests measure throughput, not research.
    -- (LuaForce.reset_recipes doesn't relock them, so teardown does.)
    for name, recipe in pairs(game.forces.player.recipes) do
      if not recipe.enabled then
        f.locked[#f.locked + 1] = name
        recipe.enabled = true
      end
    end
    reply(out)
  end,

  flow_start = function(json)
    local args = helpers.json_to_table(json)
    local f = storage.flow
    if not f then return reply{error = "no flow_setup"} end
    for _, feed in pairs(args.feeds) do
      local belt = f.built[feed.entity]
      if not (belt and belt.valid and belt.get_max_transport_line_index() >= 2) then
        return reply{error = "entity " .. feed.entity .. " is not a built belt"}
      end
      if not LANES[feed.lane] then return reply{error = "lane is left or right, not " .. feed.lane} end
      if not prototypes.item[feed.item] then return reply{error = "unknown item " .. feed.item} end
      f.feeds[#f.feeds + 1] = {belt = belt, lane = LANES[feed.lane], item = feed.item, pooled = feed.pooled}
    end
    for _, n in pairs(args.drains) do
      local belt = f.built[n]
      if not (belt and belt.valid and belt.get_max_transport_line_index() >= 2) then
        return reply{error = "entity " .. n .. " is not a built belt"}
      end
      f.drains[#f.drains + 1] = belt
    end
    for _, feed in pairs(args.fluids or {}) do
      local target = f.built[feed.entity]
      if not (target and target.valid) then return reply{error = "entity " .. feed.entity .. " was not built"} end
      if not prototypes.fluid[feed.fluid] then return reply{error = "unknown fluid " .. feed.fluid} end
      if feed.pooled then
        -- Fed from the pool straight into the entity; the pool counts what machines make.
        f.fluid_feeds[#f.fluid_feeds + 1] = {entity = target, fluid = feed.fluid}
        local stats = game.forces.player.get_fluid_production_statistics(f.surface)
        f.pooled_fluids[feed.fluid] = stats.get_input_count(feed.fluid)
        goto next_fluid
      end
      -- An unlimited source (or, with sink, an unlimited drain) on the first free
      -- tile beside it, or on the given side; it connects like a pipe.
      local sides = {south = {0, 1}, north = {0, -1}, east = {1, 0}, west = {-1, 0}}
      local tries = feed.side and {sides[feed.side]} or {sides.south, sides.north, sides.east, sides.west}
      if not tries[1] then return reply{error = "side is north, south, east or west, not " .. feed.side} end
      local source
      for _, d in pairs(tries) do
        local at = {target.position.x + d[1], target.position.y + d[2]}
        if f.surface.can_place_entity{name = "infinity-pipe", position = at, force = "player"} then
          source = f.surface.create_entity{name = "infinity-pipe", position = at, force = "player"}
          break
        end
      end
      if not source then return reply{error = "no free tile beside entity " .. feed.entity} end
      if feed.sink then
        -- level > 0 keeps the line that full, so users along it still get some.
        source.set_infinity_pipe_filter{name = feed.fluid, percentage = feed.level or 0, mode = "at-most"}
        f.sinks[#f.sinks + 1] = {entity = source, fluid = feed.fluid}
      else
        source.set_infinity_pipe_filter{name = feed.fluid, percentage = 1, mode = "at-least"}
      end
      f.helpers[#f.helpers + 1] = source
      ::next_fluid::
    end
    for name, cap in pairs(args.caps or {}) do f.caps[name] = cap end
    f.started = true
    reply{ok = true}
  end,

  flow_count = function()
    local f = storage.flow
    if not f then return reply{error = "no flow_setup"} end
    f.counts, f.since = {}, game.tick
    -- Fluids are counted from the force's production statistics: what machines made.
    local stats = game.forces.player.get_fluid_production_statistics(f.surface)
    f.fluid_base = {}
    for name in pairs(prototypes.fluid) do f.fluid_base[name] = stats.get_input_count(name) end
    reply{ok = true}
  end,

  -- Counts since flow_count, and how many of each built entity aren't working.
  flow_read = function()
    local f = storage.flow
    if not (f and f.since) then return reply{error = "no flow_count"} end
    local names = {}
    for k, v in pairs(defines.entity_status) do names[v] = k end
    local stalled = {}
    for _, e in pairs(f.built) do
      local status = e.valid and e.status and names[e.status]
      if status and status ~= "working" then
        local key = e.name .. ": " .. status
        stalled[key] = (stalled[key] or 0) + 1
      end
    end
    local stats = game.forces.player.get_fluid_production_statistics(f.surface)
    local fluids = {}
    for name, base in pairs(f.fluid_base) do
      local made = stats.get_input_count(name) - base
      if made > 0 then fluids[name] = made end
    end
    reply{counts = f.counts, fluids = fluids, ticks = game.tick - f.since, stalled = stalled, pool = f.pool}
  end,

  flow_teardown = function()
    flow_teardown()
    reply{ok = true}
  end,

  -- Fill containers of the built flow blueprint ({entities, item, count}),
  -- or count an item across them ({entities, item}).
  flow_fill = function(json)
    local args = helpers.json_to_table(json)
    local f = storage.flow
    if not f then return reply{error = "no flow_setup"} end
    for _, n in pairs(args.entities) do
      local e = f.built[n]
      if not (e and e.valid) then return reply{error = "entity " .. n .. " was not built"} end
      e.insert{name = args.item, count = args.count}
    end
    reply{ok = true}
  end,

  flow_contents = function(json)
    local args = helpers.json_to_table(json)
    local f = storage.flow
    if not f then return reply{error = "no flow_setup"} end
    local total = 0
    for _, n in pairs(args.entities) do
      local e = f.built[n]
      if not (e and e.valid) then return reply{error = "entity " .. n .. " was not built"} end
      total = total + e.get_item_count(args.item)
    end
    reply{count = total}
  end,

  tick = function()
    rcon.print(game.tick)
  end,

  -- Place every placeable entity, flip its control behaviour settings
  -- (spec: {[LuaXControlBehavior] = {b = {bool attrs}, n = {network attrs}}})
  -- and export each one as its own blueprint.
  harvest = function(spec_json)
    local spec = helpers.json_to_table(spec_json)
    local s = surface()
    local cols, step = 40, 14
    local names = {}
    for name, p in pairs(prototypes.entity) do
      if p.items_to_place_this and not p.hidden then names[#names + 1] = name end
    end
    table.sort(names)
    local rows = math.ceil(#names / cols)
    local area = {{-step, -step}, {cols * step + step, rows * step + step}}
    prepare(s, area)
    local inv = game.create_inventory(1)
    local stack = inv[1]
    local out = {exports = {}, unplaceable = {}}
    for i, name in ipairs(names) do
      local at = {((i - 1) % cols) * step, math.floor((i - 1) / cols) * step}
      local ok, entity = pcall(s.create_entity, {name = name, position = at, force = "player", raise_built = false})
      if ok and entity and entity.valid then
        local cb
        pcall(function() cb = entity.get_or_create_control_behavior() end)
        if cb then
          local flips = spec[cb.object_name] or {b = {}, n = {}}
          for _, attr in pairs(flips.b) do pcall(function() cb[attr] = not cb[attr] end) end
          for _, attr in pairs(flips.n) do pcall(function() cb[attr] = {red = true, green = false} end) end
        end
        local box = entity.bounding_box
        stack.set_stack("blueprint")
        stack.create_blueprint{surface = s, force = "player",
          area = {{box.left_top.x - 0.1, box.left_top.y - 0.1}, {box.right_bottom.x + 0.1, box.right_bottom.y + 0.1}}}
        if stack.is_blueprint_setup() then
          out.exports[#out.exports + 1] = {name = name, type = entity.type, string = stack.export_stack()}
        end
        stack.clear()
      else
        out.unplaceable[#out.unplaceable + 1] = name
      end
    end
    inv.destroy()
    clear(s, area)
    reply(out)
  end,

  -- {blueprint, from, to, start = 0..1, supplies = {item = count}, fuel, power, ammo, research = {series = levels},
  --  speed, take = {item, ...}}
  flight_setup = function(json)
    circuit_teardown()
    flow_teardown()
    flight_teardown()
    local args = helpers.json_to_table(json)
    local force = game.forces.player
    local connection, proto = connection_between(args.from, args.to)
    if not connection then return reply{error = "no space connection between " .. args.from .. " and " .. args.to} end
    if args.start and not (args.start >= 0 and args.start < 1) then
      return reply{error = "start must be from 0 to below 1 (a share of the route), not " .. args.start}
    end
    force.unlock_space_platforms()
    for name in pairs(prototypes.space_location) do force.unlock_space_location(name) end
    local inv = game.create_inventory(1)
    local stack = inv[1]
    local rc = stack.import_stack(args.blueprint)
    if not (stack.valid_for_read and stack.is_blueprint) then
      inv.destroy()
      return reply{error = "not a blueprint (import_stack returned " .. rc .. ")"}
    end
    local research, wrong, partly = set_research(force, args.research)
    if not research then
      restore_research(force, partly)
      inv.destroy()
      return reply{error = wrong}
    end
    -- The flight advances only by flight_step's exact tick counts, so it runs
    -- exactly the ticks asked. Paused only once nothing can refuse the flight:
    -- only flight_teardown unpauses, and a refusal leaves no flight to tear down.
    game.tick_paused = true
    local p = force.create_space_platform{name = "ghostwriter flight " .. game.tick, planet = "nauvis",
                                          starter_pack = "space-platform-starter-pack"}
    p.apply_starter_pack()
    local s = p.surface
    local entities = stack.get_blueprint_entities() or {}
    local tiles = stack.get_blueprint_tiles() or {}
    if #tiles > 0 then
      local laid = {}
      for _, t in pairs(tiles) do laid[#laid + 1] = {name = t.name, position = {t.position.x, t.position.y}} end
      s.set_tiles(laid)
    else
      s.set_tiles(footprints(entities, 0, 0))
    end
    stack.blueprint_snap_to_grid = {2, 2}
    stack.blueprint_absolute_snapping = true
    stack.blueprint_position_relative_to_grid = {0, 0}
    local wanted, built = #entities, 0
    for _ = 1, 4 do
      local ghosts = stack.build_blueprint{surface = s, force = "player", position = {0, 0},
                                          build_mode = defines.build_mode.forced}
      local progress = false
      for _, g in pairs(ghosts) do
        if g.valid and g.name == "entity-ghost" then
          local _, entity = g.revive()
          if entity then progress, built = true, built + 1 end
        end
      end
      if not progress then break end
    end
    for _, g in pairs(s.find_entities_filtered{name = {"entity-ghost", "tile-ghost"}}) do g.destroy() end
    inv.destroy()
    for name, count in pairs(args.supplies or {}) do p.hub.insert{name = name, count = count} end
    -- start: how far along the route (0 to 1, from `from`) the flight begins.
    -- A connection's distance runs from its own `from`, which may be our `to`.
    local reversed = proto.from.name ~= args.from
    local start = args.start or 0
    local helpers_ = {}
    if args.power then
      -- An unlimited source on every separate pole network, like flow tests.
      local networks, poles = {}, {}
      for _, e in pairs(s.find_entities_filtered{type = "electric-pole"}) do
        if not networks[e.electric_network_id] then
          networks[e.electric_network_id] = true
          poles[#poles + 1] = e
        end
      end
      -- The source stands in for power the design doesn't make yet. On the
      -- platform it took a free foundation tile among the turrets (or found
      -- none on a wide one), where asteroids destroyed it and cut the power,
      -- or, made indestructible, it would shield them. Instead it stands on
      -- empty space well behind, past the thrusters' exhaust (90 tiles), wired
      -- by script, and can't be destroyed.
      local back = 0
      for _, t in pairs(s.find_tiles_filtered{name = "space-platform-foundation"}) do
        back = math.max(back, t.position.y)
      end
      if poles[1] then helpers_ = source_for(s, nil, poles, {0.5, back + 120.5}) end
      for _, e in pairs(helpers_) do e.destructible = false end
      -- It makes nothing and starts full, so what it loses is what the platform used.
      if helpers_[2] then
        helpers_[2].power_production = 0
        helpers_[2].electric_buffer_size = BUDGET
        helpers_[2].energy = BUDGET
      end
    end
    storage.flight = {platform = p, surface = s.index, connection = connection, length = proto.length,
                      reversed = reversed, from_start = start, furthest = start, speed_max = 0, deaths = {}, fuel = args.fuel, ammo = args.ammo,
                      thrusters = s.find_entities_filtered{type = "thruster"},
                      turrets = s.find_entities_filtered{type = "ammo-turret"}, helpers = helpers_,
                      cargo = {}, hub_health = p.hub.health, start = game.tick, research = research,
                      supplied = {}, held = {}, rebuilt = {}, source = helpers_[2], power_j = 0, made = {},
                      generated_j = 0, plant_j = 0, brownout = 0, take = args.take, taken = {}, bays_lost = {},
                      fed = {}, dry = 0, origin = args.from, destination = args.to, start_speed = args.start_speed,
                      turn = args.turn, low_fuel = args.low_fuel, low = 0, pos = start, odometer = 0, watch = {},
                      ran_out = {}, filled = {}, made_base = {}}
    -- feed: the hub feeds the turrets by its own inserters, and what leaves it counts as
    -- used (fed), counted from here; loaded: each turret filled once from the hub to what
    -- an inserter fills it to (automated_ammo_count), as its feed would in orbit.
    local hub_inv = p.hub.get_inventory(defines.inventory.hub_main)
    if args.feed then
      storage.flight.feed_start = {}
      for _, name in pairs(args.feed) do storage.flight.feed_start[name] = hub_inv.get_item_count(name) end
    end
    for _, t in pairs(args.loaded and storage.flight.turrets or {}) do
      local ammo = args.loaded[t.name]
      local n = ammo and math.min(prototypes.entity[t.name].automated_ammo_count, hub_inv.get_item_count(ammo)) or 0
      if n > 0 then hub_inv.remove{name = ammo, count = t.insert{name = ammo, count = n}} end
    end
    -- charged and coolant: a plant readied before leaving, every accumulator and railgun
    -- full and each reactor's fuel slot filled with cells, and cold fluoroketone into
    -- each fusion reactor's line. Railguns take power first, as reactors do: empty,
    -- they would take nearly all an accumulator gives, and the reactors never start
    -- (nor would the inserters bringing them cells from the hub, NOTES.md).
    if args.charged then
      for _, e in pairs(s.find_entities_filtered{type = "accumulator"}) do e.energy = e.electric_buffer_size end
      for _, e in pairs(s.find_entities_filtered{name = "railgun-turret"}) do e.energy = e.electric_buffer_size end
      for _, r in pairs(s.find_entities_filtered{type = "fusion-reactor"}) do
        r.insert{name = "fusion-power-cell", count = prototypes.item["fusion-power-cell"].stack_size}
      end
    end
    if args.coolant then
      for _, r in pairs(s.find_entities_filtered{type = "fusion-reactor"}) do
        r.insert_fluid{name = "fluoroketone-cold", amount = args.coolant}
      end
    end
    -- primed (an amount) and start_speed: as if it arrived from the last leg, that much
    -- of each fluid in each thruster once (inserted, it spreads into the line) and
    -- moving (km a tick). The prime counts as supplied.
    if args.primed then
      for _, t in pairs(storage.flight.thrusters) do
        for _, f in pairs{"thruster-fuel", "thruster-oxidizer"} do
          storage.flight.supplied[f] = (storage.flight.supplied[f] or 0) + t.insert_fluid{name = f, amount = args.primed}
        end
      end
    end
    -- fill: readied in orbit first (flight_tick), the hub's feed items held at their stock,
    -- until flight_leave; otherwise it leaves now.
    if args.fill then
      storage.flight.filling = {}
      for _, name in pairs(args.feed or {}) do storage.flight.filling[name] = (args.supplies or {})[name] or 0 end
      p.paused = true
    else
      leave(storage.flight)
    end
    game.speed = args.speed or 1
    reply{entities = wanted, built = built, weight = p.weight, connection = connection, length = proto.length,
          distance = p.distance}
  end,

  flight_read = function()
    local fl = storage.flight
    if not fl then return reply{error = "no flight_setup"} end
    local p = fl.platform
    local alive = p and p.valid and p.hub and p.hub.valid
    local states = {}
    for k, v in pairs(defines.space_platform_state) do states[v] = k end
    reply{ticks = game.tick - fl.start, alive = alive and true or false, state = alive and states[p.state] or "gone",
          furthest = fl.furthest, km = (fl.furthest - fl.from_start) * fl.length, speed = alive and p.speed or 0,
          speed_max = fl.speed_max, hub_health = alive and p.hub.health or 0, deaths = fl.deaths,
          ended = fl.ended, ended_after = fl.ended and (fl.ended.tick - fl.start) / 60, cargo = fl.cargo,
          supplied = fl.supplied, held = fl.held, rebuilt = fl.rebuilt, power_j = fl.power_j, made = fl.made,
          generated_j = fl.generated_j, plant_j = fl.plant_j, brownout = fl.brownout, taken = fl.taken,
          bays_lost = fl.bays_lost, fed = fl.fed, dry = fl.dry, position = fl.pos, odometer = fl.odometer,
          turned = fl.turned and (fl.turned - fl.start) / 60 or 0, back = fl.back and (fl.back - fl.start) / 60 or 0,
          ran_out = fl.ran_out, low_fuel = fl.low, filling = fl.filling ~= nil, drawn = fl.drawn or 0,
          filled = fl.filled, at_turn = fl.at_turn or false}
  end,

  flight_leave = function()
    local fl = storage.flight
    if not (fl and fl.filling) then return reply{error = "no flight readying to leave"} end
    leave(fl)
    reply{ok = true, distance = fl.platform.distance}
  end,

  flight_step = function(ticks)
    game.ticks_to_run = tonumber(ticks)
    reply{ok = true, tick = game.tick}
  end,

  flight_teardown = function()
    flight_teardown()
    reply{ok = true}
  end,

  validate = function(text)
    local inv = game.create_inventory(1)
    local stack = inv[1]
    local result = {import = stack.import_stack(text), builds = {}}
    if stack.valid_for_read then
      result.export = stack.export_stack()
      build_all(stack, "", result.builds)
    end
    inv.destroy()
    rcon.print(helpers.table_to_json(result))
  end,
  version = function()
    rcon.print(script.active_mods["base"])
  end,
})
